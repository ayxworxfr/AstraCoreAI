import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { normalizeError } from '../services/apiClient';
import { deleteSession, fetchSessionMessages, sendChatMessage, sendChatStream } from '../services/chatService';
import type { ChatMessage, ConversationMeta } from '../types/chat';
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

type ChatStore = {
  // State
  conversations: ConversationMeta[];
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
  abortController: AbortController | null;
  sessionError: string | null;   // 当前会话错误，不持久化，刷新自动清除

  // Actions
  createConversation: () => string;
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
  setSessionError: (msg: string | null) => void;
  deleteMessage: (conversationId: string, messageId: string) => void;
  sendMessage: (prompt: string) => Promise<void>;
  cancelStream: () => void;
  /** 首次打开会话时加载最新 PAGE_SIZE 条消息 */
  loadMessages: (convId: string) => Promise<void>;
  /** 向上滚动时加载更早的消息，返回是否加载了新消息 */
  loadMoreMessages: (convId: string) => Promise<boolean>;
};

const initialConversation = buildConversation();

export const useChatStore = create<ChatStore>()(
  persist(
    (set, get) => ({
      conversations: [initialConversation],
      activeConversationId: initialConversation.id,
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
      abortController: null,
      sessionError: null,

      createConversation: () => {
        // 新会话继承当前全局默认 skill（快照），后续更改不影响此会话
        const defaultSkillId = useSkillStore.getState().settings.default_skill_id || null;
        const c: ConversationMeta = { ...buildConversation(), skillId: defaultSkillId };
        set((s) => ({
          conversations: sortConversations([c, ...s.conversations]),
          activeConversationId: c.id,
          activeSkillId: defaultSkillId,
        }));
        return c.id;
      },

      switchConversation: (id) => {
        const conv = get().conversations.find((c) => c.id === id);
        set({ activeConversationId: id, activeSkillId: conv?.skillId ?? null });
        // 若该会话消息未加载，异步从后端拉取
        if (!get().messagesByConversation[id]) {
          void get().loadMessages(id);
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
      },

      deleteConversation: (id) => {
        const { conversations, activeConversationId } = get();
        const remaining = conversations.filter((c) => c.id !== id);
        if (remaining.length === 0) {
          const fresh = buildConversation();
          set((s) => {
            const msgs = { ...s.messagesByConversation };
            delete msgs[id];
            return {
              conversations: [fresh],
              messagesByConversation: { ...msgs, [fresh.id]: [] },
              activeConversationId: fresh.id,
            };
          });
        } else {
          const nextId =
            activeConversationId === id
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
        }
        // 异步清理后端记忆，失败静默忽略（不影响前端状态）
        void deleteSession(id).catch(() => undefined);
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
      },

      togglePin: (id) => {
        set((s) => ({
          conversations: sortConversations(
            s.conversations.map((c) => (c.id === id ? { ...c, pinned: !c.pinned } : c)),
          ),
        }));
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
          // 同步写入当前会话的独立 skill，使切换会话后仍能恢复
          conversations: s.conversations.map((c) =>
            c.id === activeConversationId ? { ...c, skillId: id } : c,
          ),
        }));
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
          const messages: ChatMessage[] = result.messages.map((m, i) => ({
            id: `hist-${convId}-${i}`,
            role: m.role,
            content: m.content,
            status: 'done' as const,
            createdAt: new Date().toISOString(),
          }));
          set((s) => ({
            messagesByConversation: { ...s.messagesByConversation, [convId]: messages },
            messagesOffset: { ...s.messagesOffset, [convId]: result.messages.length },
            hasMoreMessages: { ...s.hasMoreMessages, [convId]: result.has_more },
            isLoadingMessages: false,
          }));
        } catch {
          // 加载失败时初始化为空数组，避免反复重试
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
          const older: ChatMessage[] = result.messages.map((m, i) => ({
            id: `hist-${convId}-${currentOffset + i}`,
            role: m.role,
            content: m.content,
            status: 'done' as const,
            createdAt: new Date().toISOString(),
          }));
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

      cancelStream: () => {
        const { streamingConversationId, messagesByConversation, abortController } = get();
        abortController?.abort();
        if (streamingConversationId) {
          const msgs = (messagesByConversation[streamingConversationId] ?? []).map((m) =>
            m.status === 'streaming'
              ? {
                  ...m,
                  status: 'done' as const,
                  // 未完成的工具调用一并标为已完成，防止 badge spinner 残留
                  toolActivity: m.toolActivity?.map((t) => ({ ...t, done: true })),
                }
              : m,
          );
          set((s) => ({
            messagesByConversation: { ...s.messagesByConversation, [streamingConversationId]: msgs },
            isStreaming: false,
            streamingConversationId: null,
            abortController: null,
          }));
        } else {
          set({ isStreaming: false, streamingConversationId: null, abortController: null });
        }
      },

      sendMessage: async (prompt) => {
        const { activeConversationId, useStream, enableThinking, enableRag, enableTools, enableWeb, activeSkillId, conversations } = get();
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

        const finishStreaming = (patch: Partial<Pick<ChatMessage, 'content' | 'status' | 'toolActivity'>>) => {
          updateAssistant({ status: 'done', ...patch });
          set({ isStreaming: false, streamingConversationId: null, abortController: null });
        };

        try {
          if (useStream) {
            const controller = new AbortController();
            set({ abortController: controller });
            let textBuffer = '';
            // 每个元素是一轮思考的累积文本
            const thinkingBlocks: string[] = [];

            const getUpdatedBlocks = () => (thinkingBlocks.length ? [...thinkingBlocks] : undefined);

            await sendChatStream(
              {
                message: trimmed,
                session_id: activeConversationId,
                enable_thinking: enableThinking,
                enable_rag: enableRag,
                use_tools: enableTools || enableWeb,
                enable_web: enableWeb,
                skill_id: activeSkillId !== null && activeSkillId !== 'none' ? activeSkillId : undefined,
                disable_skill: activeSkillId === 'none',
              },
              {
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
                      // 从后往前找最近一个同名且未完成的条目，标记为完成并附上结果
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
                  // 新一轮思考开始：标记所有工具为完成，push 新思考块占位
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
                  // 首条 thinking delta 若没有收到 thinking_start，自动初始化第一块
                  if (thinkingBlocks.length === 0) thinkingBlocks.push('');
                  thinkingBlocks[thinkingBlocks.length - 1] += delta;
                  updateAssistant({ thinkingBlocks: getUpdatedBlocks(), status: 'streaming' });
                },
                onDone: () => {
                  const currentMsg = (get().messagesByConversation[activeConversationId] ?? [])
                    .find((m) => m.id === assistantId);
                  finishStreaming({
                    content: textBuffer || ASSISTANT_FALLBACK_TEXT.empty,
                    toolActivity: currentMsg?.toolActivity?.map((t) => ({ ...t, done: true })),
                  });
                },
                onError: (msg) => {
                  // 保留已接收的部分内容，不用错误文本覆盖；用 sessionError 通知用户
                  finishStreaming({ content: textBuffer || ASSISTANT_FALLBACK_TEXT.interrupted });
                  set({ sessionError: msg });
                },
              },
              controller.signal,
            );
          } else {
            const res = await sendChatMessage({
              message: trimmed,
              session_id: activeConversationId,
              enable_rag: enableRag,
            });
            updateAssistant({ content: res.message || ASSISTANT_FALLBACK_TEXT.empty, status: 'done' });
          }
        } catch (e) {
          const err = e as Error;
          const isAbort = err.name === 'AbortError' || /abort/i.test(err.message ?? '');
          if (!isAbort) {
            // 保留已接收内容，将真实错误发到 sessionError（不持久化）
            updateAssistant({ status: 'done' });
            set({ sessionError: normalizeError(e) });
          }
        } finally {
          set((s) => {
            if (!s.isStreaming) return s;
            // 兜底：流意外终止时同步清理消息状态，防止 status:'streaming' 残留
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
              ...(sid && msgs ? { messagesByConversation: { ...s.messagesByConversation, [sid]: msgs } } : {}),
            };
          });
        }
      },
    }),
    {
      name: 'astracore.chat.v1',
      partialize: (s) => ({
        conversations: s.conversations,
        activeConversationId: s.activeConversationId,
        // 消息历史不持久化到 localStorage（按需从后端加载），只保留会话元数据
        useStream: s.useStream,
        enableThinking: s.enableThinking,
        enableRag: s.enableRag,
        enableTools: s.enableTools,
        enableWeb: s.enableWeb,
        activeSkillId: s.activeSkillId,
        // sessionError / messagesByConversation / 分页状态故意不持久化
      }),
    },
  ),
);
