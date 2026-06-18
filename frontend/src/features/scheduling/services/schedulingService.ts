import type {
  CreateTaskRequest,
  ScheduledTask,
  TaskListOut,
  UpdateTaskRequest,
} from '@/features/scheduling/types';
import { apiClient } from '@/shared/services/apiClient';

const BASE = '/api/v1/scheduled-tasks';

export async function listTasks(page = 1, pageSize = 20, status?: string, q?: string): Promise<TaskListOut> {
  const params: Record<string, unknown> = { page, page_size: pageSize };
  if (status) params.status = status;
  if (q) params.q = q;
  const { data } = await apiClient.get<TaskListOut>(BASE + '/', { params });
  return data;
}

export async function deleteTasksBatch(ids: string[]): Promise<number> {
  const { data } = await apiClient.post<{ deleted: number }>(`${BASE}/batch-delete`, { ids });
  return data.deleted;
}

export async function getTask(id: string): Promise<ScheduledTask> {
  const { data } = await apiClient.get<ScheduledTask>(`${BASE}/${id}`);
  return data;
}

export async function createTask(req: CreateTaskRequest): Promise<ScheduledTask> {
  const { data } = await apiClient.post<ScheduledTask>(BASE + '/', req);
  return data;
}

export async function updateTask(id: string, req: UpdateTaskRequest): Promise<ScheduledTask> {
  const { data } = await apiClient.put<ScheduledTask>(`${BASE}/${id}`, req);
  return data;
}

export async function deleteTask(id: string): Promise<void> {
  await apiClient.delete(`${BASE}/${id}`);
}

export async function pauseTask(id: string): Promise<ScheduledTask> {
  const { data } = await apiClient.post<ScheduledTask>(`${BASE}/${id}/pause`);
  return data;
}

export async function resumeTask(id: string): Promise<ScheduledTask> {
  const { data } = await apiClient.post<ScheduledTask>(`${BASE}/${id}/resume`);
  return data;
}

export async function runTaskNow(id: string): Promise<ScheduledTask> {
  const { data } = await apiClient.post<ScheduledTask>(`${BASE}/${id}/run-now`);
  return data;
}
