import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type Theme = 'light' | 'dark';

export type TTSSettings = {
  voiceName: string | null;
  rate: number;
  pitch: number;
};

type SettingsState = {
  theme: Theme;
  toggleTheme: () => void;
  tts: TTSSettings;
  setTTS: (patch: Partial<TTSSettings>) => void;
};

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set, get) => ({
      theme: 'light' as Theme,
      toggleTheme: () => set({ theme: get().theme === 'dark' ? 'light' : 'dark' }),
      tts: {
        voiceName: null,
        rate: 1.0,
        pitch: 1.0,
      },
      setTTS: (patch) => set({ tts: { ...get().tts, ...patch } }),
    }),
    { name: 'astracore.settings.v1' },
  ),
);
