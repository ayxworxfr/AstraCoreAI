import { useState, useRef, useCallback, useEffect, useLayoutEffect, type ComponentProps } from 'react';
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
  DownCircleOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons';
import { Flex, Typography, Alert, Avatar, Button, Collapse, Tooltip, Popover, Tag, theme } from 'antd';
import { useChatStore } from '../../stores/chatStore';
import MarkdownContent from './MarkdownContent';
import ModelSelector from './ModelSelector';
import SkillSelector from '../skills/SkillSelector';
import type { ChatMessage, SubAgentActivity, ThinkingMode, ToolActivity } from '../../types/chat';
import AppScrollArea from '../common/AppScrollArea';

const SUGGESTED_PROMPTS = [
  { key: '1', label: '你能做什么？', icon: <ThunderboltOutlined /> },
  { key: '2', label: 'RAG 检索怎么用？', icon: <ThunderboltOutlined /> },
  { key: '3', label: '给我讲个故事吧', icon: <ThunderboltOutlined /> },
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

const _BJT = 'Asia/Shanghai';
function formatMsgTime(isoStr: string): string {
  const d = new Date(isoStr);
  const fmt = (opts: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat('zh-CN', { timeZone: _BJT, ...opts });
  const { year, month, day, hour, minute } = Object.fromEntries(
    fmt({ year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
      .formatToParts(d)
      .map(({ type, value }) => [type, value]),
  );
  const hm = `${hour}:${minute}`;
  const todayParts = fmt({ year: 'numeric', month: 'numeric', day: 'numeric' }).formatToParts(new Date());
  const today = Object.fromEntries(todayParts.map(({ type, value }) => [type, value]));
  if (year === today.year && month === today.month && day === today.day) return `今天 ${hm}`;
  if (year === today.year) return `${month}月${day}日 ${hm}`;
  return `${year}年${month}月${day}日 ${hm}`;
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
      vertical
      style={{
        opacity: visible ? 1 : 0,
        pointerEvents: visible ? 'auto' : 'none',
        transition: 'opacity 0.15s ease',
        padding: '2px 0',
      }}
    >
      <Flex gap={2} align="center">
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
      <span style={{
        fontSize: 11,
        color: token.colorTextQuaternary,
        padding: '0 4px',
        userSelect: 'none',
        whiteSpace: 'nowrap',
      }}>
        {formatMsgTime(message.createdAt)}
      </span>
    </Flex>
  );
}

const TOOL_BADGE_VISIBLE = 3;

function formatDuration(ms: number | undefined): string | null {
  if (ms === undefined) return null;
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function SenderWithScrollbar(props: ComponentProps<typeof Sender>) {
  return <Sender {...props} />;
}

function ScrollPane({
  children,
  maxHeight,
  style,
}: {
  children: React.ReactNode;
  maxHeight: number;
  style?: React.CSSProperties;
}) {
  return (
    <AppScrollArea style={{ maxHeight, ...style }}>
      {children}
    </AppScrollArea>
  );
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
          <ScrollPane maxHeight={160} style={{ borderRadius: 4 }}>
            <pre style={preStyle}>{JSON.stringify(tool.input, null, 2)}</pre>
          </ScrollPane>
        </div>
      )}
      {tool.result !== undefined && (
        <div>
          <div style={{ color: tool.isError ? token.colorError : token.colorTextSecondary, marginBottom: 4, fontWeight: 500 }}>
            {tool.isError ? '错误信息' : '返回结果'}
          </div>
          <ScrollPane maxHeight={160} style={{ borderRadius: 4 }}>
            <pre style={{ ...preStyle, color: tool.isError ? token.colorError : undefined }}>
              {tool.result.length > 600 ? tool.result.slice(0, 600) + '\n…（已截断）' : tool.result}
            </pre>
          </ScrollPane>
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
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '2px 10px',
        borderRadius: 12, fontSize: 12, cursor: 'default', background: bg,
        border: `1px solid ${border}`, color }}>
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

function ToolActivityRow({ tools }: { tools: ToolActivity[] }) {
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
          style={{ display: 'inline-flex', alignItems: 'center', padding: '2px 10px',
            borderRadius: 12, fontSize: 12, cursor: 'pointer',
            background: token.colorFillTertiary,
            border: `1px solid ${token.colorBorderSecondary}`,
            color: token.colorTextSecondary }}
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
  const bg = running ? token.colorWarningBg : (isError ? token.colorErrorBg : token.colorSuccessBg);
  const border = running ? token.colorWarningBorder : (isError ? token.colorErrorBorder : token.colorSuccessBorder);
  const accentColor = running ? token.colorWarning : (isError ? token.colorError : token.colorSuccess);
  const textColor = running ? token.colorWarningText : (isError ? token.colorErrorText : token.colorSuccessText);
  const taskPreview = agent.task.length > 60 ? `${agent.task.slice(0, 60)}...` : agent.task;

  return (
    <Collapse
      size="small"
      defaultActiveKey={[]}
      style={{
        marginBottom: 6,
        background: bg,
        border: `1px solid ${border}`,
        borderRadius: 8,
        overflow: 'hidden',
      }}
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
            body: {
              background: token.colorBgContainer,
              borderTop: `1px solid ${border}`,
              padding: '10px 14px',
            },
          },
          children: (
            <div>
              {agent.toolActivity.length > 0 && (
                <ToolActivityRow tools={agent.toolActivity} />
              )}
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

function SubAgentPanel({ agents }: { agents: SubAgentActivity[] }) {
  if (agents.length === 0) return null;
  return (
    <div style={{ marginBottom: 10 }}>
      {agents.map((agent) => (
        <SubAgentCard key={agent.agentId} agent={agent} />
      ))}
    </div>
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
      {(message.anchorSkill || (message.autoSkills && message.autoSkills.length > 0)) && (
        <div style={{ marginBottom: 6 }}>
          {message.anchorSkill && (
            <Tag color="geekblue" style={{ fontSize: 11 }}>📌 {message.anchorSkill}</Tag>
          )}
          {message.autoSkills?.map((s) => (
            <Tag key={s} color="blue" style={{ fontSize: 11 }}>⚡ {s}</Tag>
          ))}
        </div>
      )}
      {message.toolActivity && message.toolActivity.length > 0 && (
        <ToolActivityRow tools={message.toolActivity} />
      )}
      {message.subAgents && message.subAgents.length > 0 && (
        <SubAgentPanel agents={message.subAgents} />
      )}
      <MarkdownContent content={message.content} />
    </div>
  );
}

type RolesType = Record<string, BubbleProps & { placement?: 'start' | 'end' }>;

export default function ChatMain(): JSX.Element {
  const [inputValue, setInputValue] = useState('');
  const [hoveredMsgId, setHoveredMsgId] = useState<string | null>(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const bottomAnchorRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    bottomAnchorRef.current?.scrollIntoView({ behavior, block: 'end' });
  }, []);

  // 通过 ref 让 handleScroll 始终能拿到最新的 handleScrollLoadMore，
  // 避免把 handleScrollLoadMore 加入 handleScroll 的依赖而引发重建循环
  const handleScrollLoadMoreRef = useRef<() => Promise<void>>(() => Promise.resolve());

  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const maxScroll = Math.max(0, el.scrollHeight - el.clientHeight);
    const distanceFromBottom = maxScroll - el.scrollTop;
    setShowScrollBtn(distanceFromBottom > 120);
    // scroll 事件：滚到顶部附近时触发加载更多
    if (el.scrollTop < 200) {
      void handleScrollLoadMoreRef.current();
    }
  }, []);

  // streaming 时若已在底部则自动跟随；不在底部则仅显示按钮
  useEffect(() => {
    if (!isStreaming) return;
    const el = scrollContainerRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distanceFromBottom < 120) scrollToBottom('instant');
  });

  const {
    activeConversationId,
    messagesByConversation,
    hasMoreMessages,
    isLoadingMessages,
    isLoadingMoreMessages,
    enableThinking,
    enableRag,
    enableTools,
    enableWeb,
    sessionError,
    initConversations,
    setEnableThinking,
    setEnableRag,
    setEnableTools,
    setEnableWeb,
    setSessionError,
    sendMessage,
    cancelStream,
    loadMessages,
    loadMoreMessages,
  } = useChatStore();

  // 应用启动时从后端加载对话列表
  useEffect(() => {
    void initConversations();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const messages = messagesByConversation[activeConversationId] ?? [];
  const isStreaming = messages.some((m) => m.status === 'streaming');
  const hasMore = hasMoreMessages[activeConversationId] ?? false;

  // 保存 prepend 前的 scrollHeight，以便 prepend 后还原位置
  const prevScrollHeightRef = useRef<number | null>(null);
  // 标记初次加载完成后需要滚到底部（让用户看到最新消息，之后才能上拉加载更早的）
  const shouldScrollToBottomRef = useRef(false);
  // 顶部哨兵：IntersectionObserver 的观察目标
  const loadMoreSentinelRef = useRef<HTMLDivElement>(null);

  // 加载更早的消息：每次调用时从 store 直接读取最新状态，避免 React 闭包过期问题
  const handleScrollLoadMore = useCallback(async () => {
    // 直接从 store 读取最新状态，不依赖 React 渲染周期的快照值
    const { isLoadingMoreMessages: loading, hasMoreMessages, activeConversationId: convId } = useChatStore.getState();
    if (loading || !hasMoreMessages[convId]) return;
    const el = scrollContainerRef.current;
    if (el) prevScrollHeightRef.current = el.scrollHeight;
    const loaded = await loadMoreMessages(convId);
    if (!loaded) {
      // 无新消息时清除占位，避免下次消息变化时错误补偿 scrollTop
      prevScrollHeightRef.current = null;
    }
  }, [loadMoreMessages]);

  useEffect(() => { handleScrollLoadMoreRef.current = handleScrollLoadMore; }, [handleScrollLoadMore]);

  // IntersectionObserver：顶部哨兵进入可视区域时触发加载
  useEffect(() => {
    const sentinel = loadMoreSentinelRef.current;
    const container = scrollContainerRef.current;
    if (!sentinel || !container) return;
    const io = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) void handleScrollLoadMoreRef.current(); },
      { root: container, threshold: 0 },
    );
    io.observe(sentinel);
    return () => io.disconnect();
  }, []);

  // 切换/首次进入会话时加载消息，并在加载完成后滚动到底部
  useEffect(() => {
    if (messagesByConversation[activeConversationId] === undefined) {
      shouldScrollToBottomRef.current = true;
      void loadMessages(activeConversationId);
    } else {
      // 已缓存的会话（本次会话内切换回来），直接滚到底部
      scrollToBottom('instant');
    }
  }, [activeConversationId]); // eslint-disable-line react-hooks/exhaustive-deps

  // prepend 旧消息后补偿 scrollTop；初次加载完成后滚到底部
  useLayoutEffect(() => {
    if (prevScrollHeightRef.current !== null) {
      // load-more：维持可视区域不跳动
      const el = scrollContainerRef.current;
      if (el) {
        el.scrollTop += el.scrollHeight - prevScrollHeightRef.current;
      }
      prevScrollHeightRef.current = null;
    } else if (shouldScrollToBottomRef.current && messages.length > 0) {
      // 初次加载：滚到底部让用户看到最新消息
      shouldScrollToBottomRef.current = false;
      scrollToBottom('instant');
    }
  }, [messages, scrollToBottom]);

  useEffect(() => {
    handleScroll();
  }, [messages, handleScroll]);

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
            paddingRight: isUser ? -14 : 0,
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
      <div style={{ flex: '1 1 0', minHeight: 0, overflow: 'hidden', position: 'relative' }}>
        <AppScrollArea
          style={{ height: '100%' }}
          scrollableNodeProps={{ ref: scrollContainerRef, onScroll: handleScroll }}
        >
          <div style={{ minHeight: '100%' }}>
            {/* 顶部哨兵：IntersectionObserver 的观察目标，进入可视区域时触发加载更多 */}
            <div ref={loadMoreSentinelRef} style={{ height: 1, overflow: 'hidden' }} />
            {hasMore && (
              <div style={{ textAlign: 'center', padding: '8px 0', opacity: 0.5, fontSize: 12 }}>
                {isLoadingMoreMessages ? '加载中...' : '上滑加载更早的消息'}
              </div>
            )}
            {messages.length === 0 && !isLoadingMessages ? (
              <Flex
                vertical
                align="center"
                justify="center"
                gap={32}
                style={{ minHeight: '100%', padding: '0 24px' }}
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
                style={{ padding: '16px 24px' }}
              />
            )}
            <div ref={bottomAnchorRef} style={{ height: 1 }} />
          </div>
        </AppScrollArea>

        {/* 回到最新消息按钮 */}
        {showScrollBtn && (
          <div
            style={{
              position: 'absolute',
              bottom: 16,
              left: '50%',
              transform: 'translateX(-50%)',
              zIndex: 10,
            }}
          >
            <Button
              type="primary"
              size="small"
              icon={<DownCircleOutlined />}
              onClick={() => scrollToBottom()}
              style={{
                borderRadius: 20,
                padding: '0 14px',
                height: 30,
                fontSize: 12,
                boxShadow: '0 2px 8px rgba(0,0,0,0.25)',
                opacity: 0.92,
              }}
            >
              回到最新
            </Button>
          </div>
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
        <div style={{ maxWidth: 1040, margin: '0 auto' }}>
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

            <Tooltip title={enableWeb ? '关闭联网搜索' : '开启联网搜索'}>
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
            <ModelSelector disabled={isStreaming} />
          </Flex>

          <SenderWithScrollbar
            value={inputValue}
            onChange={setInputValue}
            loading={isStreaming}
            onSubmit={(value) => {
              setInputValue('');
              handleSendMessage(value);
            }}
            onCancel={() => cancelStream(activeConversationId)}
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
          />
        </div>
      </div>
    </Flex>
  );
}
