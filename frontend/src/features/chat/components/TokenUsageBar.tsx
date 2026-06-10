import { useState } from 'react';
import { theme, Tooltip } from 'antd';
import { BarChartOutlined, UpOutlined, MinusOutlined } from '@ant-design/icons';
import { calculateCost, formatCost, formatTokens, getModelDisplayName } from '@/features/chat/constants/pricing';

type Props = {
  inputTokens: number;
  outputTokens: number;
  model: string;
};

export default function TokenUsageBar({ inputTokens, outputTokens, model }: Props): JSX.Element {
  const [collapsed, setCollapsed] = useState(false);
  const { token } = theme.useToken();
  const cost = calculateCost(inputTokens, outputTokens, model);
  const modelName = model ? getModelDisplayName(model) : '';

  if (collapsed) {
    return (
      <div
        style={{
          display: 'flex',
          justifyContent: 'flex-end',
          padding: '6px 0 16px',
        }}
      >
        <Tooltip title="会话累计用量">
          <button
            onClick={() => setCollapsed(false)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
              background: token.colorFillQuaternary,
              border: `1px solid ${token.colorBorderSecondary}`,
              borderRadius: 100,
              cursor: 'pointer',
              color: token.colorTextTertiary,
              fontSize: 11,
              padding: '2px 10px 2px 8px',
              transition: 'all 0.15s',
              lineHeight: 1.5,
            }}
          >
            <BarChartOutlined style={{ fontSize: 10 }} />
            <span style={{ fontWeight: 500 }}>{formatCost(cost)}</span>
            <UpOutlined style={{ fontSize: 8, opacity: 0.7 }} />
          </button>
        </Tooltip>
      </div>
    );
  }

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '7px 0 16px',
        gap: 8,
      }}
    >
      {/* 统计数据 */}
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 4 }}>
        <StatChip
          label="in"
          value={formatTokens(inputTokens)}
          dotColor="#1677ff"
          token={token}
        />
        <Sep token={token} />
        <StatChip
          label="out"
          value={formatTokens(outputTokens)}
          dotColor="#52c41a"
          token={token}
        />
        <Sep token={token} />
        <StatChip
          label="cost"
          value={formatCost(cost)}
          dotColor="#fa8c16"
          token={token}
        />
        {modelName && (
          <>
            <Sep token={token} />
            <StatChip
              label={null}
              value={modelName}
              dotColor={token.colorPrimary}
              token={token}
            />
          </>
        )}
      </div>

      {/* 折叠按钮 */}
      <Tooltip title="折叠">
        <button
          onClick={() => setCollapsed(true)}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 20,
            height: 20,
            flexShrink: 0,
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: token.colorTextQuaternary,
            borderRadius: 4,
            padding: 0,
            transition: 'color 0.15s',
          }}
        >
          <MinusOutlined style={{ fontSize: 10 }} />
        </button>
      </Tooltip>
    </div>
  );
}

type TokenType = ReturnType<typeof theme.useToken>['token'];

function StatChip({
  label,
  value,
  dotColor,
  token,
}: {
  label: string | null;
  value: string;
  dotColor: string;
  token: TokenType;
}): JSX.Element {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '2px 8px',
        borderRadius: 100,
        background: token.colorFillQuaternary,
        border: `1px solid ${token.colorBorderSecondary}`,
        fontSize: 11,
        lineHeight: 1.5,
        userSelect: 'none',
        whiteSpace: 'nowrap',
      }}
    >
      <span
        style={{
          width: 5,
          height: 5,
          borderRadius: '50%',
          background: dotColor,
          flexShrink: 0,
          opacity: 0.85,
        }}
      />
      {label && (
        <span style={{ color: token.colorTextQuaternary }}>{label}</span>
      )}
      <span style={{ color: token.colorTextSecondary, fontWeight: 500 }}>{value}</span>
    </span>
  );
}

function Sep({ token }: { token: TokenType }): JSX.Element {
  return (
    <span
      style={{
        color: token.colorBorderSecondary,
        fontSize: 11,
        lineHeight: 1,
        userSelect: 'none',
        padding: '0 2px',
      }}
    >
      ·
    </span>
  );
}
