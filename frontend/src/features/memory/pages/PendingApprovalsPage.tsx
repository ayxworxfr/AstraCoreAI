import { useEffect, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Flex,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { CheckOutlined, CloseOutlined, ReloadOutlined } from '@ant-design/icons';
import { apiClient } from '@/shared/services/apiClient';
import { normalizeError } from '@/shared/services/apiClient';
import { formatAppDateTime } from '@/shared/utils/time';
import { useSkillStore } from '@/features/skills/store/skillStore';
import AppScrollArea from '@/shared/components/AppScrollArea';

type PendingPromotionItem = {
  id: string;
  user_id: string;
  source_memory_id: string;
  target_scope: 'user' | 'project';
  reason: string;
  candidate_content: string;
  candidate_subject: string;
  status: 'pending' | 'approved' | 'rejected';
  created_at: string;
  reviewed_at: string | null;
};

type PendingPromotionListResponse = {
  total: number;
  items: PendingPromotionItem[];
};

const SCOPE_COLOR: Record<string, string> = {
  user: 'purple',
  project: 'cyan',
};

async function fetchPendingApprovals(limit = 50, offset = 0): Promise<PendingPromotionListResponse> {
  const { data } = await apiClient.get<PendingPromotionListResponse>('/api/v1/memory/pending-approvals', {
    params: { limit, offset },
  });
  return data;
}

async function batchReview(ids: string[], action: 'approve' | 'reject'): Promise<void> {
  await apiClient.post('/api/v1/memory/pending-approvals/batch-review', {
    decisions: ids.map((id) => ({ id, action })),
  });
}

export default function PendingApprovalsPage(): JSX.Element {
  const timezone = useSkillStore((s) => s.settings.timezone);
  const [items, setItems] = useState<PendingPromotionItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchPendingApprovals();
      setItems(result.items);
      setTotal(result.total);
    } catch (e) {
      setError(normalizeError(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleSingle = async (id: string, action: 'approve' | 'reject') => {
    setActionLoading((prev) => ({ ...prev, [id]: true }));
    try {
      await batchReview([id], action);
      await load();
      setSelectedIds((prev) => prev.filter((x) => x !== id));
    } catch (e) {
      setError(normalizeError(e));
    } finally {
      setActionLoading((prev) => ({ ...prev, [id]: false }));
    }
  };

  const handleBatch = async (action: 'approve' | 'reject') => {
    if (selectedIds.length === 0) return;
    setLoading(true);
    try {
      await batchReview(selectedIds, action);
      setSelectedIds([]);
      await load();
    } catch (e) {
      setError(normalizeError(e));
      setLoading(false);
    }
  };

  const columns = [
    {
      title: '主题',
      dataIndex: 'candidate_subject',
      key: 'candidate_subject',
      width: 180,
      render: (v: string) => <Typography.Text strong style={{ fontSize: 13 }}>{v || '（无标题）'}</Typography.Text>,
    },
    {
      title: '内容',
      dataIndex: 'candidate_content',
      key: 'candidate_content',
      render: (v: string) => (
        <Typography.Text style={{ fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          {v.length > 200 ? v.slice(0, 200) + '...' : v}
        </Typography.Text>
      ),
    },
    {
      title: '目标范围',
      dataIndex: 'target_scope',
      key: 'target_scope',
      width: 90,
      render: (v: string) => <Tag color={SCOPE_COLOR[v] ?? 'default'}>{v}</Tag>,
    },
    {
      title: '原因',
      dataIndex: 'reason',
      key: 'reason',
      width: 200,
      render: (v: string) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {v || '—'}
        </Typography.Text>
      ),
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 130,
      render: (v: string) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {formatAppDateTime(v, timezone)}
        </Typography.Text>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 110,
      render: (_: unknown, record: PendingPromotionItem) => (
        <Flex gap={6}>
          <Tooltip title="批准并晋升为持久记忆">
            <Button
              size="small"
              type="primary"
              icon={<CheckOutlined />}
              loading={actionLoading[record.id]}
              onClick={() => { void handleSingle(record.id, 'approve'); }}
              style={{ borderRadius: 6 }}
            />
          </Tooltip>
          <Tooltip title="拒绝，保持为短期记忆">
            <Button
              size="small"
              danger
              icon={<CloseOutlined />}
              loading={actionLoading[record.id]}
              onClick={() => { void handleSingle(record.id, 'reject'); }}
              style={{ borderRadius: 6 }}
            />
          </Tooltip>
        </Flex>
      ),
    },
  ];

  return (
    <AppScrollArea style={{ height: '100%' }}>
      <div style={{ maxWidth: 1100, margin: '0 auto', padding: '24px 24px' }}>
        <Flex align="center" justify="space-between" style={{ marginBottom: 16 }}>
          <Flex align="center" gap={10}>
            <Typography.Title level={4} style={{ margin: 0 }}>
              待审批记忆晋升
            </Typography.Title>
            <Badge count={total} showZero style={{ fontSize: 11 }} />
          </Flex>
          <Flex gap={8}>
            {selectedIds.length > 0 && (
              <>
                <Button
                  type="primary"
                  size="small"
                  icon={<CheckOutlined />}
                  onClick={() => { void handleBatch('approve'); }}
                  loading={loading}
                >
                  批量批准 ({selectedIds.length})
                </Button>
                <Button
                  danger
                  size="small"
                  icon={<CloseOutlined />}
                  onClick={() => { void handleBatch('reject'); }}
                  loading={loading}
                >
                  批量拒绝 ({selectedIds.length})
                </Button>
              </>
            )}
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => { void load(); }}
              loading={loading}
            >
              刷新
            </Button>
          </Flex>
        </Flex>

        {error && (
          <Alert
            type="error"
            message={error}
            closable
            onClose={() => setError(null)}
            style={{ marginBottom: 12 }}
          />
        )}

        <Card styles={{ body: { padding: 0 } }}>
          <Table<PendingPromotionItem>
            dataSource={items}
            columns={columns}
            rowKey="id"
            loading={loading}
            size="small"
            rowSelection={{
              selectedRowKeys: selectedIds,
              onChange: (keys) => setSelectedIds(keys as string[]),
            }}
            pagination={false}
            locale={{ emptyText: '暂无待审批记忆' }}
          />
        </Card>

        {items.length === 0 && !loading && (
          <Typography.Text type="secondary" style={{ fontSize: 13, display: 'block', textAlign: 'center', marginTop: 16 }}>
            没有需要审批的记忆晋升请求
          </Typography.Text>
        )}
      </div>
    </AppScrollArea>
  );
}
