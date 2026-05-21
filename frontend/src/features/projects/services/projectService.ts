import type { ConversationProjectBindingApiItem, ProjectApiItem } from '@/shared/types/api';
import { apiClient } from '@/shared/services/apiClient';

export type ProjectCreateRequest = {
  name: string;
  root_paths?: string[];
  description?: string;
};

export type ConversationProjectBindRequest = {
  project_id: string;
  locked?: boolean;
  source?: 'manual' | 'workspace' | 'path' | 'llm';
};

export async function fetchProjects(): Promise<ProjectApiItem[]> {
  const { data } = await apiClient.get<ProjectApiItem[]>('/api/v1/projects/');
  return data;
}

export async function createProject(body: ProjectCreateRequest): Promise<ProjectApiItem> {
  const { data } = await apiClient.post<ProjectApiItem>('/api/v1/projects/', body);
  return data;
}

export async function fetchConversationProject(
  conversationId: string,
): Promise<ConversationProjectBindingApiItem | null> {
  const { data } = await apiClient.get<ConversationProjectBindingApiItem | null>(
    `/api/v1/projects/conversations/${conversationId}/project`,
  );
  return data;
}

export async function bindConversationProject(
  conversationId: string,
  body: ConversationProjectBindRequest,
): Promise<ConversationProjectBindingApiItem> {
  const { data } = await apiClient.put<ConversationProjectBindingApiItem>(
    `/api/v1/projects/conversations/${conversationId}/project`,
    body,
  );
  return data;
}
