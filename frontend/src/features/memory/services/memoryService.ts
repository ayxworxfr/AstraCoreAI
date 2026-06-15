import type { MemoryApiItem, MemoryListResponse } from '@/shared/types/api';
import { apiClient } from '@/shared/services/apiClient';

export type MemoryScope = MemoryApiItem['scope'];
export type MemoryType = MemoryApiItem['type'];

export type MemoryCreateRequest = {
  scope: MemoryScope;
  type: MemoryType;
  content: string;
  subject?: string;
  summary?: string;
  session_id?: string | null;
  conversation_id?: string | null;
  project_id?: string | null;
  source_run_id?: string | null;
  importance?: number;
  confidence?: number;
  locked?: boolean;
  metadata?: Record<string, unknown>;
};

export type MemoryUpdateRequest = Partial<MemoryCreateRequest> & {
  status?: MemoryApiItem['status'];
};

export type MemoryQuery = {
  scope?: MemoryScope;
  type?: MemoryType;
  session_id?: string;
  project_id?: string;
  q?: string;
  limit?: number;
};

export async function fetchMemory(query: MemoryQuery = {}): Promise<MemoryListResponse> {
  const { data } = await apiClient.get<MemoryListResponse>('/api/v1/memory/', { params: query });
  return data;
}

export async function createMemory(body: MemoryCreateRequest): Promise<MemoryApiItem> {
  const { data } = await apiClient.post<MemoryApiItem>('/api/v1/memory/', body);
  return data;
}

export async function updateMemory(id: string, body: MemoryUpdateRequest): Promise<MemoryApiItem> {
  const { data } = await apiClient.patch<MemoryApiItem>(`/api/v1/memory/${id}`, body);
  return data;
}

export async function deleteMemory(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/memory/${id}`);
}
