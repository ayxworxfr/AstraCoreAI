# AstraCoreAI 前端重写实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从零重写 AstraCoreAI 前端 SPA，使用 Ant Design 5 + Ant Design X 构建专业、美观的 AI 对话界面，支持深/浅双主题，AI 消息 Markdown 渲染。

**Architecture:** React 18 + Vite 5 + TypeScript。全局状态用 Zustand（含 localStorage 持久化）。布局用 antd Layout，AI 对话组件用 @ant-design/x（Bubble.List / Sender / Conversations / Welcome / Prompts），Markdown 用 react-markdown + rehype-highlight。后端接口完全兼容，无需改动后端。

**Tech Stack:** antd 5, @ant-design/x, @ant-design/icons, zustand, react-router-dom, react-markdown, remark-gfm, rehype-highlight, highlight.js, axios

---

## File Map

**Create（全部新建）:**
- `frontend/src/main.tsx`
- `frontend/src/app/App.tsx`
- `frontend/src/app/router.tsx`
- `frontend/src/app/theme.ts`
- `frontend/src/types/api.ts`
- `frontend/src/types/chat.ts`
- `frontend/src/services/apiClient.ts`
- `frontend/src/services/chatService.ts`
- `frontend/src/services/ragService.ts`
- `frontend/src/services/healthService.ts`
- `frontend/src/stores/settingsStore.ts`
- `frontend/src/stores/chatStore.ts`
- `frontend/src/layouts/AppShell.tsx`
- `frontend/src/components/chat/MarkdownContent.tsx`
- `frontend/src/components/chat/ConversationSidebar.tsx`
- `frontend/src/components/chat/ChatMain.tsx`
- `frontend/src/components/rag/RagQueryPanel.tsx`
- `frontend/src/components/rag/RagResultList.tsx`
- `frontend/src/components/system/HealthStatusCard.tsx`
- `frontend/src/pages/ChatPage.tsx`
- `frontend/src/pages/RagPage.tsx`
- `frontend/src/pages/SystemPage.tsx`

**Modify:**
- `frontend/package.json`

---

## Task 1: 更新依赖

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: 更新 package.json**

替换 `frontend/package.json` 内容：

```json
{
  "name": "astracore-frontend",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "@ant-design/icons": "^5.6.1",
    "@ant-design/x": "^1.2.0",
    "antd": "^5.24.6",
    "axios": "^1.8.2",
    "highlight.js": "^11.11.1",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-markdown": "^9.0.3",
    "react-router-dom": "^6.30.1",
    "rehype-highlight": "^7.0.2",
    "remark-gfm": "^4.0.1",
    "zustand": "^5.0.3"
  },
  "devDependencies": {
    "@types/node": "^22.13.9",
    "@types/react": "^18.3.18",
    "@types/react-dom": "^18.3.5",
    "@vitejs/plugin-react": "^4.3.4",
    "typescript": "^5.7.3",
    "vite": "^5.4.14"
  }
}
```

- [ ] **Step 2: 安装依赖**

```bash
cd frontend && npm install
```

预期：无报错，`node_modules/@ant-design/x` 和 `node_modules/antd` 目录存在。

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "chore: update frontend deps to antd5 + @ant-design/x"
```

---

## Task 2: 类型定义 + 服务层

**Files:**
- Create: `frontend/src/types/api.ts`
- Create: `frontend/src/types/chat.ts`
- Create: `frontend/src/services/apiClient.ts`
- Create: `frontend/src/services/chatService.ts`
- Create: `frontend/src/services/ragService.ts`
- Create: `frontend/src/services/healthService.ts`

- [ ] **Step 1: 创建 `frontend/src/types/api.ts`**

```typescript
export type ChatRequest = {
  message: string;
  session_id?: string;
  model?: string;
  temperature?: number;
};

export type ChatResponse = {
  session_id: string;
  message: string;
  model?: string;
  metadata?: Record<string, unknown>;
};

export type RagRetrieveRequest = {
  query: string;
  top_k?: number;
};

export type RagResult = {
  content: string;
  score: number;
  citation?: string;
};

export type RagRetrieveResponse = {
  results: RagResult[];
};

export type HealthResponse = {
  status: string;
};

export type ReadyResponse = {
  ready: boolean;
};

export type ApiErrorResponse = {
  detail: string;
};
```

- [ ] **Step 2: 创建 `frontend/src/types/chat.ts`**

```typescript
export type ConversationMeta = {
  id: string;           // UUID，同时作为后端 session_id
  title: string;
  updatedAt: string;    // ISO string
  lastMessagePreview: string;
  messageCount: number;
  pinned: boolean;
};

export type MessageStatus = 'pending' | 'streaming' | 'done' | 'error';

