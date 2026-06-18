import React from "react";
import ReactDOM from "react-dom/client";
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  ConfigProvider,
  Drawer,
  Form,
  Input,
  InputNumber,
  Layout,
  Menu,
  Modal,
  Popconfirm,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
  theme
} from "antd";
import {
  ApiOutlined,
  ApartmentOutlined,
  BookOutlined,
  CopyOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  EditOutlined,
  FileSearchOutlined,
  KeyOutlined,
  LockOutlined,
  LogoutOutlined,
  PlusOutlined,
  PoweroffOutlined,
  SaveOutlined,
  SettingOutlined,
  SyncOutlined
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import "./styles.css";

const { Header, Content, Sider } = Layout;
const { Title, Text } = Typography;
const PROVIDERS = ["exa", "tavily"] as const;
type ProviderName = typeof PROVIDERS[number];

type Provider = {
  name: string;
  base_url: string;
  enabled: boolean;
  has_api_key: boolean;
  upstream_key_count: number;
  upstream_keys: ProviderKey[];
  created_at: string;
  updated_at: string;
};

type ProviderKey = {
  id: number;
  provider_name: ProviderName;
  group_id: number | null;
  group_name: string | null;
  label: string;
  enabled: boolean;
  total_quota: number;
  used_quota: number;
  remaining_quota: number;
  is_invalid: boolean;
  last_error: string | null;
  last_status_code: number | null;
  last_synced_at: string | null;
  key_preview: string;
  use_count: number;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
};

type Group = {
  id: number;
  name: string;
  platform: ProviderName;
  enabled: boolean;
  socks5_proxy: string | null;
  created_at: string;
  updated_at: string;
};

type RelayGroup = {
  id: number;
  name: string;
  platform: ProviderName;
  enabled: boolean;
  socks5_proxy: string | null;
};

type RelayKey = {
  id: number;
  label: string;
  group_id: number | null;
  group_name: string | null;
  exa_group_id: number | null;
  exa_group_name: string | null;
  exa_groups: RelayGroup[];
  tavily_group_id: number | null;
  tavily_group_name: string | null;
  tavily_groups: RelayGroup[];
  enabled: boolean;
  daily_limit: number | null;
  key_preview: string;
  has_key_value: boolean;
  created_at: string;
};

type RequestLog = {
  id: number;
  created_at: string;
  provider: string;
  endpoint: string;
  relay_key_label: string | null;
  status_code: number;
  duration_ms: number;
  request_bytes: number;
  response_bytes: number;
  provider_group_id: number | null;
  provider_group_name: string | null;
  error_code: string | null;
  error_message: string | null;
};

type CacheSettings = {
  enabled: boolean;
  ttl_seconds: number;
  max_rows: number;
};

type CacheStats = {
  total: number;
  active: number;
  expired: number;
  total_hits: number;
  approx_bytes: number;
};

type CacheEntry = {
  id: number;
  cache_key: string;
  provider: ProviderName;
  endpoint: string;
  status_code: number;
  content_type: string;
  hit_count: number;
  expires_at: string;
  created_at: string;
  updated_at: string;
  request_bytes: number;
  response_bytes: number;
  is_expired: number;
};

type Metrics = {
  requests_today: number;
  success_rate: number;
  avg_duration_ms: number;
  by_provider: Array<{ provider: string; count: number }>;
};

type PageKey = "dashboard" | "groups" | "providers" | "relay-keys" | "docs" | "cache" | "logs" | "settings";

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const messageText = payload?.error?.message || `Request failed: ${response.status}`;
    throw new Error(messageText);
  }
  return response.json() as Promise<T>;
}

function pathToPage(): PageKey {
  const part = window.location.pathname.replace(/^\/admin\/?/, "") || "dashboard";
  if (["dashboard", "groups", "providers", "relay-keys", "docs", "cache", "logs", "settings"].includes(part)) {
    return part as PageKey;
  }
  return "dashboard";
}

function providerTag(provider: ProviderName) {
  return <Tag color={provider === "exa" ? "blue" : "green"}>{provider}</Tag>;
}

function statusTag(enabled: boolean) {
  return enabled ? <Tag color="green">Enabled</Tag> : <Tag>Disabled</Tag>;
}

function compactDate(value: string | null) {
  return value || "Never";
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function httpStatusTag(statusCode: number) {
  if (statusCode >= 500) return <Tag color="red">{statusCode}</Tag>;
  if (statusCode >= 400) return <Tag color="orange">{statusCode}</Tag>;
  if (statusCode >= 200 && statusCode < 400) return <Tag color="green">{statusCode}</Tag>;
  return <Tag>{statusCode}</Tag>;
}

function groupOptions(groups: Group[], provider: ProviderName) {
  return groups
    .filter((group) => group.platform === provider)
    .map((group) => ({ label: group.name, value: group.id }));
}

function groupTags(groups: RelayGroup[]) {
  if (!groups.length) return <Text type="secondary">Unassigned</Text>;
  return (
    <Space size={4} wrap>
      {groups.map((group) => <Tag key={group.id}>{group.name}</Tag>)}
    </Space>
  );
}

async function copyText(text: string) {
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "-1000px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) {
    throw new Error("Copy failed");
  }
}

