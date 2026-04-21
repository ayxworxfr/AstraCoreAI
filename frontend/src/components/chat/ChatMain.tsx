import { useState, useRef } from 'react';
import { Bubble, Sender, Prompts } from '@ant-design/x';
import type { BubbleProps } from '@ant-design/x';
import {
  RobotOutlined,
  UserOutlined,
  ThunderboltOutlined,
  DatabaseOutlined,
  LoadingOutlined,
  ToolOutlined,
  CopyOutlined,
  CheckOutlined,
  DeleteOutlined,
  GlobalOutlined,
} from '@ant-design/icons';
import { Flex, Typography, Alert, Avatar, Button, Collapse, Tooltip, theme } from 'antd';
import { useChatStore } from '../../stores/chatStore';
import MarkdownContent from './MarkdownContent';
import SkillSelector from '../skills/SkillSelector';
import type { ChatMessage, ThinkingMode, ToolActivity } from '../../types/chat';

const SUGGESTED_PROMPTS = [
  { key: '1', label: '你能做什么？', icon: <ThunderboltOutlined /> },
  { key: '2', label: 'RAG 检索怎么用？', icon: <ThunderboltOutlined /> },
  { key: '3', label: '工具调用如何配置？', icon: <ThunderboltOutlined /> },
];

function ThinkingBlock({
  thinking,
  streaming,
  roundLabel,
  mode,
}: {
  thinking: string;
  streaming: boolean;
  roundLabel?: string;
  mode: ThinkingMode;
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

  return (
    <Collapse
      size="small"
      defaultActiveKey={streaming ? ['thinking'] : []}
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
            <div
              style={{
                maxHeight: 360,
                overflow: 'auto',
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
          ),
        },
      ]}
    />
  );
}

