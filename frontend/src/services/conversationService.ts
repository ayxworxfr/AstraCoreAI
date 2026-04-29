import type { ConversationApiItem, CreateConversationRequest, PatchConversationRequest } from '../types/api';
import type { ConversationMeta } from '../types/chat';
import { apiClient } from './apiClient';

function toMeta(item: ConversationApiItem): ConversationMeta {
  return {
    id: item.id,
    title: item.title,
    pinned: item.pinned,
    skillId: item.skill_id,
    modelId: item.model_id,
    lastMessagePreview: item.last_message_preview,
    messageCount: item.message_count,
    updatedAt: item.updated_at,
  };
}

export async function fetchConversations(): Promise<ConversationMeta[]> {
  const { data } = await apiClient.get<ConversationApiItem[]>('/api/v1/conversations/');
  return data.map(toMeta);
}

export async function createConversationApi(body: CreateConversationRequest): Promise<ConversationMeta> {
  const { data } = await apiClient.post<ConversationApiItem>('/api/v1/conversations/', body);
  return toMeta(data);
}

export async function patchConversationApi(
  id: string,
  body: PatchConversationRequest,
): Promise<void> {
  await apiClient.patch(`/api/v1/conversations/${id}`, body);
}

export async function deleteConversationApi(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/conversations/${id}`);
}
