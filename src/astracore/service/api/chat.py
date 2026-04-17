"""Chat API endpoints."""

import os
from functools import lru_cache
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from astracore.adapters.llm.anthropic import AnthropicAdapter
from astracore.adapters.llm.openai import OpenAIAdapter
from astracore.adapters.memory.hybrid import HybridMemoryAdapter
from astracore.core.application.chat import ChatUseCase
from astracore.core.application.tool_loop import ToolLoopUseCase
from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import LLMAdapter, StreamEventType
from astracore.runtime.policy.engine import PolicyEngine
from astracore.service.api import rag as rag_api
from astracore.service.builtin_tools import build_tool_adapter

router = APIRouter()


def _get_required_api_key() -> str:
    """读取统一 LLM API Key。"""
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("必须设置 LLM_API_KEY")
    return api_key


def _get_provider() -> str:
    """读取并校验 LLM Provider。"""
    provider = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
    if provider not in {"deepseek", "anthropic"}:
        raise RuntimeError("LLM_PROVIDER 仅支持 deepseek 或 anthropic")
    return provider


def _default_model(provider: str) -> str:
    """按 provider 提供默认模型。"""
    if provider == "deepseek":
        return "deepseek-chat"
    return "claude-sonnet-4-6"


def _resolved_base_url(provider: str) -> str | None:
    """读取统一 base_url；DeepSeek 未配置时使用官方默认地址。"""
    base = os.getenv("LLM_BASE_URL", "").strip()
    if provider == "deepseek":
        return base or "https://api.deepseek.com"
    return base or None


@lru_cache(maxsize=1)
def _get_llm_adapter() -> LLMAdapter:
    """按统一配置选择适配器（provider + base_url + api_key + model）。"""
    provider = _get_provider()
    model = os.getenv("MODEL", "").strip() or _default_model(provider)
    api_key = _get_required_api_key()
    base_url = _resolved_base_url(provider)

    if provider == "deepseek":
        return OpenAIAdapter(
            api_key=api_key,
            default_model=model,
            base_url=base_url,
        )

    return AnthropicAdapter(
        api_key=api_key,
        default_model=model,
        base_url=base_url,
    )


@lru_cache(maxsize=1)
def _get_memory_adapter() -> HybridMemoryAdapter:
    """Single shared memory adapter."""
    redis_url = os.getenv("ASTRACORE__MEMORY__REDIS_URL", "redis://localhost:6379/0")
    postgres_url = os.getenv(
        "ASTRACORE__MEMORY__POSTGRES_URL",
        "postgresql+asyncpg://localhost/astracore",
    )
    return HybridMemoryAdapter(redis_url=redis_url, postgres_url=postgres_url)


@lru_cache(maxsize=1)
def _get_chat_use_case() -> ChatUseCase:
    """Get chat use case instance (cached)."""
    return ChatUseCase(
        llm_adapter=_get_llm_adapter(),
        memory_adapter=_get_memory_adapter(),
        policy_engine=PolicyEngine(),
    )


@lru_cache(maxsize=1)
def _get_tool_loop_use_case() -> ToolLoopUseCase:
    """Get tool loop use case instance (cached, shares LLM adapter)."""
    return ToolLoopUseCase(
        llm_adapter=_get_llm_adapter(),
        tool_adapter=build_tool_adapter(),
        policy_engine=PolicyEngine(),
    )


class ChatRequest(BaseModel):
    """Chat request model."""

    message: str
    session_id: UUID | None = None
    model: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    use_tools: bool = False
    enable_thinking: bool = False
    thinking_budget: int = Field(default=8000, ge=1000, le=32000)
    enable_rag: bool = False
    enable_web: bool = False


class ChatResponse(BaseModel):
    """Chat response model."""

    session_id: UUID
    message: str
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


