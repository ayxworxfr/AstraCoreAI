export type ConversationMeta = {
  id: string;           // UUID，同时作为后端 session_id
  title: string;
  updatedAt: string;    // ISO string
  lastMessagePreview: string;
  messageCount: number;
  pinned: boolean;
  /** 会话独立 skill：undefined = 使用全局默认，'none' = 禁用，uuid = 指定 skill */
  skillId?: string | null;
  /** 会话独立模型 Profile：null/undefined = 使用后端默认，string = 指定 profile id */
  modelId?: string | null;
};

export type MessageStatus = 'pending' | 'streaming' | 'done' | 'error';

export type ToolActivity = {
  name: string;
  done: boolean;
  input?: Record<string, unknown>;
  result?: string;
  isError?: boolean;
  durationMs?: number;
};

export type ThinkingMode = 'normal' | 'deep' | 'tool';

export type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  /** 每个元素对应一轮工具调用的思考内容，普通模式只有一个元素 */
  thinkingBlocks?: string[];
  /** 思考模式：deep=深度思考，tool=工具分析（Agent 轮次） */
  thinkingMode?: ThinkingMode;
  /** 工具调用记录，done=false 表示执行中，done=true 表示已完成 */
  toolActivity?: ToolActivity[];
  status: MessageStatus;
  createdAt: string;    // ISO string，避免 Date 序列化问题
};
