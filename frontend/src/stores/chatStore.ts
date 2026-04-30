import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { normalizeError } from '../services/apiClient';
import type { ConversationUpdate } from '../services/chatService';
import {
  cancelChatRun,
  createChatRun,
  deleteSession,
  fetchActiveChatRun,
  fetchSessionMessages,
  sendChatMessage,
  subscribeChatRun,
} from '../services/chatService';
import {
  createConversationApi,
  deleteConversationApi,
  fetchConversations,
  patchConversationApi,
} from '../services/conversationService';
import type { ChatRunState } from '../types/api';
import type { ChatMessage, ConversationMeta, ToolActivity } from '../types/chat';
import { useSkillStore } from './skillStore';

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
    id: `hist-${convId}-${index}`,
    role: item.role,
    content: item.content,
    thinkingBlocks,
    thinkingMode: toolActivity.length ? 'tool' : thinkingBlocks ? 'deep' : undefined,
    toolActivity: toolActivity.length ? toolActivity : undefined,
    status: 'done',
    createdAt: new Date().toISOString(),
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
  isStreaming: boolean;
  streamingConversationId: string | null;
  useStream: boolean;
  enableThinking: boolean;
  enableRag: boolean;
  enableTools: boolean;
  enableWeb: boolean;
  activeSkillId: string | null;  // null = use default, 'none' = explicitly disabled, uuid = specific skill
  activeModelId: string | null;  // null = use backend default model
  abortController: AbortController | null;
  activeRunId: string | null;
  /** 已订阅的 run，避免 React StrictMode / 恢复流程重复订阅同一个 SSE */
  subscribedRunIds: Record<string, boolean>;
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
  setActiveSkillId: (id: string | null) => void;
  setActiveModelId: (id: string | null) => void;
  setSessionError: (msg: string | null) => void;
  deleteMessage: (conversationId: string, messageId: string) => void;
  sendMessage: (prompt: string) => Promise<void>;
  cancelStream: () => void;
  resumeActiveRun: (conversationId: string) => Promise<void>;
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
      isStreaming: false,
      streamingConversationId: null,
      useStream: true,
      enableThinking: false,
      enableRag: false,
      enableTools: false,
      enableWeb: false,
      activeSkillId: null,
      activeModelId: null,
      abortController: null,
      activeRunId: null,
      subscribedRunIds: {},
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
              activeSkillId: activeConv.skillId ?? null,
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
        const defaultSkillId = useSkillStore.getState().settings.default_skill_id || null;
        const id = uuid();
        const c: ConversationMeta = {
          ...buildConversation(),
          id,
          skillId: defaultSkillId,
          modelId: null,
        };
        // 乐观更新
        set((s) => ({
          conversations: sortConversations([c, ...s.conversations]),
          conversationsLoaded: true,
          activeConversationId: c.id,
          activeSkillId: defaultSkillId,
          activeModelId: null,
        }));
        // 同步到后端（失败静默，本地状态已可用）
        void createConversationApi({
          id,
          title: c.title,
          skill_id: defaultSkillId,
          model_id: null,
        }).catch(() => undefined);
        return id;
      },

      switchConversation: (id) => {
        const conv = get().conversations.find((c) => c.id === id);
        set({
          activeConversationId: id,
          activeSkillId: conv?.skillId ?? null,
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
            return { conversations: [], messagesByConversation: msgs };
          });
          void get().createConversation();
        } else {
          const nextId = activeConversationId === id
            ? sortConversations(remaining)[0].id
            : activeConversationId;
          set((s) => {
            const msgs = { ...s.messagesByConversation };
            delete msgs[id];
            return {
              conversations: sortConversations(remaining),
              messagesByConversation: msgs,
              activeConversationId: nextId,
            };
          });
          if (activeConversationId === id) {
            const nextConv = get().conversations.find((c) => c.id === nextId);
            set({ activeSkillId: nextConv?.skillId ?? null, activeModelId: nextConv?.modelId ?? null });
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
          return {
            messagesByConversation: msgs,
            messagesOffset: offsets,
            hasMoreMessages: hasMore,
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

      setActiveSkillId: (id) => {
        const { activeConversationId } = get();
        set((s) => ({
          activeSkillId: id,
          conversations: s.conversations.map((c) =>
            c.id === activeConversationId ? { ...c, skillId: id } : c,
          ),
        }));
        void patchConversationApi(activeConversationId, { skill_id: id }).catch(() => undefined);
      },

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
        set((s) => {
          const msgs = (s.messagesByConversation[conversationId] ?? []).filter(
            (m) => m.id !== messageId,
          );
          return {
            messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs },
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
      },

      loadMessages: async (convId) => {
        set({ isLoadingMessages: true });
        try {
          const result = await fetchSessionMessages(convId, PAGE_SIZE, 0);
          const messages: ChatMessage[] = result.messages.map((m, i) => toChatMessage(convId, i, m));
          set((s) => ({
            messagesByConversation: { ...s.messagesByConversation, [convId]: messages },
            messagesOffset: { ...s.messagesOffset, [convId]: result.messages.length },
            hasMoreMessages: { ...s.hasMoreMessages, [convId]: result.has_more },
            isLoadingMessages: false,
          }));
        } catch {
          set((s) => ({
            messagesByConversation: { ...s.messagesByConversation, [convId]: [] },
            isLoadingMessages: false,
          }));
        }
      },

      loadMoreMessages: async (convId) => {
        const { messagesOffset, hasMoreMessages, isLoadingMessages } = get();
        if (isLoadingMessages || !hasMoreMessages[convId]) return false;
        set({ isLoadingMessages: true });
        try {
          const currentOffset = messagesOffset[convId] ?? 0;
          const result = await fetchSessionMessages(convId, PAGE_SIZE, currentOffset);
          if (result.messages.length === 0) {
            set({ isLoadingMessages: false });
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
            isLoadingMessages: false,
          }));
          return true;
        } catch {
          set({ isLoadingMessages: false });
          return false;
        }
      },

      resumeActiveRun: async (conversationId) => {
        const run = await fetchActiveChatRun(conversationId).catch(() => null);
        if (!run || run.status !== 'running') return;
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
              status: state.status === 'running' ? 'streaming' : 'done',
              createdAt: state.created_at,
            };
            const withoutRun = prev.filter((m) =>
              m.id !== assistantId
              && m.id !== userMsg.id
              && !(m.role === 'assistant' && m.status === 'streaming'),
            );
            const next = [...withoutRun, ...(hasUser ? [] : [userMsg]), assistantMsg];
            return {
              messagesByConversation: { ...s.messagesByConversation, [conversationId]: next },
              isStreaming: state.status === 'running',
              streamingConversationId: state.status === 'running' ? conversationId : null,
              activeRunId: state.status === 'running' ? state.run_id : null,
            };
          });
        };

        applyRunState(run);
        const controller = new AbortController();
        set((s) => ({
          abortController: controller,
          activeRunId: run.run_id,
          subscribedRunIds: { ...s.subscribedRunIds, [run.run_id]: true },
        }));

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
            onToolStart: (toolName, input) => {
              set((s) => {
                const msgs = (s.messagesByConversation[conversationId] ?? []).map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        thinkingMode: 'tool' as const,
                        toolActivity: [...(m.toolActivity ?? []), { name: toolName, done: false, input }],
                      }
                    : m,
                );
                return { messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs } };
              });
            },
            onToolResult: (toolName, _input, result, isError, durationMs) => {
              set((s) => {
                const msgs = (s.messagesByConversation[conversationId] ?? []).map((m) => {
                  if (m.id !== assistantId) return m;
                  const activity = [...(m.toolActivity ?? [])];
                  for (let i = activity.length - 1; i >= 0; i--) {
                    if (activity[i].name === toolName && !activity[i].done) {
                      activity[i] = { ...activity[i], done: true, result, isError, durationMs };
                      break;
                    }
                  }
                  return { ...m, toolActivity: activity };
                });
                return { messagesByConversation: { ...s.messagesByConversation, [conversationId]: msgs } };
              });
            },
            onDone: () => {
              set((s) => {
                const next = { ...s.subscribedRunIds };
                delete next[run.run_id];
                return { isStreaming: false, streamingConversationId: null, abortController: null, activeRunId: null, subscribedRunIds: next };
              });
            },
            onError: (msg) => {
              set((s) => {
                const next = { ...s.subscribedRunIds };
                delete next[run.run_id];
                return { isStreaming: false, streamingConversationId: null, abortController: null, activeRunId: null, subscribedRunIds: next, sessionError: msg };
              });
              void get().loadMessages(conversationId);
            },
          },
          controller.signal,
        );
      },

      cancelStream: () => {
        const { streamingConversationId, messagesByConversation, abortController, activeRunId } = get();
        abortController?.abort();
        if (activeRunId) void cancelChatRun(activeRunId).catch(() => undefined);
        if (streamingConversationId) {
          const msgs = (messagesByConversation[streamingConversationId] ?? []).map((m) =>
            m.status === 'streaming'
              ? {
                  ...m,
                  status: 'done' as const,
                  toolActivity: m.toolActivity?.map((t) => ({ ...t, done: true })),
                }
              : m,
          );
          set((s) => ({
            messagesByConversation: { ...s.messagesByConversation, [streamingConversationId]: msgs },
            isStreaming: false,
            streamingConversationId: null,
            abortController: null,
            activeRunId: null,
            subscribedRunIds: activeRunId ? Object.fromEntries(Object.entries(s.subscribedRunIds).filter(([id]) => id !== activeRunId)) : s.subscribedRunIds,
          }));
        } else {
          set((s) => ({
            isStreaming: false,
            streamingConversationId: null,
            abortController: null,
            activeRunId: null,
            subscribedRunIds: activeRunId ? Object.fromEntries(Object.entries(s.subscribedRunIds).filter(([id]) => id !== activeRunId)) : s.subscribedRunIds,
          }));
        }
      },

      sendMessage: async (prompt) => {
        const {
          activeConversationId, useStream, enableThinking, enableRag, enableTools, enableWeb,
          activeSkillId, activeModelId, conversations,
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
            isStreaming: true,
            streamingConversationId: activeConversationId,
          };
        });

        const updateAssistant = (
          patch: Partial<Pick<ChatMessage, 'content' | 'thinkingBlocks' | 'status' | 'toolActivity'>>,
        ) => {
          set((s) => {
            const msgs = (s.messagesByConversation[activeConversationId] ?? []).map((m) =>
              m.id === assistantId ? { ...m, ...patch } : m,
            );
            const last = msgs[msgs.length - 1];
            return {
              messagesByConversation: { ...s.messagesByConversation, [activeConversationId]: msgs },
              conversations: sortConversations(
                s.conversations.map((c) =>
                  c.id !== activeConversationId
                    ? c
                    : {
                        ...c,
                        lastMessagePreview: last?.content.slice(0, 80) ?? c.lastMessagePreview,
                        messageCount: msgs.length,
                        updatedAt: nowIso(),
                      },
                ),
              ),
            };
          });
        };

        const finishStreaming = (
          runId: string | null,
          patch: Partial<Pick<ChatMessage, 'content' | 'status' | 'toolActivity'>>,
        ) => {
          updateAssistant({ status: 'done', ...patch });
          set((s) => {
            const next = { ...s.subscribedRunIds };
            if (runId) delete next[runId];
            return { isStreaming: false, streamingConversationId: null, abortController: null, activeRunId: null, subscribedRunIds: next };
          });
        };

        let runHandledByExistingSubscription = false;

        try {
          if (useStream) {
            const controller = new AbortController();
            set({ abortController: controller });
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
              skill_id: activeSkillId !== null && activeSkillId !== 'none' ? activeSkillId : undefined,
              disable_skill: activeSkillId === 'none',
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
              activeRunId: run.run_id,
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
                onToolStart: (toolName, input) => {
                  set((s) => {
                    const msgs = (s.messagesByConversation[activeConversationId] ?? []).map((m) =>
                      m.id !== assistantId
                        ? m
                        : {
                            ...m,
                            thinkingMode: 'tool' as const,
                            toolActivity: [...(m.toolActivity ?? []), { name: toolName, done: false, input }],
                          },
                    );
                    return { messagesByConversation: { ...s.messagesByConversation, [activeConversationId]: msgs } };
                  });
                },
                onToolResult: (toolName, _input, result, isError, durationMs) => {
                  set((s) => {
                    const msgs = (s.messagesByConversation[activeConversationId] ?? []).map((m) => {
                      if (m.id !== assistantId) return m;
                      const activity = [...(m.toolActivity ?? [])];
                      for (let i = activity.length - 1; i >= 0; i--) {
                        if (activity[i].name === toolName && !activity[i].done) {
                          activity[i] = { ...activity[i], done: true, result, isError, durationMs };
                          break;
                        }
                      }
                      return { ...m, toolActivity: activity };
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
                onDone: (conv?: ConversationUpdate) => {
                  if (conv) {
                    set((s) => ({
                      conversations: sortConversations(
                        s.conversations.map((c) =>
                          c.id !== activeConversationId
                            ? c
                            : {
                                ...c,
                                title: conv.title,
                                lastMessagePreview: conv.last_message_preview,
                                messageCount: conv.message_count,
                                updatedAt: conv.updated_at,
                              },
                        ),
                      ),
                    }));
                  }
                  const currentMsg = (get().messagesByConversation[activeConversationId] ?? [])
                    .find((m) => m.id === assistantId);
                  finishStreaming(run.run_id, {
                    content: textBuffer || ASSISTANT_FALLBACK_TEXT.empty,
                    toolActivity: currentMsg?.toolActivity?.map((t) => ({ ...t, done: true })),
                  });
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
              if (!s.isStreaming) return s;
              const sid = s.streamingConversationId;
              const msgs = sid
                ? (s.messagesByConversation[sid] ?? []).map((m) =>
                    m.status === 'streaming' ? { ...m, status: 'done' as const } : m,
                  )
                : null;
              return {
                isStreaming: false,
                streamingConversationId: null,
                abortController: null,
                activeRunId: null,
                subscribedRunIds: s.activeRunId
                  ? Object.fromEntries(Object.entries(s.subscribedRunIds).filter(([id]) => id !== s.activeRunId))
                  : s.subscribedRunIds,
                ...(sid && msgs ? { messagesByConversation: { ...s.messagesByConversation, [sid]: msgs } } : {}),
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
