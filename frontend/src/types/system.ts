export type SystemInfo = {
  llm: {
    default_profile: string;
    profiles: Array<{
      id: string;
      label?: string | null;
      provider: string;
      model: string;
      base_url: string | null;
      api_key_configured: boolean;
      max_tokens: number;
      capabilities: {
        tools: boolean;
        thinking: boolean;
        temperature: boolean;
        anthropic_blocks: boolean;
      };
    }>;
  };
  tavily_configured: boolean;
  mcp_servers: Array<{
    name: string;
    type: string;
  }>;
};
