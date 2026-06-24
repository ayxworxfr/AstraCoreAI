import type { AttachmentPreview } from '@/features/attachments/types';

export type ConversationMeta = {
  id: string;           // UUID，同时作为后端 session_id
  title: string;
  updatedAt: string;    // ISO string
  lastMessagePreview: string;
  messageCount: number;
  pinned: boolean;
  /** 会话独立模型 Profile：null/undefined = 使用后端默认，string = 指定 profile id */
  modelId?: string | null;
};

export type MessageStatus = 'pending' | 'streaming' | 'done' | 'error';

export type ToolActivity = {
  name: string;
  toolCallId?: string;
  done: boolean;
  input?: Record<string, unknown>;
  result?: string;
  isError?: boolean;
  durationMs?: number;
};

export type SubAgentStatus = 'running' | 'done' | 'error';

export type SubAgentActivity = {
  agentId: string;
  task: string;
  model?: string;
  status: SubAgentStatus;
  text: string;
  thinking: string;
  toolActivity: ToolActivity[];
  durationMs?: number;
  error?: string | null;
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
  /** 并行子 Agent 活动列表（spawn_agents 触发，仅前端追踪） */
  subAgents?: SubAgentActivity[];
  /** LLM 调用消耗的输入 token 数（仅 assistant 消息有值） */
  inputTokens?: number;
  /** LLM 调用消耗的输出 token 数（仅 assistant 消息有值） */
  outputTokens?: number;
  /** 实际使用的模型 ID（仅 assistant 消息有值，持久化到 DB） */
  model?: string;
  /** 用户本轮随消息发送的附件，用于历史回看和图片预览 */
  attachments?: AttachmentPreview[];
};
