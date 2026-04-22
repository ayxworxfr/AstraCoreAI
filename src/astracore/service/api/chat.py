"""Chat API endpoints."""

import asyncio
from functools import lru_cache
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from astracore.adapters.db.models import SkillRow, UserSettingsRow
from astracore.adapters.db.session import get_session
from astracore.adapters.llm.anthropic import AnthropicAdapter
from astracore.adapters.llm.openai import OpenAIAdapter
from astracore.adapters.memory.hybrid import HybridMemoryAdapter
from astracore.core.application.chat import ChatUseCase
from astracore.core.application.tool_loop import ToolLoopUseCase
from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import LLMAdapter, StreamEventType
from astracore.core.ports.tool import ToolAdapter
from astracore.runtime.observability.logger import get_logger
from astracore.runtime.policy.engine import PolicyEngine
from astracore.sdk.config import AstraCoreConfig
from astracore.service.api import rag as rag_api
from astracore.service.builtin_tools import build_tool_adapter

router = APIRouter()
logger = get_logger(__name__)


def _strip_dangling_tool_calls(messages: list) -> list:
    """保存前清理尾部没有对应 tool_result 的 tool_use 消息。

    工具循环出错时，session 可能以 ASSISTANT(tool_calls) 结尾而没有紧跟的
    TOOL(tool_results)。Anthropic API 不允许这种不完整序列，此函数将其移除，
    保证保存到记忆的历史始终合法。
    """
    msgs = list(messages)
    while msgs and msgs[-1].role == MessageRole.ASSISTANT and msgs[-1].tool_calls:
        msgs.pop()
    return msgs


def _prepare_for_save(messages: list) -> list:
    """保存前清理：去掉 SYSTEM 消息 + 悬空 tool_use。

    SYSTEM 消息不属于对话历史，每次请求都会动态注入最新版本（含当前 skill
    和用户名称），若保存进记忆会导致旧版本永远排在新版本前面被优先读取。
    """
    msgs = [m for m in messages if m.role != MessageRole.SYSTEM]
    return _strip_dangling_tool_calls(msgs)


@lru_cache(maxsize=1)
def _get_settings() -> AstraCoreConfig:
    return AstraCoreConfig()


@lru_cache(maxsize=1)
def _get_llm_adapter() -> LLMAdapter:
    cfg = _get_settings().llm
    if cfg.provider == "anthropic":
        return AnthropicAdapter(
            api_key=cfg.api_key,
            default_model=cfg.model,
            base_url=cfg.base_url,
            max_tokens=cfg.max_tokens,
        )
    return OpenAIAdapter(
        api_key=cfg.api_key,
        default_model=cfg.model,
        base_url=cfg.base_url,
        max_tokens=cfg.max_tokens,
    )


@lru_cache(maxsize=1)
def _get_memory_adapter() -> HybridMemoryAdapter:
    cfg = _get_settings().memory
    return HybridMemoryAdapter(redis_url=cfg.redis_url, db_url=cfg.db_url)


@lru_cache(maxsize=1)
def _get_chat_use_case() -> ChatUseCase:
    return ChatUseCase(
        llm_adapter=_get_llm_adapter(),
        memory_adapter=_get_memory_adapter(),
        policy_engine=PolicyEngine(),
    )


def _resolve_tool_adapter(http_request: Request) -> ToolAdapter:
    """Get the tool adapter from app.state (set by lifespan) or fall back to builtins."""
    adapter = getattr(http_request.app.state, "tool_adapter", None)
    return adapter if adapter is not None else build_tool_adapter()


def _get_tool_loop_use_case(tool_adapter: ToolAdapter) -> ToolLoopUseCase:
    cfg = _get_settings().agent
    return ToolLoopUseCase(
        llm_adapter=_get_llm_adapter(),
        tool_adapter=tool_adapter,
        policy_engine=PolicyEngine(),
        max_iterations=cfg.max_tool_iterations,
        max_tool_result_chars=cfg.max_tool_result_chars,
    )


