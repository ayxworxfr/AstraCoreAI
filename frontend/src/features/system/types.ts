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
        anthropic_blocks: boolean;
        vision: boolean;
      };
    }>;
  };
  tavily_configured: boolean;
  rag_enabled: boolean;
  mcp_servers: Array<{
    name: string;
    type: string;
  }>;
};
