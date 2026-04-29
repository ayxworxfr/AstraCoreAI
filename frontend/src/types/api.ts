export type ConversationApiItem = {
  id: string;
  title: string;
  pinned: boolean;
  skill_id: string | null;
  model_id: string | null;
  last_message_preview: string;
  message_count: number;
  created_at: string;
  updated_at: string;
};

export type CreateConversationRequest = {
  id: string;
  title?: string;
  skill_id?: string | null;
  model_id?: string | null;
};

export type PatchConversationRequest = {
  title?: string;
  pinned?: boolean;
  skill_id?: string | null;
  model_id?: string | null;
  last_message_preview?: string;
  message_count?: number;
};

export type ChatRequest = {
  message: string;
  session_id?: string;
  model_profile?: string;
  temperature?: number;
  enable_thinking?: boolean;
  thinking_budget?: number;
  enable_rag?: boolean;
  use_tools?: boolean;
  enable_web?: boolean;
  skill_id?: string;
  disable_skill?: boolean;
};

export type ChatResponse = {
  session_id: string;
  message: string;
  model_profile: string;
  model?: string;
  metadata?: Record<string, unknown>;
};

export type ChatRunResponse = {
  run_id: string;
  session_id: string;
  status: string;
};

export type ChatRunState = {
  run_id: string;
  session_id: string;
  status: 'running' | 'done' | 'error' | 'cancelled';
  user_message: string;
  assistant_content: string;
  thinking_blocks: string[];
  tool_activity: Array<{
    name: string;
    done: boolean;
    input?: Record<string, unknown>;
    result?: string;
    isError?: boolean;
    durationMs?: number;
  }>;
  error: string;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
};

export type RagRetrieveRequest = {
  query: string;
  top_k?: number;
};

export type RagIndexRequest = {
  document_id: string;
  text: string;
  metadata?: Record<string, string>;
};

export type RagIndexResponse = {
  document_id: string;
  success: boolean;
  message: string;
};

export type RagCitation = {
  source_id: string;
  source_type: string;
  title?: string | null;
};

export type RagResult = {
  content: string;
  score: number;
  citation?: RagCitation | null;
};

export type RagRetrieveResponse = {
  chunks: RagResult[];
  count: number;
};

export type HealthResponse = {
  status: string;
};

export type ReadyResponse = {
  status: string;
};

export type ApiErrorResponse = {
  detail: string;
};
