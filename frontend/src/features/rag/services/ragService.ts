import type { RagIndexRequest, RagIndexResponse, RagRetrieveRequest, RagRetrieveResponse } from '@/shared/types/api';
import { apiClient } from '@/shared/services/apiClient';

export async function ragRetrieve(payload: RagRetrieveRequest): Promise<RagRetrieveResponse> {
  const { data } = await apiClient.post<RagRetrieveResponse>('/api/v1/rag/retrieve', payload);
  return data;
}

export async function ragIndex(payload: RagIndexRequest): Promise<RagIndexResponse> {
  const { data } = await apiClient.post<RagIndexResponse>('/api/v1/rag/index', payload);
  return data;
}
