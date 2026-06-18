export type Skill = {
  id: string;
  name: string;
  display_name: string;
  description: string;
  instructions: string;
  category: string | null;
  is_builtin: boolean;
  order: number;
  has_references: boolean;
  has_scripts: boolean;
  created_at: string;
  updated_at: string;
};

export type CreateSkillRequest = {
  name: string;
  display_name?: string;
  description?: string;
  instructions: string;
  category?: string | null;
};

export type UpdateSkillRequest = {
  name?: string;
  display_name?: string;
  description?: string;
  instructions?: string;
  category?: string | null;
};

export type UserSettings = {
  global_instruction: string;
  temperature: number;
  top_p: number | null;
  stop_sequences: string[];
  rag_top_k: number;
  context_max_messages: number;
  ai_name: string;
  owner_name: string;
  timezone: string;
  thinking_collapse_mode: 'auto' | 'always_collapsed';
};
