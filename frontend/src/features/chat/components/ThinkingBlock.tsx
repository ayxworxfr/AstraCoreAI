import { useState, useEffect } from 'react';
import { Flex, Typography, Collapse, Dropdown, Button, Tooltip, theme } from 'antd';
import { ThunderboltOutlined, LoadingOutlined, DownOutlined } from '@ant-design/icons';
import type { MenuProps } from 'antd';
import type { ThinkingMode } from '@/features/chat/types';
import AppScrollArea from '@/shared/components/AppScrollArea';

export type ThinkingPreference = 'off' | 'on' | 'adaptive';

export const THINKING_MODE_OPTIONS: Array<{ value: ThinkingPreference; label: string; title: string }> = [
  { value: 'off', label: '关', title: '关闭思考' },
  { value: 'on', label: '深度', title: '强制使用深度思考' },
  { value: 'adaptive', label: '自适应', title: '由模型按问题复杂度决定是否深度思考' },
];

export function ThinkingModeSelector({
  value,
  disabled,
  onChange,
}: {
  value: ThinkingPreference;
  disabled: boolean;
  onChange: (value: ThinkingPreference) => void;
}) {
  const { token } = theme.useToken();
  const active = value !== 'off';
  const selected = THINKING_MODE_OPTIONS.find((option) => option.value === value);
  const menuItems: MenuProps['items'] = THINKING_MODE_OPTIONS.map((option) => ({
    key: option.value,
    label: (
      <Flex vertical gap={2}>
        <Typography.Text strong={value === option.value}>{option.label}</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{option.title}</Typography.Text>
      </Flex>
    ),
  }));

  return (
    <Tooltip title={selected?.title ?? '选择思考模式'}>
      <Dropdown
        disabled={disabled}
        trigger={['click']}
        menu={{
          items: menuItems,
          selectedKeys: [value],
          onClick: ({ key }) => onChange(key as ThinkingPreference),
        }}
      >
        <Button
          aria-label="选择思考模式"
          size="small"
          disabled={disabled}
          type={active ? 'primary' : 'default'}
          ghost={active}
          icon={<ThunderboltOutlined />}
          style={{
            borderRadius: 20,
            fontSize: 12,
            height: 26,
            padding: '0 10px',
            ...(active
              ? { borderColor: '#7c3aed', color: '#7c3aed', background: '#faf5ff' }
              : { color: token.colorTextSecondary }),
          }}
        >
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            {selected?.label ?? '思考'}
            <DownOutlined style={{ fontSize: 10, opacity: 0.7 }} />
          </span>
        </Button>
      </Dropdown>
    </Tooltip>
  );
}

export function ThinkingBlock({
  thinking,
  streaming,
  roundLabel,
  mode,
  collapseMode,
}: {
  thinking: string;
  streaming: boolean;
  roundLabel?: string;
  mode: ThinkingMode;
  collapseMode: 'auto' | 'always_collapsed';
}) {
  const { token } = theme.useToken();
  const isDark = token.colorBgBase < '#888888';
  const blockBg = isDark ? token.colorFillQuaternary : '#faf5ff';
  const borderColor = isDark ? token.colorBorderSecondary : '#e9d5ff';
  const headerBg = isDark ? token.colorFillTertiary : '#faf5ff';
  const bodyBg = isDark ? token.colorFillQuaternary : '#fdf8ff';
  const accentColor = '#9333ea';
  const textColor = isDark ? '#c084fc' : '#7c3aed';
  const contentColor = isDark ? '#a78bfa' : '#6b21a8';

  const [activeKey, setActiveKey] = useState<string[]>(
    collapseMode === 'auto' && streaming ? ['thinking'] : [],
  );

  useEffect(() => {
    if (collapseMode === 'always_collapsed') {
      setActiveKey([]);
    } else if (streaming) {
      setActiveKey(['thinking']);
    } else {
      setActiveKey([]);
    }
  }, [streaming, collapseMode]);

  return (
    <Collapse
      size="small"
      activeKey={activeKey}
      onChange={(keys) => setActiveKey(keys as string[])}
      style={{
        marginBottom: 10,
        background: blockBg,
        border: `1px solid ${borderColor}`,
        borderRadius: 10,
        overflow: 'hidden',
      }}
      items={[
        {
          key: 'thinking',
          label: (
            <Flex align="center" gap={6}>
              {streaming ? (
                <LoadingOutlined style={{ color: accentColor, fontSize: 11 }} spin />
              ) : (
                <span style={{ color: accentColor, fontSize: 12, lineHeight: 1 }}>✦</span>
              )}
              <Typography.Text style={{ fontSize: 12, color: textColor, fontWeight: 600 }}>
                {streaming
                  ? (mode === 'deep' ? '深度思考中...' : mode === 'tool' ? '处理中...' : '思考中...')
                  : roundLabel ?? '思考过程'}
              </Typography.Text>
              {!streaming && (
                <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 4 }}>
                  （点击展开）
                </Typography.Text>
              )}
            </Flex>
          ),
          styles: {
            header: { background: headerBg, padding: '6px 12px' },
            body: {
              background: bodyBg,
              borderTop: `1px solid ${borderColor}`,
              padding: '10px 14px',
            },
          },
          children: (
            <AppScrollArea style={{ maxHeight: 360 }}>
              <div
                style={{
                  paddingRight: 8,
                  fontSize: 13,
                  lineHeight: 1.75,
                  color: contentColor,
                  whiteSpace: 'pre-wrap',
                  fontFamily: 'ui-monospace, "SF Mono", Consolas, monospace',
                  opacity: 0.9,
                }}
              >
                {thinking}
              </div>
            </AppScrollArea>
          ),
        },
      ]}
    />
  );
}
