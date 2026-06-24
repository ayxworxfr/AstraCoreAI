import { create } from 'zustand';
import { getSystemInfo } from '@/features/system/services/systemService';
import type { SystemInfo } from '@/features/system/types';

type SystemStore = {
  systemInfo: SystemInfo | null;
  loaded: boolean;
  fetchSystemInfo: () => Promise<void>;
};

export const useSystemStore = create<SystemStore>()((set, get) => ({
  systemInfo: null,
  loaded: false,

  fetchSystemInfo: async () => {
    if (get().loaded) return;
    try {
      const info = await getSystemInfo();
      set({ systemInfo: info, loaded: true });
    } catch {
      set({ loaded: true });
    }
  },
}));