async def _run_with_tools(request: ChatRequest, session_id: UUID) -> str:
    """Execute a chat turn through the tool loop."""
    memory = _get_memory_adapter()
    tool_loop = _get_tool_loop_use_case()

    messages = await memory.load_short_term(session_id)
    session = SessionState(session_id=session_id)
    if messages:
        session.restore_messages(messages)

    session.add_message(Message(role=MessageRole.USER, content=request.message))
    session = await tool_loop.execute_with_tools(session, model=request.model)
    await memory.save_short_term(session_id, session.get_messages())

    last_assistant = next(
        (m for m in reversed(session.get_messages()) if m.role == MessageRole.ASSISTANT),
        None,
    )
    return last_assistant.content if last_assistant else ""


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Chat endpoint. Set use_tools=true to route through the tool loop."""
    session_id = request.session_id or uuid4()

    try:
        if request.use_tools:
            content = await _run_with_tools(request, session_id)
            return ChatResponse(session_id=session_id, message=content, model=request.model)

        use_case = _get_chat_use_case()
        response_message = await use_case.execute(
            session_id=session_id,
            user_message=request.message,
            model=request.model,
            temperature=request.temperature,
        )
        return ChatResponse(
            session_id=session_id,
            message=response_message.content,
            model=request.model,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _build_rag_context(query: str) -> str | None:
    """检索相关文档，构建 RAG 上下文系统提示。"""
    try:
        pipeline = rag_api._get_rag_pipeline()
        chunks = await pipeline.retrieve_with_citations(query=query, top_k=4)
        if not chunks:
            return None
        parts = [
            f"[来源: {c.citation.title or c.citation.source_id}]\n{c.content}"
            for c in chunks
        ]
        context = "\n\n---\n\n".join(parts)
        return (
            "以下是从知识库检索到的相关内容，请优先基于这些内容回答用户问题，"
            "并在回答中注明引用的来源：\n\n"
            + context
        )
    except Exception:
        return None


@router.post("/stream")
async def chat_stream(request: ChatRequest) -> EventSourceResponse:
    """Streaming chat endpoint.

    use_tools=True 时走工具循环流式路径，每轮思考产生独立 thinking_start/thinking 事件对。
    """
    session_id = request.session_id or uuid4()

    async def event_generator() -> Any:
        try:
            inject_system: str | None = None
            if request.enable_rag:
                inject_system = await _build_rag_context(request.message)

            llm_kwargs: dict[str, Any] = {}
            if request.enable_thinking:
                llm_kwargs["enable_thinking"] = True
                llm_kwargs["thinking_budget"] = request.thinking_budget

            if request.use_tools or request.enable_web:
                # 工具循环流式路径
                # 基础工具始终可用；web_search 仅在 enable_web=True 时开放
                _BASE_TOOLS = {"get_current_time", "calculate", "search_knowledge_base"}
                allowed_tools = _BASE_TOOLS | ({"web_search"} if request.enable_web else set())

                memory = _get_memory_adapter()
                tool_loop = _get_tool_loop_use_case()

                messages = await memory.load_short_term(session_id)
                session = SessionState(session_id=session_id)
                if messages:
                    session.restore_messages(messages)

                if inject_system:
                    session.add_message(
                        Message(role=MessageRole.SYSTEM, content=inject_system)
                    )
                session.add_message(
                    Message(role=MessageRole.USER, content=request.message)
                )

                async for event in tool_loop.execute_stream_with_tools(
                    session,
                    model=request.model,
                    allowed_tools=allowed_tools,
                    **llm_kwargs,
                ):
                    if event.event_type == StreamEventType.ROUND_START:
                        yield {"event": "thinking_start", "data": str(event.metadata.get("round", 1))}
                    elif event.event_type == StreamEventType.TEXT_DELTA:
                        yield {"event": "message", "data": event.content}
                    elif event.event_type == StreamEventType.THINKING_DELTA:
                        yield {"event": "thinking", "data": event.content}
                    elif event.event_type == StreamEventType.TOOL_CALL and event.tool_call:
                        yield {"event": "tool_use", "data": event.tool_call.name}

                await memory.save_short_term(session_id, session.get_messages())
                yield {"event": "done", "data": "[DONE]"}

            else:
                # 普通流式路径（无工具）
                use_case = _get_chat_use_case()
                async for event in use_case.execute_stream(
                    session_id=session_id,
                    user_message=request.message,
                    model=request.model,
                    temperature=request.temperature,
                    inject_system=inject_system,
                    **llm_kwargs,
                ):
                    if event.event_type == StreamEventType.TEXT_DELTA:
                        yield {"event": "message", "data": event.content}
                    elif event.event_type == StreamEventType.THINKING_DELTA:
                        yield {"event": "thinking", "data": event.content}
                    elif event.event_type == StreamEventType.DONE:
                        yield {"event": "done", "data": "[DONE]"}

        except Exception as e:
            detail = str(e)
            if e.__cause__ is not None:
                detail = f"{detail} — {e.__cause__!s}"
            yield {"event": "error", "data": detail}

    return EventSourceResponse(event_generator())
