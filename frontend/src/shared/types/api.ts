export type ConversationApiItem = {
  id: string;
  title: string;
  pinned: boolean;
  model_id: string | null;
  last_message_preview: string;
  message_count: number;
  created_at: string;
  updated_at: string;
};

export type MemoryApiItem = {
  id: string;
  scope: 'session' | 'project' | 'user' | 'global';
  type: 'fact' | 'preference' | 'decision' | 'constraint' | 'state' | 'plan' | 'summary' | 'lesson' | 'procedure';
  subject: string;
  content: string;
  summary: string;
  session_id: string | null;
  conversation_id: string | null;
  project_id: string | null;
  user_id: string;
  source_run_id: string | null;
  importance: number;
  confidence: number;
  status: 'active' | 'stale' | 'archived' | 'rejected';
  locked: boolean;
  use_count: number;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
};

export type MemoryListResponse = {
  items: MemoryApiItem[];
  total: number;
};

export type ProjectApiItem = {
  id: string;
  name: string;
  root_paths: string[];
  description: string;
  created_at: string;
  updated_at: string;
};

export type ConversationProjectBindingApiItem = {
  conversation_id: string;
  project_id: string;
  locked: boolean;
  source: string;
  created_at: string;
  updated_at: string;
};

export type CreateConversationRequest = {
  id: string;
  title?: string;
  model_id?: string | null;
};

export type PatchConversationRequest = {
  title?: string;
  pinned?: boolean;
  model_id?: string | null;
  last_message_preview?: string;
  message_count?: number;
};

export type ChatRequest = {
  message: string;
  session_id?: string;
  model_profile?: string;
  temperature?: number;
  thinking_mode?: string;
  thinking_budget?: number;
  reasoning_effort?: string;
  verbosity?: string;
  enable_rag?: boolean;
  use_tools?: boolean;
  enable_web?: boolean;
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

export type PendingQuestionOption = {
  label: string;
  description: string;
};

export type PendingQuestion = {
  question_id: string;
  question: string;
  header: string;
  options: PendingQuestionOption[];
  multi_select: boolean;
  allow_freeform: boolean;
  created_at: string;
};

export type ChatRunState = {
  run_id: string;
  session_id: string;
  status: 'running' | 'awaiting_input' | 'done' | 'error' | 'cancelled';
  user_message: string;
  assistant_content: string;
  thinking_blocks: string[];
  tool_activity: Array<{
    name: string;
    tool_call_id?: string;
    done: boolean;
    input?: Record<string, unknown>;
    result?: string;
    isError?: boolean;
    durationMs?: number;
  }>;
  error: string;
  pending_question: PendingQuestion | null;
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
  detail: unknown;
};
