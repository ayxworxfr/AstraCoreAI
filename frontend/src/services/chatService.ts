import type { ChatRequest, ChatResponse } from '../types/api';
import { apiClient, normalizeError } from './apiClient';

type StreamHandlers = {
  onMessage: (delta: string) => void;
  onThinkingStart?: () => void;   // 新一轮思考开始（工具循环每轮触发）
  onThinking?: (delta: string) => void;
  onToolUse?: (toolName: string) => void;
  onDone: () => void;
  onError: (msg: string) => void;
};

export async function deleteSession(sessionId: string): Promise<void> {
  await apiClient.delete(`/api/v1/chat/sessions/${sessionId}`);
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
  if (eventType === 'message') handlers.onMessage(data);
  else if (eventType === 'thinking_start') handlers.onThinkingStart?.();
  else if (eventType === 'thinking') handlers.onThinking?.(data);
  else if (eventType === 'tool_use') handlers.onToolUse?.(data);
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
  let rawBuffer = '';
  let doneCalled = false;

  const patchedHandlers: StreamHandlers = {
    onMessage: handlers.onMessage,
    onThinkingStart: handlers.onThinkingStart,
    onThinking: handlers.onThinking,
    onToolUse: handlers.onToolUse,
    onDone: () => {
      doneCalled = true;
      handlers.onDone();
    },
    onError: handlers.onError,
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      // 统一将 \r\n 规范化为 \n，兼容 SSE-Starlette 的 CRLF 行尾
      rawBuffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
      const parts = rawBuffer.split('\n\n');
      rawBuffer = parts.pop() ?? '';
      for (const block of parts) parseBlock(block, patchedHandlers);
    }

    if (rawBuffer.trim()) parseBlock(rawBuffer, patchedHandlers);

    // 流自然结束但没收到 done 事件时兜底
    if (!doneCalled) handlers.onDone();
  } catch (e) {
    // AbortError 或流被中断属于用户主动取消，静默处理
    const err = e as Error;
    if (err.name === 'AbortError' || /abort/i.test(err.message)) return;
    handlers.onError(normalizeError(e));
  }
}
