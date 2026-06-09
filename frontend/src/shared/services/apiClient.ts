import axios, { type AxiosError } from 'axios';
import type { ApiErrorResponse } from '@/shared/types/api';

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
  timeout: 60000,
  headers: { 'Content-Type': 'application/json' },
});

apiClient.interceptors.request.use((config) => {
  try {
    const raw = localStorage.getItem('auth-storage');
    const token: string | null = raw ? (JSON.parse(raw) as { state?: { token?: string } }).state?.token ?? null : null;
    if (token) {
      config.headers = config.headers ?? {};
      config.headers['Authorization'] = `Bearer ${token}`;
    }
  } catch {
    // ignore parse errors
  }
  return config;
});

apiClient.interceptors.response.use(undefined, (error: unknown) => {
  if (axios.isAxiosError(error) && error.response?.status === 401) {
    try {
      localStorage.removeItem('auth-storage');
    } catch {
      // ignore
    }
    if (window.location.pathname !== '/login') {
      window.location.href = '/login';
    }
  }
  return Promise.reject(error);
});

export function getAuthHeaders(): Record<string, string> {
  try {
    const raw = localStorage.getItem('auth-storage');
    const token: string | null = raw ? (JSON.parse(raw) as { state?: { token?: string } }).state?.token ?? null : null;
    if (token) return { Authorization: `Bearer ${token}` };
  } catch {
    // ignore
  }
  return {};
}

export function normalizeError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const e = error as AxiosError<ApiErrorResponse>;
    return e.response?.data?.detail ?? e.message;
  }
  if (error instanceof Error) return error.message;
  return '未知错误';
}
