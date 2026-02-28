import {
  Drawer,
  Form,
  Input,
  InputNumber,
  Switch,
  Button,
} from "@agentscope-ai/design";
import { LinkOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import type { FormInstance } from "antd";
import type { SingleChannelConfig } from "../../../../api/types";
import type { ChannelKey } from "./constants";
import styles from "../index.module.less";

interface ChannelDrawerProps {
  open: boolean;
  activeKey: ChannelKey | null;
  activeLabel: string;
  form: FormInstance<SingleChannelConfig>;
  saving: boolean;
  initialValues: SingleChannelConfig | undefined;
  onClose: () => void;
  onSubmit: (values: SingleChannelConfig) => void;
}

// DingTalk doc URL
const dingtalkDocUrl = "https://copaw.agentscope.io/docs/channels";

export function ChannelDrawer({
  open,
  activeKey,
  activeLabel,
  form,
  saving,
  initialValues,
  onClose,
  onSubmit,
}: ChannelDrawerProps) {
  const { t } = useTranslation();

  const renderExtraFields = (key: ChannelKey) => {
    switch (key) {
      case "imessage":
        return (
          <>
            <Form.Item
              name="db_path"
              label="DB Path"
              rules={[{ required: true, message: "Please input DB path" }]}
            >
              <Input placeholder="~/Library/Messages/chat.db" />
            </Form.Item>
            <Form.Item
              name="poll_sec"
              label="Poll Interval (sec)"
              rules={[
                { required: true, message: "Please input poll interval" },
              ]}
            >
              <InputNumber min={0.1} step={0.1} style={{ width: "100%" }} />
            </Form.Item>
          </>
        );
      case "discord":
        return (
          <>
            <Form.Item name="bot_token" label="Bot Token">
              <Input.Password placeholder="Discord bot token" />
            </Form.Item>
            <Form.Item name="http_proxy" label="HTTP Proxy">
              <Input placeholder="http://127.0.0.1:18118" />
            </Form.Item>
            <Form.Item name="http_proxy_auth" label="HTTP Proxy Auth">
              <Input placeholder="user:password" />
            </Form.Item>
          </>
        );
      case "dingtalk":
        return (
          <>
            <Form.Item name="client_id" label="Client ID">
              <Input />
            </Form.Item>
            <Form.Item name="client_secret" label="Client Secret">
              <Input.Password />
            </Form.Item>
          </>
        );
      case "feishu":
        return (
          <>
            <Form.Item
              name="app_id"
              label="App ID"
              rules={[{ required: true }]}
            >
              <Input placeholder="cli_xxx" />
            </Form.Item>
            <Form.Item
              name="app_secret"
              label="App Secret"
              rules={[{ required: true }]}
            >
              <Input.Password placeholder="App Secret" />
            </Form.Item>
            <Form.Item name="encrypt_key" label="Encrypt Key">
              <Input placeholder="Optional, for event encryption" />
            </Form.Item>
            <Form.Item name="verification_token" label="Verification Token">
              <Input placeholder="Optional" />
            </Form.Item>
            <Form.Item name="media_dir" label="Media Dir">
              <Input placeholder="~/.copaw/media" />
            </Form.Item>
          </>
        );
      case "qq":
        return (
          <>
            <Form.Item name="app_id" label="App ID">
              <Input />
            </Form.Item>
            <Form.Item name="client_secret" label="Client Secret">
              <Input.Password />
            </Form.Item>
          </>
        );
      case "napcat":
        return (
          <>
            <Form.Item name="ws_url" label="WS URL">
              <Input placeholder="ws://localhost:3001" />
            </Form.Item>
            <Form.Item name="http_url" label="HTTP API URL">
              <Input placeholder="http://localhost:3000" />
            </Form.Item>
            <Form.Item name="reverse_ws_port" label="Reverse WS Port">
              <InputNumber
                min={1}
                max={65535}
                placeholder="留空则不启用反向WS"
                style={{ width: "100%" }}
              />
            </Form.Item>
            <Form.Item name="access_token" label="Access Token">
              <Input.Password placeholder="Optional" />
            </Form.Item>
            <Form.Item name="admins" label="Admins (QQ IDs, comma-separated)">
              <Input placeholder="123456,789012" />
            </Form.Item>
            <Form.Item
              name="require_mention"
              label="Require @Mention (group)"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="allow_private"
              label="Allow Private Messages"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="allowed_groups"
              label="Allowed Groups (comma-separated)"
            >
              <Input placeholder="Leave empty to allow all groups" />
            </Form.Item>
            <Form.Item
              name="blocked_users"
              label="Blocked Users (comma-separated)"
            >
              <Input placeholder="QQ IDs to block" />
            </Form.Item>
            <Form.Item name="max_message_length" label="Max Message Length">
              <InputNumber min={100} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item
              name="format_markdown"
              label="Format Markdown"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="anti_risk_mode"
              label="Anti-Risk Mode"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
            <Form.Item name="rate_limit_ms" label="Rate Limit (ms)">
              <InputNumber min={0} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item
              name="auto_approve_requests"
              label="Auto Approve Friend/Group Requests"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="keyword_triggers"
              label="Keyword Triggers (comma-separated)"
            >
              <Input placeholder="Optional keywords to trigger bot" />
            </Form.Item>
            <Form.Item name="history_limit" label="History Limit">
              <InputNumber min={0} style={{ width: "100%" }} />
            </Form.Item>
          </>
        );
      default:
        return null;
    }
  };

  return (
    <Drawer
      width={420}
      placement="right"
      title={
        <div className={styles.drawerTitle}>
          <span>
            {activeLabel
              ? `${activeLabel} ${t("channels.settings")}`
              : t("channels.channelSettings")}
          </span>
          {activeKey === "dingtalk" && (
            <Button
              type="text"
              size="small"
              icon={<LinkOutlined />}
              onClick={() => window.open(dingtalkDocUrl, "_blank")}
              className={styles.dingtalkDocBtn}
            >
              DingTalk Doc
            </Button>
          )}
        </div>
      }
      open={open}
      onClose={onClose}
      destroyOnClose
    >
      {activeKey && (
        <Form
          form={form}
          layout="vertical"
          initialValues={initialValues}
          onFinish={onSubmit}
        >
          <Form.Item name="enabled" label="Enabled" valuePropName="checked">
            <Switch />
          </Form.Item>

          <Form.Item name="bot_prefix" label="Bot Prefix">
            <Input placeholder="@bot" />
          </Form.Item>

          {renderExtraFields(activeKey)}

          <Form.Item>
            <div className={styles.formActions}>
              <Button onClick={onClose}>{t("common.cancel")}</Button>
              <Button type="primary" htmlType="submit" loading={saving}>
                {t("common.save")}
              </Button>
            </div>
          </Form.Item>
        </Form>
      )}
    </Drawer>
  );
}
