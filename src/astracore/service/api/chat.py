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


def _needs_summary_fallback(messages: list[Message]) -> bool:
    """工具循环结束但没有最终正文时，触发一次禁用工具的总结收尾。"""
    visible_messages = [m for m in messages if m.role != MessageRole.SYSTEM]
    if not visible_messages:
        return False

    last_message = visible_messages[-1]
    if last_message.role == MessageRole.TOOL and last_message.has_tool_results():
        return True
    return last_message.role == MessageRole.ASSISTANT and not last_message.content.strip()


def _build_summary_fallback_messages(
    messages: list[Message], *, hit_iteration_limit: bool
) -> list[Message]:
    """构造“只总结、不再调工具”的收尾提示。"""
    prompt = (
        "你现在处于工具调用收尾阶段。请只基于已有对话和工具结果给出最终回答，"
        "不要继续调用工具，也不要继续规划下一步。"
        "如果信息不足，请明确说明已确认内容和仍然缺失的信息。"
    )
    if hit_iteration_limit:
        prompt = (
            "你已达到工具循环最大轮次，请停止继续探索，直接基于当前工具结果完成总结。"
            + prompt
        )

    copied = [message.model_copy(deep=True) for message in messages]
    if copied and copied[0].role == MessageRole.SYSTEM:
        copied[0] = copied[0].model_copy(
            update={"content": f"{copied[0].content}\n\n---\n\n{prompt}"}
        )
    else:
        copied.insert(0, Message(role=MessageRole.SYSTEM, content=prompt))
    return copied


async def _generate_summary_fallback(
    *,
    messages: list[Message],
    model: str | None,
    temperature: float,
    hit_iteration_limit: bool,
) -> str:
    """非流式收尾总结。"""
    response = await _get_llm_adapter().generate(
        messages=_build_summary_fallback_messages(
            messages, hit_iteration_limit=hit_iteration_limit
        ),
        model=model,
        temperature=temperature,
    )
    return response.content


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


def _get_stream_idle_timeout_seconds() -> float:
    """流式模式下单次等待下一个事件的最大空闲时间。"""
    timeout_ms = PolicyEngine().config.timeout.llm_timeout_ms
    return max(timeout_ms / 1000.0, 1.0)


async def _iterate_with_idle_timeout(
    stream: Any,
    *,
    timeout_seconds: float,
    stage: str,
) -> Any:
    """为流式事件迭代增加空闲超时保护，避免前端无限等待。"""
    iterator = stream.__aiter__()
    while True:
        try:
            event = await asyncio.wait_for(anext(iterator), timeout=timeout_seconds)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError as exc:
            logger.warning("%s 超时，%.1f 秒内未收到新事件", stage, timeout_seconds)
            raise TimeoutError(f"{stage} 流式响应超时，请重试") from exc
        yield event


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

    if _needs_summary_fallback(session.get_messages()):
        temperature = request.temperature
        if temperature is None:
            temperature = float(await _get_setting_value("temperature") or "0.7")
        summary = await _generate_summary_fallback(
            messages=session.get_messages(),
            model=request.model,
            temperature=temperature,
            hit_iteration_limit=False,
        )
        if summary.strip():
            session.add_message(Message(role=MessageRole.ASSISTANT, content=summary))

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
    idle_timeout_seconds = _get_stream_idle_timeout_seconds()

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
                    round_count = 0
                    async for event in _iterate_with_idle_timeout(
                        tool_loop.execute_stream_with_tools(
                            session,
                            model=request.model,
                            allowed_tools=allowed_tools,
                            **llm_kwargs,
                        ),
                        timeout_seconds=idle_timeout_seconds,
                        stage="工具模式",
                    ):
                        if event.event_type == StreamEventType.ROUND_START:
                            round_count = int(event.metadata.get("round", round_count + 1))
                            yield {"event": "thinking_start", "data": str(event.metadata.get("round", 1))}  # noqa: E501
                        elif event.event_type == StreamEventType.TEXT_DELTA:
                            yield {"event": "message", "data": event.content}
                        elif event.event_type == StreamEventType.THINKING_DELTA:
                            yield {"event": "thinking", "data": event.content}
                        elif event.event_type == StreamEventType.TOOL_CALL and event.tool_call:
                            yield {"event": "tool_use", "data": event.tool_call.name}

                    if _needs_summary_fallback(session.get_messages()):
                        summary_text = ""
                        hit_iteration_limit = (
                            round_count >= tool_loop.max_iterations
                            and session.get_messages()
                            and session.get_messages()[-1].role == MessageRole.TOOL
                        )
                        async for event in _iterate_with_idle_timeout(
                            _get_llm_adapter().generate_stream(
                                messages=_build_summary_fallback_messages(
                                    session.get_messages(),
                                    hit_iteration_limit=hit_iteration_limit,
                                ),
                                model=request.model,
                                temperature=temperature,
                            ),
                            timeout_seconds=idle_timeout_seconds,
                            stage="工具总结",
                        ):
                            if (
                                event.event_type == StreamEventType.TEXT_DELTA
                                and event.content
                            ):
                                summary_text += event.content
                                yield {"event": "message", "data": event.content}
                        if summary_text.strip():
                            session.add_message(
                                Message(role=MessageRole.ASSISTANT, content=summary_text)
                            )
                    yield {"event": "done", "data": "[DONE]"}
                finally:
                    await _save_short_term_safe(session_id, session.get_messages())

            else:
                # 普通流式路径（无工具）
                use_case = _get_chat_use_case()
                async for event in _iterate_with_idle_timeout(
                    use_case.execute_stream(
                        session_id=session_id,
                        user_message=request.message,
                        model=request.model,
                        temperature=temperature,
                        inject_system=inject_system,
                        context_max_messages=context_max,
                        **llm_kwargs,
                    ),
                    timeout_seconds=idle_timeout_seconds,
                    stage="普通模式",
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
