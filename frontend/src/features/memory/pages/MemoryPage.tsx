import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd';
import { DeleteOutlined, EditOutlined, LockOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { normalizeError } from '@/shared/services/apiClient';
import { fetchConversations } from '@/features/chat/services/conversationService';
import {
  createMemory,
  deleteMemory,
  fetchMemory,
  updateMemory,
  type MemoryCreateRequest,
  type MemoryScope,
  type MemoryType,
} from '@/features/memory/services/memoryService';
import { createProject, fetchProjects } from '@/features/projects/services/projectService';
import type { MemoryApiItem, ProjectApiItem } from '@/shared/types/api';
import type { ConversationMeta } from '@/features/chat/types';
import AppScrollArea from '@/shared/components/AppScrollArea';
import { useSkillStore } from '@/features/skills/store/skillStore';
import { useAuthStore } from '@/features/auth/store/authStore';
import { formatAppDateTime } from '@/shared/utils/time';

const SCOPE_OPTIONS: Array<{ label: string; value: MemoryScope }> = [
  { label: 'Session', value: 'session' },
  { label: 'Project', value: 'project' },
  { label: 'User', value: 'user' },
  { label: 'Global', value: 'global' },
];

const TYPE_OPTIONS: Array<{ label: string; value: MemoryType }> = [
  { label: 'Fact', value: 'fact' },
  { label: 'Preference', value: 'preference' },
  { label: 'Decision', value: 'decision' },
  { label: 'Constraint', value: 'constraint' },
  { label: 'State', value: 'state' },
  { label: 'Plan', value: 'plan' },
  { label: 'Summary', value: 'summary' },
  { label: 'Lesson', value: 'lesson' },
  { label: 'Procedure', value: 'procedure' },
];

type MemoryFormValue = MemoryCreateRequest;

const SCOPE_COLOR: Record<MemoryScope, string> = {
  session: 'geekblue',
  project: 'cyan',
  user: 'purple',
  global: 'gold',
};

const TYPE_COLOR: Record<MemoryType, string> = {
  fact: 'blue',
  preference: 'purple',
  decision: 'green',
  constraint: 'red',
  state: 'cyan',
  plan: 'gold',
  summary: 'volcano',
  lesson: 'magenta',
  procedure: 'orange',
};

export default function MemoryPage(): JSX.Element {
  const timezone = useSkillStore((s) => s.settings.timezone);
  const fetchSettings = useSkillStore((s) => s.fetchSettings);
  const currentUsername = useAuthStore((s) => s.user?.username ?? 'User Memory');
  const [items, setItems] = useState<MemoryApiItem[]>([]);
  const [projects, setProjects] = useState<ProjectApiItem[]>([]);
  const [conversations, setConversations] = useState<ConversationMeta[]>([]);
  const [scope, setScope] = useState<MemoryScope | undefined>();
  const [projectId, setProjectId] = useState<string | undefined>();
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<MemoryApiItem | null>(null);
  const [projectModalOpen, setProjectModalOpen] = useState(false);
  const [form] = Form.useForm<MemoryFormValue>();
  const [projectForm] = Form.useForm<{ name: string; root_paths?: string; description?: string }>();

  const projectOptions = useMemo(
    () => projects.map((project) => ({ label: project.name, value: project.id })),
    [projects],
  );

  const projectById = useMemo(
    () => new Map(projects.map((project) => [project.id, project])),
    [projects],
  );

  const conversationById = useMemo(
    () => new Map(conversations.map((conversation) => [conversation.id, conversation])),
    [conversations],
  );

  const conversationOptions = useMemo(
    () =>
      conversations.map((conversation) => ({
        label: `${conversation.title || '新会话'} · ${conversation.messageCount} 条 · ${formatAppDateTime(conversation.updatedAt, timezone)}`,
        value: conversation.id,
      })),
    [conversations, timezone],
  );

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const activeScope = conversationId ? 'session' : scope;
      const [memoryResult, projectResult, conversationResult] = await Promise.all([
        fetchMemory({
          scope: activeScope,
          project_id: activeScope === 'project' ? projectId : undefined,
          session_id: conversationId,
          q: search.trim() || undefined,
          limit: 200,
        }),
        fetchProjects(),
        fetchConversations(),
      ]);
      setItems(memoryResult.items);
      setProjects(projectResult);
      setConversations(conversationResult);
    } catch (e) {
      setError(normalizeError(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchSettings();
    void load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const openCreate = () => {
    setEditing(null);
    const initialScope = conversationId ? 'session' : scope ?? 'session';
    form.setFieldsValue({
      scope: initialScope,
      type: 'fact',
      project_id: initialScope === 'project' ? projectId : undefined,
      session_id: initialScope === 'session' ? conversationId : undefined,
      conversation_id: initialScope === 'session' ? conversationId : undefined,
      importance: 3,
      confidence: 1,
      locked: false,
      content: '',
      subject: '',
    });
    setModalOpen(true);
  };

  const openEdit = (item: MemoryApiItem) => {
    setEditing(item);
    form.setFieldsValue({
      scope: item.scope,
      type: item.type,
      content: item.content,
      subject: item.subject,
      project_id: item.project_id,
      session_id: item.session_id,
      conversation_id: item.conversation_id,
      importance: item.importance,
      confidence: item.confidence,
      locked: item.locked,
    });
    setModalOpen(true);
  };

  const submitMemory = async () => {
    const values = await form.validateFields();
    if (editing) {
      await updateMemory(editing.id, values);
    } else {
      await createMemory(values);
    }
    setModalOpen(false);
    await load();
  };

  const submitProject = async () => {
    const values = await projectForm.validateFields();
    await createProject({
      name: values.name,
      description: values.description,
      root_paths: values.root_paths
        ?.split('\n')
        .map((line) => line.trim())
        .filter(Boolean),
    });
    projectForm.resetFields();
    setProjectModalOpen(false);
    await load();
  };

  return (
    <AppScrollArea style={{ height: '100%' }}>
      <Flex vertical style={{ padding: 24 }} gap={16}>
      <Card
        bordered={false}
        styles={{ body: { padding: 20 } }}
        style={{
          background: 'linear-gradient(135deg, rgba(22,119,255,0.12), rgba(114,46,209,0.08))',
          border: '1px solid rgba(120, 144, 180, 0.18)',
          borderRadius: 16,
        }}
      >
        <Flex align="center" justify="space-between" gap={16} wrap="wrap">
          <div>
            <Typography.Title level={3} style={{ margin: 0 }}>
              Memory
            </Typography.Title>
            <Typography.Text type="secondary">
              管理会话、项目、用户和全局长期记忆，让模型在正确范围内记住重要信息。
            </Typography.Text>
          </div>
          <Flex gap={8} wrap="wrap">
            <Button icon={<ReloadOutlined />} onClick={() => { void load(); }}>
              刷新
            </Button>
            <Button onClick={() => setProjectModalOpen(true)}>新建项目</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              新建记忆
            </Button>
          </Flex>
        </Flex>
      </Card>

      {error && <Alert type="error" message={error} closable onClose={() => setError(null)} />}

      <Flex gap={12} wrap="wrap">
        <Card bordered={false} style={{ flex: '1 1 180px', borderRadius: 14 }}>
          <Typography.Text type="secondary">记忆总数</Typography.Text>
          <Typography.Title level={3} style={{ margin: '4px 0 0' }}>{items.length}</Typography.Title>
        </Card>
        <Card bordered={false} style={{ flex: '1 1 180px', borderRadius: 14 }}>
          <Typography.Text type="secondary">锁定记忆</Typography.Text>
          <Typography.Title level={3} style={{ margin: '4px 0 0' }}>
            {items.filter((item) => item.locked).length}
          </Typography.Title>
        </Card>
        <Card bordered={false} style={{ flex: '1 1 180px', borderRadius: 14 }}>
          <Typography.Text type="secondary">项目数</Typography.Text>
          <Typography.Title level={3} style={{ margin: '4px 0 0' }}>{projects.length}</Typography.Title>
        </Card>
      </Flex>

      <Card bordered={false} styles={{ body: { padding: 16 } }} style={{ borderRadius: 16 }}>
        <Flex align="center" gap={10} wrap="wrap">
          <Select
            allowClear
            placeholder="Scope"
            value={scope}
            options={SCOPE_OPTIONS}
            style={{ width: 150 }}
            onChange={(value) => {
              setScope(value);
              if (value !== 'session') setConversationId(undefined);
            }}
          />
          <Select
            allowClear
            showSearch
            placeholder="按对话过滤 Session"
            value={conversationId}
            options={conversationOptions}
            optionFilterProp="label"
            style={{ width: 320 }}
            onChange={(value) => {
              setConversationId(value);
              if (value) setScope('session');
            }}
          />
          <Select
            allowClear
            placeholder="Project"
            value={projectId}
            options={projectOptions}
            style={{ width: 220 }}
            disabled={Boolean(conversationId)}
            onChange={setProjectId}
          />
          <Input.Search
            allowClear
            placeholder="搜索记忆内容、主题或摘要"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onSearch={() => { void load(); }}
            style={{ flex: '1 1 280px', minWidth: 240, maxWidth: 460 }}
          />
          <Button type="primary" ghost onClick={() => { void load(); }}>
            筛选
          </Button>
        </Flex>
      </Card>

      <Card bordered={false} styles={{ body: { padding: 0 } }} style={{ borderRadius: 16, overflow: 'hidden' }}>
        <Table
          rowKey="id"
          loading={loading}
          dataSource={items}
          pagination={{ pageSize: 12, showSizeChanger: false }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={search || scope || projectId ? '没有匹配的记忆' : '暂无记忆'}
              />
            ),
          }}
          columns={[
            {
              title: 'Scope',
              dataIndex: 'scope',
              width: 110,
              render: (value: MemoryScope) => <Tag color={SCOPE_COLOR[value]}>{value}</Tag>,
            },
            {
              title: '归属',
              width: 240,
              render: (_value, item) => {
                const ownerId = item.session_id ?? item.conversation_id ?? '';
                const conversation = ownerId ? conversationById.get(ownerId) : undefined;
                const project = item.project_id ? projectById.get(item.project_id) : undefined;
                const label = item.scope === 'session'
                  ? conversation?.title || '未知对话'
                  : item.scope === 'project'
                    ? project?.name || '未知项目'
                    : item.scope === 'user'
                      ? currentUsername
                      : 'Global';
                const secondary = item.scope === 'session'
                  ? ''
                  : item.scope === 'project'
                    ? item.project_id
                    : item.scope === 'user'
                      ? 'User Memory'
                      : 'Global Memory';
                return (
                  <Flex vertical gap={2}>
                    <Flex align="center" gap={6} wrap="wrap">
                      <Typography.Text>{label}</Typography.Text>
                      <Tag color={TYPE_COLOR[item.type]} style={{ marginInlineEnd: 0 }}>
                        {item.type}
                      </Tag>
                    </Flex>
                    {secondary && (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {secondary.length > 18 ? `${secondary.slice(0, 8)}...${secondary.slice(-6)}` : secondary}
                      </Typography.Text>
                    )}
                  </Flex>
                );
              },
            },
            {
              title: 'Subject',
              dataIndex: 'subject',
              width: 180,
              render: (value: string, item) => (
                <Typography.Text>
                  {value || item.summary || '未命名'}
                </Typography.Text>
              ),
            },
            {
              title: 'Content',
              dataIndex: 'content',
              render: (_value: string, item) => (
                <Flex vertical gap={4}>
                  <Typography.Paragraph
                    type="secondary"
                    ellipsis={{ rows: 2, expandable: true, symbol: '展开' }}
                    style={{ margin: 0 }}
                  >
                    {item.content}
                  </Typography.Paragraph>
                </Flex>
              ),
            },
            {
              title: '权重',
              width: 110,
              render: (_value, item) => (
                <Flex vertical gap={2}>
                  <Typography.Text>{item.importance}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    置信 {item.confidence.toFixed(1)}
                  </Typography.Text>
                </Flex>
              ),
            },
            {
              title: '状态',
              width: 110,
              render: (_value, item) => (
                <Tag color={item.locked ? 'gold' : item.status === 'active' ? 'green' : 'default'} icon={item.locked ? <LockOutlined /> : undefined}>
                  {item.locked ? 'locked' : item.status}
                </Tag>
              ),
            },
            {
              title: '操作',
              width: 120,
              render: (_value, item) => (
                <Flex gap={4}>
                  <Button size="small" type="text" icon={<EditOutlined />} onClick={() => openEdit(item)} />
                  <Button
                    size="small"
                    type="text"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => { void deleteMemory(item.id).then(load); }}
                  />
                </Flex>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        title={editing ? '编辑记忆' : '新建记忆'}
        open={modalOpen}
        okText="保存"
        cancelText="取消"
        onOk={() => { void submitMemory(); }}
        onCancel={() => setModalOpen(false)}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" style={{ paddingTop: 8 }}>
          <Flex gap={8}>
            <Form.Item name="scope" label="Scope" rules={[{ required: true }]} style={{ flex: 1 }}>
              <Select options={SCOPE_OPTIONS} />
            </Form.Item>
            <Form.Item name="type" label="Type" rules={[{ required: true }]} style={{ flex: 1 }}>
              <Select options={TYPE_OPTIONS} />
            </Form.Item>
          </Flex>
          <Form.Item name="project_id" label="Project">
            <Select allowClear options={projectOptions} />
          </Form.Item>
          <Form.Item name="session_id" label="Session / 对话">
            <Select
              allowClear
              showSearch
              options={conversationOptions}
              optionFilterProp="label"
              onChange={(value) => {
                form.setFieldValue('conversation_id', value);
              }}
            />
          </Form.Item>
          <Form.Item name="conversation_id" hidden>
            <Input />
          </Form.Item>
          <Form.Item name="subject" label="Subject">
            <Input />
          </Form.Item>
          <Form.Item name="content" label="Content" rules={[{ required: true }]}>
            <Input.TextArea rows={5} />
          </Form.Item>
          <Flex gap={8}>
            <Form.Item name="importance" label="Importance" style={{ flex: 1 }}>
              <InputNumber min={1} max={5} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="confidence" label="Confidence" style={{ flex: 1 }}>
              <InputNumber min={0} max={1} step={0.1} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="locked" label="Locked" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Flex>
        </Form>
      </Modal>

      <Modal
        title="新建项目"
        open={projectModalOpen}
        okText="创建"
        cancelText="取消"
        onOk={() => { void submitProject(); }}
        onCancel={() => setProjectModalOpen(false)}
        destroyOnHidden
      >
        <Form form={projectForm} layout="vertical" style={{ paddingTop: 8 }}>
          <Form.Item name="name" label="项目名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="root_paths" label="根目录路径（一行一个）">
            <Input.TextArea rows={4} />
          </Form.Item>
        </Form>
      </Modal>
      </Flex>
    </AppScrollArea>
  );
}