export type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  status: MessageStatus;
  createdAt: string;    // ISO string，避免 Date 序列化问题
};
```

- [ ] **Step 3: 创建 `frontend/src/services/apiClient.ts`**

```typescript
import axios, { type AxiosError } from 'axios';
import type { ApiErrorResponse } from '../types/api';

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
  timeout: 60000,
  headers: { 'Content-Type': 'application/json' },
});

export function normalizeError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const e = error as AxiosError<ApiErrorResponse>;
    return e.response?.data?.detail ?? e.message;
  }
  if (error instanceof Error) return error.message;
  return '未知错误';
}
```

- [ ] **Step 4: 创建 `frontend/src/services/chatService.ts`**

```typescript
import type { ChatRequest, ChatResponse } from '../types/api';
import { apiClient, normalizeError } from './apiClient';

type StreamHandlers = {
  onMessage: (delta: string) => void;
  onDone: () => void;
  onError: (msg: string) => void;
};

export async function sendChatMessage(payload: ChatRequest): Promise<ChatResponse> {
  const { data } = await apiClient.post<ChatResponse>('/api/v1/chat/', {
    ...payload,
    stream: false,
  });
  return data;
}

function parseBlock(block: string, handlers: StreamHandlers): void {
  let eventType = 'message';
  const dataLines: string[] = [];
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) eventType = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  const data = dataLines.join('\n');
  if (eventType === 'message') handlers.onMessage(data);
  else if (eventType === 'done') handlers.onDone();
  else if (eventType === 'error') handlers.onError(data || '流式请求失败');
}

export async function sendChatStream(
  payload: ChatRequest,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(
      `${import.meta.env.VITE_API_BASE_URL ?? ''}/api/v1/chat/stream`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, stream: true }),
        signal,
      },
    );
  } catch (e) {
    if ((e as Error).name === 'AbortError') return;
    handlers.onError(normalizeError(e));
    return;
  }

  if (!response.ok) {
    handlers.onError(`HTTP ${response.status}`);
    return;
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop() ?? '';
    for (const block of parts) parseBlock(block, handlers);
  }

  if (buffer.trim()) parseBlock(buffer, handlers);
}
```

- [ ] **Step 5: 创建 `frontend/src/services/ragService.ts`**

```typescript
import type { RagRetrieveRequest, RagRetrieveResponse } from '../types/api';
import { apiClient } from './apiClient';

export async function ragRetrieve(payload: RagRetrieveRequest): Promise<RagRetrieveResponse> {
  const { data } = await apiClient.post<RagRetrieveResponse>('/api/v1/rag/retrieve', payload);
  return data;
}
```

- [ ] **Step 6: 创建 `frontend/src/services/healthService.ts`**

```typescript
import type { HealthResponse, ReadyResponse } from '../types/api';
import { apiClient } from './apiClient';

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await apiClient.get<HealthResponse>('/health/');
  return data;
}

export async function getReady(): Promise<ReadyResponse> {
  const { data } = await apiClient.get<ReadyResponse>('/health/ready');
  return data;
}
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types frontend/src/services
git commit -m "feat: add types and service layer"
```

---

## Task 3: 主题配置 + settingsStore + App 骨架

**Files:**
- Create: `frontend/src/app/theme.ts`
- Create: `frontend/src/stores/settingsStore.ts`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/src/app/router.tsx`
- Create: `frontend/src/layouts/AppShell.tsx`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/pages/ChatPage.tsx` （空占位）
- Create: `frontend/src/pages/RagPage.tsx` （空占位）
- Create: `frontend/src/pages/SystemPage.tsx` （空占位）

- [ ] **Step 1: 创建 `frontend/src/app/theme.ts`**

```typescript
import { theme } from 'antd';
import type { ThemeConfig } from 'antd';

const baseToken = {
  colorPrimary: '#1677ff',
  borderRadius: 8,
  fontFamily: "'PingFang SC', 'Microsoft YaHei', 'Segoe UI', sans-serif",
};

export const lightTheme: ThemeConfig = {
  token: {
    ...baseToken,
    colorBgBase: '#f5f7fa',
    colorBgContainer: '#ffffff',
    colorBorderSecondary: '#e8edf2',
  },
};

export const darkTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    ...baseToken,
    colorBgBase: '#0d1117',
    colorBgContainer: '#161b22',
    colorBorderSecondary: '#30363d',
  },
};
```

- [ ] **Step 2: 创建 `frontend/src/stores/settingsStore.ts`**

```typescript
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type Theme = 'light' | 'dark';

