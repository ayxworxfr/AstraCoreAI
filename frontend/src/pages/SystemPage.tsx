import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Descriptions,
  Flex,
  Form,
  InputNumber,
  Slider,
  Switch,
  Tabs,
  Tooltip,
  Typography,
} from 'antd';
import { ReloadOutlined, SyncOutlined } from '@ant-design/icons';
import HealthStatusCard, { type CheckResult } from '../components/system/HealthStatusCard';
import { getHealth, getReady } from '../services/healthService';
import { getSystemInfo } from '../services/systemService';
import { normalizeError } from '../services/apiClient';
import { useSkillStore } from '../stores/skillStore';
import type { SystemInfo } from '../types/system';
import type { UserSettings } from '../types/skill';

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
          <Descriptions.Item label="Provider">{defaultProfile?.provider ?? '—'}</Descriptions.Item>
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
                      {profile.provider} / {profile.model}
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
          <Descriptions.Item label="Tavily 联网搜索">
            {info ? (
              info.tavily_configured ? (
                <Badge status="success" text="已配置 TAVILY_API_KEY" />
              ) : (
                <Badge status="warning" text="未配置，联网搜索不可用" />
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
  const [form] = Form.useForm<Pick<UserSettings, 'temperature' | 'rag_top_k' | 'context_max_messages'>>();

  useEffect(() => {
    void fetchSettings();
  }, [fetchSettings]);

  useEffect(() => {
    form.setFieldsValue({
      temperature: settings.temperature,
      rag_top_k: settings.rag_top_k,
      context_max_messages: settings.context_max_messages,
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
          >
            <Form.Item name="context_max_messages" noStyle rules={[{ required: true }]}>
              <InputNumber min={4} max={200} style={{ width: 120 }} addonAfter="条" />
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

// ─── SystemPage ────────────────────────────────────────────────────────────────

export default function SystemPage(): JSX.Element {
  return (
    <Flex vertical style={{ height: '100%', overflow: 'auto', padding: 24 }} gap={16}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        系统
      </Typography.Title>
      <Tabs
        items={[
          { key: 'status', label: '系统状态', children: <StatusTab /> },
          { key: 'llm', label: 'LLM 信息', children: <LLMInfoTab /> },
          { key: 'runtime', label: '运行参数', children: <RuntimeParamsTab /> },
        ]}
      />
    </Flex>
  );
}
