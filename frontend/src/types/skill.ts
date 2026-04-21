export type Skill = {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  is_builtin: boolean;
  created_at: string;
  updated_at: string;
};

export type CreateSkillRequest = {
  name: string;
  description: string;
  system_prompt: string;
};

export type UpdateSkillRequest = {
  name?: string;
  description?: string;
  system_prompt?: string;
};

export type UserSettings = {
  default_skill_id: string;
  global_instruction: string;
  temperature: number;
  rag_top_k: number;
  context_max_messages: number;
};