type SettingsState = {
  theme: Theme;
  toggleTheme: () => void;
};

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set, get) => ({
      theme: 'dark' as Theme,
      toggleTheme: () => set({ theme: get().theme === 'dark' ? 'light' : 'dark' }),
    }),
    { name: 'astracore.settings.v1' },
  ),
);
```

- [ ] **Step 3: 创建空占位页面 `frontend/src/pages/ChatPage.tsx`**

```typescript
export default function ChatPage(): JSX.Element {
  return <div style={{ padding: 24 }}>Chat（开发中）</div>;
}
```

- [ ] **Step 4: 创建空占位页面 `frontend/src/pages/RagPage.tsx`**

```typescript
export default function RagPage(): JSX.Element {
  return <div style={{ padding: 24 }}>RAG（开发中）</div>;
}
```

- [ ] **Step 5: 创建空占位页面 `frontend/src/pages/SystemPage.tsx`**

```typescript
export default function SystemPage(): JSX.Element {
  return <div style={{ padding: 24 }}>系统（开发中）</div>;
}
```

- [ ] **Step 6: 创建 `frontend/src/layouts/AppShell.tsx`**

```typescript
import { Layout, Menu, Button, Typography, Flex } from 'antd';
import { BulbOutlined, MoonOutlined } from '@ant-design/icons';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useSettingsStore } from '../stores/settingsStore';

const { Header, Content } = Layout;

const NAV_ITEMS = [
  { key: '/chat', label: <NavLink to="/chat">对话</NavLink> },
  { key: '/rag', label: <NavLink to="/rag">RAG</NavLink> },
  { key: '/system', label: <NavLink to="/system">系统</NavLink> },
];

export default function AppShell(): JSX.Element {
  const { theme, toggleTheme } = useSettingsStore();
  const location = useLocation();

  return (
    <Layout style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <Header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 24px',
          flexShrink: 0,
        }}
      >
        <Typography.Text strong style={{ color: '#fff', fontSize: 18, letterSpacing: '-0.01em' }}>
          AstraCoreAI
        </Typography.Text>
        <Flex align="center" gap={8}>
          <Menu
            theme="dark"
            mode="horizontal"
            selectedKeys={[location.pathname]}
            items={NAV_ITEMS}
            style={{ background: 'transparent', border: 'none', minWidth: 220 }}
          />
          <Button
            type="text"
            icon={theme === 'dark' ? <BulbOutlined /> : <MoonOutlined />}
            onClick={toggleTheme}
            style={{ color: '#fff' }}
            title={theme === 'dark' ? '切换浅色' : '切换深色'}
          />
        </Flex>
      </Header>
      <Content style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <Outlet />
      </Content>
    </Layout>
  );
}
```

- [ ] **Step 7: 创建 `frontend/src/app/router.tsx`**

```typescript
import { createBrowserRouter, Navigate } from 'react-router-dom';
import AppShell from '../layouts/AppShell';
import ChatPage from '../pages/ChatPage';
import RagPage from '../pages/RagPage';
import SystemPage from '../pages/SystemPage';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: 'chat', element: <ChatPage /> },
      { path: 'rag', element: <RagPage /> },
      { path: 'system', element: <SystemPage /> },
    ],
  },
]);
```

- [ ] **Step 8: 创建 `frontend/src/app/App.tsx`**

```typescript
import { RouterProvider } from 'react-router-dom';
import { router } from './router';

export default function App(): JSX.Element {
  return <RouterProvider router={router} />;
}
```

- [ ] **Step 9: 创建 `frontend/src/main.tsx`**

```typescript
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './app/App';
import { useSettingsStore } from './stores/settingsStore';
import { lightTheme, darkTheme } from './app/theme';

