import type { HealthResponse, ReadyResponse } from '../types/api';
import { apiClient } from './apiClient';

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await apiClient.get<HealthResponse>('/health/');
  return data;
}

export async function getReady(): Promise<ReadyResponse> {
  const { data } = await apiClient.get<ReadyResponse>('/health/ready');
  return data;
}
