import type { SystemInfo } from '@/features/system/types';
import { apiClient } from '@/shared/services/apiClient';

export async function getSystemInfo(): Promise<SystemInfo> {
  const { data } = await apiClient.get<SystemInfo>('/api/v1/system/');
  return data;
}
