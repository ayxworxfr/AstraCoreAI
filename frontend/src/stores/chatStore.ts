import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { normalizeError } from '../services/apiClient';
import { deleteSession, sendChatMessage, sendChatStream } from '../services/chatService';
import type { ChatMessage, ConversationMeta } from '../types/chat';

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

type ChatStore = {
  // State
  conversations: ConversationMeta[];
  activeConversationId: string;
  messagesByConversation: Record<string, ChatMessage[]>;
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
};

const initialConversation = buildConversation();

export const useChatStore = create<ChatStore>()(
  persist(
    (set, get) => ({
      conversations: [initialConversation],
      activeConversationId: initialConversation.id,
      messagesByConversation: { [initialConversation.id]: [] },
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
        const c = buildConversation();
        set((s) => ({
          conversations: sortConversations([c, ...s.conversations]),
          messagesByConversation: { ...s.messagesByConversation, [c.id]: [] },
          activeConversationId: c.id,
        }));
        return c.id;
      },

      switchConversation: (id) => {
        if (get().isStreaming) return false;
        set({ activeConversationId: id });
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
        set((s) => ({
          messagesByConversation: { ...s.messagesByConversation, [id]: [] },
          conversations: sortConversations(
            s.conversations.map((c) =>
              c.id === id
                ? { ...c, lastMessagePreview: '', messageCount: 0, updatedAt: nowIso() }
                : c,
            ),
          ),
        }));
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
      setActiveSkillId: (id) => set({ activeSkillId: id }),
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

      cancelStream: () => {
        const { streamingConversationId, messagesByConversation, abortController } = get();
        abortController?.abort();
        if (streamingConversationId) {
          const msgs = (messagesByConversation[streamingConversationId] ?? []).map((m) =>
            m.status === 'streaming' ? { ...m, status: 'done' as const } : m,
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
        if (!trimmed || get().isStreaming) return;

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
                onToolUse: (toolName) => {
                  set((s) => {
                    const msgs = (s.messagesByConversation[activeConversationId] ?? []).map((m) =>
                      m.id !== assistantId
                        ? m
                        : {
                            ...m,
                            thinkingMode: 'tool' as const,
                            toolActivity: [...(m.toolActivity ?? []), { name: toolName, done: false }],
                          },
                    );
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
                  updateAssistant({
                    content: textBuffer || '（空响应）',
                    status: 'done',
                    toolActivity: currentMsg?.toolActivity?.map((t) => ({ ...t, done: true })),
                  });
                  set({ isStreaming: false, streamingConversationId: null, abortController: null });
                },
                onError: (msg) => {
                  // 保留已接收的部分内容，不用错误文本覆盖；用 sessionError 通知用户
                  updateAssistant({ content: textBuffer || '（请求中断）', status: 'done' });
                  set({ isStreaming: false, streamingConversationId: null, abortController: null, sessionError: msg });
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
            updateAssistant({ content: res.message || '（空响应）', status: 'done' });
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
          set((s) =>
            s.isStreaming
              ? { isStreaming: false, streamingConversationId: null, abortController: null }
              : s,
          );
        }
      },
    }),
    {
      name: 'astracore.chat.v1',
      partialize: (s) => ({
        conversations: s.conversations,
        activeConversationId: s.activeConversationId,
        // 持久化时将所有 streaming/error 状态净化为 done，防止刷新后出现残留错误提示
        messagesByConversation: Object.fromEntries(
          Object.entries(s.messagesByConversation).map(([convId, msgs]) => [
            convId,
            msgs.map((m) =>
              m.status === 'error' || m.status === 'streaming'
                ? { ...m, status: 'done' as const }
                : m,
            ),
          ]),
        ),
        useStream: s.useStream,
        enableThinking: s.enableThinking,
        enableRag: s.enableRag,
        enableTools: s.enableTools,
        enableWeb: s.enableWeb,
        activeSkillId: s.activeSkillId,
        // sessionError 故意不持久化，刷新自动清除
      }),
    },
  ),
);
