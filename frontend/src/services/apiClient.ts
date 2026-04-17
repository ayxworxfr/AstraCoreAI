import axios, { type AxiosError } from 'axios';
import type { ApiErrorResponse } from '../types/api';

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
  timeout: 60000,
  headers: { 'Content-Type': 'application/json' },
});

export function normalizeError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const e = error as AxiosError<ApiErrorResponse>;
    return e.response?.data?.detail ?? e.message;
  }
  if (error instanceof Error) return error.message;
  return '未知错误';
}