function Login({ onLogin }: { onLogin: () => void }) {
  const [loading, setLoading] = React.useState(false);

  async function submit(values: { password: string }) {
    setLoading(true);
    try {
      await api("/api/admin/login", {
        method: "POST",
        body: JSON.stringify(values)
      });
      message.success("Signed in");
      onLogin();
    } catch (error) {
      message.error(error instanceof Error ? error.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-shell">
      <Card className="login-card">
        <Title level={3}>Search Relay</Title>
        <Text type="secondary">Admin Console</Text>
        <Form layout="vertical" onFinish={submit} className="login-form">
          <Form.Item name="password" label="Password" rules={[{ required: true, message: "Enter admin password" }]}>
            <Input.Password prefix={<LockOutlined />} autoFocus />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={loading} block>
            Sign in
          </Button>
        </Form>
      </Card>
    </div>
  );
}

function Dashboard() {
  const [metrics, setMetrics] = React.useState<Metrics | null>(null);
  const [logs, setLogs] = React.useState<RequestLog[]>([]);

  React.useEffect(() => {
    api<{ metrics: Metrics; recent_logs: RequestLog[] }>("/api/admin/dashboard")
      .then((data) => {
        setMetrics(data.metrics);
        setLogs(data.recent_logs);
      })
      .catch((error) => message.error(error.message));
  }, []);

  const failureColumns: ColumnsType<RequestLog> = [
    { title: "Time", dataIndex: "created_at" },
    { title: "Provider", dataIndex: "provider" },
    { title: "Endpoint", dataIndex: "endpoint" },
    { title: "Status", dataIndex: "status_code" },
    { title: "Error", dataIndex: "error_code" }
  ];

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <div className="page-heading">
        <div>
          <Title level={2}>Dashboard</Title>
          <Text type="secondary">Operational overview for relay traffic.</Text>
        </div>
        <Tag color="green">Online</Tag>
      </div>
      <div className="stat-grid">
        <Card><Statistic title="Requests Today" value={metrics?.requests_today ?? 0} /></Card>
        <Card><Statistic title="Success Rate" value={metrics?.success_rate ?? 0} suffix="%" precision={1} /></Card>
        <Card><Statistic title="Average Latency" value={metrics?.avg_duration_ms ?? 0} suffix="ms" precision={1} /></Card>
      </div>
      <Card title="Provider Usage">
        {metrics?.by_provider.length ? (
          <Space wrap>{metrics.by_provider.map((item) => <Tag key={item.provider}>{item.provider}: {item.count}</Tag>)}</Space>
        ) : (
          <Text type="secondary">No requests today.</Text>
        )}
      </Card>
      <Card title="Recent Failures">
        <Table rowKey="id" dataSource={logs.filter((log) => log.status_code >= 400)} columns={failureColumns} pagination={false} size="small" />
      </Card>
    </Space>
  );
}

function Groups() {
  const [groups, setGroups] = React.useState<Group[]>([]);
  const [providers, setProviders] = React.useState<Provider[]>([]);
  const [relayKeys, setRelayKeys] = React.useState<RelayKey[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [modalOpen, setModalOpen] = React.useState(false);
  const [editingGroup, setEditingGroup] = React.useState<Group | null>(null);
  const [form] = Form.useForm<{ name: string; platform: ProviderName; enabled: boolean; socks5_proxy?: string }>();

  async function load() {
    setLoading(true);
    try {
      const [groupData, providerData, relayKeyData] = await Promise.all([
        api<{ groups: Group[] }>("/api/admin/groups"),
        api<{ providers: Provider[] }>("/api/admin/providers"),
        api<{ relay_keys: RelayKey[] }>("/api/admin/relay-keys")
      ]);
      setGroups(groupData.groups);
      setProviders(providerData.providers);
      setRelayKeys(relayKeyData.relay_keys);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "Failed to load groups");
    } finally {
      setLoading(false);
    }
  }

  React.useEffect(() => { void load(); }, []);

  function openCreate() {
    setEditingGroup(null);
    form.setFieldsValue({ name: "", platform: "exa", enabled: true, socks5_proxy: "" });
    setModalOpen(true);
  }

  function openEdit(group: Group) {
    setEditingGroup(group);
    form.setFieldsValue({
      name: group.name,
      platform: group.platform,
      enabled: group.enabled,
      socks5_proxy: group.socks5_proxy ?? ""
    });
    setModalOpen(true);
  }

  async function save(values: { name: string; platform: ProviderName; enabled?: boolean; socks5_proxy?: string }) {
    if (editingGroup) {
      await api(`/api/admin/groups/${editingGroup.id}`, {
        method: "PUT",
        body: JSON.stringify({
          name: values.name,
          enabled: values.enabled ?? true,
          socks5_proxy: values.socks5_proxy?.trim() || null
        })
      });
      message.success("Group updated");
    } else {
      await api("/api/admin/groups", {
        method: "POST",
        body: JSON.stringify({
          name: values.name,
          platform: values.platform,
          enabled: values.enabled ?? true,
          socks5_proxy: values.socks5_proxy?.trim() || null
        })
      });
      message.success("Group created");
    }
    setModalOpen(false);
    await load();
  }

  async function setEnabled(group: Group, enabled: boolean) {
    await api(`/api/admin/groups/${group.id}/${enabled ? "enable" : "disable"}`, { method: "POST" });
    message.success(`${group.name} ${enabled ? "enabled" : "disabled"}`);
    await load();
  }

  const upstreamKeys = providers.flatMap((provider) => provider.upstream_keys);

  function groupStats(group: Group) {
    return {
      upstream: upstreamKeys.filter((key) => key.group_id === group.id).length,
      external: relayKeys.filter((key) =>
        key.exa_groups.some((item) => item.id === group.id) ||
        key.tavily_groups.some((item) => item.id === group.id)
      ).length
    };
  }

  const columns: ColumnsType<Group> = [
    { title: "ID", dataIndex: "id", width: 80 },
    { title: "Platform", render: (_, group) => providerTag(group.platform), width: 120 },
    {
      title: "Group",
      render: (_, group) => (
        <Space direction="vertical" size={0}>
          <Text strong>{group.name}</Text>
          <Text type="secondary">Updated {group.updated_at}</Text>
        </Space>
      )
    },
    {
      title: "Proxy",
      width: 240,
      render: (_, group) => group.socks5_proxy ? (
        <Tooltip title={group.socks5_proxy}>
          <Text code className="proxy-text">{group.socks5_proxy}</Text>
        </Tooltip>
      ) : <Text type="secondary">Direct</Text>
    },
    {
      title: "Bindings",
      width: 190,
      render: (_, group) => {
        const stats = groupStats(group);
        return (
          <Space wrap size={4}>
            <Tag>{stats.upstream} platform</Tag>
            <Tag>{stats.external} API</Tag>
          </Space>
        );
      }
    },
    { title: "Status", render: (_, group) => statusTag(group.enabled), width: 120 },
    { title: "Created", dataIndex: "created_at", width: 180 },
    {
      title: "Actions",
      width: 210,
      render: (_, group) => (
        <Space>
          <Button icon={<EditOutlined />} onClick={() => openEdit(group)}>Edit</Button>
          <Button icon={<PoweroffOutlined />} onClick={() => setEnabled(group, !group.enabled)}>
            {group.enabled ? "Disable" : "Enable"}
          </Button>
        </Space>
      )
    }
  ];

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <div className="page-heading">
        <div>
          <Title level={2}>Groups</Title>
          <Text type="secondary">Provider-specific pools used to isolate upstream keys.</Text>
        </div>
        <Space>
          <Tag color="blue">{groups.filter((group) => group.enabled).length} active</Tag>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>Add Group</Button>
        </Space>
      </div>
      <Card title="Group List" className="table-card">
        <Table rowKey="id" loading={loading} dataSource={groups} columns={columns} scroll={{ x: 1120 }} size="small" />
      </Card>
      <Modal
        title={editingGroup ? "Edit Group" : "Add Group"}
        open={modalOpen}
        okText={editingGroup ? "Save" : "Create"}
        onOk={() => form.submit()}
        onCancel={() => setModalOpen(false)}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={save} initialValues={{ platform: "exa", enabled: true }}>
          <Form.Item name="platform" label="Platform" rules={[{ required: true, message: "Platform is required" }]}>
            <Select
              disabled={!!editingGroup}
              options={PROVIDERS.map((provider) => ({ label: provider, value: provider }))}
            />
          </Form.Item>
          <Form.Item name="name" label="Group Name" rules={[{ required: true, message: "Group name is required" }]}>
            <Input placeholder="Group name" />
          </Form.Item>
          <Form.Item name="enabled" label="Enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item
            name="socks5_proxy"
            label="SOCKS5 Proxy"
            extra="Use socks5://host:port or socks5h://host:port. Leave empty for direct upstream access."
          >
            <Input placeholder="socks5://127.0.0.1:1080" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

function Providers() {
  const [providers, setProviders] = React.useState<Provider[]>([]);
  const [groups, setGroups] = React.useState<Group[]>([]);
  const [selectedProvider, setSelectedProvider] = React.useState<ProviderName>("exa");
  const [loading, setLoading] = React.useState(false);
  const [modalOpen, setModalOpen] = React.useState(false);
  const [editingKey, setEditingKey] = React.useState<ProviderKey | null>(null);
  const [form] = Form.useForm<{
    provider_name: ProviderName;
    group_id: number;
    label: string;
    api_key?: string;
    total_quota?: number;
    enabled?: boolean;
  }>();

  async function load() {
    setLoading(true);
    try {
      const [providerData, groupData] = await Promise.all([
        api<{ providers: Provider[] }>("/api/admin/providers"),
        api<{ groups: Group[] }>("/api/admin/groups")
      ]);
      setProviders(providerData.providers);
      setGroups(groupData.groups);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "Failed to load providers");
    } finally {
      setLoading(false);
    }
  }

  React.useEffect(() => { void load(); }, []);

  async function saveProvider(provider: Provider, values: { enabled: boolean }) {
    await api(`/api/admin/providers/${provider.name}`, {
      method: "PUT",
      body: JSON.stringify(values)
    });
    message.success(`${provider.name} saved`);
    await load();
  }

  function openCreateProviderKey() {
    setEditingKey(null);
    setSelectedProvider("exa");
    form.setFieldsValue({ provider_name: "exa", group_id: undefined, label: "", api_key: "", total_quota: 1000, enabled: true });
    setModalOpen(true);
  }

  function openEditProviderKey(key: ProviderKey) {
    setEditingKey(key);
    setSelectedProvider(key.provider_name);
    form.setFieldsValue({
      provider_name: key.provider_name,
      group_id: key.group_id ?? undefined,
      label: key.label,
      api_key: "",
      total_quota: key.total_quota,
      enabled: key.enabled
    });
    setModalOpen(true);
  }

  async function saveProviderKey(values: { provider_name: ProviderName; group_id: number; label: string; api_key?: string; total_quota?: number; enabled?: boolean }) {
    if (editingKey) {
      await api(`/api/admin/providers/${editingKey.provider_name}/keys/${editingKey.id}`, {
        method: "PUT",
        body: JSON.stringify({
          label: values.label,
          api_key: values.api_key?.trim() || undefined,
          group_id: values.group_id,
          total_quota: values.total_quota ?? editingKey.total_quota,
          enabled: values.enabled ?? true
        })
      });
      message.success(`${editingKey.label} updated`);
    } else {
      await api(`/api/admin/providers/${values.provider_name}/keys`, {
        method: "POST",
        body: JSON.stringify({ ...values, total_quota: values.total_quota ?? 1000, enabled: values.enabled ?? true })
      });
      message.success(`${values.provider_name} key added`);
    }
    setModalOpen(false);
    await load();
  }

  async function syncProviderKeyUsage(key: ProviderKey) {
    await api(`/api/admin/providers/${key.provider_name}/keys/${key.id}/sync-usage`, {
      method: "POST"
    });
    message.success(`${key.label} usage synced`);
    await load();
  }

  async function setProviderKeyEnabled(key: ProviderKey, enabled: boolean) {
    await api(`/api/admin/providers/${key.provider_name}/keys/${key.id}/${enabled ? "enable" : "disable"}`, {
      method: "POST"
    });
    message.success(`${key.label} ${enabled ? "enabled" : "disabled"}`);
    await load();
  }

  async function deleteProviderKey(key: ProviderKey) {
    await api(`/api/admin/providers/${key.provider_name}/keys/${key.id}`, { method: "DELETE" });
    message.success(`${key.label} deleted`);
    await load();
  }

  const upstreamKeys = providers.flatMap((provider) => provider.upstream_keys);

  const keyColumns: ColumnsType<ProviderKey> = [
    { title: "Platform", render: (_, key) => providerTag(key.provider_name), width: 110 },
    { title: "Group", render: (_, key) => key.group_name ? <Tag>{key.group_name}</Tag> : <Text type="secondary">Unassigned</Text>, width: 160 },
    {
      title: "Key",
      render: (_, key) => (
        <Space direction="vertical" size={0}>
          <Text strong>{key.label}</Text>
          <Text code>{key.key_preview}</Text>
        </Space>
      )
    },
    {
      title: "Quota",
      width: 180,
      render: (_, key) => (
        <Space direction="vertical" size={0}>
          <Text>{key.used_quota} / {key.total_quota}</Text>
          <Text type="secondary">{key.remaining_quota} remaining</Text>
        </Space>
      )
    },
    {
      title: "Status",
      render: (_, key) => {
        if (key.is_invalid) return <Tag color="red">Invalid</Tag>;
        return statusTag(key.enabled);
      },
      width: 110
    },
    { title: "Used", dataIndex: "use_count", width: 90 },
    {
      title: "Activity",
      width: 230,
      render: (_, key) => (
        <Space direction="vertical" size={0}>
          <Text>Used: {compactDate(key.last_used_at)}</Text>
          <Text type="secondary">Sync: {compactDate(key.last_synced_at)}</Text>
        </Space>
      )
    },
    {
      title: "Last Error",
      width: 180,
      render: (_, key) => key.last_error ? (
        <Tooltip title={key.last_error}>
          <Tag color="red">{key.last_status_code ?? "error"}</Tag>
        </Tooltip>
      ) : <Text type="secondary">None</Text>
    },
    {
      title: "Actions",
      width: 320,
      render: (_, key) => (
        <Space>
          <Button icon={<EditOutlined />} onClick={() => openEditProviderKey(key)}>Edit</Button>
          {key.provider_name === "tavily" && (
            <Button
              icon={<SyncOutlined />}
              disabled={key.is_invalid}
              onClick={() => syncProviderKeyUsage(key)}
            >
              Sync
            </Button>
          )}
          <Button
            icon={<PoweroffOutlined />}
            disabled={key.is_invalid}
            onClick={() => setProviderKeyEnabled(key, !key.enabled)}
          >
            {key.enabled ? "Disable" : "Enable"}
          </Button>
          <Popconfirm
            title="Delete upstream key?"
            description={`Remove ${key.label} from ${key.provider_name}.`}
            okText="Delete"
            okButtonProps={{ danger: true }}
            onConfirm={() => deleteProviderKey(key)}
          >
            <Button danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      )
    }
  ];

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <div className="page-heading">
        <div>
          <Title level={2}>Platform Keys</Title>
          <Text type="secondary">Unified upstream key pool. Platform and group tags decide routing.</Text>
        </div>
        <Space>
          <Tag color="blue">{upstreamKeys.length} upstream keys</Tag>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreateProviderKey}>Add Platform Key</Button>
        </Space>
      </div>
      <div className="platform-status-grid">
        {providers.map((provider) => (
          <Card key={provider.name} title={`${provider.name.toUpperCase()} Status`} loading={loading}>
            <Form
              key={`${provider.name}-${provider.enabled}`}
              layout="inline"
              initialValues={{ enabled: provider.enabled }}
              onFinish={(values) => saveProvider(provider, values)}
              className="provider-status-form"
            >
              <Alert
                type={provider.enabled ? "success" : "warning"}
                showIcon
                message={provider.enabled ? "Enabled" : "Disabled"}
                description={provider.upstream_key_count ? `${provider.upstream_key_count} upstream keys configured.` : "No upstream API keys configured."}
                className="form-alert"
              />
              <Form.Item name="enabled" label="Enabled" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Button type="primary" htmlType="submit" icon={<SaveOutlined />}>Save</Button>
            </Form>
          </Card>
        ))}
      </div>
      <Card title="Platform Key List" className="table-card">
        <Table
          rowKey={(key) => `${key.provider_name}-${key.id}`}
          loading={loading}
          dataSource={upstreamKeys}
          columns={keyColumns}
          scroll={{ x: 1450 }}
          size="small"
        />
      </Card>
      <Modal
        title={editingKey ? "Edit Platform Key" : "Add Platform Key"}
        open={modalOpen}
        okText={editingKey ? "Save" : "Add Key"}
        onOk={() => form.submit()}
        onCancel={() => setModalOpen(false)}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={saveProviderKey}
          onValuesChange={(changed) => {
            if (!editingKey && changed.provider_name) setSelectedProvider(changed.provider_name);
          }}
          initialValues={{ provider_name: "exa", total_quota: 1000, enabled: true }}
        >
          <Form.Item name="provider_name" label="Platform" rules={[{ required: true, message: "Platform is required" }]}>
            <Select
              disabled={!!editingKey}
              options={PROVIDERS.map((provider) => ({ label: provider, value: provider }))}
            />
          </Form.Item>
          <Form.Item name="group_id" label="Group" rules={[{ required: true, message: "Group is required" }]}>
            <Select placeholder="Group" options={groupOptions(groups, selectedProvider)} />
          </Form.Item>
          <Form.Item name="label" label="Label" rules={[{ required: true, message: "Label is required" }]}>
            <Input placeholder="Key label" />
          </Form.Item>
          <Form.Item
            name="api_key"
            label={editingKey ? "Replace API Key" : "API Key"}
            rules={editingKey ? [] : [{ required: true, message: "API key is required" }]}
          >
            <Input.Password placeholder={editingKey ? "Leave blank to keep existing key" : "Upstream API key"} />
          </Form.Item>
          <Form.Item name="total_quota" label="Total Quota" rules={[{ required: true, message: "Quota is required" }]}>
            <InputNumber min={1} className="full-width-input" />
          </Form.Item>
          <Form.Item name="enabled" label="Enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

function RelayKeys() {
  const [keys, setKeys] = React.useState<RelayKey[]>([]);
  const [groups, setGroups] = React.useState<Group[]>([]);
  const [createdKey, setCreatedKey] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [modalOpen, setModalOpen] = React.useState(false);
  const [editingKey, setEditingKey] = React.useState<RelayKey | null>(null);
  const [form] = Form.useForm<{
    label: string;
    exa_group_ids?: number[];
    tavily_group_ids?: number[];
    daily_limit?: number;
    enabled?: boolean;
  }>();

  async function load() {
    setLoading(true);
    try {
      const [keyData, groupData] = await Promise.all([
        api<{ relay_keys: RelayKey[] }>("/api/admin/relay-keys"),
        api<{ groups: Group[] }>("/api/admin/groups")
      ]);
      setKeys(keyData.relay_keys);
      setGroups(groupData.groups);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "Failed to load API keys");
    } finally {
      setLoading(false);
    }
  }

  React.useEffect(() => { void load(); }, []);

  function openCreate() {
    setEditingKey(null);
    form.setFieldsValue({
      label: "",
      exa_group_ids: [],
      tavily_group_ids: [],
      daily_limit: undefined,
      enabled: true
    });
    setModalOpen(true);
  }

  function openEdit(key: RelayKey) {
    setEditingKey(key);
    form.setFieldsValue({
      label: key.label,
      exa_group_ids: key.exa_groups.map((group) => group.id),
      tavily_group_ids: key.tavily_groups.map((group) => group.id),
      daily_limit: key.daily_limit ?? undefined,
      enabled: key.enabled
    });
    setModalOpen(true);
  }

  async function save(values: {
    label: string;
    exa_group_ids?: number[];
    tavily_group_ids?: number[];
    daily_limit?: number;
    enabled?: boolean;
  }) {
    const payload = {
      label: values.label,
      exa_group_ids: values.exa_group_ids ?? [],
      tavily_group_ids: values.tavily_group_ids ?? [],
      daily_limit: values.daily_limit ?? null,
      enabled: values.enabled ?? true
    };
    if (editingKey) {
      await api(`/api/admin/relay-keys/${editingKey.id}`, {
        method: "PUT",
        body: JSON.stringify(payload)
      });
      message.success("API key updated");
    } else {
      const data = await api<{ relay_key: string }>("/api/admin/relay-keys", {
        method: "POST",
        body: JSON.stringify(payload)
      });
      setCreatedKey(data.relay_key);
      message.success("Relay key created");
    }
    setModalOpen(false);
    await load();
  }

  async function copyKey(key: RelayKey) {
    const data = await api<{ relay_key: string }>(`/api/admin/relay-keys/${key.id}/value`);
    await copyText(data.relay_key);
    message.success("API key copied");
  }

  async function setEnabled(key: RelayKey, enabled: boolean) {
    await api(`/api/admin/relay-keys/${key.id}/${enabled ? "enable" : "disable"}`, { method: "POST" });
    await load();
  }

  async function deleteKey(key: RelayKey) {
    await api(`/api/admin/relay-keys/${key.id}`, { method: "DELETE" });
    message.success(`${key.label} deleted`);
    await load();
  }

  const columns: ColumnsType<RelayKey> = [
    { title: "ID", dataIndex: "id", width: 80 },
    {
      title: "API Key",
      render: (_, key) => (
        <Space direction="vertical" size={0}>
          <Text strong>{key.label}</Text>
          {key.key_preview ? <Text code>{key.key_preview}</Text> : <Text type="secondary">Legacy key</Text>}
        </Space>
      )
    },
    {
      title: "Groups",
      width: 260,
      render: (_, key) => (
        <Space direction="vertical" size={2}>
          <Space size={4}>{providerTag("exa")} {groupTags(key.exa_groups)}</Space>
          <Space size={4}>{providerTag("tavily")} {groupTags(key.tavily_groups)}</Space>
        </Space>
      )
    },
    { title: "Status", render: (_, key) => statusTag(key.enabled), width: 110 },
    { title: "Daily Limit", render: (_, key) => key.daily_limit ?? "Unlimited", width: 120 },
    { title: "Created", dataIndex: "created_at", width: 180 },
    {
      title: "Actions",
      width: 330,
      render: (_, key) => (
        <Space>
          <Button icon={<EditOutlined />} onClick={() => openEdit(key)}>Edit</Button>
          <Button
            icon={<CopyOutlined />}
            disabled={!key.has_key_value}
            title={key.has_key_value ? "Copy API key" : "Legacy key cannot be copied"}
            onClick={() => copyKey(key)}
          >
            Copy
          </Button>
          <Button onClick={() => setEnabled(key, !key.enabled)}>
            {key.enabled ? "Disable" : "Enable"}
          </Button>
          <Popconfirm
            title="Delete API key?"
            description={`Remove ${key.label}. Existing clients using this key will stop working.`}
            okText="Delete"
            okButtonProps={{ danger: true }}
            onConfirm={() => deleteKey(key)}
          >
            <Button danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      )
    }
  ];

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <div className="page-heading">
        <div>
          <Title level={2}>API Keys</Title>
          <Text type="secondary">External keys used by clients calling `/exa/*` and `/tavily/*`.</Text>
        </div>
        <Space>
          <Tag color="blue">{keys.filter((key) => key.enabled).length} active</Tag>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>Create API Key</Button>
        </Space>
      </div>
      {createdKey && (
        <Alert
          type="success"
          showIcon
          closable
          onClose={() => setCreatedKey(null)}
          message="Copy this key now"
          description={
            <Space direction="vertical" size="small">
              <Typography.Text code>{createdKey}</Typography.Text>
              <Button size="small" icon={<CopyOutlined />} onClick={() => copyText(createdKey).then(() => message.success("API key copied"))}>
                Copy API Key
              </Button>
            </Space>
          }
        />
      )}
      <Card title="External Key List" className="table-card">
        <Table rowKey="id" loading={loading} dataSource={keys} columns={columns} scroll={{ x: 1100 }} size="small" />
      </Card>
      <Modal
        title={editingKey ? "Edit API Key" : "Create API Key"}
        open={modalOpen}
        okText={editingKey ? "Save" : "Create"}
        onOk={() => form.submit()}
        onCancel={() => setModalOpen(false)}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={save} initialValues={{ enabled: true }}>
          <Form.Item name="label" label="Label" rules={[{ required: true, message: "Label is required" }]}>
            <Input placeholder="Label" />
          </Form.Item>
          <Form.Item name="exa_group_ids" label="Exa Groups">
            <Select mode="multiple" allowClear placeholder="Exa groups" options={groupOptions(groups, "exa")} />
          </Form.Item>
          <Form.Item name="tavily_group_ids" label="Tavily Groups">
            <Select mode="multiple" allowClear placeholder="Tavily groups" options={groupOptions(groups, "tavily")} />
          </Form.Item>
          <Form.Item name="daily_limit" label="Daily Limit">
            <InputNumber min={0} placeholder="Unlimited" className="full-width-input" />
          </Form.Item>
          <Form.Item name="enabled" label="Enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

function Docs() {
  const baseUrl = window.location.origin;
  const exaExample = `import requests

BASE_URL = "${baseUrl}"
RELAY_KEY = "relay_xxx"

response = requests.post(
    f"{BASE_URL}/exa/search",
    headers={
        "Authorization": f"Bearer {RELAY_KEY}",
        "Content-Type": "application/json",
    },
    json={
        "query": "latest AI search APIs",
        "numResults": 3,
    },
    timeout=60,
)

response.raise_for_status()
print(response.json())`;

  const tavilyExample = `import requests

BASE_URL = "${baseUrl}"
RELAY_KEY = "relay_xxx"

response = requests.post(
    f"{BASE_URL}/tavily/search",
    headers={
        "Authorization": f"Bearer {RELAY_KEY}",
        "Content-Type": "application/json",
    },
    json={
        "query": "latest AI search APIs",
        "max_results": 3,
    },
    timeout=60,
)

response.raise_for_status()
print(response.json())`;

  const bodyAuthExample = `import requests

response = requests.post(
    "${baseUrl}/exa/search",
    json={
        "api_key": "relay_xxx",
        "query": "latest AI search APIs",
        "numResults": 3,
    },
    timeout=60,
)

response.raise_for_status()
print(response.json())`;

  const noCacheExample = `import requests

response = requests.post(
    "${baseUrl}/exa/search?no_cache=true",
    headers={"Authorization": "Bearer relay_xxx"},
    json={"query": "latest AI search APIs", "numResults": 3},
    timeout=60,
)

response.raise_for_status()
print(response.json())`;

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <div className="page-heading">
        <div>
          <Title level={2}>使用文档</Title>
          <Text type="secondary">面向客户端接入的中文说明，示例使用 Python requests。</Text>
        </div>
        <Tag color="blue">Python</Tag>
      </div>

      <Card title="核心概念" className="table-card">
        <Space direction="vertical" size="small">
          <Text>客户端只需要使用 API Keys 页面生成的对外 Key，例如 <Text code>relay_xxx</Text>。</Text>
          <Text>管理员在 Platform Keys 页面维护 Exa、Tavily 等上游真实 Key，并通过分组隔离不同池子。</Text>
          <Text>一个对外 Key 可以分别绑定一个 Exa 分组和一个 Tavily 分组；访问不同平台时只会使用对应分组内的上游 Key。</Text>
          <Text type="secondary">当前没有统一聚合 <Text code>/search</Text> 路由，请分别使用 <Text code>/exa/*</Text> 和 <Text code>/tavily/*</Text>。</Text>
        </Space>
      </Card>

      <Card title="认证方式" className="table-card">
        <Space direction="vertical" size="small">
          <Text>推荐使用 Bearer Header：</Text>
          <Typography.Paragraph copyable code>
            Authorization: Bearer relay_xxx
          </Typography.Paragraph>
          <Text>兼容方式：JSON body 字段 <Text code>api_key</Text> / <Text code>apiKey</Text>，或 query 参数 <Text code>api_key</Text> / <Text code>apiKey</Text>。</Text>
          <Text type="secondary">query 参数可能出现在日志中，生产环境优先使用 Bearer Header。</Text>
        </Space>
      </Card>

      <Card title="路由和 Key 池" className="table-card">
        <Space direction="vertical" size="small">
          <Text>访问 <Text code>/exa/search</Text> 时，只会从该对外 Key 绑定的 Exa 分组中选择上游 Key。</Text>
          <Text>访问 <Text code>/tavily/search</Text> 时，只会从该对外 Key 绑定的 Tavily 分组中选择上游 Key。</Text>
          <Text>同一分组内优先使用启用、有效、剩余额度最多的上游 Key。</Text>
          <Text>如果某个上游 Key 返回 <Text code>401</Text>、额度耗尽、限流或临时 <Text code>5xx</Text>，中转服务会自动尝试同组下一个 Key。</Text>
          <Text type="secondary">如果对外 Key 没有绑定对应平台分组，会返回 <Text code>provider_group_unassigned</Text>。</Text>
        </Space>
      </Card>

      <Card title="支持端点" className="table-card">
        <Space wrap>
          <Text code>/exa/search</Text>
          <Text code>/exa/contents</Text>
          <Text code>/exa/answer</Text>
          <Text code>/tavily/search</Text>
          <Text code>/tavily/extract</Text>
          <Text code>/tavily/crawl</Text>
          <Text code>/tavily/map</Text>
          <Text code>/tavily/research</Text>
        </Space>
      </Card>

      <Card title="Python 示例：Exa Search" className="table-card">
        <Typography.Paragraph copyable>
          <pre>{exaExample}</pre>
        </Typography.Paragraph>
      </Card>

      <Card title="Python 示例：Tavily Search" className="table-card">
        <Typography.Paragraph copyable>
          <pre>{tavilyExample}</pre>
        </Typography.Paragraph>
      </Card>

      <Card title="Python 示例：body 传入 API Key" className="table-card">
        <Space direction="vertical" size="small">
          <Text>这种方式用于兼容不能设置 Header 的客户端。中转服务会在转发给上游前移除 <Text code>api_key</Text> / <Text code>apiKey</Text>。</Text>
          <Typography.Paragraph copyable>
            <pre>{bodyAuthExample}</pre>
          </Typography.Paragraph>
        </Space>
      </Card>

      <Card title="搜索缓存" className="table-card">
        <Space direction="vertical" size="small">
          <Text>缓存仅用于成功的 <Text code>/exa/search</Text> 和 <Text code>/tavily/search</Text> 响应。</Text>
          <Text>缓存按平台、端点、对外 Key 绑定的平台分组、清洗后的 query 参数和请求体隔离。</Text>
          <Text>响应头 <Text code>X-Search-Relay-Cache: hit</Text> 表示命中缓存，<Text code>miss</Text> 表示访问了上游并写入缓存。</Text>
          <Typography.Paragraph copyable>
            <pre>{noCacheExample}</pre>
          </Typography.Paragraph>
        </Space>
      </Card>

      <Card title="常见错误码" className="table-card">
        <Space direction="vertical" size="small">
          <Text><Text code>relay_auth_failed</Text>：缺少或错误的对外 API Key。</Text>
          <Text><Text code>relay_key_disabled</Text>：对外 API Key 已禁用。</Text>
          <Text><Text code>provider_group_unassigned</Text>：该 Key 未绑定对应平台分组。</Text>
          <Text><Text code>group_disabled</Text>：绑定的平台分组已禁用。</Text>
          <Text><Text code>unsupported_route</Text>：平台不支持该端点。</Text>
          <Text><Text code>daily_limit_exceeded</Text>：对外 API Key 达到每日限制。</Text>
          <Text><Text code>provider_unavailable</Text>：平台未启用或分组内没有可用上游 Key。</Text>
          <Text><Text code>upstream_timeout</Text>：上游请求超时。</Text>
        </Space>
      </Card>
    </Space>
  );
}

function CacheCenter() {
  const [settings, setSettings] = React.useState<CacheSettings | null>(null);
  const [stats, setStats] = React.useState<CacheStats | null>(null);
  const [entries, setEntries] = React.useState<CacheEntry[]>([]);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(false);
  const [providerFilter, setProviderFilter] = React.useState<ProviderName | undefined>();
  const [statusFilter, setStatusFilter] = React.useState("all");
  const [limit, setLimit] = React.useState(50);
  const [offset, setOffset] = React.useState(0);
  const [form] = Form.useForm<CacheSettings>();

  async function load() {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        status: statusFilter,
        limit: String(limit),
        offset: String(offset)
      });
      if (providerFilter) params.set("provider", providerFilter);
      const [statsData, entriesData] = await Promise.all([
        api<{ settings: CacheSettings; stats: CacheStats }>("/api/admin/cache/stats"),
        api<{ entries: CacheEntry[]; total: number }>(`/api/admin/cache?${params.toString()}`)
      ]);
      setSettings(statsData.settings);
      setStats(statsData.stats);
      setEntries(entriesData.entries);
      setTotal(entriesData.total);
      form.setFieldsValue(statsData.settings);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "Failed to load cache");
    } finally {
      setLoading(false);
    }
  }

  React.useEffect(() => { void load(); }, [providerFilter, statusFilter, limit, offset]);

  async function saveSettings(values: CacheSettings) {
    const data = await api<{ settings: CacheSettings }>("/api/admin/cache/settings", {
      method: "PUT",
      body: JSON.stringify(values)
    });
    setSettings(data.settings);
    form.setFieldsValue(data.settings);
    message.success("Cache settings saved");
    await load();
  }

  async function deleteEntry(entry: CacheEntry) {
    await api(`/api/admin/cache/${entry.id}`, { method: "DELETE" });
    message.success("Cache entry deleted");
    await load();
  }

  async function pruneExpired() {
    const data = await api<{ deleted: number }>("/api/admin/cache/prune", { method: "POST" });
    message.success(`${data.deleted} expired entries removed`);
    setOffset(0);
    await load();
  }

  async function clearAll() {
    const data = await api<{ deleted: number }>("/api/admin/cache/clear", { method: "POST" });
    message.success(`${data.deleted} cache entries removed`);
    setOffset(0);
    await load();
  }

  const columns: ColumnsType<CacheEntry> = [
    { title: "ID", dataIndex: "id", width: 80 },
    { title: "Provider", render: (_, entry) => providerTag(entry.provider), width: 110 },
    { title: "Endpoint", render: (_, entry) => <Text code>/{entry.endpoint}</Text>, width: 120 },
    {
      title: "Cache Key",
      render: (_, entry) => (
        <Tooltip title={entry.cache_key}>
          <Text code>{entry.cache_key.slice(0, 12)}...</Text>
        </Tooltip>
      )
    },
    {
      title: "Status",
      width: 120,
      render: (_, entry) => entry.is_expired ? <Tag color="orange">Expired</Tag> : <Tag color="green">Active</Tag>
    },
    { title: "Hits", dataIndex: "hit_count", width: 90 },
    {
      title: "Size",
      width: 150,
      render: (_, entry) => (
        <Space direction="vertical" size={0}>
          <Text>{formatBytes(entry.response_bytes)}</Text>
          <Text type="secondary">req {formatBytes(entry.request_bytes)}</Text>
        </Space>
      )
    },
    { title: "Expires", dataIndex: "expires_at", width: 190 },
    { title: "Updated", dataIndex: "updated_at", width: 190 },
    {
      title: "Actions",
      width: 90,
      render: (_, entry) => (
        <Popconfirm
          title="Delete cache entry?"
          okText="Delete"
          okButtonProps={{ danger: true }}
          onConfirm={() => deleteEntry(entry)}
        >
          <Button danger icon={<DeleteOutlined />} />
        </Popconfirm>
      )
    }
  ];

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <div className="page-heading">
        <div>
          <Title level={2}>Cache</Title>
          <Text type="secondary">Search response cache controls, statistics, and maintenance.</Text>
        </div>
        <Space>
          {settings && (settings.enabled ? <Tag color="green">Enabled</Tag> : <Tag>Disabled</Tag>)}
          <Button icon={<SyncOutlined />} onClick={load}>Refresh</Button>
        </Space>
      </div>

      <div className="stat-grid">
        <Card><Statistic title="Total Entries" value={stats?.total ?? 0} /></Card>
        <Card><Statistic title="Active Entries" value={stats?.active ?? 0} /></Card>
        <Card><Statistic title="Expired Entries" value={stats?.expired ?? 0} /></Card>
        <Card><Statistic title="Cache Hits" value={stats?.total_hits ?? 0} /></Card>
        <Card><Statistic title="Approx Size" value={formatBytes(stats?.approx_bytes ?? 0)} /></Card>
      </div>

      <Card title="Cache Controls" className="control-card">
        <Form form={form} layout="inline" onFinish={saveSettings} className="compact-form">
          <Form.Item name="enabled" label="Enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="ttl_seconds" label="TTL Seconds" rules={[{ required: true, message: "TTL is required" }]}>
            <InputNumber min={1} className="cache-number-input" />
          </Form.Item>
          <Form.Item name="max_rows" label="Max Rows" rules={[{ required: true, message: "Max rows is required" }]}>
            <InputNumber min={1} className="cache-number-input" />
          </Form.Item>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />}>Save</Button>
          <Button onClick={pruneExpired}>Prune Expired</Button>
          <Popconfirm
            title="Clear all cache?"
            description="This removes every cached search response."
            okText="Clear"
            okButtonProps={{ danger: true }}
            onConfirm={clearAll}
          >
            <Button danger>Clear All</Button>
          </Popconfirm>
        </Form>
      </Card>

      <Card title="Cache Entries" className="table-card">
        <Space wrap className="table-toolbar">
          <Select
            allowClear
            placeholder="Provider"
            value={providerFilter}
            options={PROVIDERS.map((provider) => ({ label: provider, value: provider }))}
            onChange={(value) => { setProviderFilter(value); setOffset(0); }}
            style={{ width: 150 }}
          />
          <Select
            value={statusFilter}
            options={[
              { label: "All", value: "all" },
              { label: "Active", value: "active" },
              { label: "Expired", value: "expired" }
            ]}
            onChange={(value) => { setStatusFilter(value); setOffset(0); }}
            style={{ width: 150 }}
          />
        </Space>
        <Table
          rowKey="id"
          loading={loading}
          dataSource={entries}
          columns={columns}
          scroll={{ x: 1320 }}
          size="small"
          pagination={{
            current: Math.floor(offset / limit) + 1,
            pageSize: limit,
            total,
            showSizeChanger: true
          }}
          onChange={(pagination) => {
            const nextLimit = pagination.pageSize ?? limit;
            setLimit(nextLimit);
            setOffset(((pagination.current ?? 1) - 1) * nextLimit);
          }}
        />
      </Card>
    </Space>
  );
}

function Logs() {
  const [logs, setLogs] = React.useState<RequestLog[]>([]);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(false);
  const [providerFilter, setProviderFilter] = React.useState<ProviderName | undefined>();
  const [statusFilter, setStatusFilter] = React.useState("all");
  const [search, setSearch] = React.useState("");
  const [endpoint, setEndpoint] = React.useState("");
  const [createdFrom, setCreatedFrom] = React.useState("");
  const [createdTo, setCreatedTo] = React.useState("");
  const [limit, setLimit] = React.useState(50);
  const [offset, setOffset] = React.useState(0);
  const [reloadTick, setReloadTick] = React.useState(0);
  const [selectedLog, setSelectedLog] = React.useState<RequestLog | null>(null);
  const [drawerOpen, setDrawerOpen] = React.useState(false);

  async function load() {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        status: statusFilter,
        limit: String(limit),
        offset: String(offset)
      });
      if (providerFilter) params.set("provider", providerFilter);
      if (search.trim()) params.set("q", search.trim());
      if (endpoint.trim()) params.set("endpoint", endpoint.trim());
      if (createdFrom.trim()) params.set("created_from", createdFrom.trim());
      if (createdTo.trim()) params.set("created_to", createdTo.trim());
      const data = await api<{ logs: RequestLog[]; total: number }>(`/api/admin/logs?${params.toString()}`);
      setLogs(data.logs);
      setTotal(data.total);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "Failed to load logs");
    } finally {
      setLoading(false);
    }
  }

  React.useEffect(() => { void load(); }, [providerFilter, statusFilter, limit, offset, reloadTick]);

  async function openDetail(log: RequestLog) {
    const data = await api<{ log: RequestLog }>(`/api/admin/logs/${log.id}`);
    setSelectedLog(data.log);
    setDrawerOpen(true);
  }

  function applyFilters() {
    setOffset(0);
    setReloadTick((value) => value + 1);
  }

  function resetFilters() {
    setProviderFilter(undefined);
    setStatusFilter("all");
    setSearch("");
    setEndpoint("");
    setCreatedFrom("");
    setCreatedTo("");
    setOffset(0);
    setReloadTick((value) => value + 1);
  }

  const columns: ColumnsType<RequestLog> = [
    { title: "Time", dataIndex: "created_at", width: 180 },
    { title: "Provider", render: (_, log) => providerTag(log.provider as ProviderName), width: 110 },
    { title: "Endpoint", render: (_, log) => <Text code>{log.endpoint}</Text>, width: 130 },
    { title: "Relay Key", render: (_, log) => log.relay_key_label ?? <Text type="secondary">None</Text>, width: 150 },
    {
      title: "Group",
      render: (_, log) => log.provider_group_name ? <Tag>{log.provider_group_name}</Tag> : <Text type="secondary">None</Text>,
      width: 150
    },
    { title: "Status", render: (_, log) => httpStatusTag(log.status_code), width: 90 },
    { title: "Latency", render: (_, log) => `${log.duration_ms} ms`, width: 100 },
    {
      title: "Size",
      width: 140,
      render: (_, log) => (
        <Space direction="vertical" size={0}>
          <Text>res {formatBytes(log.response_bytes)}</Text>
          <Text type="secondary">req {formatBytes(log.request_bytes)}</Text>
        </Space>
      )
    },
    {
      title: "Error",
      render: (_, log) => log.error_code ? (
        <Tooltip title={log.error_message ?? log.error_code}>
          <Tag color="red">{log.error_code}</Tag>
        </Tooltip>
      ) : <Text type="secondary">None</Text>
    },
    {
      title: "Actions",
      width: 90,
      render: (_, log) => <Button onClick={() => openDetail(log)}>Detail</Button>
    }
  ];

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <div className="page-heading">
        <div>
          <Title level={2}>Logs</Title>
          <Text type="secondary">Recent relay request metadata and upstream failures.</Text>
        </div>
        <Space>
          <Tag>{total} records</Tag>
          <Button icon={<SyncOutlined />} onClick={load}>Refresh</Button>
        </Space>
      </div>
      <Card title="Filters" className="control-card">
        <Space wrap className="table-toolbar">
          <Select
            allowClear
            placeholder="Provider"
            value={providerFilter}
            options={PROVIDERS.map((provider) => ({ label: provider, value: provider }))}
            onChange={(value) => setProviderFilter(value)}
            style={{ width: 150 }}
          />
          <Select
            value={statusFilter}
            options={[
              { label: "All", value: "all" },
              { label: "Success", value: "success" },
              { label: "Error", value: "error" },
              { label: "4xx", value: "client_error" },
              { label: "5xx", value: "server_error" }
            ]}
            onChange={setStatusFilter}
            style={{ width: 150 }}
          />
          <Input value={endpoint} onChange={(event) => setEndpoint(event.target.value)} placeholder="/search" style={{ width: 150 }} />
          <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search logs" style={{ width: 220 }} />
          <Input value={createdFrom} onChange={(event) => setCreatedFrom(event.target.value)} placeholder="From timestamp" style={{ width: 190 }} />
          <Input value={createdTo} onChange={(event) => setCreatedTo(event.target.value)} placeholder="To timestamp" style={{ width: 190 }} />
          <Button type="primary" onClick={applyFilters}>Apply</Button>
          <Button onClick={resetFilters}>Reset</Button>
        </Space>
      </Card>
      <Card className="table-card">
        <Table
          rowKey="id"
          loading={loading}
          dataSource={logs}
          columns={columns}
          scroll={{ x: 1430 }}
          size="small"
          pagination={{
            current: Math.floor(offset / limit) + 1,
            pageSize: limit,
            total,
            showSizeChanger: true
          }}
          onChange={(pagination) => {
            const nextLimit = pagination.pageSize ?? limit;
            setLimit(nextLimit);
            setOffset(((pagination.current ?? 1) - 1) * nextLimit);
          }}
        />
      </Card>
      <Drawer title="Request Log Detail" open={drawerOpen} onClose={() => setDrawerOpen(false)} width={520}>
        {selectedLog && (
          <Space direction="vertical" size="middle" className="detail-stack">
            <div><Text type="secondary">Time</Text><br /><Text>{selectedLog.created_at}</Text></div>
            <div><Text type="secondary">Provider</Text><br />{providerTag(selectedLog.provider as ProviderName)}</div>
            <div><Text type="secondary">Endpoint</Text><br /><Text code>{selectedLog.endpoint}</Text></div>
            <div><Text type="secondary">Relay Key</Text><br /><Text>{selectedLog.relay_key_label ?? "None"}</Text></div>
            <div><Text type="secondary">Provider Group</Text><br /><Text>{selectedLog.provider_group_name ?? "None"}</Text></div>
            <div><Text type="secondary">Status</Text><br />{httpStatusTag(selectedLog.status_code)}</div>
            <div><Text type="secondary">Latency</Text><br /><Text>{selectedLog.duration_ms} ms</Text></div>
            <div><Text type="secondary">Bytes</Text><br /><Text>request {formatBytes(selectedLog.request_bytes)} / response {formatBytes(selectedLog.response_bytes)}</Text></div>
            <div><Text type="secondary">Error Code</Text><br /><Text>{selectedLog.error_code ?? "None"}</Text></div>
            <div><Text type="secondary">Error Message</Text><br /><Text>{selectedLog.error_message ?? "None"}</Text></div>
          </Space>
        )}
      </Drawer>
    </Space>
  );
}

function Settings() {
  const [form] = Form.useForm<{ current_password: string; new_password: string; confirm_password: string }>();

  async function save(values: { current_password: string; new_password: string }) {
    await api("/api/admin/settings/password", {
      method: "POST",
      body: JSON.stringify({
        current_password: values.current_password,
        new_password: values.new_password
      })
    });
    message.success("Password changed");
    form.resetFields();
  }

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <div className="page-heading">
        <div>
          <Title level={2}>Settings</Title>
          <Text type="secondary">Service settings and admin password.</Text>
        </div>
      </div>
      <Card title="Admin Password">
        <Form form={form} layout="vertical" onFinish={save} className="settings-form">
          <Form.Item name="current_password" label="Current password" rules={[{ required: true, message: "Current password is required" }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="new_password" label="New password" rules={[{ required: true, min: 12 }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label="Confirm new password"
            dependencies={["new_password"]}
            rules={[
              { required: true, message: "Please confirm the new password" },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue("new_password") === value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error("Passwords do not match"));
                }
              })
            ]}
          >
            <Input.Password />
          </Form.Item>
          <Button type="primary" htmlType="submit">Change password</Button>
        </Form>
      </Card>
      <Card title="Relay API">
        <Space direction="vertical">
          <Text code>/exa/search</Text>
          <Text code>/exa/contents</Text>
          <Text code>/exa/answer</Text>
          <Text code>/tavily/search</Text>
          <Text code>/tavily/extract</Text>
          <Text code>/tavily/crawl</Text>
          <Text code>/tavily/map</Text>
          <Text code>/tavily/research</Text>
        </Space>
      </Card>
    </Space>
  );
}

function AdminApp() {
  const [authenticated, setAuthenticated] = React.useState<boolean | null>(null);
  const [page, setPage] = React.useState<PageKey>(pathToPage());

  React.useEffect(() => {
    api<{ authenticated: boolean }>("/api/admin/me")
      .then((data) => setAuthenticated(data.authenticated))
      .catch(() => setAuthenticated(false));
  }, []);

  React.useEffect(() => {
    const onPop = () => setPage(pathToPage());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  function navigate(key: PageKey) {
    setPage(key);
    window.history.pushState(null, "", `/admin/${key}`);
  }

  async function logout() {
    await api("/api/admin/logout", { method: "POST" });
    setAuthenticated(false);
    window.history.pushState(null, "", "/admin/login");
  }

  if (authenticated === null) return <div className="loading-page">Loading...</div>;
  if (!authenticated) return <Login onLogin={() => { setAuthenticated(true); navigate("dashboard"); }} />;

  const menuItems = [
    { key: "dashboard", icon: <DashboardOutlined />, label: "Dashboard" },
    { key: "groups", icon: <ApartmentOutlined />, label: "Groups" },
    { key: "providers", icon: <ApiOutlined />, label: "Platform Keys" },
    { key: "relay-keys", icon: <KeyOutlined />, label: "API Keys" },
    { key: "docs", icon: <BookOutlined />, label: "Docs" },
    { key: "cache", icon: <DatabaseOutlined />, label: "Cache" },
    { key: "logs", icon: <FileSearchOutlined />, label: "Logs" },
    { key: "settings", icon: <SettingOutlined />, label: "Settings" }
  ];

  const pageNode = {
    dashboard: <Dashboard />,
    groups: <Groups />,
    providers: <Providers />,
    "relay-keys": <RelayKeys />,
    docs: <Docs />,
    cache: <CacheCenter />,
    logs: <Logs />,
    settings: <Settings />
  }[page];

  return (
    <Layout className="app-shell">
      <Sider breakpoint="lg" collapsedWidth="0" className="app-sider">
        <div className="brand-block">
          <div className="brand-logo">SR</div>
          <div className="brand-copy">
            <div className="brand-title">Search Relay</div>
            <div className="brand-subtitle">API Gateway</div>
          </div>
        </div>
        <div className="nav-section-title">Admin</div>
        <Menu theme="light" mode="inline" selectedKeys={[page]} items={menuItems} onClick={({ key }) => navigate(key as PageKey)} className="app-menu" />
      </Sider>
      <Layout>
        <Header className="app-header">
          <Text strong>Admin Console</Text>
          <Button icon={<LogoutOutlined />} onClick={logout}>Sign out</Button>
        </Header>
        <Content className="app-content">{pageNode}</Content>
      </Layout>
    </Layout>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: "#2563eb",
          borderRadius: 6,
          fontFamily: "Inter, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
        }
      }}
    >
      <AntApp>
        <AdminApp />
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>
);
