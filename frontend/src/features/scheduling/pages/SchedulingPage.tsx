import { useEffect, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Flex,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  CaretRightOutlined,
  DeleteOutlined,
  EditOutlined,
  MessageOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';
import { useSchedulingStore } from '@/features/scheduling/store/schedulingStore';
import CreateTaskModal from '@/features/scheduling/components/CreateTaskModal';
import type { CreateTaskRequest, ScheduledTask, UpdateTaskRequest } from '@/features/scheduling/types';
import { useChatStore } from '@/features/chat/store/chatStore';
import AppScrollArea from '@/shared/components/AppScrollArea';
import { formatAppDateTime } from '@/shared/utils/time';

const STATUS_COLOR: Record<string, string> = {
  active: 'success',
  paused: 'warning',
  finished: 'default',
};

const STATUS_LABEL: Record<string, string> = {
  active: '运行中',
  paused: '已暂停',
  finished: '已完成',
};

const TRIGGER_LABEL: Record<string, string> = {
  cron: 'Cron',
  interval: '间隔',
  date: '单次',
};

const STATUS_OPTIONS = [
  { label: '全部', value: '' },
  { label: '运行中', value: 'active' },
  { label: '已暂停', value: 'paused' },
  { label: '已完成', value: 'finished' },
];

function triggerDesc(task: ScheduledTask): string {
  const { trigger_type, trigger_config } = task;
  if (trigger_type === 'cron' && trigger_config.expr) return trigger_config.expr;
  if (trigger_type === 'interval' && trigger_config.seconds != null) {
    const s = trigger_config.seconds;
    if (s >= 3600) return `每 ${s / 3600}h`;
    if (s >= 60) return `每 ${s / 60}min`;
    return `每 ${s}s`;
  }
  if (trigger_type === 'date' && trigger_config.run_at) {
    return formatAppDateTime(trigger_config.run_at);
  }
  return '-';
}

export default function SchedulingPage(): JSX.Element {
  const navigate = useNavigate();
  const {
    tasks,
    total,
    page,
    pageSize,
    search,
    statusFilter,
    isLoading,
    error,
    fetchTasks,
    setSearch,
    setStatusFilter,
    createTask,
    updateTask,
    deleteTask,
    batchDeleteTasks,
    pauseTask,
    resumeTask,
    runNow,
    clearError,
  } = useSchedulingStore();
  const initConversations = useChatStore((s) => s.initConversations);
  const switchConversation = useChatStore((s) => s.switchConversation);

  const [modalOpen, setModalOpen] = useState(false);
  const [editingTask, setEditingTask] = useState<ScheduledTask | null>(null);
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([]);

  useEffect(() => {
    void fetchTasks(1, 20);
  }, [fetchTasks]);

  const withLoading = (id: string, fn: () => Promise<void>) => async () => {
    setActionLoading((prev) => ({ ...prev, [id]: true }));
    try {
      await fn();
    } finally {
      setActionLoading((prev) => ({ ...prev, [id]: false }));
    }
  };

  const handleCreate = () => {
    setEditingTask(null);
    setModalOpen(true);
  };

  const handleEdit = (task: ScheduledTask) => {
    setEditingTask(task);
    setModalOpen(true);
  };

  const handleCreate_ = async (req: CreateTaskRequest) => {
    await createTask(req);
  };

  const handleUpdate_ = async (id: string, req: UpdateTaskRequest) => {
    await updateTask(id, req);
  };

  const handleViewResult = async (conversationId: string) => {
    await initConversations();
    switchConversation(conversationId);
    navigate('/chat');
  };

  const handleSearch = () => {
    void fetchTasks(1, pageSize);
    setSelectedRowKeys([]);
  };

  const handleBatchDelete = () => {
    Modal.confirm({
      title: `删除 ${selectedRowKeys.length} 个任务？`,
      content: '此操作不可撤销。',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        await batchDeleteTasks(selectedRowKeys);
        setSelectedRowKeys([]);
      },
    });
  };

  const columns: ColumnsType<ScheduledTask> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 160,
      ellipsis: true,
      render: (name: string) => <Typography.Text strong>{name}</Typography.Text>,
    },
    {
      title: '提示词',
      dataIndex: 'prompt',
      key: 'prompt',
      ellipsis: true,
      render: (prompt: string) => (
        <Tooltip title={prompt}>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>{prompt}</Typography.Text>
        </Tooltip>
      ),
    },
    {
      title: '触发方式',
      key: 'trigger',
      width: 140,
      render: (_, task) => (
        <Space size={4} direction="vertical" style={{ gap: 2 }}>
          <Tag>{TRIGGER_LABEL[task.trigger_type] ?? task.trigger_type}</Tag>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>{triggerDesc(task)}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (status: string) => (
        <Badge status={STATUS_COLOR[status] as 'success' | 'warning' | 'default'} text={STATUS_LABEL[status] ?? status} />
      ),
    },
    {
      title: '运行次数',
      key: 'runs',
      width: 90,
      render: (_, task) => (
        <Space direction="vertical" size={0} style={{ gap: 0 }}>
          <span>{task.run_count} 次</span>
          {task.error_count > 0 && (
            <Typography.Text type="danger" style={{ fontSize: 12 }}>{task.error_count} 错误</Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '下次执行',
      key: 'next_run_at',
      width: 140,
      render: (_, task) =>
        task.next_run_at ? (
          <Typography.Text style={{ fontSize: 12 }}>{formatAppDateTime(task.next_run_at)}</Typography.Text>
        ) : (
          <Typography.Text type="secondary">-</Typography.Text>
        ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 220,
      render: (_, task) => {
        const loading = actionLoading[task.id] ?? false;
        return (
          <Space size={4}>
            <Tooltip title="立即执行">
              <Button
                size="small"
                icon={<CaretRightOutlined />}
                loading={loading}
                onClick={withLoading(task.id, () => runNow(task.id))}
                disabled={task.status === 'finished'}
              />
            </Tooltip>
            {task.status === 'active' ? (
              <Tooltip title="暂停">
                <Button
                  size="small"
                  icon={<PauseCircleOutlined />}
                  loading={loading}
                  onClick={withLoading(task.id, () => pauseTask(task.id))}
                />
              </Tooltip>
            ) : task.status === 'paused' ? (
              <Tooltip title="恢复">
                <Button
                  size="small"
                  icon={<PlayCircleOutlined />}
                  loading={loading}
                  onClick={withLoading(task.id, () => resumeTask(task.id))}
                />
              </Tooltip>
            ) : null}
            <Tooltip title="编辑">
              <Button
                size="small"
                icon={<EditOutlined />}
                onClick={() => handleEdit(task)}
                disabled={task.status === 'finished'}
              />
            </Tooltip>
            <Tooltip title={task.conversation_id ? '查看结果' : '暂无运行结果'}>
              <Button
                size="small"
                icon={<MessageOutlined />}
                disabled={!task.conversation_id}
                onClick={() => task.conversation_id && void handleViewResult(task.conversation_id)}
              />
            </Tooltip>
            <Popconfirm
              title="确认删除此任务？"
              onConfirm={withLoading(task.id, () => deleteTask(task.id))}
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
            >
              <Tooltip title="删除">
                <Button size="small" danger icon={<DeleteOutlined />} loading={loading} />
              </Tooltip>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  return (
    <AppScrollArea style={{ height: '100%' }}>
      <Flex vertical style={{ padding: 24 }} gap={16}>
        <Flex align="center" justify="space-between" wrap="wrap" gap={8}>
          <Typography.Title level={4} style={{ margin: 0 }}>计划任务</Typography.Title>
          <Flex gap={8} wrap="wrap">
            <Button
              icon={<ReloadOutlined />}
              onClick={() => { void fetchTasks(page, pageSize); setSelectedRowKeys([]); }}
              loading={isLoading}
            >
              刷新
            </Button>
            {selectedRowKeys.length > 0 && (
              <Button danger icon={<DeleteOutlined />} onClick={handleBatchDelete}>
                删除所选 ({selectedRowKeys.length})
              </Button>
            )}
            <Button icon={<PlusOutlined />} type="primary" onClick={handleCreate}>
              新建任务
            </Button>
          </Flex>
        </Flex>

        <Flex gap={8} wrap="wrap">
          <Select
            value={statusFilter ?? ''}
            options={STATUS_OPTIONS}
            style={{ width: 120 }}
            onChange={(val) => {
              setStatusFilter(val || undefined);
            }}
          />
          <Input.Search
            allowClear
            placeholder="搜索名称或提示词"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onSearch={handleSearch}
            style={{ flex: '1 1 240px', maxWidth: 400 }}
          />
          <Button type="primary" ghost onClick={handleSearch}>
            筛选
          </Button>
        </Flex>

        {error && <Alert type="error" message={error} closable onClose={clearError} />}

        <Table<ScheduledTask>
          rowKey="id"
          columns={columns}
          dataSource={tasks}
          loading={isLoading}
          rowSelection={{
            selectedRowKeys,
            onChange: (keys) => setSelectedRowKeys(keys as string[]),
          }}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: false,
            showTotal: (t) => `共 ${t} 个任务`,
            onChange: (p) => { void fetchTasks(p, pageSize); setSelectedRowKeys([]); },
          }}
          expandable={{
            expandedRowRender: (task) =>
              task.last_error ? (
                <Alert
                  type="error"
                  message={`最后一次错误：${task.last_error}`}
                  style={{ margin: '4px 0' }}
                />
              ) : (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  上次运行：{task.last_run_at ? formatAppDateTime(task.last_run_at) : '从未运行'}
                  {task.last_run_status ? `（${task.last_run_status}）` : ''}
                </Typography.Text>
              ),
            rowExpandable: (task) => !!(task.last_error || task.last_run_at),
          }}
          size="middle"
        />

        <CreateTaskModal
          open={modalOpen}
          task={editingTask}
          onClose={() => setModalOpen(false)}
          onCreate={handleCreate_}
          onUpdate={handleUpdate_}
        />
      </Flex>
    </AppScrollArea>
  );
}
