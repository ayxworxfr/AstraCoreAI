import type { RefObject } from 'react';
import { Avatar, Button, Flex, Typography } from 'antd';
import { Prompts } from '@ant-design/x';
import { DownCircleOutlined, RobotOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useChatStore } from '@/features/chat/store/chatStore';
import AppScrollArea from '@/shared/components/AppScrollArea';
import { MessageRow } from './MessageRow';

const SUGGESTED_PROMPTS = [
  { key: '1', label: '你能做什么？', icon: <ThunderboltOutlined /> },
  { key: '2', label: 'RAG 检索怎么用？', icon: <ThunderboltOutlined /> },
  { key: '3', label: '给我讲个故事吧', icon: <ThunderboltOutlined /> },
  { key: '4', label: '我们来玩个游戏吧', icon: <ThunderboltOutlined /> },
];

type ChatMessageListProps = {
  scrollContainerRef: RefObject<HTMLDivElement>;
  bottomAnchorRef: RefObject<HTMLDivElement>;
  loadMoreSentinelRef: RefObject<HTMLDivElement>;
  hoveredMsgId: string | null;
  onMsgEnter: (id: string) => void;
  onMsgLeave: () => void;
  showScrollBtn: boolean;
  onScrollToBottom: () => void;
  onScroll: () => void;
  onSendMessage: (value: string) => void;
};

export function ChatMessageList({
  scrollContainerRef,
  bottomAnchorRef,
  loadMoreSentinelRef,
  hoveredMsgId,
  onMsgEnter,
  onMsgLeave,
  showScrollBtn,
  onScrollToBottom,
  onScroll,
  onSendMessage,
}: ChatMessageListProps) {
  const {
    activeConversationId,
    messagesByConversation,
    isLoadingMessages,
    isLoadingMoreMessages,
    hasMoreMessages,
  } = useChatStore();

  const messages = messagesByConversation[activeConversationId] ?? [];
  const hasMore = hasMoreMessages[activeConversationId] ?? false;

  return (
    <div style={{ flex: '1 1 0', minHeight: 0, overflow: 'hidden', position: 'relative' }}>
      {messages.length === 0 && !isLoadingMessages ? (
        <Flex
          vertical
          align="center"
          justify="center"
          gap={32}
          style={{ height: '100%', padding: '0 var(--content-padding-x)' }}
        >
          <Flex vertical align="center" gap={16}>
            <Avatar
              size={72}
              icon={<RobotOutlined />}
              style={{ background: 'linear-gradient(135deg, #1677ff 0%, #722ed1 100%)', fontSize: 32 }}
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
              if (typeof data.label === 'string') onSendMessage(data.label);
            }}
          />
        </Flex>
      ) : (
        <AppScrollArea
          style={{ height: '100%' }}
          scrollableNodeProps={{ ref: scrollContainerRef, onScroll: onScroll }}
        >
          {/* 顶部哨兵：IntersectionObserver 的观察目标，进入可视区域时触发加载更多 */}
          <div ref={loadMoreSentinelRef} style={{ height: 1, overflow: 'hidden' }} />
          {hasMore && (
            <div style={{ textAlign: 'center', padding: '8px 0', opacity: 0.5, fontSize: 12 }}>
              {isLoadingMoreMessages ? '加载中...' : '上滑加载更早的消息'}
            </div>
          )}
          <div
            style={{
              maxWidth: 'var(--content-max-width)',
              margin: '0 auto',
              width: '100%',
              padding: '24px var(--content-padding-x) 16px',
            }}
          >
            {messages.map((m) => (
              <MessageRow
                key={m.id}
                message={m}
                conversationId={activeConversationId}
                hoveredMsgId={hoveredMsgId}
                onMsgEnter={onMsgEnter}
                onMsgLeave={onMsgLeave}
              />
            ))}
          </div>
          <div ref={bottomAnchorRef} style={{ height: 1 }} />
        </AppScrollArea>
      )}

      {/* 回到最新消息按钮 */}
      {showScrollBtn && (
        <div
          style={{ position: 'absolute', bottom: 16, left: '50%', transform: 'translateX(-50%)', zIndex: 10 }}
        >
          <Button
            type="primary"
            size="small"
            icon={<DownCircleOutlined />}
            onClick={onScrollToBottom}
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
  );
}
