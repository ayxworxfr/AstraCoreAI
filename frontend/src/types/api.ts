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
