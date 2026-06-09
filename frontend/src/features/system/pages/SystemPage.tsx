import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Card,
  Descriptions,
  Dropdown,
  Flex,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Select,
  Slider,
  Switch,
  Tag,
  Tabs,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  CheckCircleOutlined,
  CrownOutlined,
  DeleteOutlined,
  LockOutlined,
  MoreOutlined,
  PlusOutlined,
  ReloadOutlined,
  StopOutlined,
  SyncOutlined,
  UserOutlined,
} from '@ant-design/icons';
import HealthStatusCard, { type CheckResult } from '@/features/system/components/HealthStatusCard';
import { getHealth, getReady } from '@/features/system/services/healthService';
import { getSystemInfo } from '@/features/system/services/systemService';
import { normalizeError } from '@/shared/services/apiClient';
import { useSkillStore } from '@/features/skills/store/skillStore';
import { useAuthStore } from '@/features/auth/store/authStore';
import type { SystemInfo } from '@/features/system/types';
import type { UserSettings } from '@/features/skills/types';
import type { UserItem } from '@/features/users/services/userService';
import { createUser, deleteUser, listUsers, patchUser } from '@/features/users/services/userService';
import AppScrollArea from '@/shared/components/AppScrollArea';

const TIMEZONE_OPTIONS = [
  { value: 'Asia/Shanghai', label: '北京时间（Asia/Shanghai）' },
  { value: 'UTC', label: 'UTC' },
  { value: 'America/New_York', label: '纽约时间（America/New_York）' },
  { value: 'Europe/London', label: '伦敦时间（Europe/London）' },
  { value: 'Asia/Tokyo', label: '东京时间（Asia/Tokyo）' },
];

// ─── 系统状态 Tab ─────────────────────────────────────────────────────────────

function StatusTab(): JSX.Element {
  const [health, setHealth] = useState<CheckResult>({ status: 'loading', message: '检查中...' });
  const [ready, setReady] = useState<CheckResult>({ status: 'loading', message: '检查中...' });
  const [autoRefresh, setAutoRefresh] = useState(false);

  const check = useCallback(async () => {
    setHealth({ status: 'loading', message: '检查中...' });
    setReady({ status: 'loading', message: '检查中...' });
    await Promise.allSettled([
      getHealth()
        .then((h) => setHealth({ status: 'ok', message: h.status }))
        .catch((e: unknown) => setHealth({ status: 'error', message: normalizeError(e) })),
      getReady()
        .then((r) =>
          setReady({
            status: r.status === 'ready' ? 'ok' : 'error',
            message: r.status === 'ready' ? '就绪' : r.status,
          }),
        )
        .catch((e: unknown) => setReady({ status: 'error', message: normalizeError(e) })),
    ]);
  }, []);

  useEffect(() => { void check(); }, [check]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => { void check(); }, 10000);
    return () => clearInterval(id);
  }, [autoRefresh, check]);

  return (
    <Flex vertical gap={20}>
      <Flex align="center" justify="space-between" gap={8}>
        <div>
          <Typography.Text strong style={{ fontSize: 14 }}>服务状态</Typography.Text>
          <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12, marginTop: 2 }}>
            实时检测后端服务健康状况
          </Typography.Text>
        </div>
        <Flex align="center" gap={8}>
          <Tooltip title={autoRefresh ? '已开启，每 10s 自动刷新' : '开启后每 10s 自动刷新'}>
            <Flex
              align="center"
              gap={6}
              style={{
                padding: '5px 12px',
                borderRadius: 6,
                border: '1px solid rgba(5, 5, 5, 0.15)',
                cursor: 'pointer',
              }}
              onClick={() => setAutoRefresh(!autoRefresh)}
            >
              <SyncOutlined
                spin={autoRefresh}
                style={{ fontSize: 13, color: autoRefresh ? '#1677ff' : 'rgba(0,0,0,0.45)' }}
              />
              <Typography.Text style={{ fontSize: 13, userSelect: 'none' }}>自动刷新</Typography.Text>
              <Switch size="small" checked={autoRefresh} onChange={setAutoRefresh} />
            </Flex>
          </Tooltip>
          <Button icon={<ReloadOutlined />} onClick={() => { void check(); }}>
            刷新
          </Button>
        </Flex>
      </Flex>
      <Flex gap={16}>
        <HealthStatusCard title="Health" subtitle="服务健康检查" result={health} />
        <HealthStatusCard title="Ready" subtitle="服务就绪状态" result={ready} />
      </Flex>
    </Flex>
  );
}

