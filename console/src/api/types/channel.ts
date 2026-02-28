export interface BaseChannelConfig {
  enabled: boolean;
  bot_prefix: string;
}

export interface IMessageChannelConfig extends BaseChannelConfig {
  db_path: string;
  poll_sec: number;
}

export interface DiscordConfig extends BaseChannelConfig {
  bot_token: string;
  http_proxy: string;
  http_proxy_auth: string;
}

export interface DingTalkConfig extends BaseChannelConfig {
  client_id: string;
  client_secret: string;
}

export interface FeishuConfig extends BaseChannelConfig {
  app_id: string;
  app_secret: string;
  encrypt_key: string;
  verification_token: string;
  media_dir: string;
}

export interface QQConfig extends BaseChannelConfig {
  app_id: string;
  client_secret: string;
}

export interface NapCatConfig extends BaseChannelConfig {
  ws_url: string;
  http_url: string;
  reverse_ws_port: number | null;
  access_token: string;
  admins: number[];
  require_mention: boolean;
  enable_deduplication: boolean;
  allow_private: boolean;
  allowed_groups: number[];
  blocked_users: number[];
  max_message_length: number;
  format_markdown: boolean;
  anti_risk_mode: boolean;
  rate_limit_ms: number;
  auto_approve_requests: boolean;
  keyword_triggers: string[];
  history_limit: number;
}

export type ConsoleConfig = BaseChannelConfig;

export interface ChannelConfig {
  imessage: IMessageChannelConfig;
  discord: DiscordConfig;
  dingtalk: DingTalkConfig;
  feishu: FeishuConfig;
  qq: QQConfig;
  napcat: NapCatConfig;
  console: ConsoleConfig;
}

export type SingleChannelConfig =
  | IMessageChannelConfig
  | DiscordConfig
  | DingTalkConfig
  | FeishuConfig
  | QQConfig
  | NapCatConfig
  | ConsoleConfig;