function Root(): JSX.Element {
  const theme = useSettingsStore((s) => s.theme);
  return (
    <ConfigProvider locale={zhCN} theme={theme === 'dark' ? darkTheme : lightTheme}>
      <App />
    </ConfigProvider>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
```

- [ ] **Step 10: 验证骨架可运行**

```bash
cd frontend && npm run dev
```

预期：浏览器打开 `http://localhost:5173`，可看到深色顶栏，「对话」「RAG」「系统」导航，点击主题按钮可切换深/浅色。

- [ ] **Step 11: Commit**

```bash
git add frontend/src
git commit -m "feat: app shell with antd theme and routing"
```

---

## Task 4: chatStore

**Files:**
- Create: `frontend/src/stores/chatStore.ts`

- [ ] **Step 1: 创建 `frontend/src/stores/chatStore.ts`**

```typescript
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { ConversationMeta, ChatMessage } from '../types/chat';
import { sendChatMessage, sendChatStream } from '../services/chatService';
import { normalizeError } from '../services/apiClient';

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
  abortController: AbortController | null;

  // Actions
  createConversation: () => string;
  switchConversation: (id: string) => boolean;
  renameConversation: (id: string, title: string) => void;
  deleteConversation: (id: string) => void;
  clearConversation: (id: string) => void;
  togglePin: (id: string) => void;
  setUseStream: (value: boolean) => void;
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
      abortController: null,

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
          return;
        }
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
      },

      togglePin: (id) => {
        set((s) => ({
          conversations: sortConversations(
            s.conversations.map((c) => (c.id === id ? { ...c, pinned: !c.pinned } : c)),
          ),
        }));
      },

      setUseStream: (value) => set({ useStream: value }),

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
        const { activeConversationId, useStream, conversations } = get();
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
        const assistantMsg: ChatMessage = {
          id: assistantId,
          role: 'assistant',
          content: '',
          status: 'streaming',
          createdAt: nowIso(),
        };

        // Append user + placeholder assistant message
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

        const updateAssistant = (content: string, status: ChatMessage['status']) => {
          set((s) => {
            const msgs = (s.messagesByConversation[activeConversationId] ?? []).map((m) =>
              m.id === assistantId ? { ...m, content, status } : m,
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
            let buffer = '';
            await sendChatStream(
              { message: trimmed, session_id: activeConversationId },
              {
                onMessage: (delta) => {
                  buffer += delta;
                  updateAssistant(buffer, 'streaming');
                },
                onDone: () => {
                  updateAssistant(buffer || '（空响应）', 'done');
                  set({ isStreaming: false, streamingConversationId: null, abortController: null });
                },
                onError: (msg) => {
                  updateAssistant(msg, 'error');
                  set({ isStreaming: false, streamingConversationId: null, abortController: null });
                },
              },
              controller.signal,
            );
          } else {
            const res = await sendChatMessage({
              message: trimmed,
              session_id: activeConversationId,
            });
            updateAssistant(res.message || '（空响应）', 'done');
          }
        } catch (e) {
          updateAssistant(normalizeError(e), 'error');
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
      // abortController 不持久化
      partialize: (s) => ({
        conversations: s.conversations,
        activeConversationId: s.activeConversationId,
        messagesByConversation: s.messagesByConversation,
        useStream: s.useStream,
      }),
    },
  ),
);
```

- [ ] **Step 2: 验证编译**

```bash
cd frontend && npx tsc --noEmit
```

预期：无 TypeScript 报错。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/stores/chatStore.ts frontend/src/stores/settingsStore.ts
git commit -m "feat: chatStore and settingsStore with zustand persist"
```

---

## Task 5: Chat UI 组件

**Files:**
- Create: `frontend/src/components/chat/MarkdownContent.tsx`
- Create: `frontend/src/components/chat/ConversationSidebar.tsx`
- Create: `frontend/src/components/chat/ChatMain.tsx`
- Modify: `frontend/src/pages/ChatPage.tsx`

- [ ] **Step 1: 创建 `frontend/src/components/chat/MarkdownContent.tsx`**

```typescript
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import 'highlight.js/styles/github-dark.css';
import type { Components } from 'react-markdown';

type Props = { content: string };

const components: Components = {
  code({ className, children, ...props }) {
    const isBlock = Boolean(className?.includes('language-'));
    if (isBlock) {
      return (
        <pre
          style={{
            margin: '8px 0',
            borderRadius: 6,
            overflow: 'auto',
            fontSize: 13,
          }}
        >
          <code className={className} {...props}>
            {children}
          </code>
        </pre>
      );
    }
    return (
      <code
        style={{
          background: 'rgba(127,127,127,0.15)',
          padding: '2px 6px',
          borderRadius: 4,
          fontSize: '0.88em',
          fontFamily: 'monospace',
        }}
        {...props}
      >
        {children}
      </code>
    );
  },
  p({ children }) {
    return <p style={{ margin: '6px 0', lineHeight: 1.7 }}>{children}</p>;
  },
  ul({ children }) {
    return <ul style={{ margin: '6px 0', paddingLeft: 20 }}>{children}</ul>;
  },
  ol({ children }) {
    return <ol style={{ margin: '6px 0', paddingLeft: 20 }}>{children}</ol>;
  },
  blockquote({ children }) {
    return (
      <blockquote
        style={{
          borderLeft: '3px solid #1677ff',
          margin: '8px 0',
          paddingLeft: 12,
          color: '#888',
        }}
      >
        {children}
      </blockquote>
    );
  },
};

export default function MarkdownContent({ content }: Props): JSX.Element {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={components}
    >
      {content}
    </ReactMarkdown>
  );
}
```

- [ ] **Step 2: 创建 `frontend/src/components/chat/ConversationSidebar.tsx`**

```typescript
import { useState } from 'react';
import { Button, Input, Flex, Typography, message as antMessage } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { Conversations } from '@ant-design/x';
import type { ConversationsProps } from '@ant-design/x';
import { useChatStore } from '../../stores/chatStore';

type ConversationItem = NonNullable<ConversationsProps['items']>[number];

export default function ConversationSidebar(): JSX.Element {
  const {
    conversations,
    activeConversationId,
    isStreaming,
    createConversation,
    switchConversation,
    renameConversation,
    deleteConversation,
    clearConversation,
    togglePin,
  } = useChatStore();

  const [search, setSearch] = useState('');

  const filtered = search.trim()
    ? conversations.filter((c) => c.title.toLowerCase().includes(search.trim().toLowerCase()))
    : conversations;

  const items: ConversationItem[] = filtered.map((c) => ({
    key: c.id,
    label: c.title,
    timestamp: new Date(c.updatedAt).getTime(),
  }));

  const handleActiveChange = (key: string) => {
    if (!switchConversation(key)) {
      void antMessage.warning('响应生成中，请先取消');
    }
  };

  return (
    <Flex
      vertical
      style={{ height: '100%', padding: '12px 10px', overflow: 'hidden' }}
      gap={10}
    >
      <Button
        type="primary"
        icon={<PlusOutlined />}
        block
        disabled={isStreaming}
        onClick={() => createConversation()}
      >
        新建会话
      </Button>

      <Input.Search
        placeholder="搜索会话"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        allowClear
        size="small"
      />

      {filtered.length === 0 && (
        <Typography.Text type="secondary" style={{ fontSize: 13, textAlign: 'center', padding: 8 }}>
          {search ? '无匹配结果' : '暂无会话'}
        </Typography.Text>
      )}

      <div style={{ flex: 1, overflow: 'auto' }}>
        <Conversations
          items={items}
          activeKey={activeConversationId}
          onActiveChange={handleActiveChange}
          menu={(item: ConversationItem) => {
            const conv = conversations.find((c) => c.id === item.key);
            return {
              items: [
                { key: 'pin', label: conv?.pinned ? '取消置顶' : '置顶' },
                { key: 'rename', label: '重命名' },
                { key: 'clear', label: '清空消息' },
                { key: 'delete', label: '删除', danger: true },
              ],
              onClick: ({ key }: { key: string }) => {
                const id = String(item.key);
                if (key === 'pin') togglePin(id);
                if (key === 'rename') {
                  const current = conversations.find((c) => c.id === id);
                  const newTitle = window.prompt('请输入新标题', current?.title ?? '');
                  if (newTitle?.trim()) renameConversation(id, newTitle.trim());
                }
                if (key === 'clear') clearConversation(id);
                if (key === 'delete') deleteConversation(id);
              },
            };
          }}
        />
      </div>
    </Flex>
  );
}
```

- [ ] **Step 3: 创建 `frontend/src/components/chat/ChatMain.tsx`**

```typescript
import { Bubble, Sender, Welcome, Prompts } from '@ant-design/x';
import type { BubbleProps } from '@ant-design/x';
import { RobotOutlined, UserOutlined } from '@ant-design/icons';
import { Flex, Switch, Typography, Alert } from 'antd';
import { useChatStore } from '../../stores/chatStore';
import MarkdownContent from './MarkdownContent';

const SUGGESTED_PROMPTS = [
  { key: '1', label: '你能做什么？' },
  { key: '2', label: 'RAG 检索怎么用？' },
  { key: '3', label: '工具调用如何配置？' },
];

type RolesType = Record<string, BubbleProps & { placement?: 'start' | 'end' }>;

const roles: RolesType = {
  user: {
    placement: 'end',
    avatar: { icon: <UserOutlined />, style: { background: '#1677ff' } },
    variant: 'filled' as const,
  },
  assistant: {
    placement: 'start',
    avatar: { icon: <RobotOutlined />, style: { background: '#722ed1' } },
    messageRender: (content) => (
      <MarkdownContent content={typeof content === 'string' ? content : ''} />
    ),
  },
};

export default function ChatMain(): JSX.Element {
  const {
    activeConversationId,
    messagesByConversation,
    isStreaming,
    useStream,
    setUseStream,
    sendMessage,
    cancelStream,
  } = useChatStore();

  const messages = messagesByConversation[activeConversationId] ?? [];

  const bubbleItems = messages.map((m) => ({
    key: m.id,
    role: m.role,
    content: m.content,
    loading: m.status === 'streaming' && m.content.length === 0,
  }));

  const hasError = messages.some((m) => m.status === 'error');
  const lastErrorMsg = [...messages].reverse().find((m) => m.status === 'error');

  return (
    <Flex vertical style={{ height: '100%', overflow: 'hidden' }}>
      {/* Messages area */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {messages.length === 0 ? (
          <Flex
            vertical
            align="center"
            justify="center"
            gap={24}
            style={{ height: '100%', padding: '0 24px' }}
          >
            <Welcome
              icon={<RobotOutlined style={{ fontSize: 52, color: '#1677ff' }} />}
              title="AstraCoreAI"
              description="专业 AI 基础设施，开始你的对话"
            />
            <Prompts
              items={SUGGESTED_PROMPTS}
              onItemClick={({ data }) => {
                if (typeof data.label === 'string') {
                  void sendMessage(data.label);
                }
              }}
            />
          </Flex>
        ) : (
          <Bubble.List
            items={bubbleItems}
            roles={roles}
            autoScroll
            style={{ height: '100%', padding: '16px 24px' }}
          />
        )}
      </div>

      {/* Error banner */}
      {hasError && lastErrorMsg && (
        <div style={{ padding: '0 24px' }}>
          <Alert
            type="error"
            message={lastErrorMsg.content}
            closable
            style={{ marginBottom: 8 }}
          />
        </div>
      )}

      {/* Input area */}
      <div
        style={{
          padding: '12px 24px 16px',
          borderTop: '1px solid rgba(5, 5, 5, 0.06)',
          flexShrink: 0,
        }}
      >
        <Sender
          loading={isStreaming}
          onSubmit={(value) => {
            void sendMessage(value);
          }}
          onCancel={cancelStream}
          placeholder="输入问题，Enter 发送，Shift+Enter 换行"
          actions={(defaultActions) => (
            <Flex align="center" gap={12}>
              <Flex align="center" gap={6}>
                <Switch
                  size="small"
                  checked={useStream}
                  onChange={setUseStream}
                  disabled={isStreaming}
                />
                <Typography.Text type="secondary" style={{ fontSize: 12, userSelect: 'none' }}>
                  流式输出
                </Typography.Text>
              </Flex>
              {defaultActions}
            </Flex>
          )}
        />
      </div>
    </Flex>
  );
}
```

- [ ] **Step 4: 更新 `frontend/src/pages/ChatPage.tsx`**

```typescript
import { Layout } from 'antd';
import ConversationSidebar from '../components/chat/ConversationSidebar';
import ChatMain from '../components/chat/ChatMain';

const { Sider, Content } = Layout;

export default function ChatPage(): JSX.Element {
  return (
    <Layout style={{ height: '100%', overflow: 'hidden' }}>
      <Sider
        width={260}
        style={{
          overflow: 'hidden',
          height: '100%',
          borderRight: '1px solid rgba(5, 5, 5, 0.06)',
        }}
      >
        <ConversationSidebar />
      </Sider>
      <Content style={{ overflow: 'hidden', height: '100%', display: 'flex', flexDirection: 'column' }}>
        <ChatMain />
      </Content>
    </Layout>
  );
}
```

- [ ] **Step 5: 验证 Chat 页功能**

```bash
cd frontend && npm run dev
```

验证：
- 访问 `/chat`，左侧显示「新建会话」按钮和会话列表
- 右侧显示 Welcome 屏和推荐 Prompt 卡片
- 点击推荐 Prompt 触发发送
- 输入框可输入，Enter 发送消息
- 流式开关可切换
- 深/浅主题切换后所有组件跟随

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/chat frontend/src/pages/ChatPage.tsx
git commit -m "feat: chat UI with Bubble/Sender/Conversations and Markdown rendering"
```

---

## Task 6: RAG 页面

**Files:**
- Create: `frontend/src/components/rag/RagQueryPanel.tsx`
- Create: `frontend/src/components/rag/RagResultList.tsx`
- Modify: `frontend/src/pages/RagPage.tsx`

- [ ] **Step 1: 创建 `frontend/src/components/rag/RagQueryPanel.tsx`**

```typescript
import { Form, Input, InputNumber, Button, Card } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { ragRetrieve } from '../../services/ragService';
import { normalizeError } from '../../services/apiClient';
import type { RagResult } from '../../types/api';

type FormValues = {
  query: string;
  top_k: number;
};

type Props = {
  onResults: (results: RagResult[]) => void;
  loading: boolean;
  onLoadingChange: (loading: boolean) => void;
  onError: (msg: string | null) => void;
};

export default function RagQueryPanel({ onResults, loading, onLoadingChange, onError }: Props): JSX.Element {
  const [form] = Form.useForm<FormValues>();

  const handleFinish = async (values: FormValues) => {
    onLoadingChange(true);
    onError(null);
    try {
      const res = await ragRetrieve({ query: values.query, top_k: values.top_k });
      onResults(res.results);
    } catch (e) {
      onError(normalizeError(e));
      onResults([]);
    } finally {
      onLoadingChange(false);
    }
  };

  return (
    <Card title="检索参数">
      <Form
        form={form}
        layout="inline"
        initialValues={{ top_k: 5 }}
        onFinish={(values) => { void handleFinish(values); }}
        style={{ flexWrap: 'wrap', gap: 8 }}
      >
        <Form.Item
          name="query"
          rules={[{ required: true, message: '请输入查询内容' }]}
          style={{ flex: 1, minWidth: 200, marginBottom: 0 }}
        >
          <Input placeholder="输入查询内容" allowClear />
        </Form.Item>
        <Form.Item name="top_k" label="top_k" style={{ marginBottom: 0 }}>
          <InputNumber min={1} max={20} style={{ width: 80 }} />
        </Form.Item>
        <Form.Item style={{ marginBottom: 0 }}>
          <Button type="primary" htmlType="submit" icon={<SearchOutlined />} loading={loading}>
            检索
          </Button>
        </Form.Item>
      </Form>
    </Card>
  );
}
```

- [ ] **Step 2: 创建 `frontend/src/components/rag/RagResultList.tsx`**

```typescript
import { Card, Tag, Empty, Typography, Flex } from 'antd';
import type { RagResult } from '../../types/api';

type Props = { results: RagResult[] };

function scoreColor(score: number): string {
  if (score >= 0.8) return 'green';
  if (score >= 0.5) return 'orange';
  return 'default';
}

export default function RagResultList({ results }: Props): JSX.Element {
  if (results.length === 0) {
    return <Empty description="暂无检索结果" style={{ padding: '40px 0' }} />;
  }

  return (
    <Flex vertical gap={12}>
      {results.map((r, i) => (
        <Card
          key={i}
          size="small"
          title={`结果 ${i + 1}`}
          extra={
            <Tag color={scoreColor(r.score)}>
              相关度 {(r.score * 100).toFixed(1)}%
            </Tag>
          }
        >
          <Typography.Paragraph style={{ margin: 0, lineHeight: 1.7 }}>
            {r.content}
          </Typography.Paragraph>
          {r.citation && (
            <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: 8, display: 'block' }}>
              来源：{r.citation}
            </Typography.Text>
          )}
        </Card>
      ))}
    </Flex>
  );
}
```

- [ ] **Step 3: 更新 `frontend/src/pages/RagPage.tsx`**

```typescript
import { useState } from 'react';
import { Flex, Typography, Alert } from 'antd';
import RagQueryPanel from '../components/rag/RagQueryPanel';
import RagResultList from '../components/rag/RagResultList';
import type { RagResult } from '../types/api';

export default function RagPage(): JSX.Element {
  const [results, setResults] = useState<RagResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <Flex vertical style={{ height: '100%', overflow: 'auto', padding: 24 }} gap={16}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        RAG 检索
      </Typography.Title>
      <RagQueryPanel
        onResults={setResults}
        loading={loading}
        onLoadingChange={setLoading}
        onError={setError}
      />
      {error && (
        <Alert type="error" message={error} closable onClose={() => setError(null)} />
      )}
      <RagResultList results={results} />
    </Flex>
  );
}
```

- [ ] **Step 4: 验证 RAG 页面**

访问 `/rag`，可看到检索表单。（后端不可用时显示错误 Alert 即可。）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/rag frontend/src/pages/RagPage.tsx
git commit -m "feat: RAG page with query panel and result list"
```

---

## Task 7: System 页面

**Files:**
- Create: `frontend/src/components/system/HealthStatusCard.tsx`
- Modify: `frontend/src/pages/SystemPage.tsx`

- [ ] **Step 1: 创建 `frontend/src/components/system/HealthStatusCard.tsx`**

```typescript
import { Card, Badge, Typography } from 'antd';

type Status = 'ok' | 'error' | 'loading';

export type CheckResult = {
  status: Status;
  message: string;
};

type Props = {
  title: string;
  result: CheckResult;
};

const BADGE_STATUS_MAP: Record<Status, 'success' | 'error' | 'processing'> = {
  ok: 'success',
  error: 'error',
  loading: 'processing',
};

export default function HealthStatusCard({ title, result }: Props): JSX.Element {
  return (
    <Card style={{ minWidth: 220, flex: 1 }}>
      <Badge status={BADGE_STATUS_MAP[result.status]} text={title} />
      <Typography.Text
        type={result.status === 'error' ? 'danger' : 'secondary'}
        style={{ display: 'block', marginTop: 8, fontSize: 13 }}
      >
        {result.message}
      </Typography.Text>
    </Card>
  );
}
```

- [ ] **Step 2: 更新 `frontend/src/pages/SystemPage.tsx`**

```typescript
import { useEffect, useState, useCallback } from 'react';
import { Flex, Typography, Button, Switch } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import HealthStatusCard, { type CheckResult } from '../components/system/HealthStatusCard';
import { getHealth, getReady } from '../services/healthService';
import { normalizeError } from '../services/apiClient';

export default function SystemPage(): JSX.Element {
  const [health, setHealth] = useState<CheckResult>({ status: 'loading', message: '检查中...' });
  const [ready, setReady] = useState<CheckResult>({ status: 'loading', message: '检查中...' });
  const [autoRefresh, setAutoRefresh] = useState(false);

  const check = useCallback(async () => {
    setHealth({ status: 'loading', message: '检查中...' });
    setReady({ status: 'loading', message: '检查中...' });

    await Promise.allSettled([
      getHealth()
        .then((h) => setHealth({ status: 'ok', message: h.status }))
        .catch((e) => setHealth({ status: 'error', message: normalizeError(e) })),
      getReady()
        .then((r) => setReady({ status: r.ready ? 'ok' : 'error', message: r.ready ? '就绪' : '未就绪' }))
        .catch((e) => setReady({ status: 'error', message: normalizeError(e) })),
    ]);
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => { void check(); }, 10000);
    return () => clearInterval(id);
  }, [autoRefresh, check]);

  return (
    <Flex vertical style={{ height: '100%', overflow: 'auto', padding: 24 }} gap={16}>
      <Flex align="center" justify="space-between">
        <Typography.Title level={4} style={{ margin: 0 }}>
          系统状态
        </Typography.Title>
        <Flex gap={12} align="center">
          <Switch
            checkedChildren="自动刷新 10s"
            unCheckedChildren="自动刷新"
            checked={autoRefresh}
            onChange={setAutoRefresh}
          />
          <Button icon={<ReloadOutlined />} onClick={() => { void check(); }}>
            刷新
          </Button>
        </Flex>
      </Flex>
      <Flex gap={12} wrap="wrap">
        <HealthStatusCard title="Health" result={health} />
        <HealthStatusCard title="Ready" result={ready} />
      </Flex>
    </Flex>
  );
}
```

- [ ] **Step 3: 验证 System 页面**

访问 `/system`，可看到两个状态卡片，点击刷新可重新检查。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/system frontend/src/pages/SystemPage.tsx
git commit -m "feat: system health page with auto-refresh"
```

---

## Task 8: 最终验证与收尾

- [ ] **Step 1: TypeScript 全量检查**

```bash
cd frontend && npx tsc --noEmit
```

预期：无任何报错。若有报错，根据报错信息修复类型不匹配问题。

- [ ] **Step 2: 全功能验收**

启动后端（如可用）：`python examples/run_service.py`
启动前端：`cd frontend && npm run dev`

验收清单：
- [ ] 深色/浅色主题切换，所有页面组件跟随
- [ ] 新建会话、切换会话、重命名（右键菜单）、删除（右键菜单）、清空（右键菜单）
- [ ] 置顶会话后排在列表最上方
- [ ] 搜索会话过滤
- [ ] 流式模式：输入问题，AI 回复流式输出，有光标动效
- [ ] 非流式模式：关闭流式开关，发送后等待完整响应
- [ ] AI 回复包含 Markdown（代码块有高亮，列表、标题正确渲染）
- [ ] 刷新页面后会话列表和消息历史恢复
- [ ] `/rag` 页面检索表单可用
- [ ] `/system` 页面状态卡片可用，自动刷新开关正常

- [ ] **Step 3: 构建验证**

```bash
cd frontend && npm run build
```

预期：构建成功，无报错。

- [ ] **Step 4: 最终 Commit**

```bash
git add .
git commit -m "feat: complete frontend rewrite with antd5 + @ant-design/x"
```

---

## 自检：Spec 覆盖确认

| 设计要求 | 对应 Task |
|---|---|
| 双主题深/浅切换 | Task 3（theme.ts + settingsStore） |
| AI Markdown 渲染 | Task 5（MarkdownContent） |
| Conversations 会话列表 | Task 5（ConversationSidebar） |
| Bubble.List 消息流 | Task 5（ChatMain） |
| Sender 输入框 + 流式开关 | Task 5（ChatMain） |
| Welcome 空态 + Prompts | Task 5（ChatMain） |
| 会话 CRUD + 持久化 | Task 4（chatStore） |
| SSE 流式 + 取消 | Task 4（chatStore） |
| RAG 检索页 | Task 6 |
| 系统健康检查页 | Task 7 |
| 后端接口兼容 | Task 2（services 层） |
