export type TriggerType = 'cron' | 'interval' | 'date';
export type TaskStatus = 'active' | 'paused' | 'finished';

export type TriggerConfig = {
  expr?: string;    // cron expression
  seconds?: number; // interval seconds
  run_at?: string;  // ISO8601 for one-shot date
};

export type ScheduledTask = {
  id: string;
  user_id: string;
  name: string;
  prompt: string;
  trigger_type: TriggerType;
  trigger_config: TriggerConfig;
  timezone: string;
  status: TaskStatus;
  model_profile: string | null;
  use_tools: boolean;
  conversation_id: string | null;
  last_run_id: string | null;
  last_run_at: string | null;
  last_run_status: string | null;
  next_run_at: string | null;
  run_count: number;
  error_count: number;
  last_error: string;
  created_at: string;
  updated_at: string;
};

export type CreateTaskRequest = {
  prompt: string;
  trigger_type: TriggerType;
  trigger_config: TriggerConfig;
  name?: string;
  timezone?: string;
  model_profile?: string | null;
  use_tools?: boolean;
  conversation_id?: string | null;
};

export type UpdateTaskRequest = {
  name?: string;
  prompt?: string;
  trigger_type?: TriggerType;
  trigger_config?: TriggerConfig;
  timezone?: string;
  model_profile?: string | null;
  use_tools?: boolean;
};

export type TaskListOut = {
  items: ScheduledTask[];
  total: number;
  page: number;
  page_size: number;
};
