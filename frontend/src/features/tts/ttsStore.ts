import { create } from 'zustand';

export type TTSStatus = 'idle' | 'playing';

type TTSStore = {
  activeMessageId: string | null;
  status: TTSStatus;
  setActive: (id: string) => void;
  clearActive: () => void;
};

/** Global singleton tracking which message is currently being spoken. */
export const useTTSStore = create<TTSStore>((set) => ({
  activeMessageId: null,
  status: 'idle',
  setActive: (id) => set({ activeMessageId: id, status: 'playing' }),
  clearActive: () => set({ activeMessageId: null, status: 'idle' }),
}));
