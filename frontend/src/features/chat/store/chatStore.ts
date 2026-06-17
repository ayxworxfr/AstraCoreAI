import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { normalizeError } from '@/shared/services/apiClient';
import type { ConversationUpdate } from '@/features/chat/services/chatService';
import {
  cancelChatRun,
  createChatRun,
  deleteSession,
  deleteSessionMessage,
  fetchActiveChatRun,
  fetchSessionMessages,
  sendChatMessage,
  submitAnswer,
  subscribeChatRun,
} from '@/features/chat/services/chatService';
import {
  createConversationApi,
  deleteConversationApi,
  fetchConversations,
  patchConversationApi,
} from '@/features/chat/services/conversationService';
import type { ChatRunState, PendingQuestion } from '@/shared/types/api';
import type { ChatMessage, ConversationMeta, ToolActivity } from '@/features/chat/types';

const PAGE_SIZE = 30;

function uuid(): string {
  return crypto.randomUUID();
}

function nowIso(): string {
  return new Date().toISOString();
}

function buildConversation(title = '新会话'): ConversationMeta {
  return {
    id: uuid(),
    title,
    updatedAt: nowIso(),
    lastMessagePreview: '',
    messageCount: 0,
    pinned: false,
  };
}

function sortConversations(list: ConversationMeta[]): ConversationMeta[] {
  return [...list].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    return b.updatedAt.localeCompare(a.updatedAt);
  });
}

const ASSISTANT_FALLBACK_TEXT = {
  empty: '（空响应）',
  interrupted: '（请求中断）',
} as const;

function normalizeToolActivity(items: ChatRunState['tool_activity']): ToolActivity[] {
  return items.map((item) => ({
    name: item.name,
    toolCallId: item.tool_call_id,
    done: item.done,
    input: item.input,
    result: item.result,
    isError: item.isError,
    durationMs: item.durationMs,
  }));
}

type SessionMessageItem = Awaited<ReturnType<typeof fetchSessionMessages>>['messages'][number];

function toChatMessage(convId: string, index: number, item: SessionMessageItem): ChatMessage {
  const toolActivity = normalizeToolActivity(item.tool_activity);
  const thinkingBlocks = item.thinking_blocks.length ? item.thinking_blocks : undefined;

  return {
    id: item.id || `hist-${convId}-${index}`,
    role: item.role,
    content: item.content,
    thinkingBlocks,
    thinkingMode: toolActivity.length ? 'tool' : thinkingBlocks ? 'deep' : undefined,
    toolActivity: toolActivity.length ? toolActivity : undefined,
    status: 'done',
    createdAt: item.created_at || new Date().toISOString(),
    inputTokens: item.input_tokens ?? undefined,
    outputTokens: item.output_tokens ?? undefined,
    model: item.model ?? undefined,
  };
}

type ChatStore = {
  // State
  conversations: ConversationMeta[];
  conversationsLoaded: boolean;
  activeConversationId: string;
  messagesByConversation: Record<string, ChatMessage[]>;
  /** 已从后端加载的消息数（用于 loadMore 的 offset 计算） */
  messagesOffset: Record<string, number>;
  hasMoreMessages: Record<string, boolean>;
  isLoadingMessages: boolean;
  /** loadMoreMessages 专用 loading flag，与 loadMessages 互不阻塞 */
  isLoadingMoreMessages: boolean;
  useStream: boolean;
  enableThinking: boolean;
  enableRag: boolean;
  enableTools: boolean;
  enableWeb: boolean;
  activeModelId: string | null;  // null = use backend default model
  /** 每个 conversation 正在进行的 run_id（有值表示生成中） */
  runIdByConversation: Record<string, string>;
  /** 每个 conversation 的 AbortController，用于中止 SSE 订阅 */
  abortControllerByConversation: Record<string, AbortController>;
  /** 已订阅的 run，避免 React StrictMode / 恢复流程重复订阅同一个 SSE */
  subscribedRunIds: Record<string, boolean>;
  /** 每个 conversation 最近一次的 token 使用量，用于底部状态栏展示 */
  latestUsageByConversation: Record<string, { inputTokens: number; outputTokens: number; model: string }>;
  /** 每个 conversation 当前阻塞等待用户输入的问题（HITL ask_user） */
  pendingQuestionByConversation: Record<string, PendingQuestion | null>;
  sessionError: string | null;   // 当前会话错误，不持久化，刷新自动清除

  // Actions
  initConversations: () => Promise<void>;
  createConversation: () => Promise<string>;
  switchConversation: (id: string) => boolean;
  renameConversation: (id: string, title: string) => void;
  deleteConversation: (id: string) => void;
  clearConversation: (id: string) => void;
  togglePin: (id: string) => void;
  setUseStream: (value: boolean) => void;
  setEnableThinking: (value: boolean) => void;
  setEnableRag: (value: boolean) => void;
  setEnableTools: (value: boolean) => void;
  setEnableWeb: (value: boolean) => void;
  setActiveModelId: (id: string | null) => void;
  setSessionError: (msg: string | null) => void;
  deleteMessage: (conversationId: string, messageId: string) => void;
  sendMessage: (prompt: string) => Promise<void>;
  cancelStream: (conversationId: string) => void;
  resumeActiveRun: (conversationId: string) => Promise<void>;
  submitAnswer: (conversationId: string, selected: string[], freeform?: string | null) => Promise<void>;
  /** 首次打开会话时加载最新 PAGE_SIZE 条消息 */
  loadMessages: (convId: string) => Promise<void>;
  /** 向上滚动时加载更早的消息，返回是否加载了新消息 */
  loadMoreMessages: (convId: string) => Promise<boolean>;
};

