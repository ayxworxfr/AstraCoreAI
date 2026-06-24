import { useState } from 'react';
import { Flex, Typography, Collapse, Popover, theme } from 'antd';
import { CheckOutlined, CloseCircleOutlined, LoadingOutlined } from '@ant-design/icons';
import type { SubAgentActivity, ToolActivity } from '@/features/chat/types';
import AppScrollArea from '@/shared/components/AppScrollArea';

const TOOL_BADGE_VISIBLE = 3;

function formatDuration(ms: number | undefined): string | null {
  if (ms === undefined) return null;
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function ToolDetailPopover({ tool }: { tool: ToolActivity }) {
  const { token } = theme.useToken();
  const preStyle: React.CSSProperties = {
    margin: 0,
    background: token.colorFillTertiary,
    padding: '6px 8px',
    borderRadius: 4,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-all',
    fontSize: 11,
    fontFamily: 'ui-monospace, "SF Mono", Consolas, monospace',
  };
  const hasInput = tool.input && Object.keys(tool.input).length > 0;
  return (
    <div style={{ maxWidth: 380, fontSize: 12 }}>
      {hasInput && (
        <div style={{ marginBottom: tool.result !== undefined ? 10 : 0 }}>
          <div style={{ color: token.colorTextSecondary, marginBottom: 4, fontWeight: 500 }}>输入参数</div>
          <AppScrollArea style={{ maxHeight: 160, borderRadius: 4 }}>
            <pre style={preStyle}>{JSON.stringify(tool.input, null, 2)}</pre>
          </AppScrollArea>
        </div>
      )}
      {tool.result !== undefined && (
        <div>
          <div style={{ color: tool.isError ? token.colorError : token.colorTextSecondary, marginBottom: 4, fontWeight: 500 }}>
            {tool.isError ? '错误信息' : '返回结果'}
          </div>
          <AppScrollArea style={{ maxHeight: 160, borderRadius: 4 }}>
            <pre style={{ ...preStyle, color: tool.isError ? token.colorError : undefined }}>
              {tool.result.length > 600 ? tool.result.slice(0, 600) + '\n…（已截断）' : tool.result}
            </pre>
          </AppScrollArea>
        </div>
      )}
      {!hasInput && tool.result === undefined && (
        <span style={{ color: token.colorTextSecondary }}>执行中…</span>
      )}
      {tool.done && tool.durationMs !== undefined && (
        <div style={{ marginTop: 8, color: token.colorTextSecondary, fontSize: 11 }}>
          执行耗时: {formatDuration(tool.durationMs)}
        </div>
      )}
    </div>
  );
}

function ToolBadge({ tool }: { tool: ToolActivity }) {
  const { token } = theme.useToken();
  const running = !tool.done;
  const bg = running ? token.colorWarningBg : (tool.isError ? token.colorErrorBg : token.colorSuccessBg);
  const border = running ? token.colorWarningBorder : (tool.isError ? token.colorErrorBorder : token.colorSuccessBorder);
  const color = running ? token.colorWarningText : (tool.isError ? token.colorErrorText : token.colorSuccessText);
  return (
    <Popover
      title={tool.name}
      content={<ToolDetailPopover tool={tool} />}
      trigger="hover"
      placement="top"
      overlayStyle={{ maxWidth: 420 }}
    >
      <span style={{
        display: 'inline-flex', alignItems: 'center', gap: 5, padding: '2px 10px',
        borderRadius: 12, fontSize: 12, cursor: 'default', background: bg,
        border: `1px solid ${border}`, color,
      }}>
        {running
          ? <LoadingOutlined style={{ fontSize: 10 }} spin />
          : <CheckOutlined style={{ fontSize: 10 }} />}
        {tool.name}
        {tool.done && tool.durationMs !== undefined && (
          <span style={{ opacity: 0.6, fontSize: 10 }}>{formatDuration(tool.durationMs)}</span>
        )}
      </span>
    </Popover>
  );
}

export function ToolActivityRow({ tools }: { tools: ToolActivity[] }) {
  const { token } = theme.useToken();
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? tools : tools.slice(0, TOOL_BADGE_VISIBLE);
  const hidden = tools.length - TOOL_BADGE_VISIBLE;
  return (
    <Flex wrap gap={6} align="center" style={{ marginBottom: 8 }}>
      {shown.map((t, i) => <ToolBadge key={i} tool={t} />)}
      {!expanded && hidden > 0 && (
        <span
          onClick={() => setExpanded(true)}
          style={{
            display: 'inline-flex', alignItems: 'center', padding: '2px 10px',
            borderRadius: 12, fontSize: 12, cursor: 'pointer',
            background: token.colorFillTertiary,
            border: `1px solid ${token.colorBorderSecondary}`,
            color: token.colorTextSecondary,
          }}
        >
          +{hidden} 更多
        </span>
      )}
    </Flex>
  );
}

function SubAgentCard({ agent }: { agent: SubAgentActivity }) {
  const { token } = theme.useToken();
  const running = agent.status === 'running';
  const isError = agent.status === 'error';
  const isDark = token.colorBgBase < '#888888';
  const bg = isDark
    ? running ? token.colorWarningBg : (isError ? token.colorErrorBg : token.colorSuccessBg)
    : running ? '#fff1d6' : (isError ? '#fff0ee' : '#eef7e8');
  const bodyBg = isDark
    ? token.colorBgContainer
    : running ? '#fffaf0' : (isError ? '#fff8f7' : '#f7fbf3');
  const border = isDark
    ? running ? token.colorWarningBorder : (isError ? token.colorErrorBorder : token.colorSuccessBorder)
    : running ? '#f0c36d' : (isError ? '#f0a39a' : '#b8d9a6');
  const accentColor = running ? token.colorWarning : (isError ? token.colorError : token.colorSuccess);
  const textColor = running ? token.colorWarningText : (isError ? token.colorErrorText : token.colorSuccessText);
  const taskPreview = agent.task.length > 60 ? `${agent.task.slice(0, 60)}...` : agent.task;

  return (
    <Collapse
      size="small"
      defaultActiveKey={[]}
      style={{ marginBottom: 6, background: bg, border: `1px solid ${border}`, borderRadius: 8, overflow: 'hidden' }}
      items={[
        {
          key: 'agent',
          label: (
            <Flex align="center" gap={6}>
              {running ? (
                <LoadingOutlined style={{ color: accentColor, fontSize: 11 }} spin />
              ) : isError ? (
                <CloseCircleOutlined style={{ color: accentColor, fontSize: 11 }} />
              ) : (
                <CheckOutlined style={{ color: accentColor, fontSize: 11 }} />
              )}
              <Typography.Text style={{ fontSize: 12, color: textColor, fontWeight: 600, flex: 1 }}>
                {taskPreview}
              </Typography.Text>
              {agent.model && (
                <Typography.Text type="secondary" style={{ fontSize: 11, marginRight: 4 }}>
                  {agent.model}
                </Typography.Text>
              )}
              {!running && agent.durationMs !== undefined && (
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  {formatDuration(agent.durationMs)}
                </Typography.Text>
              )}
            </Flex>
          ),
          styles: {
            header: { background: bg, padding: '5px 12px' },
            body: { background: bodyBg, borderTop: `1px solid ${border}`, padding: '10px 14px' },
          },
          children: (
            <div>
              {agent.toolActivity.length > 0 && <ToolActivityRow tools={agent.toolActivity} />}
              {agent.error && (
                <Typography.Text type="danger" style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>
                  {agent.error}
                </Typography.Text>
              )}
              {agent.text ? (
                <div style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {agent.text}
                </div>
              ) : running && agent.toolActivity.length === 0 && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>执行中...</Typography.Text>
              )}
            </div>
          ),
        },
      ]}
    />
  );
}

export function SubAgentPanel({ agents }: { agents: SubAgentActivity[] }) {
  if (agents.length === 0) return null;
  return (
    <div style={{ marginBottom: 10 }}>
      {agents.map((agent) => (
        <SubAgentCard key={agent.agentId} agent={agent} />
      ))}
    </div>
  );
}
