import type { ChatRequest, ChatResponse, ChatRunResponse, ChatRunState } from '../types/api';
import { apiClient, normalizeError } from './apiClient';

export type SessionMessagesResponse = {
  messages: Array<{
    role: 'user' | 'assistant';
    content: string;
    thinking_blocks: string[];
    tool_activity: ChatRunState['tool_activity'];
  }>;
  total: number;
  has_more: boolean;
};

export type ConversationUpdate = {
  title: string;
  last_message_preview: string;
  message_count: number;
  updated_at: string;
};

type StreamHandlers = {
  onConversationStart?: (sessionId: string, message: string) => void;
  onRunState?: (state: ChatRunState) => void;
  onMessage: (delta: string) => void;
  onThinkingStart?: () => void;
  onThinkingStop?: (durationMs: number) => void;
  onThinking?: (delta: string) => void;
  onToolStart?: (toolName: string, input: Record<string, unknown>) => void;
  onToolResult?: (toolName: string, input: Record<string, unknown>, result: string, isError: boolean, durationMs: number) => void;
  onDone: (conversation?: ConversationUpdate) => void;
  onError: (msg: string) => void;
};

export async function deleteSession(sessionId: string): Promise<void> {
  await apiClient.delete(`/api/v1/chat/sessions/${sessionId}`);
}

export async function fetchSessionMessages(
  sessionId: string,
  limit = 30,
  offset = 0,
): Promise<SessionMessagesResponse> {
  const { data } = await apiClient.get<SessionMessagesResponse>(
    `/api/v1/chat/sessions/${sessionId}/messages`,
    { params: { limit, offset } },
  );
  return data;
}

export async function createChatRun(payload: ChatRequest): Promise<ChatRunResponse> {
  const { data } = await apiClient.post<ChatRunResponse>('/api/v1/chat/runs', payload);
  return data;
}

export async function fetchActiveChatRun(sessionId: string): Promise<ChatRunState | null> {
  const { data } = await apiClient.get<ChatRunState | null>(
    `/api/v1/chat/sessions/${sessionId}/runs/active`,
  );
  return data;
}