function MessageActions({
  message,
  conversationId,
  visible,
}: {
  message: ChatMessage;
  conversationId: string;
  visible: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const { deleteMessage } = useChatStore();
  const { token } = theme.useToken();

  const handleCopy = () => {
    void navigator.clipboard.writeText(message.content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const btnStyle: React.CSSProperties = {
    width: 28,
    height: 28,
    borderRadius: 8,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: token.colorTextTertiary,
    fontSize: 13,
  };

  return (
    <Flex
      gap={2}
      style={{
        opacity: visible ? 1 : 0,
        pointerEvents: visible ? 'auto' : 'none',
        transition: 'opacity 0.15s ease',
        padding: '2px 0',
      }}
    >
      <Tooltip title={copied ? '已复制' : '复制'}>
        <Button
          type="text"
          size="small"
          icon={copied ? <CheckOutlined style={{ color: token.colorSuccess }} /> : <CopyOutlined />}
          onClick={handleCopy}
          style={btnStyle}
        />
      </Tooltip>
      <Tooltip title="删除">
        <Button
          type="text"
          size="small"
          icon={<DeleteOutlined />}
          onClick={() => deleteMessage(conversationId, message.id)}
          style={{ ...btnStyle, color: token.colorError }}
        />
      </Tooltip>
    </Flex>
  );
}

function ToolActivityRow({ tools }: { tools: ToolActivity[] }) {
  const { token } = theme.useToken();
  return (
    <Flex wrap gap={6} style={{ marginBottom: 8 }}>
      {tools.map((t, i) => (
        <span
          key={i}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 5,
            padding: '2px 10px',
            borderRadius: 12,
            fontSize: 12,
            background: t.done ? token.colorSuccessBg : token.colorWarningBg,
            border: `1px solid ${t.done ? token.colorSuccessBorder : token.colorWarningBorder}`,
            color: t.done ? token.colorSuccessText : token.colorWarningText,
          }}
        >
          {t.done
            ? <CheckOutlined style={{ fontSize: 10 }} />
            : <LoadingOutlined style={{ fontSize: 10 }} spin />}
          {t.name}
        </span>
      ))}
    </Flex>
  );
}

function AssistantContent({ message }: { message: ChatMessage }) {
  const blocks = message.thinkingBlocks ?? [];
  const isStreaming = message.status === 'streaming';
  const mode: ThinkingMode = message.thinkingMode ?? (message.toolActivity?.length ? 'tool' : 'normal');

  // 只渲染有内容的块，或最后一个正在流式生成的块（内容还没来）
  const visible = blocks
    .map((block, idx) => {
      const isLast = idx === blocks.length - 1;
      const streaming = isStreaming && isLast && !message.content;
      return { block, idx, streaming };
    })
    .filter(({ block, streaming }) => block.trim().length > 0 || streaming);

  const multiRound = visible.length > 1;

  return (
    <div>
      {visible.map(({ block, idx, streaming }, renderedIdx) => (
        <ThinkingBlock
          key={idx}
          thinking={block}
          streaming={streaming}
          roundLabel={multiRound ? `第 ${renderedIdx + 1} 轮思考` : undefined}
          mode={mode}
        />
      ))}
      {message.toolActivity && message.toolActivity.length > 0 && (
        <ToolActivityRow tools={message.toolActivity} />
      )}
      <MarkdownContent content={message.content} />
    </div>
  );
}

type RolesType = Record<string, BubbleProps & { placement?: 'start' | 'end' }>;

export default function ChatMain(): JSX.Element {
  const [inputValue, setInputValue] = useState('');
  const [hoveredMsgId, setHoveredMsgId] = useState<string | null>(null);
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const {
    activeConversationId,
    messagesByConversation,
    isStreaming,
    enableThinking,
    enableRag,
    enableTools,
    enableWeb,
    sessionError,
    setEnableThinking,
    setEnableRag,
    setEnableTools,
    setEnableWeb,
    setSessionError,
    sendMessage,
    cancelStream,
  } = useChatStore();

  const messages = messagesByConversation[activeConversationId] ?? [];
  // 发送新消息时清除上一次的会话错误
  const handleSendMessage = (value: string) => {
    setSessionError(null);
    void sendMessage(value);
  };

  const onMsgEnter = (id: string) => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    setHoveredMsgId(id);
  };
  const onMsgLeave = () => {
    hoverTimerRef.current = setTimeout(() => setHoveredMsgId(null), 120);
  };

  const roles: RolesType = {
    user: {
      placement: 'end',
      avatar: { icon: <UserOutlined />, style: { background: '#1677ff' } },
      variant: 'filled' as const,
    },
    assistant: {
      placement: 'start',
      avatar: { icon: <RobotOutlined />, style: { background: '#722ed1' } },
    },
  };

  const bubbleItems = messages.map((m) => {
    const actionsVisible = hoveredMsgId === m.id && m.status !== 'streaming';
    const isUser = m.role === 'user';
    return {
      key: m.id,
      role: m.role,
      content: m.content,
      loading: m.status === 'streaming' && m.content.length === 0 && !m.thinkingBlocks?.length,
      messageRender: isUser
        ? () => (
            <div
              onMouseEnter={() => onMsgEnter(m.id)}
              onMouseLeave={onMsgLeave}
              style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
            >
              {m.content}
            </div>
          )
        : () => (
            <div onMouseEnter={() => onMsgEnter(m.id)} onMouseLeave={onMsgLeave}>
              <AssistantContent message={m} />
            </div>
          ),
      footer: (
        <div
          onMouseEnter={() => onMsgEnter(m.id)}
          onMouseLeave={onMsgLeave}
          style={{
            display: 'flex',
            justifyContent: isUser ? 'flex-end' : 'flex-start',
            marginTop: -12,   // 贴近气泡，抵消 Bubble 默认间距
          }}
        >
          <MessageActions
            message={m}
            conversationId={activeConversationId}
            visible={actionsVisible}
          />
        </div>
      ),
    };
  });

  return (
    <Flex vertical style={{ height: '100%', overflow: 'hidden' }}>
      {/* 消息区域 */}
      <div style={{ flex: '1 1 0', minHeight: 0, overflow: 'hidden' }}>
        {messages.length === 0 ? (
          <Flex
            vertical
            align="center"
            justify="center"
            gap={32}
            style={{ height: '100%', padding: '0 24px' }}
          >
            <Flex vertical align="center" gap={16}>
              <Avatar
                size={72}
                icon={<RobotOutlined />}
                style={{
                  background: 'linear-gradient(135deg, #1677ff 0%, #722ed1 100%)',
                  fontSize: 32,
                }}
              />
              <Flex vertical align="center" gap={4}>
                <Typography.Title level={4} style={{ margin: 0 }}>
                  你好，我是 AstraCoreAI
                </Typography.Title>
                <Typography.Text type="secondary" style={{ fontSize: 14 }}>
                  专业 AI 基础设施，有什么可以帮你的？
                </Typography.Text>
              </Flex>
            </Flex>
            <Prompts
              items={SUGGESTED_PROMPTS}
              onItemClick={({ data }) => {
                if (typeof data.label === 'string') handleSendMessage(data.label);
              }}
            />
          </Flex>
        ) : (
          <Bubble.List
            items={bubbleItems}
            roles={roles}
            autoScroll
            style={{ height: '100%', padding: '16px 24px' }}
          />
        )}
      </div>

      {/* 会话错误提示：仅当前会话有效，刷新自动消失 */}
      {sessionError && (
        <div style={{ padding: '0 24px' }}>
          <Alert
            type="error"
            message={sessionError}
            closable
            onClose={() => setSessionError(null)}
            style={{ marginBottom: 8 }}
          />
        </div>
      )}

      {/* 输入区域 */}
      <div
        style={{
          padding: '8px 24px 20px',
          borderTop: '1px solid rgba(5, 5, 5, 0.06)',
          flexShrink: 0,
        }}
      >
        <div style={{ maxWidth: 860, margin: '0 auto' }}>
          {/* 工具栏独立一行，不占 Sender 内部空间 */}
          <Flex align="center" gap={6} style={{ marginBottom: 8, flexWrap: 'wrap' }}>
            <Tooltip title={enableThinking ? '关闭深度思考' : '开启深度思考（Extended Thinking）'}>
              <Button
                size="small"
                type={enableThinking ? 'primary' : 'default'}
                ghost={enableThinking}
                disabled={isStreaming}
                onClick={() => setEnableThinking(!enableThinking)}
                style={{
                  borderRadius: 20,
                  fontSize: 12,
                  height: 26,
                  padding: '0 10px',
                  ...(enableThinking
                    ? { borderColor: '#722ed1', color: '#722ed1', background: '#f9f0ff' }
                    : {}),
                }}
                icon={<span style={{ fontSize: 11 }}>✦</span>}
              >
                深度思考
              </Button>
            </Tooltip>

            <Tooltip title={enableRag ? '关闭知识库检索' : '开启知识库检索（RAG）'}>
              <Button
                size="small"
                type={enableRag ? 'primary' : 'default'}
                ghost={enableRag}
                disabled={isStreaming}
                onClick={() => setEnableRag(!enableRag)}
                style={{
                  borderRadius: 20,
                  fontSize: 12,
                  height: 26,
                  padding: '0 10px',
                  ...(enableRag
                    ? { borderColor: '#1677ff', color: '#1677ff', background: '#e6f4ff' }
                    : {}),
                }}
                icon={<DatabaseOutlined />}
              >
                知识库
              </Button>
            </Tooltip>

            <Tooltip title={enableTools ? '关闭工具调用（Agent 模式）' : '开启工具调用，AI 会多轮思考并使用工具'}>
              <Button
                size="small"
                type={enableTools ? 'primary' : 'default'}
                ghost={enableTools}
                disabled={isStreaming}
                onClick={() => setEnableTools(!enableTools)}
                style={{
                  borderRadius: 20,
                  fontSize: 12,
                  height: 26,
                  padding: '0 10px',
                  ...(enableTools
                    ? { borderColor: '#fa8c16', color: '#fa8c16', background: '#fff7e6' }
                    : {}),
                }}
                icon={<ToolOutlined />}
              >
                工具
              </Button>
            </Tooltip>

            <Tooltip title={enableWeb ? '关闭联网搜索' : '开启联网搜索（需配置 TAVILY_API_KEY）'}>
              <Button
                size="small"
                type={enableWeb ? 'primary' : 'default'}
                ghost={enableWeb}
                disabled={isStreaming}
                onClick={() => setEnableWeb(!enableWeb)}
                style={{
                  borderRadius: 20,
                  fontSize: 12,
                  height: 26,
                  padding: '0 10px',
                  ...(enableWeb
                    ? { borderColor: '#13c2c2', color: '#13c2c2', background: '#e6fffb' }
                    : {}),
                }}
                icon={<GlobalOutlined />}
              >
                联网
              </Button>
            </Tooltip>

            <SkillSelector disabled={isStreaming} />
          </Flex>

          <Sender
            value={inputValue}
            onChange={setInputValue}
            loading={isStreaming}
            onSubmit={(value) => {
              setInputValue('');
              handleSendMessage(value);
            }}
            onCancel={cancelStream}
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
          />
        </div>
      </div>
    </Flex>
  );
}