async def _save_short_term_safe(session_id: UUID, messages: list[Message]) -> None:
    """在请求取消场景下，安全保存会话，避免取消传播到连接回收。"""
    try:
        await asyncio.shield(
            _get_memory_adapter().save_short_term(
                session_id=session_id,
                messages=_prepare_for_save(messages),
            )
        )
    except asyncio.CancelledError:
        logger.warning("会话保存在取消阶段被中断，session_id=%s", session_id)
    except Exception:
        logger.exception("会话保存失败，session_id=%s", session_id)


async def _load_skill(skill_id: str) -> SkillRow | None:
    """Fetch a skill by id; return None if not found."""
    db_url = _get_settings().memory.db_url
    async with get_session(db_url) as db:
        return await db.get(SkillRow, skill_id)


async def _get_setting_value(key: str) -> str:
    """Fetch a single user-settings value; return '' if not set."""
    db_url = _get_settings().memory.db_url
    async with get_session(db_url) as db:
        row = await db.get(UserSettingsRow, key)
        return row.value if row else ""


class ChatRequest(BaseModel):
    """Chat request model."""

    message: str
    session_id: UUID | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    use_tools: bool = False
    enable_thinking: bool = False
    thinking_budget: int = Field(default=8000, ge=1000, le=32000)
    enable_rag: bool = False
    enable_web: bool = False
    skill_id: UUID | None = None
    disable_skill: bool = False


class ChatResponse(BaseModel):
    """Chat response model."""

    session_id: UUID
    message: str
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def _render_skill_prompt(prompt: str, ai_name: str, owner_name: str) -> str:
    """将 system_prompt 中的 {{ai_name}} / {{owner_name}} 替换为用户设定的值。"""
    result = prompt.replace("{{ai_name}}", ai_name or "AI 助手")
    result = result.replace("{{owner_name}}", owner_name or "用户")
    return result


async def _build_system_prompt(
    skill_id: UUID | None,
    disable_skill: bool,
    enable_rag: bool,
    message: str,
) -> str | None:
    """Compose the three-layer system prompt: skill → global instruction → RAG context."""
    parts: list[str] = []

    # Layer 1: Skill
    if not disable_skill:
        resolved_id = str(skill_id) if skill_id else await _get_setting_value("default_skill_id")
        if resolved_id:
            skill = await _load_skill(resolved_id)
            if skill and skill.system_prompt:
                ai_name = await _get_setting_value("ai_name") or "小卡"
                owner_name = await _get_setting_value("owner_name")
                parts.append(_render_skill_prompt(skill.system_prompt, ai_name, owner_name))

    # Layer 2: Global instruction
    instruction = await _get_setting_value("global_instruction")
    if instruction:
        parts.append(instruction)

    # Layer 3: RAG context
    if enable_rag:
        rag_ctx = await _build_rag_context(message)
        if rag_ctx:
            parts.append(rag_ctx)

    return "\n\n---\n\n".join(parts) or None


