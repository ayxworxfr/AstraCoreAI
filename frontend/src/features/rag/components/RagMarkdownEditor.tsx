import { Input, Flex, theme as antTheme } from 'antd';
import MarkdownContent from '@/features/chat/components/MarkdownContent';
import AppScrollArea from '@/shared/components/AppScrollArea';

type Props = {
  value: string;
  onChange: (value: string) => void;
  height?: number;
};

export default function RagMarkdownEditor({ value, onChange, height = 550 }: Props): JSX.Element {
  const { token } = antTheme.useToken();

  const panelHeaderStyle: React.CSSProperties = {
    padding: '6px 12px',
    background: token.colorFillQuaternary,
    borderBottom: `1px solid ${token.colorBorder}`,
    fontSize: 12,
    color: token.colorTextSecondary,
    flexShrink: 0,
  };

  return (
    <Flex
      style={{
        height,
        border: `1px solid ${token.colorBorder}`,
        borderRadius: token.borderRadius,
        overflow: 'hidden',
      }}
    >
      {/* 编辑区 */}
      <Flex
        vertical
        style={{ flex: 1, borderRight: `1px solid ${token.colorBorder}`, minWidth: 0 }}
      >
        <div style={panelHeaderStyle}>编辑</div>
        <Input.TextArea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          style={{
            flex: 1,
            resize: 'none',
            border: 'none',
            borderRadius: 0,
            boxShadow: 'none',
            fontFamily: 'ui-monospace, "SF Mono", Consolas, monospace',
            fontSize: 13,
            lineHeight: 1.65,
            height: '100%',
          }}
          placeholder="输入 Markdown 内容..."
        />
      </Flex>

      {/* 预览区 */}
      <Flex vertical style={{ flex: 1, minWidth: 0 }}>
        <div style={panelHeaderStyle}>预览</div>
        <AppScrollArea style={{ flex: 1 }}>
          <div style={{ padding: '12px 16px' }}>
            <MarkdownContent content={value} />
          </div>
        </AppScrollArea>
      </Flex>
    </Flex>
  );
}
