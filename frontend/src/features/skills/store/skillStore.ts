import { create } from 'zustand';
import { normalizeError } from '@/shared/services/apiClient';
import {
  createSkill,
  deleteSkill,
  getSettings,
  listSkills,
  saveSettings,
  updateSkill,
} from '@/features/skills/services/skillService';
import type { CreateSkillRequest, Skill, UpdateSkillRequest, UserSettings } from '@/features/skills/types';

type SkillStore = {
  skills: Skill[];
  settings: UserSettings;
  isLoading: boolean;
  error: string | null;

  fetchSkills: () => Promise<void>;
  fetchSettings: () => Promise<void>;
  createSkill: (req: CreateSkillRequest) => Promise<void>;
  updateSkill: (id: string, req: UpdateSkillRequest) => Promise<void>;
  deleteSkill: (id: string) => Promise<void>;
  saveSettings: (patch: Partial<UserSettings>) => Promise<void>;
  clearError: () => void;
};

export const useSkillStore = create<SkillStore>()((set) => ({
  skills: [],
  settings: {
    global_instruction: '',
    temperature: 0.7,
    top_p: null,
    stop_sequences: [],
    rag_top_k: 4,
    context_max_messages: 20,
    ai_name: '小卡',
    owner_name: '',
    timezone: 'Asia/Shanghai',
    thinking_collapse_mode: 'auto',
  },
  isLoading: false,
  error: null,

  fetchSkills: async () => {
    set({ isLoading: true, error: null });
    try {
      const skills = await listSkills();
      set({ skills, isLoading: false });
    } catch (e) {
      set({ error: normalizeError(e), isLoading: false });
    }
  },

  fetchSettings: async () => {
    try {
      const settings = await getSettings();
      set({ settings });
    } catch (e) {
      set({ error: normalizeError(e) });
    }
  },

  createSkill: async (req) => {
    const skill = await createSkill(req);
    set((s) => ({ skills: [...s.skills, skill] }));
  },

  updateSkill: async (id, req) => {
    const updated = await updateSkill(id, req);
    set((s) => ({ skills: s.skills.map((sk) => (sk.id === id ? updated : sk)) }));
  },

  deleteSkill: async (id) => {
    await deleteSkill(id);
    set((s) => ({ skills: s.skills.filter((sk) => sk.id !== id) }));
  },

  saveSettings: async (patch) => {
    const settings = await saveSettings(patch);
    set({ settings });
  },

  clearError: () => set({ error: null }),
}));
