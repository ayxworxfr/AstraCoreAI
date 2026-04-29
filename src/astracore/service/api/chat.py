"""Chat API endpoints."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from astracore.adapters.db.models import ChatRunRow, ConversationRow, SkillRow, UserSettingsRow
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
from astracore.sdk.config import AstraCoreConfig, LLMProfileConfig
from astracore.service.api import rag as rag_api
from astracore.service.builtin_tools import build_tool_adapter

router = APIRouter()
logger = get_logger(__name__)

_RUN_TERMINAL_STATUSES = {"done", "error", "cancelled"}


class _ActiveRun:
    """进程内 run 状态与订阅者；token 热路径只写这里，不写数据库。"""

    def __init__(self, row: ChatRunRow):
        self.task: asyncio.Task[None] | None = None
        self.subscribers: set[asyncio.Queue[tuple[str, str]]] = set()
        self.state: dict[str, Any] = {
            "run_id": row.id,
            "session_id": row.session_id,
            "status": row.status,
            "user_message": row.user_message,
            "assistant_content": row.assistant_content,
            "thinking_blocks": row.thinking_blocks or [],
            "tool_activity": row.tool_activity or [],
            "error": row.error,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    def update(self, **patch: Any) -> None:
        self.state.update(patch)
        self.state["updated_at"] = datetime.now(UTC).isoformat()

    def payload(self) -> dict[str, Any]:
        return dict(self.state)


_ACTIVE_RUNS: dict[str, _ActiveRun] = {}


def _json_event(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _enqueue_run_event(queue: asyncio.Queue[tuple[str, str]], item: tuple[str, str]) -> None:
    """向订阅队列写入事件；队列满时丢弃旧事件，保留最新状态。"""

    while True:
        try:
            queue.put_nowait(item)
            return
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return


def _broadcast_run_event(run_id: str, event: str, data: dict[str, Any]) -> None:
    active = _ACTIVE_RUNS.get(run_id)
    if active is None:
        return
    payload = _json_event(data)
    for queue in active.subscribers:
        _enqueue_run_event(queue, (event, payload))


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
    model_profile: str | None,
    temperature: float,
    hit_iteration_limit: bool,
) -> str:
    """非流式收尾总结。"""
    response = await _get_llm_adapter(model_profile).generate(
        messages=_build_summary_fallback_messages(
            messages, hit_iteration_limit=hit_iteration_limit
        ),
        temperature=temperature,
    )
    return response.content


@lru_cache(maxsize=1)
def _get_settings() -> AstraCoreConfig:
    return AstraCoreConfig()


def _get_llm_profile(profile_id: str | None = None) -> LLMProfileConfig:
    return _get_settings().llm.get_profile(profile_id)


@lru_cache(maxsize=32)
def _get_llm_adapter_by_profile_id(profile_id: str) -> LLMAdapter:
    profile = _get_settings().llm.get_profile(profile_id)
    if profile.provider == "anthropic":
        return AnthropicAdapter(
            api_key=profile.api_key,
            default_model=profile.model,
            base_url=profile.base_url,
            max_tokens=profile.max_tokens,
            supports_temperature=profile.capabilities.temperature,
            use_anthropic_blocks=profile.capabilities.anthropic_blocks,
        )
    return OpenAIAdapter(
        api_key=profile.api_key,
        default_model=profile.model,
        base_url=profile.base_url,
        max_tokens=profile.max_tokens,
    )


def _get_llm_adapter(profile_id: str | None = None) -> LLMAdapter:
    profile = _get_llm_profile(profile_id)
    return _get_llm_adapter_by_profile_id(profile.id)


@lru_cache(maxsize=1)
def _get_memory_adapter() -> HybridMemoryAdapter:
    cfg = _get_settings().memory
    return HybridMemoryAdapter(redis_url=cfg.redis_url, db_url=cfg.db_url)


@lru_cache(maxsize=32)
def _get_chat_use_case(profile_id: str | None = None) -> ChatUseCase:
    return ChatUseCase(
        llm_adapter=_get_llm_adapter(profile_id),
        memory_adapter=_get_memory_adapter(),
        policy_engine=PolicyEngine(),
    )


def _resolve_tool_adapter(http_request: Request) -> ToolAdapter:
    """Get the tool adapter from app.state (set by lifespan) or fall back to builtins."""
    adapter = getattr(http_request.app.state, "tool_adapter", None)
    return adapter if adapter is not None else build_tool_adapter()


def _get_tool_loop_use_case(tool_adapter: ToolAdapter, profile_id: str | None = None) -> ToolLoopUseCase:
    cfg = _get_settings().agent
    return ToolLoopUseCase(
        llm_adapter=_get_llm_adapter(profile_id),
        tool_adapter=tool_adapter,
        policy_engine=PolicyEngine(),
        max_iterations=cfg.max_tool_iterations,
        max_tool_result_chars=cfg.max_tool_result_chars,
        tool_timeout_s=cfg.tool_timeout_s,
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


class MessageItem(BaseModel):
    role: str
    content: str
    thinking_blocks: list[str] = Field(default_factory=list)
    tool_activity: list[dict[str, Any]] = Field(default_factory=list)


class SessionMessagesResponse(BaseModel):
    messages: list[MessageItem]
    total: int
    has_more: bool


class ChatRequest(BaseModel):
    """Chat request model."""

    message: str
    session_id: UUID | None = None
    model_profile: str | None = None
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
    model_profile: str
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatRunResponse(BaseModel):
    run_id: str
    session_id: str
    status: str


class ChatRunStateResponse(BaseModel):
    run_id: str
    session_id: str
    status: str
    user_message: str
    assistant_content: str = ""
    thinking_blocks: list[str] = Field(default_factory=list)
    tool_activity: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""
    created_at: str
    updated_at: str
    completed_at: str | None = None


def _build_llm_kwargs(request: ChatRequest, profile: LLMProfileConfig) -> dict[str, Any]:
    """Build provider kwargs after checking the selected profile capabilities."""
    llm_kwargs: dict[str, Any] = {}
    if request.enable_thinking:
        if not profile.capabilities.thinking:
            logger.info("LLM profile '%s' does not support thinking; falling back to normal mode", profile.id)
            return llm_kwargs
        llm_kwargs["enable_thinking"] = True
        llm_kwargs["thinking_budget"] = request.thinking_budget
    return llm_kwargs


def _ensure_tool_capability(request: ChatRequest, profile: LLMProfileConfig) -> None:
    if (request.use_tools or request.enable_web) and not profile.capabilities.tools:
        raise ValueError(f"LLM profile '{profile.id}' does not support tool calling")


async def _resolve_temperature(request: ChatRequest, profile: LLMProfileConfig) -> float:
    if request.temperature is not None:
        return request.temperature
    saved = await _get_setting_value("temperature")
    return float(saved) if saved else profile.temperature


_BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
_WEEKDAY_CN = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def _build_current_time_info(now: datetime | None = None) -> str:
    """生成注入给模型的当前北京时间上下文。"""
    beijing_now = (now or datetime.now(_BEIJING_TZ)).astimezone(_BEIJING_TZ)
    time_text = (
        f"{beijing_now.year}年{beijing_now.month}月{beijing_now.day}日 "
        f"{beijing_now.hour:02d}:{beijing_now.minute:02d}:{beijing_now.second:02d}"
        f"（{_WEEKDAY_CN[beijing_now.weekday()]}）"
    )
    today_text = f"{beijing_now.year}年{beijing_now.month}月{beijing_now.day}日"
    return "\n".join(
        [
            "【当前时间信息】",
            f"- 北京时间：{time_text}",
            "- 当用户问\"现在几点\"、\"什么时间\"时，直接告诉用户上述时间",
            f"- 当用户提到\"今天\"时，指的是{today_text}",
        ]
    )


def _render_skill_prompt(prompt: str, ai_name: str, owner_name: str) -> str:
    """将 system_prompt 中的动态占位符替换为请求时的上下文。"""
    result = prompt.replace("{{ai_name}}", ai_name or "AI 助手")
    result = result.replace("{{owner_name}}", owner_name or "用户")
    result = result.replace("{{current_time_info}}", _build_current_time_info())
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


def _run_row_to_state(row: ChatRunRow) -> ChatRunStateResponse:
    return ChatRunStateResponse(
        run_id=row.id,
        session_id=row.session_id,
        status=row.status,
        user_message=row.user_message,
        assistant_content=row.assistant_content,
        thinking_blocks=row.thinking_blocks or [],
        tool_activity=row.tool_activity or [],
        error=row.error,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
    )


async def _get_run_row(run_id: str) -> ChatRunRow | None:
    async with get_session(_get_settings().memory.db_url) as db:
        return await db.get(ChatRunRow, run_id)


async def _get_active_run_row(session_id: UUID) -> ChatRunRow | None:
    async with get_session(_get_settings().memory.db_url) as db:
        result = await db.execute(
            select(ChatRunRow)
            .where(
                ChatRunRow.session_id == str(session_id),
                ChatRunRow.status == "running",
            )
            .order_by(ChatRunRow.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def _update_run_row(run_id: str, **patch: Any) -> ChatRunRow | None:
    async with get_session(_get_settings().memory.db_url) as db:
        row = await db.get(ChatRunRow, run_id)
        if row is None:
            return None
        for key, value in patch.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(UTC)
        if row.status in _RUN_TERMINAL_STATUSES and row.completed_at is None:
            row.completed_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        return row


async def _update_conversation_from_messages(session_id: UUID, messages: list[Message]) -> None:
    visible = [m for m in messages if m.role in (MessageRole.USER, MessageRole.ASSISTANT)]
    preview = visible[-1].content[:256] if visible else ""
    async with get_session(_get_settings().memory.db_url) as db:
        row = await db.get(ConversationRow, str(session_id))
        if row is None:
            return
        if row.title == "新会话" and row.message_count == 0 and visible:
            first_user = next((m for m in visible if m.role == MessageRole.USER), None)
            if first_user:
                row.title = first_user.content[:24] or "新会话"
        row.last_message_preview = preview
        row.message_count = len(visible)
        row.updated_at = datetime.now(UTC)
        await db.commit()


def _update_active_run_state(run_id: str, **patch: Any) -> None:
    active = _ACTIVE_RUNS.get(run_id)
    if active is None:
        return
    active.update(**patch)


def _run_state_payload(row: ChatRunRow) -> dict[str, Any]:
    return _run_row_to_state(row).model_dump()


async def _create_run_row(request: ChatRequest, session_id: UUID) -> ChatRunRow:
    run_id = str(uuid4())
    now = datetime.now(UTC)
    row = ChatRunRow(
        id=run_id,
        session_id=str(session_id),
        status="running",
        request=request.model_dump(mode="json"),
        user_message=request.message,
        created_at=now,
        updated_at=now,
    )
    async with get_session(_get_settings().memory.db_url) as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row


def _broadcast_snapshot(run_id: str, row: ChatRunRow) -> None:
    _broadcast_run_event(run_id, "run_state", _run_state_payload(row))


async def _execute_normal_run(
    *,
    run_id: str,
    request: ChatRequest,
    session_id: UUID,
    profile: LLMProfileConfig,
    inject_system: str | None,
    temperature: float,
    context_max: int,
    llm_kwargs: dict[str, Any],
) -> None:
    memory = _get_memory_adapter()
    messages = [m for m in await memory.load_short_term(session_id) if m.role != MessageRole.SYSTEM]
    session = SessionState(session_id=session_id)
    session.restore_messages(messages)
    session.add_message(Message(role=MessageRole.USER, content=request.message))
    await _save_short_term_safe(session_id, session.get_messages())
    await _update_conversation_from_messages(session_id, session.get_messages())

    llm_messages = session.get_messages()
    if context_max and len(llm_messages) > context_max:
        llm_messages = llm_messages[-context_max:]
    if inject_system:
        llm_messages = [Message(role=MessageRole.SYSTEM, content=inject_system)] + llm_messages

    accumulated_content = ""
    thinking_blocks: list[str] = []
    assistant_metadata: dict[str, Any] = {}
    async for event in _get_llm_adapter(profile.id).generate_stream(
        messages=llm_messages,
        temperature=temperature,
        **llm_kwargs,
    ):
        if event.event_type == StreamEventType.TEXT_DELTA and event.content:
            accumulated_content += event.content
            _broadcast_run_event(run_id, "message", {"text": event.content})
            _update_active_run_state(
                run_id,
                assistant_content=accumulated_content,
                thinking_blocks=list(thinking_blocks),
                tool_activity=[],
            )
        elif event.event_type == StreamEventType.THINKING_DELTA and event.content:
            if not thinking_blocks:
                thinking_blocks.append("")
                _broadcast_run_event(run_id, "thinking_start", {"round": 1})
            thinking_blocks[-1] += event.content
            _broadcast_run_event(run_id, "thinking", {"text": event.content})
            _update_active_run_state(
                run_id,
                assistant_content=accumulated_content,
                thinking_blocks=list(thinking_blocks),
                tool_activity=[],
            )
        elif event.event_type == StreamEventType.DONE:
            raw_blocks = event.metadata.get(ChatUseCase._ANTHROPIC_BLOCKS_KEY)
            if isinstance(raw_blocks, list) and raw_blocks:
                assistant_metadata[ChatUseCase._ANTHROPIC_BLOCKS_KEY] = raw_blocks

    session.add_message(
        Message(
            role=MessageRole.ASSISTANT,
            content=accumulated_content,
            metadata=assistant_metadata,
        )
    )
    await _save_short_term_safe(session_id, session.get_messages())
    await _update_conversation_from_messages(session_id, session.get_messages())
    row = await _update_run_row(
        run_id,
        assistant_content=accumulated_content,
        thinking_blocks=thinking_blocks,
        tool_activity=[],
        status="done",
    )
    if row:
        _broadcast_snapshot(run_id, row)
    _broadcast_run_event(run_id, "done", {})


async def _execute_tool_run(
    *,
    run_id: str,
    request: ChatRequest,
    session_id: UUID,
    profile: LLMProfileConfig,
    tool_adapter: ToolAdapter,
    inject_system: str | None,
    temperature: float,
    context_max: int,
    llm_kwargs: dict[str, Any],
) -> None:
    all_tools = {d.name for d in tool_adapter.get_definitions()}
    allowed_tools = all_tools
    if not request.enable_rag:
        allowed_tools = allowed_tools - {"search_knowledge_base"}
    if not request.enable_web:
        allowed_tools = allowed_tools - {"web_search"}

    memory = _get_memory_adapter()
    tool_loop = _get_tool_loop_use_case(tool_adapter, profile.id)
    messages = [m for m in await memory.load_short_term(session_id) if m.role != MessageRole.SYSTEM]
    if len(messages) > context_max:
        messages = messages[-context_max:]
    session = SessionState(session_id=session_id)
    initial_messages: list[Message] = []
    if inject_system:
        initial_messages.append(Message(role=MessageRole.SYSTEM, content=inject_system))
    initial_messages.extend(messages)
    session.restore_messages(initial_messages)
    session.add_message(Message(role=MessageRole.USER, content=request.message))
    await _save_short_term_safe(session_id, session.get_messages())
    await _update_conversation_from_messages(session_id, session.get_messages())

    round_count = 0
    round_text_buffer: list[str] = []
    in_tool_round = False
    assistant_content = ""
    thinking_blocks: list[str] = []
    tool_activity: list[dict[str, Any]] = []
    async for event in tool_loop.execute_stream_with_tools(
        session,
        allowed_tools=allowed_tools,
        **llm_kwargs,
    ):
        if event.event_type == StreamEventType.ROUND_START:
            for text in round_text_buffer:
                if not thinking_blocks:
                    thinking_blocks.append("")
                thinking_blocks[-1] += text
                _broadcast_run_event(run_id, "thinking", {"text": text})
            round_text_buffer = []
            in_tool_round = False
            round_count = int(event.metadata.get("round", round_count + 1))
            thinking_blocks.append("")
            _broadcast_run_event(run_id, "thinking_start", {"round": round_count})
        elif event.event_type == StreamEventType.TEXT_DELTA and event.content:
            if in_tool_round:
                if not thinking_blocks:
                    thinking_blocks.append("")
                thinking_blocks[-1] += event.content
                _broadcast_run_event(run_id, "thinking", {"text": event.content})
            else:
                round_text_buffer.append(event.content)
        elif event.event_type == StreamEventType.THINKING_DELTA and event.content:
            if not thinking_blocks:
                thinking_blocks.append("")
            thinking_blocks[-1] += event.content
            _broadcast_run_event(run_id, "thinking", {"text": event.content})
        elif event.event_type == StreamEventType.THINKING_STOP:
            _broadcast_run_event(
                run_id,
                "thinking_stop",
                {"duration_ms": event.metadata.get("duration_ms", 0)},
            )
        elif event.event_type == StreamEventType.TOOL_CALL and event.tool_call:
            if not in_tool_round:
                if not thinking_blocks:
                    thinking_blocks.append("")
                for text in round_text_buffer:
                    thinking_blocks[-1] += text
                    _broadcast_run_event(run_id, "thinking", {"text": text})
                round_text_buffer = []
                in_tool_round = True
            item = {
                "name": event.tool_call.name,
                "done": False,
                "input": event.tool_call.arguments,
            }
            tool_activity.append(item)
            _broadcast_run_event(
                run_id,
                "tool_start",
                {"tool": event.tool_call.name, "input": event.tool_call.arguments},
            )
        elif event.event_type == StreamEventType.TOOL_RESULT:
            tool_name = str(event.metadata.get("tool", ""))
            result_text = str(event.metadata.get("result", ""))
            for item in reversed(tool_activity):
                if item.get("name") == tool_name and not item.get("done"):
                    item.update(
                        {
                            "done": True,
                            "result": result_text,
                            "isError": bool(event.metadata.get("is_error", False)),
                            "durationMs": int(event.metadata.get("duration_ms", 0)),
                        }
                    )
                    break
            _broadcast_run_event(
                run_id,
                "tool_result",
                {
                    "tool": tool_name,
                    "input": event.metadata.get("input", {}),
                    "result": result_text,
                    "is_error": event.metadata.get("is_error", False),
                    "duration_ms": event.metadata.get("duration_ms", 0),
                },
            )

        _update_active_run_state(
            run_id,
            assistant_content=assistant_content,
            thinking_blocks=list(thinking_blocks),
            tool_activity=list(tool_activity),
        )

    for text in round_text_buffer:
        assistant_content += text
        _broadcast_run_event(run_id, "message", {"text": text})

    safe_messages = _strip_dangling_tool_calls(session.get_messages())
    if _needs_summary_fallback(safe_messages):
        hit_iteration_limit = (
            not tool_loop.unlimited
            and round_count >= tool_loop.max_iterations
            and safe_messages
            and safe_messages[-1].role == MessageRole.TOOL
        )
        summary_text = ""
        async for event in _get_llm_adapter(profile.id).generate_stream(
            messages=_build_summary_fallback_messages(
                safe_messages,
                hit_iteration_limit=hit_iteration_limit,
            ),
            temperature=temperature,
        ):
            if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                summary_text += event.content
                assistant_content += event.content
                _broadcast_run_event(run_id, "message", {"text": event.content})
        if summary_text.strip():
            session.add_message(Message(role=MessageRole.ASSISTANT, content=summary_text))
        else:
            hint = "信息量较大，本轮分析已暂停。会话已保存，请发送「继续」让 AI 继续完成分析。"
            assistant_content += hint
            session.add_message(Message(role=MessageRole.ASSISTANT, content=hint))
            _broadcast_run_event(run_id, "message", {"text": hint})

    await _save_short_term_safe(session_id, session.get_messages())
    await _update_conversation_from_messages(session_id, session.get_messages())
    row = await _update_run_row(
        run_id,
        assistant_content=assistant_content,
        thinking_blocks=thinking_blocks,
        tool_activity=[{**item, "done": True} for item in tool_activity],
        status="done",
    )
    if row:
        _broadcast_snapshot(run_id, row)
    _broadcast_run_event(run_id, "done", {})


async def _run_chat_in_background(
    *,
    run_id: str,
    request: ChatRequest,
    session_id: UUID,
    tool_adapter: ToolAdapter,
) -> None:
    try:
        profile = _get_llm_profile(request.model_profile)
        _ensure_tool_capability(request, profile)
        inject_system = await _build_system_prompt(
            skill_id=request.skill_id,
            disable_skill=request.disable_skill,
            enable_rag=request.enable_rag,
            message=request.message,
        )
        temperature = await _resolve_temperature(request, profile)
        context_max = int(await _get_setting_value("context_max_messages") or "20")
        llm_kwargs = _build_llm_kwargs(request, profile)

        if request.use_tools or request.enable_web:
            await _execute_tool_run(
                run_id=run_id,
                request=request,
                session_id=session_id,
                profile=profile,
                tool_adapter=tool_adapter,
                inject_system=inject_system,
                temperature=temperature,
                context_max=context_max,
                llm_kwargs=llm_kwargs,
            )
        else:
            await _execute_normal_run(
                run_id=run_id,
                request=request,
                session_id=session_id,
                profile=profile,
                inject_system=inject_system,
                temperature=temperature,
                context_max=context_max,
                llm_kwargs=llm_kwargs,
            )
    except asyncio.CancelledError:
        row = await _update_run_row(run_id, status="cancelled", error="用户已停止生成")
        if row:
            _broadcast_snapshot(run_id, row)
        _broadcast_run_event(run_id, "error", {"message": "用户已停止生成"})
        raise
    except Exception as e:
        logger.exception("后台 chat run 失败: run_id=%s", run_id)
        row = await _update_run_row(run_id, status="error", error=str(e))
        if row:
            _broadcast_snapshot(run_id, row)
        _broadcast_run_event(run_id, "error", {"message": str(e)})
    finally:
        _ACTIVE_RUNS.pop(run_id, None)


async def _run_with_tools(
    request: "ChatRequest", session_id: UUID, tool_adapter: ToolAdapter
) -> str:
    """Execute a chat turn through the tool loop."""
    profile = _get_llm_profile(request.model_profile)
    _ensure_tool_capability(request, profile)
    memory = _get_memory_adapter()
    tool_loop = _get_tool_loop_use_case(tool_adapter, profile.id)

    messages = await memory.load_short_term(session_id)
    session = SessionState(session_id=session_id)
    if messages:
        session.restore_messages(messages)

    all_tools = {d.name for d in tool_adapter.get_definitions()}
    allowed_tools = all_tools
    if not request.enable_rag:
        allowed_tools = allowed_tools - {"search_knowledge_base"}
    if not request.enable_web:
        allowed_tools = allowed_tools - {"web_search"}

    session.add_message(Message(role=MessageRole.USER, content=request.message))
    try:
        session = await tool_loop.execute_with_tools(session, allowed_tools=allowed_tools)
    except Exception:
        # 出错时也保存（至少保留用户消息），同时清理悬空的 tool_use
        await _save_short_term_safe(session_id, session.get_messages())
        raise

    safe_messages = _strip_dangling_tool_calls(session.get_messages())
    if _needs_summary_fallback(safe_messages):
        temperature = await _resolve_temperature(request, profile)
        summary = await _generate_summary_fallback(
            messages=safe_messages,
            model_profile=profile.id,
            temperature=temperature,
            hit_iteration_limit=False,
        )
        if summary.strip():
            session.add_message(Message(role=MessageRole.ASSISTANT, content=summary))
        else:
            hint = "信息量较大，本轮分析已暂停。会话已保存，请发送「继续」让 AI 继续完成分析。"
            session.add_message(Message(role=MessageRole.ASSISTANT, content=hint))

    await _save_short_term_safe(session_id, session.get_messages())

    last_assistant = next(
        (m for m in reversed(session.get_messages()) if m.role == MessageRole.ASSISTANT),
        None,
    )
    return last_assistant.content if last_assistant else ""


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: UUID) -> None:
    """删除指定会话的后端记忆（短期消息历史）。"""
    logger.info("删除会话: session_id=%s", session_id)
    await _get_memory_adapter().delete_session_memory(session_id)


@router.get("/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
async def get_session_messages(
    session_id: UUID,
    limit: int = 30,
    offset: int = 0,
) -> SessionMessagesResponse:
    """分页加载会话消息（仅返回 user/assistant，从末尾往前翻页）。

    offset=0, limit=30 → 最新 30 条；offset=30, limit=30 → 再往前 30 条。
    """
    all_msgs = await _get_memory_adapter().load_short_term(session_id)
    visible = [
        m for m in all_msgs
        if m.role in (MessageRole.USER, MessageRole.ASSISTANT)
    ]
    total = len(visible)
    end = total - offset
    start = max(0, end - limit)
    page = visible[start:end]

    async with get_session(_get_settings().memory.db_url) as db:
        result = await db.execute(
            select(ChatRunRow)
            .where(
                ChatRunRow.session_id == str(session_id),
                ChatRunRow.status == "done",
            )
            .order_by(ChatRunRow.created_at.asc())
        )
        runs = result.scalars().all()

    run_meta: dict[tuple[str, str], list[tuple[list[str], list[dict[str, Any]]]]] = {}
    for run in runs:
        if not run.assistant_content:
            continue
        key = (run.user_message, run.assistant_content)
        run_meta.setdefault(key, []).append((run.thinking_blocks or [], run.tool_activity or []))

    message_meta: dict[int, tuple[list[str], list[dict[str, Any]]]] = {}
    for index in range(1, len(visible)):
        previous = visible[index - 1]
        current = visible[index]
        if previous.role != MessageRole.USER or current.role != MessageRole.ASSISTANT:
            continue
        matches = run_meta.get((previous.content, current.content))
        if matches:
            message_meta[index] = matches.pop(0)

    return SessionMessagesResponse(
        messages=[
            MessageItem(
                role=m.role.value,
                content=m.content,
                thinking_blocks=message_meta.get(start + i, ([], []))[0],
                tool_activity=message_meta.get(start + i, ([], []))[1],
            )
            for i, m in enumerate(page)
        ],
        total=total,
        has_more=start > 0,
    )


@router.get("/sessions/{session_id}/runs/active", response_model=ChatRunStateResponse | None)
async def get_active_run(session_id: UUID) -> ChatRunStateResponse | None:
    for active in _ACTIVE_RUNS.values():
        if active.state.get("session_id") == str(session_id):
            return ChatRunStateResponse(**active.payload())

    row = await _get_active_run_row(session_id)
    if row is None:
        return None
    return _run_row_to_state(row)


@router.post("/runs", response_model=ChatRunResponse, status_code=202)
async def create_chat_run(request: ChatRequest, http_request: Request) -> ChatRunResponse:
    session_id = request.session_id or uuid4()
    active = await _get_active_run_row(session_id)
    if active is not None and active.id in _ACTIVE_RUNS:
        return ChatRunResponse(run_id=active.id, session_id=active.session_id, status=active.status)
    if active is not None:
        await _update_run_row(active.id, status="error", error="服务重启导致生成任务中断")

    row = await _create_run_row(request, session_id)
    tool_adapter = _resolve_tool_adapter(http_request)
    active_run = _ActiveRun(row)
    _ACTIVE_RUNS[row.id] = active_run
    active_run.task = asyncio.create_task(
        _run_chat_in_background(
            run_id=row.id,
            request=request,
            session_id=session_id,
            tool_adapter=tool_adapter,
        )
    )
    logger.info("创建后台 chat run: run_id=%s, session=%s", row.id, session_id)
    return ChatRunResponse(run_id=row.id, session_id=str(session_id), status=row.status)


@router.get("/runs/{run_id}/stream")
async def stream_chat_run(run_id: UUID) -> EventSourceResponse:
    async def event_generator() -> AsyncIterator[dict[str, str]]:
        rid = str(run_id)
        row = await _get_run_row(rid)
        if row is None:
            yield {"event": "error", "data": _json_event({"message": "Run not found"})}
            return

        active = _ACTIVE_RUNS.get(rid)
        if active is not None:
            yield {"event": "run_state", "data": _json_event(active.payload())}
        else:
            yield {"event": "run_state", "data": _json_event(_run_state_payload(row))}

        if row.status in _RUN_TERMINAL_STATUSES:
            yield {"event": "done" if row.status == "done" else "error", "data": _json_event({"message": row.error})}
            return

        if active is None:
            row = await _update_run_row(rid, status="error", error="生成任务已中断，请重新发送")
            if row:
                yield {"event": "run_state", "data": _json_event(_run_state_payload(row))}
            yield {"event": "error", "data": _json_event({"message": "生成任务已中断，请重新发送"})}
            return

        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=200)
        active.subscribers.add(queue)
        try:
            while True:
                event, data = await queue.get()
                yield {"event": event, "data": data}
                if event in {"done", "error"}:
                    break
        finally:
            active.subscribers.discard(queue)

    return EventSourceResponse(event_generator())


@router.post("/runs/{run_id}/cancel", response_model=ChatRunStateResponse)
async def cancel_chat_run(run_id: UUID) -> ChatRunStateResponse:
    rid = str(run_id)
    active = _ACTIVE_RUNS.get(rid)
    if active is not None and active.task is not None:
        active.task.cancel()
        active.update(status="cancelled", error="用户已停止生成", completed_at=datetime.now(UTC).isoformat())
    row = await _update_run_row(rid, status="cancelled", error="用户已停止生成")
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    _broadcast_snapshot(rid, row)
    _broadcast_run_event(rid, "error", {"message": "用户已停止生成"})
    return _run_row_to_state(row)


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest, http_request: Request) -> ChatResponse:
    """Chat endpoint. Set use_tools=true to route through the tool loop."""
    session_id = request.session_id or uuid4()
    profile = _get_llm_profile(request.model_profile)

    try:
        if request.use_tools:
            tool_adapter = _resolve_tool_adapter(http_request)
            content = await _run_with_tools(request, session_id, tool_adapter)
            return ChatResponse(session_id=session_id, message=content, model_profile=profile.id, model=profile.model)

        temperature = await _resolve_temperature(request, profile)
        use_case = _get_chat_use_case(profile.id)
        response_message = await use_case.execute(
            session_id=session_id,
            user_message=request.message,
            temperature=temperature,
        )
        return ChatResponse(
            session_id=session_id,
            message=response_message.content,
            model_profile=profile.id,
            model=profile.model,
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
    """兼容旧入口：创建后台 run，并订阅该 run。"""
    run = await create_chat_run(request, http_request)
    return await stream_chat_run(UUID(run.run_id))
