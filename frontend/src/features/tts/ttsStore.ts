import { create } from 'zustand';

export type TTSStatus = 'idle' | 'loading' | 'playing';

type TTSStore = {
  activeMessageId: string | null;
  status: TTSStatus;
  setLoading: (id: string) => void;
  setPlaying: (id: string) => void;
  clearActive: () => void;
};

/** Global singleton tracking which message is currently being spoken. */
export const useTTSStore = create<TTSStore>((set) => ({
  activeMessageId: null,
  status: 'idle',
  setLoading: (id) => set({ activeMessageId: id, status: 'loading' }),
  setPlaying: (id) => set({ activeMessageId: id, status: 'playing' }),
  clearActive: () => set({ activeMessageId: null, status: 'idle' }),
}));