export async function cancelChatRun(runId: string): Promise<ChatRunState> {
  const { data } = await apiClient.post<ChatRunState>(`/api/v1/chat/runs/${runId}/cancel`);
  return data;
}

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
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('event:')) eventType = line.slice(6).trim();
    else if (line.startsWith('data:')) {
      // SSE 格式：'data: <value>'，去掉 'data:' 后保留一个前导空格以外的内容
      // 不能用 trim()，否则会吃掉 token 间的空格（如 " user" → "user"）
      const raw = line.slice(5);
      dataLines.push(raw.startsWith(' ') ? raw.slice(1) : raw);
    }
  }
  const data = dataLines.join('\n');
  // 所有事件的 data 均为 JSON，统一解析后分发
  const safeJson = (): Record<string, unknown> => {
    try { return JSON.parse(data) as Record<string, unknown>; } catch { return {}; }
  };
  if (eventType === 'conversation') {
    const d = safeJson();
    handlers.onConversationStart?.(String(d.session_id ?? ''), String(d.message ?? ''));
  }
  else if (eventType === 'run_state') handlers.onRunState?.(safeJson() as ChatRunState);
  else if (eventType === 'message') handlers.onMessage(String(safeJson().text ?? data));
  else if (eventType === 'thinking_start') handlers.onThinkingStart?.();
  else if (eventType === 'thinking_stop') handlers.onThinkingStop?.((safeJson().duration_ms as number) ?? 0);
  else if (eventType === 'thinking') handlers.onThinking?.(String(safeJson().text ?? data));
  else if (eventType === 'tool_start') {
    const d = safeJson();
    handlers.onToolStart?.(String(d.tool ?? ''), (d.input as Record<string, unknown>) ?? {});
  }
  else if (eventType === 'tool_result') {
    const d = safeJson();
    handlers.onToolResult?.(
      String(d.tool ?? ''),
      (d.input as Record<string, unknown>) ?? {},
      String(d.result ?? ''),
      Boolean(d.is_error),
      Number(d.duration_ms ?? 0),
    );
  }
  else if (eventType === 'done') handlers.onDone(safeJson().conversation as ConversationUpdate | undefined);
  else if (eventType === 'error') handlers.onError(String(safeJson().message ?? data) || '流式请求失败');
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
  let rawBuffer = '';
  let doneCalled = false;

  // abort 后的事件一律丢弃，防止竞态覆盖 cancelStream 已清理好的状态
  const isAborted = () => signal?.aborted ?? false;

  const patchedHandlers: StreamHandlers = {
    onConversationStart: (sid, msg) => { if (!isAborted()) handlers.onConversationStart?.(sid, msg); },
    onMessage: (d) => { if (!isAborted()) handlers.onMessage(d); },
    onThinkingStart: () => { if (!isAborted()) handlers.onThinkingStart?.(); },
    onThinkingStop: (ms) => { if (!isAborted()) handlers.onThinkingStop?.(ms); },
    onThinking: (d) => { if (!isAborted()) handlers.onThinking?.(d); },
    onToolStart: (name, input) => { if (!isAborted()) handlers.onToolStart?.(name, input); },
    onToolResult: (name, input, result, isError, durationMs) => { if (!isAborted()) handlers.onToolResult?.(name, input, result, isError, durationMs); },
    onDone: (conv) => {
      if (isAborted()) return;
      doneCalled = true;
      handlers.onDone(conv);
    },
    onError: (msg) => { if (!isAborted()) handlers.onError(msg); },
  };

  try {
    while (true) {
      if (isAborted()) break;
      const { done, value } = await reader.read();
      if (done) break;
      // 统一将 \r\n 规范化为 \n，兼容 SSE-Starlette 的 CRLF 行尾
      rawBuffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
      const parts = rawBuffer.split('\n\n');
      rawBuffer = parts.pop() ?? '';
      for (const block of parts) {
        if (isAborted()) break;
        parseBlock(block, patchedHandlers);
      }
    }

    if (!isAborted() && rawBuffer.trim()) parseBlock(rawBuffer, patchedHandlers);

    // 流自然结束但没收到 done 事件时兜底
    if (!isAborted() && !doneCalled) handlers.onDone();
  } catch (e) {
    // AbortError 或流被中断属于用户主动取消，静默处理
    const err = e as Error;
    if (err.name === 'AbortError' || /abort/i.test(err.message)) return;
    handlers.onError(normalizeError(e));
  }
}

export async function subscribeChatRun(
  runId: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(
      `${import.meta.env.VITE_API_BASE_URL ?? ''}/api/v1/chat/runs/${runId}/stream`,
      { method: 'GET', signal },
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
  let rawBuffer = '';
  const isAborted = () => signal?.aborted ?? false;

  const patchedHandlers: StreamHandlers = {
    onConversationStart: (sid, msg) => { if (!isAborted()) handlers.onConversationStart?.(sid, msg); },
    onRunState: (state) => { if (!isAborted()) handlers.onRunState?.(state); },
    onMessage: (d) => { if (!isAborted()) handlers.onMessage(d); },
    onThinkingStart: () => { if (!isAborted()) handlers.onThinkingStart?.(); },
    onThinkingStop: (ms) => { if (!isAborted()) handlers.onThinkingStop?.(ms); },
    onThinking: (d) => { if (!isAborted()) handlers.onThinking?.(d); },
    onToolStart: (name, input) => { if (!isAborted()) handlers.onToolStart?.(name, input); },
    onToolResult: (name, input, result, isError, durationMs) => { if (!isAborted()) handlers.onToolResult?.(name, input, result, isError, durationMs); },
    onDone: (conv) => { if (!isAborted()) handlers.onDone(conv); },
    onError: (msg) => { if (!isAborted()) handlers.onError(msg); },
  };

  try {
    while (true) {
      if (isAborted()) break;
      const { done, value } = await reader.read();
      if (done) break;
      rawBuffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
      const parts = rawBuffer.split('\n\n');
      rawBuffer = parts.pop() ?? '';
      for (const block of parts) {
        if (isAborted()) break;
        parseBlock(block, patchedHandlers);
      }
    }
    if (!isAborted() && rawBuffer.trim()) parseBlock(rawBuffer, patchedHandlers);
  } catch (e) {
    const err = e as Error;
    if (err.name === 'AbortError' || /abort/i.test(err.message)) return;
    handlers.onError(normalizeError(e));
  }
}
