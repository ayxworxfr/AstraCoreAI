export type ConversationMeta = {
  id: string;           // UUID，同时作为后端 session_id
  title: string;
  updatedAt: string;    // ISO string
  lastMessagePreview: string;
  messageCount: number;
  pinned: boolean;
};

export type MessageStatus = 'pending' | 'streaming' | 'done' | 'error';

export type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  /** 每个元素对应一轮工具调用的思考内容，普通模式只有一个元素 */
  thinkingBlocks?: string[];
  status: MessageStatus;
  createdAt: string;    // ISO string，避免 Date 序列化问题
};
