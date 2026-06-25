import { Flex, Typography, Dropdown, Button, Tooltip, theme } from 'antd';
import { RocketOutlined, DownOutlined } from '@ant-design/icons';
import type { MenuProps } from 'antd';

const EFFORT_LABELS: Record<string, string> = {
  none: '无',
  minimal: '极低',
  low: '低',
  medium: '中',
  high: '高',
  xhigh: '极高',
  max: '最高',
};

const EFFORT_DESCRIPTIONS: Record<string, string> = {
  none: '不推理',
  minimal: '极低推理深度，速度最快',
  low: '低推理深度',
  medium: '标准推理（默认）',
  high: '较深推理',
  xhigh: '极高推理深度',
  max: '最深推理，更耗时',
};

export function ReasoningEffortSelector({
  value,
  levels,
  defaultValue,
  disabled,
  onChange,
}: {
  value: string | null;
  levels: string[];
  defaultValue?: string;
  disabled: boolean;
  onChange: (effort: string | null) => void;
}) {
  const { token } = theme.useToken();
  const effectiveValue = value ?? defaultValue ?? levels[0];
  const isOverride = value !== null;
  const active = effectiveValue !== 'none';

  const menuItems: MenuProps['items'] = levels.map((level) => ({
    key: level,
    label: (
      <Flex vertical gap={2}>
        <Typography.Text strong={effectiveValue === level}>
          {EFFORT_LABELS[level] ?? level}
        </Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {EFFORT_DESCRIPTIONS[level] ?? level}
        </Typography.Text>
      </Flex>
    ),
  }));

  const displayLabel = EFFORT_LABELS[effectiveValue] ?? effectiveValue;
  const buttonLabel = isOverride ? displayLabel : `默认${displayLabel}`;
  const tooltipTitle = isOverride
    ? `推理深度：${displayLabel}`
    : `使用模型默认推理深度：${displayLabel}`;

  return (
    <Tooltip title={tooltipTitle}>
      <Dropdown
        disabled={disabled}
        trigger={['click']}
        menu={{
          items: menuItems,
          selectedKeys: [effectiveValue],
          onClick: ({ key }) => onChange(key),
        }}
      >
        <Button
          aria-label="选择推理深度"
          size="small"
          disabled={disabled}
          type={active ? 'primary' : 'default'}
          ghost={active}
          icon={<RocketOutlined />}
          style={{
            borderRadius: 20,
            fontSize: 12,
            height: 26,
            padding: '0 10px',
            ...(active
              ? { borderColor: '#059669', color: '#059669', background: '#f0fdf4' }
              : { color: token.colorTextSecondary }),
          }}
        >
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            {buttonLabel}
            <DownOutlined style={{ fontSize: 10, opacity: 0.7 }} />
          </span>
        </Button>
      </Dropdown>
    </Tooltip>
  );
}
