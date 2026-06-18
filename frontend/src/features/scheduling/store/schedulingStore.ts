import { create } from 'zustand';
import { normalizeError } from '@/shared/services/apiClient';
import {
  createTask,
  deleteTask,
  deleteTasksBatch,
  listTasks,
  pauseTask,
  resumeTask,
  runTaskNow,
  updateTask,
} from '@/features/scheduling/services/schedulingService';
import type { CreateTaskRequest, ScheduledTask, UpdateTaskRequest } from '@/features/scheduling/types';

type SchedulingStore = {
  tasks: ScheduledTask[];
  total: number;
  page: number;
  pageSize: number;
  search: string;
  statusFilter: string | undefined;
  isLoading: boolean;
  error: string | null;

  fetchTasks: (page?: number, pageSize?: number) => Promise<void>;
  setSearch: (q: string) => void;
  setStatusFilter: (status: string | undefined) => void;
  createTask: (req: CreateTaskRequest) => Promise<void>;
  updateTask: (id: string, req: UpdateTaskRequest) => Promise<void>;
  deleteTask: (id: string) => Promise<void>;
  batchDeleteTasks: (ids: string[]) => Promise<void>;
  pauseTask: (id: string) => Promise<void>;
  resumeTask: (id: string) => Promise<void>;
  runNow: (id: string) => Promise<void>;
  clearError: () => void;
};

export const useSchedulingStore = create<SchedulingStore>()((set, get) => ({
  tasks: [],
  total: 0,
  page: 1,
  pageSize: 20,
  search: '',
  statusFilter: undefined,
  isLoading: false,
  error: null,

  fetchTasks: async (page = 1, pageSize = 20) => {
    const { search, statusFilter } = get();
    set({ isLoading: true, error: null });
    try {
      const result = await listTasks(page, pageSize, statusFilter, search || undefined);
      set({ tasks: result.items, total: result.total, page: result.page, pageSize: result.page_size, isLoading: false });
    } catch (e) {
      set({ error: normalizeError(e), isLoading: false });
    }
  },

  setSearch: (q) => set({ search: q }),
  setStatusFilter: (status) => set({ statusFilter: status }),

  createTask: async (req) => {
    const task = await createTask(req);
    set((s) => ({ tasks: [task, ...s.tasks], total: s.total + 1 }));
  },

  updateTask: async (id, req) => {
    const updated = await updateTask(id, req);
    set((s) => ({ tasks: s.tasks.map((t) => (t.id === id ? updated : t)) }));
  },

  deleteTask: async (id) => {
    await deleteTask(id);
    set((s) => ({ tasks: s.tasks.filter((t) => t.id !== id), total: Math.max(0, s.total - 1) }));
  },

  batchDeleteTasks: async (ids) => {
    const deleted = await deleteTasksBatch(ids);
    set((s) => ({
      tasks: s.tasks.filter((t) => !ids.includes(t.id)),
      total: Math.max(0, s.total - deleted),
    }));
  },

  pauseTask: async (id) => {
    const updated = await pauseTask(id);
    set((s) => ({ tasks: s.tasks.map((t) => (t.id === id ? updated : t)) }));
  },

  resumeTask: async (id) => {
    const updated = await resumeTask(id);
    set((s) => ({ tasks: s.tasks.map((t) => (t.id === id ? updated : t)) }));
  },

  runNow: async (id) => {
    await runTaskNow(id);
    const { page, pageSize } = get();
    await get().fetchTasks(page, pageSize);
  },

  clearError: () => set({ error: null }),
}));
