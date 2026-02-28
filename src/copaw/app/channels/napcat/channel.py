# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches,too-many-statements,too-many-instance-attributes
"""NapCat Channel — OneBot v11 over WebSocket.

Supports:
- Forward WS (ws_url): connect to NapCat as client.
- Reverse WS (reverse_ws_port): listen as server, NapCat connects to us.
- HTTP API (http_url): send messages via NapCat HTTP API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

import aiohttp

from agentscope_runtime.engine.schemas.agent_schemas import (
    ContentType,
    TextContent,
)

from ....config.config import NapCatConfig as NapCatChannelConfig
from ..base import (
    BaseChannel,
    OnReplySent,
    OutgoingContentPart,
    ProcessHandler,
)

logger = logging.getLogger(__name__)

RECONNECT_DELAYS = [1, 2, 5, 10, 30, 60]
MAX_RECONNECT_ATTEMPTS = 100


class NapCatChannel(BaseChannel):
    """NapCat channel: OneBot v11 WebSocket <-> CoPaw agent.

    Message flow:
      NapCat WS event -> _handle_event -> enqueue AgentRequest
      AgentResponse -> send() -> NapCat HTTP API
    """

    channel = "napcat"

    def __init__(
        self,
        process: ProcessHandler,
        enabled: bool,
        ws_url: str = "",
        http_url: str = "",
        reverse_ws_port: Optional[int] = None,
        access_token: str = "",
        admins: Optional[List[int]] = None,
        require_mention: bool = True,
        enable_deduplication: bool = True,
        allow_private: bool = True,
        allowed_groups: Optional[List[int]] = None,
        blocked_users: Optional[List[int]] = None,
        max_message_length: int = 4000,
        format_markdown: bool = False,
        anti_risk_mode: bool = False,
        rate_limit_ms: int = 1000,
        auto_approve_requests: bool = False,
        keyword_triggers: Optional[List[str]] = None,
        history_limit: int = 5,
        bot_prefix: str = "",
        on_reply_sent: OnReplySent = None,
        show_tool_details: bool = True,
    ):
        super().__init__(
            process,
            on_reply_sent=on_reply_sent,
            show_tool_details=show_tool_details,
        )
        self.enabled = enabled
        self.ws_url = ws_url
        self.http_url = http_url.rstrip("/") if http_url else ""
        self.reverse_ws_port = reverse_ws_port
        self.access_token = access_token
        self.admins: List[int] = admins or []
        self.require_mention = require_mention
        self.enable_deduplication = enable_deduplication
        self.allow_private = allow_private
        self.allowed_groups: List[int] = allowed_groups or []
        self.blocked_users: List[int] = blocked_users or []
        self.max_message_length = max_message_length
        self.format_markdown = format_markdown
        self.anti_risk_mode = anti_risk_mode
        self.rate_limit_ms = rate_limit_ms
        self.auto_approve_requests = auto_approve_requests
        self.keyword_triggers: List[str] = keyword_triggers or []
        self.history_limit = history_limit
        self.bot_prefix = bot_prefix

        # Self QQ ID (populated after connected)
        self._self_id: Optional[int] = None

        # Rate limiting
        self._last_send_time: float = 0.0
        self._rate_limit_lock = asyncio.Lock()

        # Deduplication: track recent message IDs
        self._seen_message_ids: set = set()
        self._seen_lock = threading.Lock()

        # Forward WS thread
        self._ws_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Reverse WS server (asyncio)
        self._reverse_ws_server: Any = None
        self._reverse_ws_clients: set = set()

        # Active WS send socket (for API calls via WS, unused when http_url set)
        self._ws_send_conn: Any = None
        self._ws_send_lock = threading.Lock()

        # Echo counter for WS API calls
        self._echo_counter = 0
        self._echo_futures: Dict[str, asyncio.Future] = {}
        self._echo_lock = threading.Lock()

        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @classmethod
    def from_env(
        cls,
        process: ProcessHandler,
        on_reply_sent: OnReplySent = None,
    ) -> "NapCatChannel":
        import os
        return cls(
            process=process,
            enabled=os.getenv("NAPCAT_CHANNEL_ENABLED", "0") == "1",
            ws_url=os.getenv("NAPCAT_WS_URL", ""),
            http_url=os.getenv("NAPCAT_HTTP_URL", ""),
            reverse_ws_port=int(os.getenv("NAPCAT_REVERSE_WS_PORT", "0")) or None,
            access_token=os.getenv("NAPCAT_ACCESS_TOKEN", ""),
            on_reply_sent=on_reply_sent,
        )

    @classmethod
    def from_config(
        cls,
        process: ProcessHandler,
        config: NapCatChannelConfig,
        on_reply_sent: OnReplySent = None,
        show_tool_details: bool = True,
    ) -> "NapCatChannel":
        return cls(
            process=process,
            enabled=config.enabled,
            ws_url=config.ws_url or "",
            http_url=config.http_url or "",
            reverse_ws_port=config.reverse_ws_port,
            access_token=config.access_token or "",
            admins=list(config.admins),
            require_mention=config.require_mention,
            enable_deduplication=config.enable_deduplication,
            allow_private=config.allow_private,
            allowed_groups=list(config.allowed_groups),
            blocked_users=list(config.blocked_users),
            max_message_length=config.max_message_length,
            format_markdown=config.format_markdown,
            anti_risk_mode=config.anti_risk_mode,
            rate_limit_ms=config.rate_limit_ms,
            auto_approve_requests=config.auto_approve_requests,
            keyword_triggers=list(config.keyword_triggers),
            history_limit=config.history_limit,
            bot_prefix=config.bot_prefix or "",
            on_reply_sent=on_reply_sent,
            show_tool_details=show_tool_details,
        )

    # ------------------------------------------------------------------ #
    # Incoming message handling
    # ------------------------------------------------------------------ #

    def _is_dedup(self, message_id: Any) -> bool:
        """Return True if this message_id was already seen (duplicate)."""
        if not self.enable_deduplication or not message_id:
            return False
        key = str(message_id)
        with self._seen_lock:
            if key in self._seen_message_ids:
                return True
            self._seen_message_ids.add(key)
            # Keep set bounded
            if len(self._seen_message_ids) > 2000:
                old = list(self._seen_message_ids)[:1000]
                for k in old:
                    self._seen_message_ids.discard(k)
            return False

    def _extract_text_from_message(self, message: Any) -> str:
        """Extract plain text from OneBot v11 message (str or list of segments)."""
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            parts = []
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "text":
                    parts.append(seg.get("data", {}).get("text", ""))
            return "".join(parts)
        return ""

    def _mentions_bot(self, message: Any, self_id: int) -> bool:
        """Return True if the message contains an @self_id CQ code."""
        if isinstance(message, str):
            return f"[CQ:at,qq={self_id}]" in message
        if isinstance(message, list):
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "at":
                    target = str(seg.get("data", {}).get("qq", ""))
                    if target == str(self_id) or target == "all":
                        return False  # @all does not count as mention to bot
                    if target == str(self_id):
                        return True
            # Re-scan for exact self_id
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "at":
                    target = str(seg.get("data", {}).get("qq", ""))
                    if target == str(self_id):
                        return True
        return False

    def _strip_at_mention(self, text: str, self_id: int) -> str:
        """Remove @bot CQ code from text."""
        import re
        text = re.sub(rf"\[CQ:at,qq={self_id}[^\]]*\]", "", text)
        return text.strip()

    def _has_keyword(self, text: str) -> bool:
        """Return True if text contains any keyword trigger."""
        if not self.keyword_triggers:
            return False
        low = text.lower()
        return any(kw.lower() in low for kw in self.keyword_triggers)

    def _handle_event(self, data: Dict[str, Any]) -> None:
        """Dispatch a OneBot v11 event dict to the appropriate handler."""
        post_type = data.get("post_type")

        if post_type == "meta_event":
            meta_type = data.get("meta_event_type")
            if meta_type == "lifecycle":
                if data.get("sub_type") == "connect":
                    self._self_id = data.get("self_id")
                    logger.info("napcat connected self_id=%s", self._self_id)
            elif meta_type == "heartbeat":
                self._self_id = self._self_id or data.get("self_id")
            return

        if post_type == "request" and self.auto_approve_requests:
            request_type = data.get("request_type")
            flag = data.get("flag", "")
            if request_type == "friend" and flag:
                self._approve_request_sync("friend", flag)
            elif request_type == "group" and flag:
                sub_type = data.get("sub_type", "")
                if sub_type == "invite":
                    self._approve_request_sync("group", flag)
            return

        if post_type != "message":
            return

        message_type = data.get("message_type")  # "private" or "group"
        message_id = data.get("message_id")
        user_id = data.get("user_id")
        self_id = data.get("self_id") or self._self_id
        raw_message = data.get("message", "")

        # Ignore self messages
        if user_id and self_id and user_id == self_id:
            return

        # Blocked users
        if user_id and user_id in self.blocked_users:
            logger.debug("napcat blocked user=%s", user_id)
            return

        # Deduplication
        if self._is_dedup(message_id):
            logger.debug("napcat dedup message_id=%s", message_id)
            return

        text = self._extract_text_from_message(raw_message)

        if message_type == "private":
            if not self.allow_private:
                return
            self._handle_private_message(data, user_id, text, message_id)

        elif message_type == "group":
            group_id = data.get("group_id")
            # Allowed groups filter
            if self.allowed_groups and group_id not in self.allowed_groups:
                logger.debug("napcat group %s not in allowed_groups", group_id)
                return
            self._handle_group_message(data, user_id, group_id, text, message_id, self_id)

    def _handle_private_message(
        self,
        data: Dict[str, Any],
        user_id: int,
        text: str,
        message_id: Any,
    ) -> None:
        """Handle a private (direct) message."""
        if not text.strip() and not self._has_keyword(""):
            return
        sender_str = str(user_id)
        meta = {
            "message_type": "private",
            "message_id": message_id,
            "sender_id": sender_str,
            "user_id": user_id,
            "incoming_raw": data,
        }
        native = {
            "channel_id": "napcat",
            "sender_id": sender_str,
            "content_parts": [
                TextContent(type=ContentType.TEXT, text=text.strip()),
            ],
            "meta": meta,
        }
        request = self.build_agent_request_from_native(native)
        request.channel_meta = meta
        if self._enqueue is not None:
            self._enqueue(request)
        logger.info("napcat recv private from=%s text=%r", user_id, text[:80])

    def _handle_group_message(
        self,
        data: Dict[str, Any],
        user_id: int,
        group_id: int,
        text: str,
        message_id: Any,
        self_id: Optional[int],
    ) -> None:
        """Handle a group message."""
        raw_message = data.get("message", "")
        mentioned = self_id and self._mentions_bot(raw_message, self_id)
        has_keyword = self._has_keyword(text)

        # require_mention: must be @bot or keyword
        if self.require_mention and not mentioned and not has_keyword:
            return

        # Strip @bot from text
        if mentioned and self_id:
            text = self._strip_at_mention(text, self_id)

        if not text.strip() and not has_keyword:
            return

        sender_str = str(user_id)
        group_str = str(group_id)
        meta = {
            "message_type": "group",
            "message_id": message_id,
            "sender_id": sender_str,
            "user_id": user_id,
            "group_id": group_id,
            "incoming_raw": data,
        }
        # Session per group (all members share group context)
        session_id = f"napcat:group:{group_str}"
        native = {
            "channel_id": "napcat",
            "sender_id": sender_str,
            "session_id": session_id,
            "content_parts": [
                TextContent(type=ContentType.TEXT, text=text.strip()),
            ],
            "meta": meta,
        }
        request = self.build_agent_request_from_native(native)
        request.channel_meta = meta
        if self._enqueue is not None:
            self._enqueue(request)
        logger.info(
            "napcat recv group=%s from=%s text=%r",
            group_id,
            user_id,
            text[:80],
        )

    def _approve_request_sync(self, request_type: str, flag: str) -> None:
        """Fire-and-forget approve friend/group request."""
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._approve_request_async(request_type, flag),
                self._loop,
            )

    async def _approve_request_async(
        self, request_type: str, flag: str
    ) -> None:
        """Approve friend/group request via HTTP API."""
        if not self.http_url or not self._http:
            return
        try:
            if request_type == "friend":
                endpoint = "/set_friend_add_request"
                body = {"flag": flag, "approve": True}
            else:
                endpoint = "/set_group_add_request"
                body = {"flag": flag, "approve": True, "type": "invite"}
            async with self._http.post(
                self.http_url + endpoint, json=body
            ) as resp:
                if resp.status >= 400:
                    logger.warning(
                        "napcat approve %s failed: %s", request_type, resp.status
                    )
        except Exception:
            logger.exception("napcat approve request failed")

    def build_agent_request_from_native(self, native_payload: Any) -> Any:
        """Build AgentRequest from napcat native dict."""
        payload = native_payload if isinstance(native_payload, dict) else {}
        channel_id = payload.get("channel_id") or self.channel
        sender_id = payload.get("sender_id") or ""
        content_parts = payload.get("content_parts") or []
        meta = payload.get("meta") or {}
        # Use explicit session_id if provided (group messages)
        explicit_session = payload.get("session_id")
        if explicit_session:
            session_id = explicit_session
        else:
            session_id = self.resolve_session_id(sender_id, meta)
        return self.build_agent_request_from_user_content(
            channel_id=channel_id,
            sender_id=sender_id,
            session_id=session_id,
            content_parts=content_parts,
            channel_meta=meta,
        )

    def resolve_session_id(
        self,
        sender_id: str,
        channel_meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        meta = channel_meta or {}
        if meta.get("message_type") == "group":
            group_id = meta.get("group_id")
            if group_id:
                return f"napcat:group:{group_id}"
        return f"napcat:{sender_id}"

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #

    async def _rate_limit(self) -> None:
        """Enforce rate_limit_ms between sends."""
        if self.rate_limit_ms <= 0:
            return
        async with self._rate_limit_lock:
            now = time.monotonic()
            wait = (self.rate_limit_ms / 1000.0) - (now - self._last_send_time)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_send_time = time.monotonic()

    def _chunk_text(self, text: str) -> List[str]:
        """Split text into chunks <= max_message_length."""
        if self.max_message_length <= 0 or len(text) <= self.max_message_length:
            return [text]
        chunks = []
        while text:
            chunks.append(text[: self.max_message_length])
            text = text[self.max_message_length :]
        return chunks

    async def _send_http(self, endpoint: str, body: Dict[str, Any]) -> Any:
        """POST to NapCat HTTP API."""
        if not self.http_url or not self._http:
            return None
        url = self.http_url + endpoint
        headers = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        try:
            async with self._http.post(url, json=body, headers=headers) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    logger.warning("napcat HTTP %s %s: %s", endpoint, resp.status, data)
                return data
        except Exception:
            logger.exception("napcat HTTP API call failed: %s", endpoint)
            return None

    async def send(
        self,
        to_handle: str,
        text: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Send text message via NapCat HTTP API."""
        if not self.enabled or not text.strip():
            return
        meta = meta or {}
        message_type = meta.get("message_type", "private")
        user_id = meta.get("user_id") or to_handle
        group_id = meta.get("group_id")

        prefix = meta.get("bot_prefix", "") or self.bot_prefix
        if prefix and not text.startswith(prefix):
            text = prefix + text

        chunks = self._chunk_text(text.strip())
        for chunk in chunks:
            await self._rate_limit()
            if message_type == "group" and group_id:
                await self._send_http(
                    "/send_group_msg",
                    {"group_id": int(group_id), "message": chunk},
                )
            else:
                await self._send_http(
                    "/send_private_msg",
                    {"user_id": int(user_id) if str(user_id).isdigit() else user_id,
                     "message": chunk},
                )

    async def send_content_parts(
        self,
        to_handle: str,
        parts: List[OutgoingContentPart],
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Send content parts; handle images with CQ codes."""
        meta = meta or {}
        message_type = meta.get("message_type", "private")
        group_id = meta.get("group_id")
        user_id = meta.get("user_id") or to_handle

        text_parts: List[str] = []
        image_urls: List[str] = []

        for p in parts:
            t = getattr(p, "type", None)
            if t == ContentType.TEXT and getattr(p, "text", None):
                text_parts.append(p.text or "")
            elif t == ContentType.REFUSAL and getattr(p, "refusal", None):
                text_parts.append(p.refusal or "")
            elif t == ContentType.IMAGE and getattr(p, "image_url", None):
                image_urls.append(p.image_url)

        body_text = "\n".join(text_parts)
        prefix = meta.get("bot_prefix", "") or self.bot_prefix
        if prefix and body_text:
            body_text = prefix + body_text

        # Build CQ message with embedded images
        cq_parts = []
        if body_text.strip():
            cq_parts.append(body_text.strip())
        for url in image_urls:
            cq_parts.append(f"[CQ:image,file={url}]")

        if cq_parts:
            full_msg = "\n".join(cq_parts)
            chunks = self._chunk_text(full_msg)
            for chunk in chunks:
                await self._rate_limit()
                if message_type == "group" and group_id:
                    await self._send_http(
                        "/send_group_msg",
                        {"group_id": int(group_id), "message": chunk},
                    )
                else:
                    await self._send_http(
                        "/send_private_msg",
                        {"user_id": int(user_id) if str(user_id).isdigit() else user_id,
                         "message": chunk},
                    )

    # ------------------------------------------------------------------ #
    # Forward WS (connect to NapCat)
    # ------------------------------------------------------------------ #

    def _run_forward_ws_forever(self) -> None:
        """Thread: connect to NapCat WS, receive events, reconnect on error."""
        try:
            import websocket
        except ImportError:
            logger.error(
                "websocket-client not installed. pip install websocket-client",
            )
            return

        reconnect_attempts = 0

        while not self._stop_event.is_set():
            headers = {}
            if self.access_token:
                headers["Authorization"] = f"Bearer {self.access_token}"
            logger.info("napcat forward WS connecting to %s", self.ws_url)
            try:
                ws = websocket.create_connection(
                    self.ws_url,
                    header=headers,
                    timeout=15,
                )
            except Exception as e:
                logger.warning("napcat WS connect failed: %s", e)
                delay = RECONNECT_DELAYS[
                    min(reconnect_attempts, len(RECONNECT_DELAYS) - 1)
                ]
                reconnect_attempts += 1
                if reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
                    logger.error("napcat max reconnect attempts reached")
                    return
                self._stop_event.wait(timeout=delay)
                continue

            reconnect_attempts = 0
            with self._ws_send_lock:
                self._ws_send_conn = ws

            try:
                while not self._stop_event.is_set():
                    raw = ws.recv()
                    if not raw:
                        break
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    # API response (echo)
                    if "echo" in data and "retcode" in data:
                        self._handle_api_response(data)
                        continue

                    self._handle_event(data)

            except Exception as e:
                logger.warning("napcat WS recv error: %s", e)
            finally:
                with self._ws_send_lock:
                    if self._ws_send_conn is ws:
                        self._ws_send_conn = None
                try:
                    ws.close()
                except Exception:
                    pass

            if self._stop_event.is_set():
                break
            delay = RECONNECT_DELAYS[
                min(reconnect_attempts, len(RECONNECT_DELAYS) - 1)
            ]
            reconnect_attempts += 1
            if reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
                logger.error("napcat max reconnect attempts reached")
                return
            logger.info("napcat reconnecting in %ss", delay)
            self._stop_event.wait(timeout=delay)

        logger.info("napcat forward WS thread stopped")

    def _handle_api_response(self, data: Dict[str, Any]) -> None:
        """Resolve pending echo futures from WS API calls."""
        echo = str(data.get("echo", ""))
        with self._echo_lock:
            fut = self._echo_futures.pop(echo, None)
        if fut and self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(
                lambda f=fut, d=data: f.set_result(d) if not f.done() else None
            )

    # ------------------------------------------------------------------ #
    # Reverse WS server
    # ------------------------------------------------------------------ #

    async def _handle_reverse_ws_client(
        self, websocket_conn: Any, path: str = "/"
    ) -> None:
        """Handle one reverse WS client connection from NapCat."""
        # Validate token if set
        token = self.access_token
        if token:
            auth = websocket_conn.request_headers.get("Authorization", "")
            if auth != f"Bearer {token}":
                await websocket_conn.close(1008, "Unauthorized")
                return

        self._reverse_ws_clients.add(websocket_conn)
        logger.info("napcat reverse WS client connected")
        try:
            async for raw in websocket_conn:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if "echo" in data and "retcode" in data:
                    self._handle_api_response(data)
                    continue
                self._handle_event(data)
        except Exception as e:
            logger.debug("napcat reverse WS client disconnected: %s", e)
        finally:
            self._reverse_ws_clients.discard(websocket_conn)
            logger.info("napcat reverse WS client disconnected")

    async def _start_reverse_ws_server(self) -> None:
        """Start the reverse WS server listening on reverse_ws_port."""
        try:
            import websockets
        except ImportError:
            logger.error(
                "websockets not installed. pip install websockets",
            )
            return

        self._reverse_ws_server = await websockets.serve(
            self._handle_reverse_ws_client,
            "0.0.0.0",
            self.reverse_ws_port,
        )
        logger.info(
            "napcat reverse WS server listening on port %s",
            self.reverse_ws_port,
        )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        if not self.enabled:
            logger.debug("napcat channel disabled")
            return

        has_forward_ws = bool(self.ws_url)
        has_reverse_ws = bool(self.reverse_ws_port)

        if not has_forward_ws and not has_reverse_ws:
            raise RuntimeError(
                "NapCat channel requires either ws_url (forward WS) or "
                "reverse_ws_port (reverse WS) to be configured."
            )
        if not self.http_url:
            logger.warning(
                "napcat: http_url not set; send() will be no-op. "
                "Set http_url to enable sending messages."
            )

        self._loop = asyncio.get_running_loop()
        self._stop_event.clear()

        self._http = aiohttp.ClientSession()

        if has_forward_ws:
            self._ws_thread = threading.Thread(
                target=self._run_forward_ws_forever,
                daemon=True,
                name="napcat-ws",
            )
            self._ws_thread.start()

        if has_reverse_ws:
            await self._start_reverse_ws_server()

    async def stop(self) -> None:
        if not self.enabled:
            return
        self._stop_event.set()

        if self._ws_thread:
            self._ws_thread.join(timeout=8)
            self._ws_thread = None

        if self._reverse_ws_server:
            self._reverse_ws_server.close()
            try:
                await asyncio.wait_for(
                    self._reverse_ws_server.wait_closed(), timeout=5
                )
            except asyncio.TimeoutError:
                pass
            self._reverse_ws_server = None

        if self._http is not None:
            await self._http.close()
            self._http = None

        logger.info("napcat channel stopped")
