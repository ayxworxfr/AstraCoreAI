import { apiClient } from '@/shared/services/apiClient';

export type UserItem = {
  id: string;
  username: string;
  role: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type CreateUserRequest = {
  username: string;
  password: string;
  role: string;
};

export type PatchUserRequest = {
  role?: string;
  is_active?: boolean;
  password?: string;
};

export async function listUsers(): Promise<UserItem[]> {
  const res = await apiClient.get<UserItem[]>('/api/v1/users/');
  return res.data;
}

export async function createUser(req: CreateUserRequest): Promise<UserItem> {
  const res = await apiClient.post<UserItem>('/api/v1/users/', req);
  return res.data;
}

export async function patchUser(userId: string, req: PatchUserRequest): Promise<UserItem> {
  const res = await apiClient.patch<UserItem>(`/api/v1/users/${userId}`, req);
  return res.data;
}

export async function deleteUser(userId: string): Promise<void> {
  await apiClient.delete(`/api/v1/users/${userId}`);
}
