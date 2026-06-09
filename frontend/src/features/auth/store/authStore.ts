import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { apiClient } from '@/shared/services/apiClient';

type AuthUser = {
  id: string;
  username: string;
  role: string;
  is_active: boolean;
};

type AuthStore = {
  token: string | null;
  user: AuthUser | null;
  error: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  clearError: () => void;
};

export const useAuthStore = create<AuthStore>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      error: null,

      login: async (username, password) => {
        set({ error: null });
        const params = new URLSearchParams();
        params.append('username', username);
        params.append('password', password);
        const tokenRes = await apiClient.post<{ access_token: string }>(
          '/api/v1/auth/login',
          params,
          { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } },
        );
        const token = tokenRes.data.access_token;
        const meRes = await apiClient.get<AuthUser>('/api/v1/auth/me', {
          headers: { Authorization: `Bearer ${token}` },
        });
        set({ token, user: meRes.data });
      },

      logout: () => set({ token: null, user: null }),

      clearError: () => set({ error: null }),
    }),
    {
      name: 'auth-storage',
      partialize: (s) => ({ token: s.token, user: s.user }),
    },
  ),
);
