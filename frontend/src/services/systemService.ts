import type { SystemInfo } from '../types/system';
import { apiClient } from './apiClient';

export async function getSystemInfo(): Promise<SystemInfo> {
  const { data } = await apiClient.get<SystemInfo>('/api/v1/system/');
  return data;
}
