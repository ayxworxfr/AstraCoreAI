import { useState, useRef, useCallback, useEffect, useLayoutEffect } from 'react';
import { Alert, Flex } from 'antd';
import { useChatStore } from '@/features/chat/store/chatStore';
import QuestionCard from './QuestionCard';
import { ChatMessageList } from './ChatMessageList';
import { ChatInputArea } from './ChatInputArea';

export default function ChatMain(): JSX.Element {
  const [hoveredMsgId, setHoveredMsgId] = useState<string | null>(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const bottomAnchorRef = useRef<HTMLDivElement>(null);
  const loadMoreSentinelRef = useRef<HTMLDivElement>(null);

  // 保存 prepend 前的 scrollHeight，以便 prepend 后还原位置
  const prevScrollHeightRef = useRef<number | null>(null);
  // 标记初次加载完成后需要滚到底部（让用户看到最新消息，之后才能上拉加载更早的）
  const shouldScrollToBottomRef = useRef(false);
  // 记录当前会话是否已做过首屏滚底；刷新时 activeConversationId 可能来自持久化，不一定会变化
  const initialBottomScrolledConvRef = useRef<string | null>(null);
  // 通过 ref 让 handleScroll 始终能拿到最新的 handleScrollLoadMore，
  // 避免把 handleScrollLoadMore 加入 handleScroll 的依赖而引发重建循环
  const handleScrollLoadMoreRef = useRef<() => Promise<void>>(() => Promise.resolve());

  const {
    activeConversationId,
    conversationsLoaded,
    messagesByConversation,
    isLoadingMessages,
    sessionError,
    pendingQuestionByConversation,
    initConversations,
    setSessionError,
    sendMessage,
    loadMessages,
    loadMoreMessages,
    submitAnswer,
  } = useChatStore();

  const messages = messagesByConversation[activeConversationId] ?? [];
  const isStreaming = messages.some((m) => m.status === 'streaming');
  const pendingQuestion = pendingQuestionByConversation[activeConversationId] ?? null;

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const scrollBehavior = behavior === 'instant' ? 'auto' : behavior;
    // SimpleBar 和 Markdown 高亮会在提交后继续更新高度，延后一帧再对齐真实底部锚点。
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        bottomAnchorRef.current?.scrollIntoView({ block: 'end', behavior: scrollBehavior });
      });
    });
  }, []);

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

  // 应用启动时从后端加载对话列表
  useEffect(() => {
    void initConversations();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // 加载更早的消息：每次调用时从 store 直接读取最新状态，避免 React 闭包过期问题
  const handleScrollLoadMore = useCallback(async () => {
    const { isLoadingMoreMessages: loading, hasMoreMessages: more, activeConversationId: convId } = useChatStore.getState();
    if (loading || !more[convId]) return;
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

  // 切换/首次进入会话时加载消息，并在加载完成后滚到底部。
  // 初始加载由 initConversations 统一处理（含 resumeActiveRun），
  // 此处只处理 conversationsLoaded 后因 activeConversationId 变化触发的会话切换。
  useEffect(() => {
    if (!activeConversationId || !conversationsLoaded) return;
    if (messagesByConversation[activeConversationId] === undefined) {
      shouldScrollToBottomRef.current = true;
      if (!isLoadingMessages) void loadMessages(activeConversationId);
    } else {
      // 已缓存的会话（本次会话内切换回来），直接滚到底部
      scrollToBottom('instant');
    }
  }, [activeConversationId]); // eslint-disable-line react-hooks/exhaustive-deps

  // prepend 旧消息后补偿 scrollTop；初次加载完成后滚到底部
  useLayoutEffect(() => {
    if (!activeConversationId) return;
    if (prevScrollHeightRef.current !== null) {
      // load-more：维持可视区域不跳动
      const el = scrollContainerRef.current;
      if (el) {
        el.scrollTop += el.scrollHeight - prevScrollHeightRef.current;
      }
      prevScrollHeightRef.current = null;
    } else if (
      messages.length > 0 &&
      (shouldScrollToBottomRef.current || initialBottomScrolledConvRef.current !== activeConversationId)
    ) {
      // 初次加载 / 页面刷新恢复活跃会话：滚到底部让用户看到最新消息
      shouldScrollToBottomRef.current = false;
      initialBottomScrolledConvRef.current = activeConversationId;
      scrollToBottom('instant');
    }
  }, [messages, scrollToBottom]);

  useEffect(() => {
    handleScroll();
  }, [messages, handleScroll]);

  const onMsgEnter = (id: string) => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    setHoveredMsgId(id);
  };
  const onMsgLeave = () => {
    hoverTimerRef.current = setTimeout(() => setHoveredMsgId(null), 120);
  };

  const handleSendMessage = useCallback((value: string) => {
    setSessionError(null);
    void sendMessage(value);
  }, [setSessionError, sendMessage]);

  return (
    <Flex vertical style={{ height: '100%', overflow: 'hidden' }}>
      <ChatMessageList
        scrollContainerRef={scrollContainerRef}
        bottomAnchorRef={bottomAnchorRef}
        loadMoreSentinelRef={loadMoreSentinelRef}
        hoveredMsgId={hoveredMsgId}
        onMsgEnter={onMsgEnter}
        onMsgLeave={onMsgLeave}
        showScrollBtn={showScrollBtn}
        onScrollToBottom={scrollToBottom}
        onScroll={handleScroll}
        onSendMessage={handleSendMessage}
      />

      {/* HITL 问题卡：AI 调用 ask_user 时阻塞等待用户回复 */}
      {pendingQuestion && (
        <div style={{ padding: '0 24px', maxWidth: 860, margin: '0 auto', width: '100%' }}>
          <QuestionCard
            question={pendingQuestion}
            onSubmit={(selected, freeform) => submitAnswer(activeConversationId, selected, freeform)}
          />
        </div>
      )}

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

      <ChatInputArea onSendMessage={handleSendMessage} />
    </Flex>
  );
}
