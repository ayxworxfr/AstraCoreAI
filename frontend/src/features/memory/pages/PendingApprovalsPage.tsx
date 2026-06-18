import { useEffect, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Empty,
  Flex,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { CheckOutlined, CloseOutlined, ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { apiClient, normalizeError } from '@/shared/services/apiClient';
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

const SCOPE_LABEL: Record<string, string> = {
  user: '用户级',
  project: '项目级',
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

type ApprovalCardProps = {
  item: PendingPromotionItem;
  selected: boolean;
  actionLoading: boolean;
  timezone: string;
  onSelect: (id: string, checked: boolean) => void;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
};

function ApprovalCard({
  item,
  selected,
  actionLoading,
  timezone,
  onSelect,
  onApprove,
  onReject,
}: ApprovalCardProps) {
  return (
    <Card
      size="small"
      styles={{ body: { padding: '14px 16px' } }}
      style={{
        borderRadius: 12,
        border: selected ? '1.5px solid #1677ff' : undefined,
        transition: 'border-color 0.15s',
      }}
    >
      <Flex gap={12} align="flex-start">
        <Checkbox
          checked={selected}
          onChange={(e) => onSelect(item.id, e.target.checked)}
          style={{ marginTop: 3, flexShrink: 0 }}
        />
        <Flex vertical gap={8} style={{ flex: 1, minWidth: 0 }}>
          {/* 标题行：scope tag + subject + 时间 */}
          <Flex align="center" justify="space-between" gap={8}>
            <Flex align="center" gap={8} style={{ minWidth: 0, flex: 1 }}>
              <Tag color={SCOPE_COLOR[item.target_scope] ?? 'default'} style={{ flexShrink: 0, marginInlineEnd: 0 }}>
                {SCOPE_LABEL[item.target_scope] ?? item.target_scope}
              </Tag>
              <Typography.Text
                strong
                style={{ fontSize: 14 }}
                ellipsis={{ tooltip: item.candidate_subject || '未命名记忆' }}
              >
                {item.candidate_subject || '未命名记忆'}
              </Typography.Text>
            </Flex>
            <Typography.Text type="secondary" style={{ fontSize: 12, flexShrink: 0 }}>
              {formatAppDateTime(item.created_at, timezone)}
            </Typography.Text>
          </Flex>

          {/* 记忆内容 */}
          <Typography.Paragraph
            style={{ margin: 0, fontSize: 13 }}
            ellipsis={{ rows: 3, expandable: true, symbol: '展开' }}
          >
            {item.candidate_content}
          </Typography.Paragraph>

          {/* 原因 + 操作 */}
          <Flex align="flex-end" justify="space-between" gap={12} wrap="wrap">
            {item.reason ? (
              <Typography.Text type="secondary" italic style={{ fontSize: 12, flex: 1, minWidth: 0 }}>
                AI 建议原因：{item.reason}
              </Typography.Text>
            ) : (
              <span style={{ flex: 1 }} />
            )}
            <Flex gap={8} style={{ flexShrink: 0 }}>
              <Tooltip title="批准并晋升为持久记忆">
                <Button
                  type="primary"
                  size="small"
                  icon={<CheckOutlined />}
                  loading={actionLoading}
                  onClick={() => onApprove(item.id)}
                >
                  批准
                </Button>
              </Tooltip>
              <Tooltip title="拒绝，保持为短期记忆">
                <Button
                  danger
                  size="small"
                  icon={<CloseOutlined />}
                  loading={actionLoading}
                  onClick={() => onReject(item.id)}
                >
                  拒绝
                </Button>
              </Tooltip>
            </Flex>
          </Flex>
        </Flex>
      </Flex>
    </Card>
  );
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

  const toggleSelect = (id: string, checked: boolean) => {
    setSelectedIds((prev) => (checked ? [...prev, id] : prev.filter((x) => x !== id)));
  };

  const allSelected = items.length > 0 && selectedIds.length === items.length;
  const someSelected = selectedIds.length > 0 && selectedIds.length < items.length;

  const toggleSelectAll = () => {
    setSelectedIds(allSelected ? [] : items.map((i) => i.id));
  };

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

  const selectedCount = selectedIds.length;

  return (
    <AppScrollArea style={{ height: '100%' }}>
      <Flex vertical gap={16} style={{ maxWidth: 900, margin: '0 auto', padding: '24px 24px' }}>
        {/* 页面 header */}
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
              <Flex align="center" gap={10}>
                <SafetyCertificateOutlined style={{ fontSize: 20, color: '#1677ff' }} />
                <Typography.Title level={3} style={{ margin: 0 }}>
                  待审批
                </Typography.Title>
                <Badge count={total} showZero color="#1677ff" />
              </Flex>
              <Typography.Text type="secondary" style={{ marginTop: 4, display: 'block' }}>
                AI 判断为值得长期保留的记忆，晋升前需要你确认。
              </Typography.Text>
            </div>
            <Flex gap={8} align="center" wrap="wrap">
              {selectedCount > 0 && (
                <>
                  <Button
                    type="primary"
                    size="small"
                    icon={<CheckOutlined />}
                    loading={loading}
                    onClick={() => { void handleBatch('approve'); }}
                  >
                    批准 ({selectedCount})
                  </Button>
                  <Button
                    danger
                    size="small"
                    icon={<CloseOutlined />}
                    loading={loading}
                    onClick={() => { void handleBatch('reject'); }}
                  >
                    拒绝 ({selectedCount})
                  </Button>
                </>
              )}
              <Button icon={<ReloadOutlined />} loading={loading} onClick={() => { void load(); }}>
                刷新
              </Button>
            </Flex>
          </Flex>
        </Card>

        {error && (
          <Alert type="error" message={error} closable onClose={() => setError(null)} />
        )}

        {/* 列表区域 */}
        {loading && items.length === 0 ? (
          <Flex justify="center" style={{ padding: '60px 0' }}>
            <Spin size="large" />
          </Flex>
        ) : items.length === 0 ? (
          <Card bordered={false} style={{ borderRadius: 16 }}>
            <Empty
              description="暂无待审批的记忆晋升"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              style={{ padding: '40px 0' }}
            />
          </Card>
        ) : (
          <Flex vertical gap={8}>
            {/* 全选栏 */}
            <Flex align="center" gap={10} style={{ padding: '0 4px' }}>
              <Checkbox checked={allSelected} indeterminate={someSelected} onChange={toggleSelectAll}>
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                  {allSelected ? '取消全选' : '全选'}
                </Typography.Text>
              </Checkbox>
              {selectedCount > 0 && (
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                  已选 {selectedCount} / {items.length}
                </Typography.Text>
              )}
            </Flex>

            {items.map((item) => (
              <ApprovalCard
                key={item.id}
                item={item}
                selected={selectedIds.includes(item.id)}
                actionLoading={actionLoading[item.id] ?? false}
                timezone={timezone}
                onSelect={toggleSelect}
                onApprove={(id) => { void handleSingle(id, 'approve'); }}
                onReject={(id) => { void handleSingle(id, 'reject'); }}
              />
            ))}
          </Flex>
        )}
      </Flex>
    </AppScrollArea>
  );
}