// ─── LLM 信息 Tab ─────────────────────────────────────────────────────────────

function LLMInfoTab(): JSX.Element {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSystemInfo()
      .then(setInfo)
      .catch((e: unknown) => setError(normalizeError(e)));
  }, []);

  if (error) {
    return <Alert type="error" message={error} />;
  }

  const defaultProfile = info?.llm.profiles.find((profile) => profile.id === info.llm.default_profile);

  return (
    <Flex gap={16} align="flex-start">
      <Card title="LLM 配置" style={{ flex: 1 }}>
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="默认 Profile">
            {defaultProfile ? (defaultProfile.label || defaultProfile.id) : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="Protocol">{defaultProfile?.protocol ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="Model">{defaultProfile?.model ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="Base URL">
            {defaultProfile?.base_url ?? <Typography.Text type="secondary">（使用默认端点）</Typography.Text>}
          </Descriptions.Item>
          <Descriptions.Item label="API Key">
            {info ? (
              defaultProfile?.api_key_configured ? (
                <Badge status="success" text="已配置" />
              ) : (
                <Badge status="error" text="未配置" />
              )
            ) : '—'}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="模型 Profiles" style={{ flex: 1 }}>
        {info ? (
          <Flex vertical gap={10}>
            {info.llm.profiles.map((profile) => (
              <Card key={profile.id} size="small" styles={{ body: { padding: '10px 12px' } }}>
                <Flex align="center" justify="space-between" gap={12}>
                  <div>
                    <Typography.Text strong>
                      {profile.label || profile.id}
                    </Typography.Text>
                    <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12, marginTop: 2 }}>
                      {profile.protocol} / {profile.model}
                    </Typography.Text>
                  </div>
                  <Flex align="center" gap={6}>
                    {profile.id === info.llm.default_profile && <Badge status="processing" text="默认" />}
                    <Badge status={profile.api_key_configured ? 'success' : 'error'} text={profile.api_key_configured ? 'Key 已配置' : 'Key 缺失'} />
                  </Flex>
                </Flex>
              </Card>
            ))}
          </Flex>
        ) : '—'}
      </Card>

      <Card title="工具 & 集成" style={{ flex: 1 }}>
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="MCP 工具链">
            {info ? (
              info.mcp_servers.length > 0 ? (
                <Flex vertical gap={6}>
                  <Badge status="success" text={`已启用（${info.mcp_servers.length} 个 server）`} />
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {info.mcp_servers
                      .map((s) => (s.name === s.type ? s.name : `${s.name} (${s.type})`))
                      .join(' / ')}
                  </Typography.Text>
                </Flex>
              ) : (
                <Badge status="default" text="未启用（未配置 YAML MCP servers）" />
              )
            ) : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="联网搜索">
            {info ? (
              info.tavily_configured ? (
                <Badge status="success" text="Tavily（已配置 TAVILY_API_KEY）" />
              ) : (
                <Badge status="processing" text="DuckDuckGo（未配置 TAVILY_API_KEY）" />
              )
            ) : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="内置工具">
            <Badge status="success" text="时间 / 计算 / 知识库检索" />
          </Descriptions.Item>
        </Descriptions>
      </Card>
    </Flex>
  );
}

// ─── 运行参数 Tab ──────────────────────────────────────────────────────────────

const DIVIDER_STYLE: React.CSSProperties = {
  borderBottom: '1px solid rgba(5, 5, 5, 0.06)',
  paddingBottom: 20,
  marginBottom: 20,
};

function ParamRow({
  title,
  description,
  children,
  style,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <Flex align="center" justify="space-between" gap={32} style={style}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Typography.Text strong style={{ fontSize: 14 }}>
          {title}
        </Typography.Text>
        <Typography.Text
          type="secondary"
          style={{ display: 'block', fontSize: 12, marginTop: 3, lineHeight: 1.6 }}
        >
          {description}
        </Typography.Text>
      </div>
      <div style={{ flexShrink: 0 }}>{children}</div>
    </Flex>
  );
}

function RuntimeParamsTab(): JSX.Element {
  const { settings, fetchSettings, saveSettings } = useSkillStore();
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [form] = Form.useForm<Pick<UserSettings, 'temperature' | 'rag_top_k' | 'context_max_messages' | 'timezone' | 'thinking_collapse_mode'>>();

  useEffect(() => {
    void fetchSettings();
  }, [fetchSettings]);

  useEffect(() => {
    form.setFieldsValue({
      temperature: settings.temperature,
      rag_top_k: settings.rag_top_k,
      context_max_messages: settings.context_max_messages,
      timezone: settings.timezone,
      thinking_collapse_mode: settings.thinking_collapse_mode,
    });
  }, [settings, form]);

  const handleSave = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      await saveSettings(values);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Flex gap={24} align="flex-start">
      {/* 左：表单 */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {/* 页头 */}
        <Flex align="flex-start" justify="space-between" gap={16} style={{ marginBottom: 28 }}>
          <div>
            <Typography.Text strong style={{ fontSize: 15 }}>
              运行参数
            </Typography.Text>
            <Typography.Text
              type="secondary"
              style={{ display: 'block', fontSize: 13, marginTop: 4 }}
            >
              调整 AI 的推理行为，修改后点击保存生效
            </Typography.Text>
          </div>
          <Button
            type="primary"
            loading={saving}
            onClick={() => { void handleSave(); }}
            style={saved ? { background: '#52c41a', borderColor: '#52c41a' } : {}}
          >
            {saved ? '✓ 已保存' : '保存'}
          </Button>
        </Flex>

        <Form form={form}>
          {/* Temperature */}
          <div style={DIVIDER_STYLE}>
            <Flex align="center" justify="space-between" gap={16} style={{ marginBottom: 12 }}>
              <div>
                <Typography.Text strong style={{ fontSize: 14 }}>
                  Temperature
                </Typography.Text>
                <Typography.Text
                  type="secondary"
                  style={{ display: 'block', fontSize: 12, marginTop: 3, lineHeight: 1.6 }}
                >
                  控制输出随机性。值越高越有创意，值越低越稳定精确。推荐范围 0.3 ~ 1.0。
                </Typography.Text>
              </div>
              <Form.Item noStyle shouldUpdate={(p, c) => p.temperature !== c.temperature}>
                {({ getFieldValue, setFieldValue }) => (
                  <InputNumber
                    min={0}
                    max={2}
                    step={0.05}
                    style={{ width: 90 }}
                    value={(getFieldValue('temperature') as number) ?? 0.7}
                    onChange={(v) => setFieldValue('temperature', v ?? 0.7)}
                  />
                )}
              </Form.Item>
            </Flex>
            <Form.Item name="temperature" noStyle rules={[{ required: true }]}>
              <Slider min={0} max={2} step={0.05} />
            </Form.Item>
          </div>

          {/* RAG top_k */}
          <ParamRow
            title="RAG 检索数量"
            description="开启知识库检索时，每次从向量库中召回的文档片段数量（top_k）。数量越多上下文越丰富，延迟也越高。"
            style={DIVIDER_STYLE}
          >
            <Form.Item name="rag_top_k" noStyle rules={[{ required: true }]}>
              <InputNumber min={1} max={20} style={{ width: 120 }} addonAfter="条" />
            </Form.Item>
          </ParamRow>

          {/* Context length */}
          <ParamRow
            title="对话上下文长度"
            description="每次请求发送给 LLM 的历史消息条数上限。值越大对话记忆越长，消耗的 Token 也越多。"
            style={DIVIDER_STYLE}
          >
            <Form.Item name="context_max_messages" noStyle rules={[{ required: true }]}>
              <InputNumber min={4} max={200} style={{ width: 120 }} addonAfter="条" />
            </Form.Item>
          </ParamRow>

          <ParamRow
            title="显示时区"
            description="控制前端所有时间展示和会话日期分组。默认使用北京时间。"
            style={DIVIDER_STYLE}
          >
            <Form.Item name="timezone" noStyle rules={[{ required: true }]}>
              <Select options={TIMEZONE_OPTIONS} style={{ width: 260 }} />
            </Form.Item>
          </ParamRow>

          <ParamRow
            title="思考过程折叠"
            description="控制 AI 思考过程（Extended Thinking）的折叠行为。"
          >
            <Form.Item name="thinking_collapse_mode" noStyle rules={[{ required: true }]}>
              <Select
                style={{ width: 260 }}
                options={[
                  { value: 'auto', label: '流式时展开' },
                  { value: 'always_collapsed', label: '始终折叠' },
                ]}
              />
            </Form.Item>
          </ParamRow>
        </Form>
      </div>

      {/* 右：参数参考卡片 */}
      <Card
        size="small"
        title="参考值"
        style={{ width: 260, flexShrink: 0 }}
        styles={{ header: { fontSize: 13 } }}
      >
        <Flex vertical gap={16}>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Temperature
            </Typography.Text>
            <Flex vertical gap={4} style={{ marginTop: 6 }}>
              {[
                { label: '精确问答 / 代码', range: '0.1 ~ 0.4' },
                { label: '通用对话', range: '0.5 ~ 0.8' },
                { label: '创意写作', range: '0.9 ~ 1.2' },
              ].map((item) => (
                <Flex key={item.label} justify="space-between" align="center">
                  <Typography.Text style={{ fontSize: 12 }}>{item.label}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>
                    {item.range}
                  </Typography.Text>
                </Flex>
              ))}
            </Flex>
          </div>

          <div style={{ borderTop: '1px solid rgba(5,5,5,0.06)', paddingTop: 12 }}>
            <Typography.Text type="secondary" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              RAG 检索数量
            </Typography.Text>
            <Flex vertical gap={4} style={{ marginTop: 6 }}>
              {[
                { label: '快速检索', range: '2 ~ 4 条' },
                { label: '均衡', range: '4 ~ 6 条' },
                { label: '深度召回', range: '8 ~ 12 条' },
              ].map((item) => (
                <Flex key={item.label} justify="space-between" align="center">
                  <Typography.Text style={{ fontSize: 12 }}>{item.label}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {item.range}
                  </Typography.Text>
                </Flex>
              ))}
            </Flex>
          </div>

          <div style={{ borderTop: '1px solid rgba(5,5,5,0.06)', paddingTop: 12 }}>
            <Typography.Text type="secondary" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              上下文长度
            </Typography.Text>
            <Flex vertical gap={4} style={{ marginTop: 6 }}>
              {[
                { label: '短对话', range: '10 ~ 20 条' },
                { label: '项目协作', range: '30 ~ 50 条' },
                { label: '长文档处理', range: '60+ 条' },
              ].map((item) => (
                <Flex key={item.label} justify="space-between" align="center">
                  <Typography.Text style={{ fontSize: 12 }}>{item.label}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {item.range}
                  </Typography.Text>
                </Flex>
              ))}
            </Flex>
          </div>
        </Flex>
      </Card>
    </Flex>
  );
}

// ─── 用户管理 Tab ───────────────────────────────────────────────────────────────

type CreateForm = { username: string; password: string; role: string };
type ResetForm = { password: string };

function UserManagementTab(): JSX.Element {
  const currentUser = useAuthStore((s) => s.user);
  const [users, setUsers] = useState<UserItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [resetTarget, setResetTarget] = useState<UserItem | null>(null);
  const [createForm] = Form.useForm<CreateForm>();
  const [resetForm] = Form.useForm<ResetForm>();
  const [messageApi, contextHolder] = message.useMessage();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setUsers(await listUsers());
    } catch (e) {
      void messageApi.error(normalizeError(e));
    } finally {
      setLoading(false);
    }
  }, [messageApi]);

  useEffect(() => { void load(); }, [load]);

  const handleCreate = async (values: CreateForm) => {
    try {
      const user = await createUser(values);
      setUsers((prev) => [...prev, user]);
      setCreateOpen(false);
      createForm.resetFields();
      void messageApi.success('用户已创建');
    } catch (e) {
      void messageApi.error(normalizeError(e));
    }
  };

  const handleToggleActive = async (user: UserItem) => {
    try {
      const updated = await patchUser(user.id, { is_active: !user.is_active });
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
    } catch (e) {
      void messageApi.error(normalizeError(e));
    }
  };

  const handleDelete = async (userId: string) => {
    try {
      await deleteUser(userId);
      setUsers((prev) => prev.filter((u) => u.id !== userId));
      void messageApi.success('用户已删除');
    } catch (e) {
      void messageApi.error(normalizeError(e));
    }
  };

  const handleResetPassword = async (values: ResetForm) => {
    if (!resetTarget) return;
    try {
      await patchUser(resetTarget.id, { password: values.password });
      setResetTarget(null);
      resetForm.resetFields();
      void messageApi.success('密码已重置');
    } catch (e) {
      void messageApi.error(normalizeError(e));
    }
  };

  const filteredUsers = useMemo(
    () => users.filter((u) => u.username.toLowerCase().includes(searchText.toLowerCase())),
    [users, searchText],
  );

  const makeMenuItems = (user: UserItem) => [
    {
      key: 'reset',
      icon: <LockOutlined />,
      label: '重置密码',
      onClick: () => setResetTarget(user),
    },
    {
      key: 'toggle',
      icon: user.is_active ? <StopOutlined /> : <CheckCircleOutlined />,
      label: user.is_active ? '停用账户' : '启用账户',
      onClick: () => { void handleToggleActive(user); },
    },
    { type: 'divider' as const },
    {
      key: 'delete',
      icon: <DeleteOutlined />,
      label: '删除用户',
      danger: true,
      disabled: user.id === currentUser?.id,
      onClick: () => {
        Modal.confirm({
          title: `删除用户 "${user.username}"？`,
          content: '此操作不可撤销，用户的所有数据将永久删除。',
          okText: '确认删除',
          okType: 'danger',
          cancelText: '取消',
          onOk: () => { void handleDelete(user.id); },
        });
      },
    },
  ];

  return (
    <Flex vertical gap={16}>
      {contextHolder}

      {/* 页头 */}
      <Flex justify="space-between" align="center">
        <div>
          <Flex align="center" gap={8}>
            <Typography.Text strong style={{ fontSize: 14 }}>用户账户</Typography.Text>
            {!loading && (
              <Tag style={{ borderRadius: 10, fontSize: 11, margin: 0 }}>{users.length} 人</Tag>
            )}
          </Flex>
          <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: 3, display: 'block' }}>
            管理所有系统用户的角色与访问权限
          </Typography.Text>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          新建用户
        </Button>
      </Flex>

      {/* 搜索栏 */}
      <Input.Search
        placeholder="搜索用户名…"
        allowClear
        value={searchText}
        onChange={(e) => setSearchText(e.target.value)}
        style={{ maxWidth: 320 }}
      />

      {/* 用户列表 */}
      <List
        bordered
        style={{ borderRadius: 8, overflow: 'hidden' }}
        loading={loading}
        dataSource={filteredUsers}
        locale={{ emptyText: searchText ? `未找到"${searchText}"` : '暂无用户' }}
        renderItem={(user) => (
          <List.Item
            style={{
              padding: '10px 16px',
              opacity: user.is_active ? 1 : 0.5,
              transition: 'opacity 0.2s',
              background: user.id === currentUser?.id ? 'rgba(22,119,255,0.02)' : undefined,
            }}
          >
            <Flex align="center" gap={12} style={{ width: '100%' }}>
              {/* 头像 */}
              <Avatar
                style={{ backgroundColor: user.role === 'admin' ? '#faad14' : '#1677ff', fontWeight: 600, flexShrink: 0 }}
              >
                {user.username[0].toUpperCase()}
              </Avatar>

              {/* 用户名（弹性区，吸收剩余空间） */}
              <Flex align="center" gap={8} style={{ flex: 1, minWidth: 0 }}>
                <Typography.Text strong style={{ fontSize: 13 }}>{user.username}</Typography.Text>
                {user.id === currentUser?.id && (
                  <Tag color="blue" style={{ fontSize: 11, lineHeight: '18px', padding: '0 5px', borderRadius: 4, margin: 0 }}>我</Tag>
                )}
              </Flex>

              {/* 右侧元信息（固定不伸缩） */}
              <Flex align="center" gap={16} style={{ flexShrink: 0 }}>
                {user.role === 'admin' ? (
                  <Tag icon={<CrownOutlined />} color="gold" style={{ borderRadius: 4, margin: 0 }}>管理员</Tag>
                ) : (
                  <Tag icon={<UserOutlined />} style={{ borderRadius: 4, margin: 0 }}>普通用户</Tag>
                )}
                <Badge
                  status={user.is_active ? 'success' : 'default'}
                  text={
                    <Typography.Text style={{ fontSize: 12, color: user.is_active ? undefined : 'rgba(0,0,0,0.35)' }}>
                      {user.is_active ? '活跃' : '停用'}
                    </Typography.Text>
                  }
                />
                <Typography.Text type="secondary" style={{ fontSize: 12, width: 68, textAlign: 'right' }}>
                  {new Date(user.created_at).toLocaleDateString('zh-CN')}
                </Typography.Text>
                <Dropdown trigger={['click']} menu={{ items: makeMenuItems(user) }}>
                  <Button type="text" size="small" icon={<MoreOutlined />} style={{ color: 'rgba(0,0,0,0.45)' }} />
                </Dropdown>
              </Flex>
            </Flex>
          </List.Item>
        )}
      />

      {/* 新建用户 Modal */}
      <Modal
        title="新建用户"
        open={createOpen}
        onCancel={() => { setCreateOpen(false); createForm.resetFields(); }}
        onOk={() => { void createForm.validateFields().then((v) => handleCreate(v)); }}
        okText="创建"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={createForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input placeholder="3-32 位字母或数字" autoComplete="off" />
          </Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 6, message: '密码至少 6 位' }]}>
            <Input.Password placeholder="至少 6 位" autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="role" label="角色" initialValue="user">
            <Select options={[{ value: 'user', label: '普通用户' }, { value: 'admin', label: '管理员' }]} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 重置密码 Modal */}
      <Modal
        title={`重置密码 — ${resetTarget?.username ?? ''}`}
        open={!!resetTarget}
        onCancel={() => { setResetTarget(null); resetForm.resetFields(); }}
        onOk={() => { void resetForm.validateFields().then((v) => handleResetPassword(v)); }}
        okText="确认重置"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={resetForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="password" label="新密码" rules={[{ required: true, min: 6, message: '密码至少 6 位' }]}>
            <Input.Password placeholder="至少 6 位" autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </Flex>
  );
}

// ─── SystemPage ────────────────────────────────────────────────────────────────

export default function SystemPage(): JSX.Element {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin';

  const tabs = [
    { key: 'status', label: '系统状态', children: <StatusTab /> },
    { key: 'llm', label: 'LLM 信息', children: <LLMInfoTab /> },
    { key: 'runtime', label: '运行参数', children: <RuntimeParamsTab /> },
    ...(isAdmin ? [{ key: 'users', label: '用户管理', children: <UserManagementTab /> }] : []),
  ];

  return (
    <AppScrollArea style={{ height: '100%' }}>
      <Flex vertical style={{ padding: 24 }} gap={16}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        系统
      </Typography.Title>
      <Tabs items={tabs} />
      </Flex>
    </AppScrollArea>
  );
}
