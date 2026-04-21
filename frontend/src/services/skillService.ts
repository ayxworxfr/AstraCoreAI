import type { CreateSkillRequest, Skill, UpdateSkillRequest, UserSettings } from '../types/skill';
import { apiClient } from './apiClient';

export async function listSkills(): Promise<Skill[]> {
  const { data } = await apiClient.get<Skill[]>('/api/v1/skills/');
  return data;
}

export async function createSkill(req: CreateSkillRequest): Promise<Skill> {
  const { data } = await apiClient.post<Skill>('/api/v1/skills/', req);
  return data;
}

export async function updateSkill(id: string, req: UpdateSkillRequest): Promise<Skill> {
  const { data } = await apiClient.put<Skill>(`/api/v1/skills/${id}`, req);
  return data;
}

export async function deleteSkill(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/skills/${id}`);
}

export async function getSettings(): Promise<UserSettings> {
  const { data } = await apiClient.get<UserSettings>('/api/v1/settings/');
  return data;
}

export async function saveSettings(patch: Partial<UserSettings>): Promise<UserSettings> {
  const { data } = await apiClient.put<UserSettings>('/api/v1/settings/', patch);
  return data;
}