export const useChatStore = create<ChatStore>()(
  persist(
    (set, get) => ({
      conversations: [],
      conversationsLoaded: false,
      activeConversationId: '',
      messagesByConversation: {},
      messagesOffset: {},
      hasMoreMessages: {},
      isLoadingMessages: false,
      isLoadingMoreMessages: false,
      useStream: true,
      enableThinking: false,
      enableRag: false,
      enableTools: false,
      enableWeb: false,
      activeModelId: null,
      runIdByConversation: {},
      abortControllerByConversation: {},
      subscribedRunIds: {},
      latestUsageByConversation: {},
      pendingQuestionByConversation: {},
      sessionError: null,

      initConversations: async () => {
        try {
          const list = await fetchConversations();
          if (list.length === 0) {
            // 后端无对话时创建一个默认对话
            await get().createConversation();
          } else {
            const { activeConversationId } = get();
            const activeConv = list.find((c) => c.id === activeConversationId) ?? list[0];
            set({
              conversations: list,
              conversationsLoaded: true,
              activeConversationId: activeConv.id,
              activeModelId: activeConv.modelId ?? null,
            });
            if (!get().messagesByConversation[activeConv.id]) {
              void get().loadMessages(activeConv.id).then(() => get().resumeActiveRun(activeConv.id));
            } else {
              void get().resumeActiveRun(activeConv.id);
            }
          }
        } catch {
          // 后端不可用时降级：建一个本地占位对话，保证 UI 可用
          const fallback = buildConversation();
          set({
            conversations: [fallback],
            conversationsLoaded: true,
            activeConversationId: fallback.id,
          });
        }
      },

      createConversation: async () => {
        const id = uuid();
        const c: ConversationMeta = {
          ...buildConversation(),
          id,
          modelId: null,
        };
        // 乐观更新
        set((s) => ({
          conversations: sortConversations([c, ...s.conversations]),
          conversationsLoaded: true,
          activeConversationId: c.id,
          activeModelId: null,
        }));
        // 同步到后端（失败静默，本地状态已可用）
        void createConversationApi({
          id,
          title: c.title,
          model_id: null,
        }).catch(() => undefined);
        return id;
      },

      switchConversation: (id) => {
        const conv = get().conversations.find((c) => c.id === id);
        set({
          activeConversationId: id,
          activeModelId: conv?.modelId ?? null,
        });
        if (!get().messagesByConversation[id]) {
          void get().loadMessages(id).then(() => get().resumeActiveRun(id));
        } else {
          void get().resumeActiveRun(id);
        }
        return true;
      },

      renameConversation: (id, title) => {
        const trimmed = title.trim().slice(0, 24) || '新会话';
        set((s) => ({
          conversations: sortConversations(
            s.conversations.map((c) => (c.id === id ? { ...c, title: trimmed } : c)),
          ),
        }));
        void patchConversationApi(id, { title: trimmed }).catch(() => undefined);
      },

      deleteConversation: (id) => {
        const { conversations, activeConversationId } = get();
        const remaining = conversations.filter((c) => c.id !== id);
        if (remaining.length === 0) {
          // 删完后自动创建新对话（异步），此处先清空列表
          set((s) => {
            const msgs = { ...s.messagesByConversation };
            delete msgs[id];
            const usage = { ...s.latestUsageByConversation };
            delete usage[id];
            return { conversations: [], messagesByConversation: msgs, latestUsageByConversation: usage };
          });
          void get().createConversation();
        } else {
          const nextId = activeConversationId === id
            ? sortConversations(remaining)[0].id
            : activeConversationId;
          set((s) => {
            const msgs = { ...s.messagesByConversation };
            delete msgs[id];
            const usage = { ...s.latestUsageByConversation };
            delete usage[id];
            return {
              conversations: sortConversations(remaining),
              messagesByConversation: msgs,
              latestUsageByConversation: usage,
              activeConversationId: nextId,
            };
          });
          if (activeConversationId === id) {
            const nextConv = get().conversations.find((c) => c.id === nextId);
            set({ activeModelId: nextConv?.modelId ?? null });
          }
        }
        // 后端同时删除对话元数据 + 消息历史
        void deleteConversationApi(id).catch(() => undefined);
      },

      clearConversation: (id) => {
        set((s) => {
          const msgs = { ...s.messagesByConversation };
          delete msgs[id];
          const offsets = { ...s.messagesOffset };
          delete offsets[id];
          const hasMore = { ...s.hasMoreMessages };
          delete hasMore[id];
          const usage = { ...s.latestUsageByConversation };
          delete usage[id];
          return {
            messagesByConversation: msgs,
            messagesOffset: offsets,
            hasMoreMessages: hasMore,
            latestUsageByConversation: usage,
            conversations: sortConversations(
              s.conversations.map((c) =>
                c.id === id
                  ? { ...c, lastMessagePreview: '', messageCount: 0, updatedAt: nowIso() }
                  : c,
              ),
            ),
          };
        });
        void deleteSession(id).catch(() => undefined);
        void patchConversationApi(id, { last_message_preview: '', message_count: 0 }).catch(() => undefined);
      },

      togglePin: (id) => {
        const conv = get().conversations.find((c) => c.id === id);
        const newPinned = !conv?.pinned;
        set((s) => ({
          conversations: sortConversations(
            s.conversations.map((c) => (c.id === id ? { ...c, pinned: newPinned } : c)),
          ),
        }));
        void patchConversationApi(id, { pinned: newPinned }).catch(() => undefined);
      },

      setUseStream: (value) => set({ useStream: value }),
      setEnableThinking: (value) => set({ enableThinking: value }),
      setEnableRag: (value) => set({ enableRag: value }),
      setEnableTools: (value) => set({ enableTools: value }),
      setEnableWeb: (value) => set({ enableWeb: value }),

      setActiveModelId: (id) => {
        const { activeConversationId } = get();
        set((s) => ({
          activeModelId: id,
          conversations: s.conversations.map((c) =>
            c.id === activeConversationId ? { ...c, modelId: id } : c,
          ),
        }));
        void patchConversationApi(activeConversationId, { model_id: id }).catch(() => undefined);
      },

      setSessionError: (msg) => set({ sessionError: msg }),

      deleteMessage: (conversationId, messageId) => {
        const allMsgs = get().messagesByConversation[conversationId] ?? [];
        const msgIndex = allMsgs.findIndex((m) => m.id === messageId);
        if (msgIndex === -1) return;
        const msg = allMsgs[msgIndex];

        // 找到该轮次对应的 USER 消息 UUID（用于标识后端轮次）
        let userMsgId: string;
        let removeIds: Set<string>;
        if (msg.role === 'user') {
          userMsgId = msg.id;
          // 删除 USER 本身及其后所有消息，直到下一条 USER
          removeIds = new Set([messageId]);
          for (let i = msgIndex + 1; i < allMsgs.length; i++) {
            if (allMsgs[i].role === 'user') break;
            removeIds.add(allMsgs[i].id);
          }
        } else if (msg.role === 'assistant') {
          // 找前面最近的 USER 消息
          const prevUser = allMsgs.slice(0, msgIndex).reverse().find((m) => m.role === 'user');
          if (!prevUser) return;
          userMsgId = prevUser.id;
          removeIds = new Set([messageId]);
        } else {
          return;
        }

        // 乐观更新（同时修正 messagesOffset，避免后续分页 offset 越界）
        set((s) => {
          const msgs = (s.messagesByConversation[conversationId] ?? []).filter(
            (m) => !removeIds.has(m.id),
          );
          const removedCount = removeIds.size;
          const currentOffset = s.messagesOffset[conversationId] ?? 0;
          return {
            messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs },
            messagesOffset: {
              ...s.messagesOffset,
              [conversationId]: Math.max(0, currentOffset - removedCount),
            },
            conversations: sortConversations(
              s.conversations.map((c) =>
                c.id !== conversationId
                  ? c
                  : {
                      ...c,
                      messageCount: msgs.length,
                      lastMessagePreview: msgs[msgs.length - 1]?.content.slice(0, 80) ?? '',
                      updatedAt: nowIso(),
                    },
              ),
            ),
          };
        });
        // 持久化到后端，失败时从服务端重新加载回滚本地状态
        void deleteSessionMessage(conversationId, msg.role, userMsgId).catch(() => {
          void get().loadMessages(conversationId);
        });
      },

      submitAnswer: async (conversationId, selected, freeform) => {
        const { runIdByConversation, pendingQuestionByConversation } = get();
        const runId = runIdByConversation[conversationId];
        const question = pendingQuestionByConversation[conversationId];
        if (!runId || !question) return;
        set((s) => ({
          pendingQuestionByConversation: { ...s.pendingQuestionByConversation, [conversationId]: null },
        }));
        await submitAnswer(runId, { question_id: question.question_id, selected, freeform: freeform ?? null });
      },

      loadMessages: async (convId) => {
        set({ isLoadingMessages: true });
        try {
          const result = await fetchSessionMessages(convId, PAGE_SIZE, 0);
          const messages: ChatMessage[] = result.messages.map((m, i) => toChatMessage(convId, i, m));
          const assistantMsgs = messages.filter((m) => m.role === 'assistant');
          const sessionInputTokens = assistantMsgs.reduce((sum, m) => sum + (m.inputTokens ?? 0), 0);
          const sessionOutputTokens = assistantMsgs.reduce((sum, m) => sum + (m.outputTokens ?? 0), 0);
          const lastModel = [...assistantMsgs].reverse().find((m) => m.model)?.model ?? '';
          set((s) => ({
            messagesByConversation: { ...s.messagesByConversation, [convId]: messages },
            messagesOffset: { ...s.messagesOffset, [convId]: result.messages.length },
            hasMoreMessages: { ...s.hasMoreMessages, [convId]: result.has_more },
            isLoadingMessages: false,
            latestUsageByConversation: sessionInputTokens || sessionOutputTokens
              ? {
                  ...s.latestUsageByConversation,
                  [convId]: {
                    inputTokens: sessionInputTokens,
                    outputTokens: sessionOutputTokens,
                    model: lastModel || s.latestUsageByConversation[convId]?.model || '',
                  },
                }
              : s.latestUsageByConversation,
          }));
        } catch {
          set((s) => ({
            messagesByConversation: { ...s.messagesByConversation, [convId]: [] },
            isLoadingMessages: false,
          }));
        }
      },

      loadMoreMessages: async (convId) => {
        const { messagesOffset, hasMoreMessages, isLoadingMoreMessages } = get();
        if (isLoadingMoreMessages || !hasMoreMessages[convId]) return false;
        set({ isLoadingMoreMessages: true });
        try {
          const currentOffset = messagesOffset[convId] ?? 0;
          const result = await fetchSessionMessages(convId, PAGE_SIZE, currentOffset);
          if (result.messages.length === 0) {
            set({ isLoadingMoreMessages: false });
            return false;
          }
          const older: ChatMessage[] = result.messages.map((m, i) => toChatMessage(convId, currentOffset + i, m));
          set((s) => ({
            messagesByConversation: {
              ...s.messagesByConversation,
              [convId]: [...older, ...(s.messagesByConversation[convId] ?? [])],
            },
            messagesOffset: { ...s.messagesOffset, [convId]: currentOffset + result.messages.length },
            hasMoreMessages: { ...s.hasMoreMessages, [convId]: result.has_more },
            isLoadingMoreMessages: false,
          }));
          return true;
        } catch {
          set({ isLoadingMoreMessages: false });
          return false;
        }
      },

      resumeActiveRun: async (conversationId) => {
        const run = await fetchActiveChatRun(conversationId).catch(() => null);
        if (!run || (run.status !== 'running' && run.status !== 'awaiting_input')) return;
        if (get().subscribedRunIds[run.run_id]) return;

        const assistantId = `run-${run.run_id}`;

        const applyRunState = (state: ChatRunState) => {
          set((s) => {
            const prev = s.messagesByConversation[conversationId] ?? [];
            const hasUser = prev.some((m) => m.role === 'user' && m.content === state.user_message);
            const userMsg: ChatMessage = {
              id: `run-user-${state.run_id}`,
              role: 'user',
              content: state.user_message,
              status: 'done',
              createdAt: state.created_at,
            };
            const assistantMsg: ChatMessage = {
              id: assistantId,
              role: 'assistant',
              content: state.assistant_content,
              thinkingBlocks: state.thinking_blocks.length ? state.thinking_blocks : undefined,
              thinkingMode: state.tool_activity.length ? 'tool' : 'normal',
              toolActivity: normalizeToolActivity(state.tool_activity),
              status: (state.status === 'running' || state.status === 'awaiting_input') ? 'streaming' : 'done',
              createdAt: state.created_at,
            };
            const withoutRun = prev.filter((m) =>
              m.id !== assistantId
              && !(m.role === 'assistant' && m.status === 'streaming'),
            );
            const next = [...withoutRun, ...(hasUser ? [] : [userMsg]), assistantMsg];
            const isActive = state.status === 'running' || state.status === 'awaiting_input';
            const runIds = isActive
              ? { ...s.runIdByConversation, [conversationId]: state.run_id }
              : (() => { const n = { ...s.runIdByConversation }; delete n[conversationId]; return n; })();
            return {
              messagesByConversation: { ...s.messagesByConversation, [conversationId]: next },
              runIdByConversation: runIds,
              pendingQuestionByConversation: {
                ...s.pendingQuestionByConversation,
                [conversationId]: state.pending_question ?? null,
              },
            };
          });
        };

        applyRunState(run);
        const controller = new AbortController();
        set((s) => ({
          abortControllerByConversation: { ...s.abortControllerByConversation, [conversationId]: controller },
          runIdByConversation: { ...s.runIdByConversation, [conversationId]: run.run_id },
          subscribedRunIds: { ...s.subscribedRunIds, [run.run_id]: true },
        }));

        const clearRunState = (extra?: Record<string, unknown>) => {
          set((s) => {
            const runIds = { ...s.subscribedRunIds };
            delete runIds[run.run_id];
            const convRunIds = { ...s.runIdByConversation };
            delete convRunIds[conversationId];
            const controllers = { ...s.abortControllerByConversation };
            delete controllers[conversationId];
            return { subscribedRunIds: runIds, runIdByConversation: convRunIds, abortControllerByConversation: controllers, ...extra };
          });
        };

        void subscribeChatRun(
          run.run_id,
          {
            onRunState: applyRunState,
            onMessage: (delta) => {
              set((s) => {
                const msgs = (s.messagesByConversation[conversationId] ?? []).map((m) =>
                  m.id === assistantId ? { ...m, content: `${m.content}${delta}`, status: 'streaming' as const } : m,
                );
                return { messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs } };
              });
            },
            onThinkingStart: () => {
              set((s) => {
                const msgs = (s.messagesByConversation[conversationId] ?? []).map((m) =>
                  m.id === assistantId
                    ? { ...m, thinkingBlocks: [...(m.thinkingBlocks ?? []), ''], status: 'streaming' as const }
                    : m,
                );
                return { messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs } };
              });
            },
            onThinking: (delta) => {
              set((s) => {
                const msgs = (s.messagesByConversation[conversationId] ?? []).map((m) => {
                  if (m.id !== assistantId) return m;
                  const blocks = [...(m.thinkingBlocks ?? [''])];
                  blocks[blocks.length - 1] = `${blocks[blocks.length - 1]}${delta}`;
                  return { ...m, thinkingBlocks: blocks, status: 'streaming' as const };
                });
                return { messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs } };
              });
            },
            onToolStart: (toolName, toolCallId, input) => {
              set((s) => {
                const msgs = (s.messagesByConversation[conversationId] ?? []).map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        thinkingMode: 'tool' as const,
                        toolActivity: [...(m.toolActivity ?? []), { name: toolName, toolCallId, done: false, input }],
                      }
                    : m,
                );
                return { messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs } };
              });
            },
            onToolResult: (_toolName, toolCallId, _input, result, isError, durationMs) => {
              set((s) => {
                const msgs = (s.messagesByConversation[conversationId] ?? []).map((m) => {
                  if (m.id !== assistantId) return m;
                  const activity = [...(m.toolActivity ?? [])];
                  for (let i = 0; i < activity.length; i++) {
                    if (activity[i].toolCallId === toolCallId && !activity[i].done) {
                      activity[i] = { ...activity[i], done: true, result, isError, durationMs };
                      break;
                    }
                  }
                  return { ...m, toolActivity: activity };
                });
                return { messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs } };
              });
            },
            onAgentStart: (agentId, task, model) => {
              set((s) => {
                const msgs = (s.messagesByConversation[conversationId] ?? []).map((m) =>
                  m.id !== assistantId
                    ? m
                    : {
                        ...m,
                        subAgents: [
                          ...(m.subAgents ?? []),
                          { agentId, task, model, status: 'running' as const, text: '', thinking: '', toolActivity: [] },
                        ],
                      },
                );
                return { messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs } };
              });
            },
            onAgentMessage: (agentId, delta) => {
              set((s) => {
                const msgs = (s.messagesByConversation[conversationId] ?? []).map((m) => {
                  if (m.id !== assistantId) return m;
                  const subAgents = (m.subAgents ?? []).map((a) =>
                    a.agentId === agentId ? { ...a, text: a.text + delta } : a,
                  );
                  return { ...m, subAgents };
                });
                return { messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs } };
              });
            },
            onAgentThinking: (agentId, delta) => {
              set((s) => {
                const msgs = (s.messagesByConversation[conversationId] ?? []).map((m) => {
                  if (m.id !== assistantId) return m;
                  const subAgents = (m.subAgents ?? []).map((a) =>
                    a.agentId === agentId ? { ...a, thinking: a.thinking + delta } : a,
                  );
                  return { ...m, subAgents };
                });
                return { messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs } };
              });
            },
            onAgentToolStart: (agentId, toolName, toolCallId, input) => {
              set((s) => {
                const msgs = (s.messagesByConversation[conversationId] ?? []).map((m) => {
                  if (m.id !== assistantId) return m;
                  const subAgents = (m.subAgents ?? []).map((a) =>
                    a.agentId === agentId
                      ? { ...a, toolActivity: [...a.toolActivity, { name: toolName, toolCallId, done: false, input }] }
                      : a,
                  );
                  return { ...m, subAgents };
                });
                return { messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs } };
              });
            },
            onAgentToolResult: (agentId, _toolName, toolCallId, result, isError, durationMs) => {
              set((s) => {
                const msgs = (s.messagesByConversation[conversationId] ?? []).map((m) => {
                  if (m.id !== assistantId) return m;
                  const subAgents = (m.subAgents ?? []).map((a) => {
                    if (a.agentId !== agentId) return a;
                    const toolActivity = [...a.toolActivity];
                    for (let i = 0; i < toolActivity.length; i++) {
                      if (toolActivity[i].toolCallId === toolCallId && !toolActivity[i].done) {
                        toolActivity[i] = { ...toolActivity[i], done: true, result, isError, durationMs };
                        break;
                      }
                    }
                    return { ...a, toolActivity };
                  });
                  return { ...m, subAgents };
                });
                return { messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs } };
              });
            },
            onAgentDone: (agentId, durationMs, error) => {
              set((s) => {
                const msgs = (s.messagesByConversation[conversationId] ?? []).map((m) => {
                  if (m.id !== assistantId) return m;
                  const subAgents = (m.subAgents ?? []).map((a) =>
                    a.agentId === agentId
                      ? { ...a, status: error ? 'error' as const : 'done' as const, durationMs, error }
                      : a,
                  );
                  return { ...m, subAgents };
                });
                return { messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs } };
              });
            },
            onUsage: (inputTokens, outputTokens, model) => {
              set((s) => {
                const prev = s.latestUsageByConversation[conversationId];
                return {
                  latestUsageByConversation: {
                    ...s.latestUsageByConversation,
                    [conversationId]: {
                      inputTokens: (prev?.inputTokens ?? 0) + inputTokens,
                      outputTokens: (prev?.outputTokens ?? 0) + outputTokens,
                      model,
                    },
                  },
                };
              });
            },
            onUserInputRequired: (_runId, question) => {
              set((s) => ({
                pendingQuestionByConversation: { ...s.pendingQuestionByConversation, [conversationId]: question },
              }));
            },
            onUserInputResolved: (_runId, _questionId) => {
              set((s) => ({
                pendingQuestionByConversation: { ...s.pendingQuestionByConversation, [conversationId]: null },
              }));
            },
            onDone: () => {
              clearRunState();
              void get().loadMessages(conversationId);
            },
            onError: (msg) => {
              clearRunState({ sessionError: msg });
              void get().loadMessages(conversationId);
            },
          },
          controller.signal,
        );
      },

      cancelStream: (conversationId) => {
        const { runIdByConversation, abortControllerByConversation, messagesByConversation } = get();
        const runId = runIdByConversation[conversationId];
        abortControllerByConversation[conversationId]?.abort();
        if (runId) void cancelChatRun(runId).catch(() => undefined);

        const msgs = (messagesByConversation[conversationId] ?? []).map((m) =>
          m.status === 'streaming'
            ? { ...m, status: 'done' as const, toolActivity: m.toolActivity?.map((t) => ({ ...t, done: true })) }
            : m,
        );
        set((s) => {
          const convRunIds = { ...s.runIdByConversation };
          delete convRunIds[conversationId];
          const controllers = { ...s.abortControllerByConversation };
          delete controllers[conversationId];
          const runIds = { ...s.subscribedRunIds };
          if (runId) delete runIds[runId];
          const pendingQ = { ...s.pendingQuestionByConversation };
          delete pendingQ[conversationId];
          return {
            messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs },
            runIdByConversation: convRunIds,
            abortControllerByConversation: controllers,
            subscribedRunIds: runIds,
            pendingQuestionByConversation: pendingQ,
          };
        });
        // 中断后同步后端真实 UUID，否则删除消息会 404
        void get().loadMessages(conversationId);
      },

      sendMessage: async (prompt) => {
        const {
          activeConversationId, useStream, enableThinking, enableRag, enableTools, enableWeb,
          activeModelId, conversations,
        } = get();
        const trimmed = prompt.trim();
        const hasStreaming = (get().messagesByConversation[activeConversationId] ?? []).some(
          (m) => m.status === 'streaming',
        );
        if (!trimmed || hasStreaming) return;

        const conv = conversations.find((c) => c.id === activeConversationId);
        if (!conv) return;
        const isUntitled = conv.title === '新会话' && conv.messageCount === 0;

        const userMsg: ChatMessage = {
          id: uuid(),
          role: 'user',
          content: trimmed,
          status: 'done',
          createdAt: nowIso(),
        };
        const assistantId = uuid();
        const thinkingMode = enableThinking ? 'deep' : 'normal';
        const assistantMsg: ChatMessage = {
          id: assistantId,
          role: 'assistant',
          content: '',
          thinkingBlocks: undefined,
          thinkingMode,
          status: 'streaming',
          createdAt: nowIso(),
        };

        set((s) => {
          const prev = s.messagesByConversation[activeConversationId] ?? [];
          const next = [...prev, userMsg, assistantMsg];
          return {
            messagesByConversation: { ...s.messagesByConversation, [activeConversationId]: next },
            conversations: sortConversations(
              s.conversations.map((c) =>
                c.id !== activeConversationId
                  ? c
                  : {
                      ...c,
                      title: isUntitled ? trimmed.slice(0, 24) : c.title,
                      updatedAt: nowIso(),
                      lastMessagePreview: trimmed.slice(0, 80),
                      messageCount: next.length,
                    },
              ),
            ),
          };
        });

        const updateAssistant = (
          patch: Partial<Pick<ChatMessage, 'content' | 'thinkingBlocks' | 'status' | 'toolActivity' | 'subAgents'>>,
        ) => {
          set((s) => {
            const msgs = (s.messagesByConversation[activeConversationId] ?? []).map((m) =>
              m.id === assistantId ? { ...m, ...patch } : m,
            );
            return { messagesByConversation: { ...s.messagesByConversation, [activeConversationId]: msgs } };
          });
        };

        const finishStreaming = (
          runId: string | null,
          patch: Partial<Pick<ChatMessage, 'content' | 'status' | 'toolActivity'>>,
        ) => {
          updateAssistant({ status: 'done', ...patch });
          set((s) => {
            const runIds = { ...s.subscribedRunIds };
            if (runId) delete runIds[runId];
            const convRunIds = { ...s.runIdByConversation };
            delete convRunIds[activeConversationId];
            const controllers = { ...s.abortControllerByConversation };
            delete controllers[activeConversationId];
            return { subscribedRunIds: runIds, runIdByConversation: convRunIds, abortControllerByConversation: controllers };
          });
        };

        let runHandledByExistingSubscription = false;

        try {
          if (useStream) {
            const controller = new AbortController();
            let textBuffer = '';
            const thinkingBlocks: string[] = [];
            const getUpdatedBlocks = () => (thinkingBlocks.length ? [...thinkingBlocks] : undefined);

            const run = await createChatRun({
              message: trimmed,
              session_id: activeConversationId,
              model_profile: activeModelId ?? undefined,
              enable_thinking: enableThinking,
              enable_rag: enableRag,
              use_tools: enableTools || enableWeb,
              enable_web: enableWeb,
            });

            if (get().subscribedRunIds[run.run_id]) {
              runHandledByExistingSubscription = true;
              set((s) => {
                const msgs = (s.messagesByConversation[activeConversationId] ?? []).filter(
                  (m) => m.id !== assistantId && m.id !== userMsg.id,
                );
                return {
                  messagesByConversation: { ...s.messagesByConversation, [activeConversationId]: msgs },
                };
              });
              return;
            }

            set((s) => ({
              runIdByConversation: { ...s.runIdByConversation, [activeConversationId]: run.run_id },
              abortControllerByConversation: { ...s.abortControllerByConversation, [activeConversationId]: controller },
              subscribedRunIds: { ...s.subscribedRunIds, [run.run_id]: true },
            }));

            await subscribeChatRun(
              run.run_id,
              {
                onRunState: (state) => {
                  textBuffer = state.assistant_content;
                  while (thinkingBlocks.length) thinkingBlocks.pop();
                  thinkingBlocks.push(...state.thinking_blocks);
                  updateAssistant({
                    content: state.assistant_content,
                    thinkingBlocks: state.thinking_blocks.length ? state.thinking_blocks : undefined,
                    toolActivity: normalizeToolActivity(state.tool_activity),
                    status: state.status === 'running' ? 'streaming' : 'done',
                  });
                },
                onMessage: (delta) => {
                  textBuffer += delta;
                  updateAssistant({ content: textBuffer, status: 'streaming' });
                },
                onToolStart: (toolName, toolCallId, input) => {
                  set((s) => {
                    const msgs = (s.messagesByConversation[activeConversationId] ?? []).map((m) =>
                      m.id !== assistantId
                        ? m
                        : {
                            ...m,
                            thinkingMode: 'tool' as const,
                            toolActivity: [...(m.toolActivity ?? []), { name: toolName, toolCallId, done: false, input }],
                          },
                    );
                    return { messagesByConversation: { ...s.messagesByConversation, [activeConversationId]: msgs } };
                  });
                },
                onToolResult: (_toolName, toolCallId, _input, result, isError, durationMs) => {
                  set((s) => {
                    const msgs = (s.messagesByConversation[activeConversationId] ?? []).map((m) => {
                      if (m.id !== assistantId) return m;
                      const activity = [...(m.toolActivity ?? [])];
                      for (let i = 0; i < activity.length; i++) {
                        if (activity[i].toolCallId === toolCallId && !activity[i].done) {
                          activity[i] = { ...activity[i], done: true, result, isError, durationMs };
                          break;
                        }
                      }
                      return { ...m, toolActivity: activity };
                    });
                    return { messagesByConversation: { ...s.messagesByConversation, [activeConversationId]: msgs } };
                  });
                },
                onAgentStart: (agentId, task, model) => {
                  set((s) => {
                    const msgs = (s.messagesByConversation[activeConversationId] ?? []).map((m) =>
                      m.id !== assistantId
                        ? m
                        : {
                            ...m,
                            subAgents: [
                              ...(m.subAgents ?? []),
                              { agentId, task, model, status: 'running' as const, text: '', thinking: '', toolActivity: [] },
                            ],
                          },
                    );
                    return { messagesByConversation: { ...s.messagesByConversation, [activeConversationId]: msgs } };
                  });
                },
                onAgentMessage: (agentId, delta) => {
                  set((s) => {
                    const msgs = (s.messagesByConversation[activeConversationId] ?? []).map((m) => {
                      if (m.id !== assistantId) return m;
                      const subAgents = (m.subAgents ?? []).map((a) =>
                        a.agentId === agentId ? { ...a, text: a.text + delta } : a,
                      );
                      return { ...m, subAgents };
                    });
                    return { messagesByConversation: { ...s.messagesByConversation, [activeConversationId]: msgs } };
                  });
                },
                onAgentThinking: (agentId, delta) => {
                  set((s) => {
                    const msgs = (s.messagesByConversation[activeConversationId] ?? []).map((m) => {
                      if (m.id !== assistantId) return m;
                      const subAgents = (m.subAgents ?? []).map((a) =>
                        a.agentId === agentId ? { ...a, thinking: a.thinking + delta } : a,
                      );
                      return { ...m, subAgents };
                    });
                    return { messagesByConversation: { ...s.messagesByConversation, [activeConversationId]: msgs } };
                  });
                },
                onAgentToolStart: (agentId, toolName, toolCallId, input) => {
                  set((s) => {
                    const msgs = (s.messagesByConversation[activeConversationId] ?? []).map((m) => {
                      if (m.id !== assistantId) return m;
                      const subAgents = (m.subAgents ?? []).map((a) =>
                        a.agentId === agentId
                          ? { ...a, toolActivity: [...a.toolActivity, { name: toolName, toolCallId, done: false, input }] }
                          : a,
                      );
                      return { ...m, subAgents };
                    });
                    return { messagesByConversation: { ...s.messagesByConversation, [activeConversationId]: msgs } };
                  });
                },
                onAgentToolResult: (agentId, _toolName, toolCallId, result, isError, durationMs) => {
                  set((s) => {
                    const msgs = (s.messagesByConversation[activeConversationId] ?? []).map((m) => {
                      if (m.id !== assistantId) return m;
                      const subAgents = (m.subAgents ?? []).map((a) => {
                        if (a.agentId !== agentId) return a;
                        const toolActivity = [...a.toolActivity];
                        for (let i = 0; i < toolActivity.length; i++) {
                          if (toolActivity[i].toolCallId === toolCallId && !toolActivity[i].done) {
                            toolActivity[i] = { ...toolActivity[i], done: true, result, isError, durationMs };
                            break;
                          }
                        }
                        return { ...a, toolActivity };
                      });
                      return { ...m, subAgents };
                    });
                    return { messagesByConversation: { ...s.messagesByConversation, [activeConversationId]: msgs } };
                  });
                },
                onAgentDone: (agentId, durationMs, error) => {
                  set((s) => {
                    const msgs = (s.messagesByConversation[activeConversationId] ?? []).map((m) => {
                      if (m.id !== assistantId) return m;
                      const subAgents = (m.subAgents ?? []).map((a) =>
                        a.agentId === agentId
                          ? { ...a, status: error ? 'error' as const : 'done' as const, durationMs, error }
                          : a,
                      );
                      return { ...m, subAgents };
                    });
                    return { messagesByConversation: { ...s.messagesByConversation, [activeConversationId]: msgs } };
                  });
                },
                onThinkingStart: () => {
                  thinkingBlocks.push('');
                  const currentMsg = (get().messagesByConversation[activeConversationId] ?? [])
                    .find((m) => m.id === assistantId);
                  updateAssistant({
                    thinkingBlocks: getUpdatedBlocks(),
                    status: 'streaming',
                    toolActivity: currentMsg?.toolActivity?.map((t) => ({ ...t, done: true })),
                  });
                },
                onThinking: (delta) => {
                  if (thinkingBlocks.length === 0) thinkingBlocks.push('');
                  thinkingBlocks[thinkingBlocks.length - 1] += delta;
                  updateAssistant({ thinkingBlocks: getUpdatedBlocks(), status: 'streaming' });
                },
                onUserInputRequired: (_runId, question) => {
                  set((s) => ({
                    pendingQuestionByConversation: { ...s.pendingQuestionByConversation, [activeConversationId]: question },
                  }));
                },
                onUserInputResolved: (_runId, _questionId) => {
                  set((s) => ({
                    pendingQuestionByConversation: { ...s.pendingQuestionByConversation, [activeConversationId]: null },
                  }));
                },
                onUsage: (inputTokens, outputTokens, model) => {
                  set((s) => {
                    const prev = s.latestUsageByConversation[activeConversationId];
                    return {
                      latestUsageByConversation: {
                        ...s.latestUsageByConversation,
                        [activeConversationId]: {
                          inputTokens: (prev?.inputTokens ?? 0) + inputTokens,
                          outputTokens: (prev?.outputTokens ?? 0) + outputTokens,
                          model,
                        },
                      },
                    };
                  });
                },
                onDone: (conv?: ConversationUpdate) => {
                  set((s) => ({
                    conversations: sortConversations(
                      s.conversations.map((c) => {
                        if (c.id !== activeConversationId) return c;
                        if (conv) {
                          return {
                            ...c,
                            title: conv.title,
                            lastMessagePreview: conv.last_message_preview,
                            messageCount: conv.message_count,
                            updatedAt: conv.updated_at,
                          };
                        }
                        return {
                          ...c,
                          lastMessagePreview: (textBuffer || ASSISTANT_FALLBACK_TEXT.empty).slice(0, 80),
                          updatedAt: nowIso(),
                        };
                      }),
                    ),
                  }));
                  const currentMsg = (get().messagesByConversation[activeConversationId] ?? [])
                    .find((m) => m.id === assistantId);
                  finishStreaming(run.run_id, {
                    content: textBuffer || ASSISTANT_FALLBACK_TEXT.empty,
                    toolActivity: currentMsg?.toolActivity?.map((t) => ({ ...t, done: true })),
                  });
                  // 用后端真实 UUID 替换前端临时 ID，确保删除操作能定位正确消息
                  void get().loadMessages(activeConversationId);
                },
                onError: (msg) => {
                  finishStreaming(run.run_id, { content: textBuffer || ASSISTANT_FALLBACK_TEXT.interrupted });
                  set({ sessionError: msg });
                  void get().loadMessages(activeConversationId);
                },
              },
              controller.signal,
            );
          } else {
            const res = await sendChatMessage({
              message: trimmed,
              session_id: activeConversationId,
              model_profile: activeModelId ?? undefined,
              enable_rag: enableRag,
            });
            updateAssistant({ content: res.message || ASSISTANT_FALLBACK_TEXT.empty, status: 'done' });
          }
        } catch (e) {
          const err = e as Error;
          const isAbort = err.name === 'AbortError' || /abort/i.test(err.message ?? '');
          if (!isAbort) {
            updateAssistant({ status: 'done' });
            set({ sessionError: normalizeError(e) });
          }
        } finally {
          if (!runHandledByExistingSubscription) {
            set((s) => {
              const runId = s.runIdByConversation[activeConversationId];
              const hasController = activeConversationId in s.abortControllerByConversation;
              // onDone / onError / cancelStream 已清理过则跳过
              if (!runId && !hasController) return s;
              const msgs = (s.messagesByConversation[activeConversationId] ?? []).map((m) =>
                m.status === 'streaming' ? { ...m, status: 'done' as const } : m,
              );
              const runIds = { ...s.subscribedRunIds };
              if (runId) delete runIds[runId];
              const convRunIds = { ...s.runIdByConversation };
              delete convRunIds[activeConversationId];
              const controllers = { ...s.abortControllerByConversation };
              delete controllers[activeConversationId];
              return {
                messagesByConversation: { ...s.messagesByConversation, [activeConversationId]: msgs },
                subscribedRunIds: runIds,
                runIdByConversation: convRunIds,
                abortControllerByConversation: controllers,
              };
            });
          }
        }
      },
    }),
    {
      name: 'astracore.chat.v2',
      partialize: (s) => ({
        // 对话列表由后端 DB 维护，localStorage 只保留活跃会话 ID 和全局 UI 偏好
        activeConversationId: s.activeConversationId,
        useStream: s.useStream,
        enableThinking: s.enableThinking,
        enableRag: s.enableRag,
        enableTools: s.enableTools,
        enableWeb: s.enableWeb,
      }),
    },
  ),
);
