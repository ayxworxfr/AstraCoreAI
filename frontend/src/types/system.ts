export type SystemInfo = {
  llm: {
    provider: string;
    model: string;
    base_url: string | null;
    api_key_configured: boolean;
  };
  tavily_configured: boolean;
  mcp_servers: Array<{
    name: string;
    type: string;
  }>;
};