async def _run_with_tools(
    request: "ChatRequest", session_id: UUID, tool_adapter: ToolAdapter
) -> str:
    """Execute a chat turn through the tool loop."""
    memory = _get_memory_adapter()
    tool_loop = _get_tool_loop_use_case(tool_adapter)

    messages = await memory.load_short_term(session_id)
    session = SessionState(session_id=session_id)
    if messages:
        session.restore_messages(messages)

    session.add_message(Message(role=MessageRole.USER, content=request.message))
    try:
        session = await tool_loop.execute_with_tools(session, model=request.model)
    except Exception:
        # 出错时也保存（至少保留用户消息），同时清理悬空的 tool_use
        await _save_short_term_safe(session_id, session.get_messages())
        raise
    await _save_short_term_safe(session_id, session.get_messages())

    last_assistant = next(
        (m for m in reversed(session.get_messages()) if m.role == MessageRole.ASSISTANT),
        None,
    )
    return last_assistant.content if last_assistant else ""


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: UUID) -> None:
    """删除指定会话的后端记忆（短期消息历史）。"""
    await _get_memory_adapter().delete_session_memory(session_id)


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest, http_request: Request) -> ChatResponse:
    """Chat endpoint. Set use_tools=true to route through the tool loop."""
    session_id = request.session_id or uuid4()

    try:
        if request.use_tools:
            tool_adapter = _resolve_tool_adapter(http_request)
            content = await _run_with_tools(request, session_id, tool_adapter)
            return ChatResponse(session_id=session_id, message=content, model=request.model)

        temperature = request.temperature
        if temperature is None:
            temperature = float(await _get_setting_value("temperature") or "0.7")
        use_case = _get_chat_use_case()
        response_message = await use_case.execute(
            session_id=session_id,
            user_message=request.message,
            model=request.model,
            temperature=temperature,
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
        top_k = int(await _get_setting_value("rag_top_k") or "4")
        pipeline = rag_api._get_rag_pipeline()
        chunks = await pipeline.retrieve_with_citations(query=query, top_k=top_k)
        if not chunks:
            return None
        parts = [
            f"[来源: {c.citation.title or c.citation.source_id}]\n{c.content}"
            for c in chunks
        ]
        context = "\n\n---\n\n".join(parts)
        return (
            "以下是从知识库检索到的相关内容，请优先基于这些内容回答用户问题，"
            "并在回答中注明引用的来源：\n\n" + context
        )
    except Exception:
        return None


@router.post("/stream")
async def chat_stream(request: ChatRequest, http_request: Request) -> EventSourceResponse:
    """Streaming chat endpoint.

    use_tools=True 时走工具循环流式路径，每轮思考产生独立 thinking_start/thinking 事件对。
    """
    session_id = request.session_id or uuid4()
    tool_adapter = _resolve_tool_adapter(http_request)

    async def event_generator() -> Any:
        try:
            inject_system = await _build_system_prompt(
                skill_id=request.skill_id,
                disable_skill=request.disable_skill,
                enable_rag=request.enable_rag,
                message=request.message,
            )

            temperature = request.temperature
            if temperature is None:
                temperature = float(await _get_setting_value("temperature") or "0.7")

            context_max = int(await _get_setting_value("context_max_messages") or "20")

            llm_kwargs: dict[str, Any] = {}
            if request.enable_thinking:
                llm_kwargs["enable_thinking"] = True
                llm_kwargs["thinking_budget"] = request.thinking_budget

            if request.use_tools or request.enable_web:
                # 工具循环流式路径：动态获取所有已注册工具（含 MCP 工具）
                all_tools = {d.name for d in tool_adapter.get_definitions()}
                # web_search 仅在 enable_web=True 时开放
                allowed_tools = all_tools if request.enable_web else all_tools - {"web_search"}

                memory = _get_memory_adapter()
                tool_loop = _get_tool_loop_use_case(tool_adapter)

                messages = await memory.load_short_term(session_id)
                # 过滤旧 SYSTEM 消息（兼容历史数据），取最近 context_max 条
                messages = [m for m in messages if m.role != MessageRole.SYSTEM]
                if len(messages) > context_max:
                    messages = messages[-context_max:]
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

                try:
                    async for event in tool_loop.execute_stream_with_tools(
                        session,
                        model=request.model,
                        allowed_tools=allowed_tools,
                        **llm_kwargs,
                    ):
                        if event.event_type == StreamEventType.ROUND_START:
                            yield {"event": "thinking_start", "data": str(event.metadata.get("round", 1))}  # noqa: E501
                        elif event.event_type == StreamEventType.TEXT_DELTA:
                            yield {"event": "message", "data": event.content}
                        elif event.event_type == StreamEventType.THINKING_DELTA:
                            yield {"event": "thinking", "data": event.content}
                        elif event.event_type == StreamEventType.TOOL_CALL and event.tool_call:
                            yield {"event": "tool_use", "data": event.tool_call.name}
                    yield {"event": "done", "data": "[DONE]"}
                finally:
                    await _save_short_term_safe(session_id, session.get_messages())

            else:
                # 普通流式路径（无工具）
                use_case = _get_chat_use_case()
                async for event in use_case.execute_stream(
                    session_id=session_id,
                    user_message=request.message,
                    model=request.model,
                    temperature=temperature,
                    inject_system=inject_system,
                    context_max_messages=context_max,
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
