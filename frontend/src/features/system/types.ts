export type ThinkingControl = {
  kind: 'thinking';
  modes: string[];
  default: string;
};

export type ReasoningEffortControl = {
  kind: 'reasoning_effort';
  levels: string[];
  default: string;
};

export type TemperatureControl = {
  kind: 'temperature';
  min: number;
  max: number;
  step: number;
  profile_default: number;
};

export type TopPControl = {
  kind: 'top_p';
  min: number;
  max: number;
  step: number;
  profile_default: number | null;
};

export type TopKControl = {
  kind: 'top_k';
  min: number;
  max: number;
  step: number;
};

export type ModelControl =
  | ThinkingControl
  | ReasoningEffortControl
  | TemperatureControl
  | TopPControl
  | TopKControl;

export type SystemInfo = {
  llm: {
    default_profile: string;
    profiles: Array<{
      id: string;
      label?: string | null;
      protocol: string;
      model: string;
      base_url: string | null;
      api_key_configured: boolean;
      max_tokens: number;
      capabilities: {
        tools: boolean;
        thinking: boolean;
        temperature: boolean;
        top_k: boolean;
        anthropic_blocks: boolean;
        vision: boolean;
        reasoning_effort_protocol: 'responses' | 'extra_body' | null;
      };
      controls: ModelControl[];
    }>;
  };
  tavily_configured: boolean;
  rag_enabled: boolean;
  mcp_servers: Array<{
    name: string;
    type: string;
  }>;
};
