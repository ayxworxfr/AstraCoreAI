"""Chat API endpoints."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from astracore.adapters.db.models import ChatRunRow, ConversationRow
from astracore.adapters.db.session import get_session
from astracore.adapters.memory.hybrid import HybridMemoryAdapter
from astracore.core.application.chat import ChatUseCase
from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import StreamEventType
from astracore.core.ports.tool import ToolAdapter
from astracore.runtime.observability.logger import get_logger
from astracore.runtime.policy.engine import PolicyEngine
from astracore.sdk.config import AstraCoreConfig, LLMProfileConfig
from astracore.service.api import rag as rag_api
from astracore.service.builtin_tools import build_tool_adapter
from astracore.service.chat_orchestrator import ChatOrchestrator

router = APIRouter()
logger = get_logger(__name__)

_RUN_TERMINAL_STATUSES = {"done", "error", "cancelled"}


class _ActiveRun:
    """In-process run state and subscriber queues; hot token path writes here, not the DB."""

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
    """Write to subscriber queue; drop oldest entry when full to keep latest state."""
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


def _update_active_run_state(run_id: str, **patch: Any) -> None:
    active = _ACTIVE_RUNS.get(run_id)
    if active is None:
        return
    active.update(**patch)


def _broadcast_snapshot(run_id: str, row: ChatRunRow) -> None:
    _broadcast_run_event(run_id, "run_state", _run_row_to_state(row).model_dump())


# ------------------------------------------------------------------
# Module-level singletons
# ------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_settings() -> AstraCoreConfig:
    return AstraCoreConfig()


def _get_llm_profile(profile_id: str | None = None) -> LLMProfileConfig:
    return _get_settings().llm.get_profile(profile_id)


@lru_cache(maxsize=1)
def _get_memory_adapter() -> HybridMemoryAdapter:
    cfg = _get_settings().memory
    return HybridMemoryAdapter(redis_url=cfg.redis_url, db_url=cfg.db_url)


@lru_cache(maxsize=1)
def _get_chat_orchestrator() -> ChatOrchestrator:
    return ChatOrchestrator(
        config=_get_settings(),
        memory=_get_memory_adapter(),
        rag_pipeline=rag_api._get_rag_pipeline(),
        policy=PolicyEngine(),
    )


@lru_cache(maxsize=32)
def _get_chat_use_case(profile_id: str) -> ChatUseCase:
    profile = _get_llm_profile(profile_id)
    return ChatUseCase(
        llm_adapter=_get_chat_orchestrator().get_llm_adapter(profile),
        memory_adapter=_get_memory_adapter(),
        policy_engine=PolicyEngine(),
    )


def _resolve_tool_adapter(http_request: Request) -> ToolAdapter:
    """Get the tool adapter from app.state (set by lifespan) or fall back to builtins."""
    adapter = getattr(http_request.app.state, "tool_adapter", None)
    return adapter if adapter is not None else build_tool_adapter()


def _ensure_tool_capability(request: "ChatRequest", profile: LLMProfileConfig) -> None:
    if (request.use_tools or request.enable_web) and not profile.capabilities.tools:
        raise ValueError(f"LLM profile '{profile.id}' does not support tool calling")


def _build_llm_kwargs(request: "ChatRequest", profile: LLMProfileConfig) -> dict[str, Any]:
    if not request.enable_thinking:
        return {}
    if not profile.capabilities.thinking:
        logger.info(
            "LLM profile '%s' does not support thinking; falling back to normal mode", profile.id
        )
        return {}
    return {"enable_thinking": True, "thinking_budget": request.thinking_budget}


# ------------------------------------------------------------------
# HTTP models
# ------------------------------------------------------------------


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


# ------------------------------------------------------------------
# DB helpers (run / conversation tracking — HTTP-specific)
# ------------------------------------------------------------------


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


async def _update_conversation_from_messages(session_id: UUID) -> dict[str, Any] | None:
    """Update conversation metadata from the currently persisted session.

    Returns the updated fields, or None if the conversation row does not exist.
    """
    messages = await _get_memory_adapter().load_short_term(session_id)
    visible = [m for m in messages if m.role in (MessageRole.USER, MessageRole.ASSISTANT)]
    preview = visible[-1].content[:256] if visible else ""
    async with get_session(_get_settings().memory.db_url) as db:
        row = await db.get(ConversationRow, str(session_id))
        if row is None:
            return None
        if row.title == "新会话" and row.message_count == 0 and visible:
            first_user = next((m for m in visible if m.role == MessageRole.USER), None)
            if first_user:
                row.title = first_user.content[:24] or "新会话"
        row.last_message_preview = preview
        row.message_count = len(visible)
        row.updated_at = datetime.now(UTC)
        await db.commit()
        return {
            "title": row.title,
            "last_message_preview": row.last_message_preview,
            "message_count": row.message_count,
            "updated_at": row.updated_at.isoformat(),
        }


# ------------------------------------------------------------------
# Chat execution (background tasks)
# ------------------------------------------------------------------


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
    orchestrator = _get_chat_orchestrator()
    accumulated_content = ""
    thinking_blocks: list[str] = []

    async for event in orchestrator.stream_normal(
        session_id=session_id,
        message=request.message,
        profile=profile,
        inject_system=inject_system,
        temperature=temperature,
        context_max=context_max,
        llm_kwargs=llm_kwargs,
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

    conv_meta = await _update_conversation_from_messages(session_id)
    row = await _update_run_row(
        run_id,
        assistant_content=accumulated_content,
        thinking_blocks=thinking_blocks,
        tool_activity=[],
        status="done",
    )
    if row:
        _broadcast_snapshot(run_id, row)
    _broadcast_run_event(run_id, "done", {"conversation": conv_meta} if conv_meta else {})


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
    orchestrator = _get_chat_orchestrator()
    round_count = 0
    round_text_buffer: list[str] = []
    in_tool_round = False
    assistant_content = ""
    thinking_blocks: list[str] = []
    tool_activity: list[dict[str, Any]] = []

    async for event in orchestrator.stream_with_tools(
        session_id=session_id,
        message=request.message,
        profile=profile,
        tool_adapter=tool_adapter,
        inject_system=inject_system,
        temperature=temperature,
        context_max=context_max,
        enable_rag=request.enable_rag,
        enable_web=request.enable_web,
        llm_kwargs=llm_kwargs,
    ):
        if event.event_type == StreamEventType.DONE:
            if event.metadata.get("source") == "tool_loop":
                # Phase boundary: tool loop ended, summary phase begins.
                # Flush any buffered intermediate text as final assistant content.
                for text in round_text_buffer:
                    assistant_content += text
                    _broadcast_run_event(run_id, "message", {"text": text})
                round_text_buffer = []
                in_tool_round = False
            else:
                # Final DONE from orchestrator; flush remaining buffer and exit.
                for text in round_text_buffer:
                    assistant_content += text
                    _broadcast_run_event(run_id, "message", {"text": text})
                break
        elif event.event_type == StreamEventType.ROUND_START:
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
            item: dict[str, Any] = {
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

    conv_meta = await _update_conversation_from_messages(session_id)
    row = await _update_run_row(
        run_id,
        assistant_content=assistant_content,
        thinking_blocks=thinking_blocks,
        tool_activity=[{**item, "done": True} for item in tool_activity],
        status="done",
    )
    if row:
        _broadcast_snapshot(run_id, row)
    _broadcast_run_event(run_id, "done", {"conversation": conv_meta} if conv_meta else {})


async def _run_chat_in_background(
    *,
    run_id: str,
    request: ChatRequest,
    session_id: UUID,
    tool_adapter: ToolAdapter,
) -> None:
    try:
        orchestrator = _get_chat_orchestrator()
        profile = _get_llm_profile(request.model_profile)
        _ensure_tool_capability(request, profile)
        inject_system = await orchestrator.build_system_prompt(
            skill_id=request.skill_id,
            disable_skill=request.disable_skill,
            enable_rag=request.enable_rag,
            message=request.message,
        )
        temperature = await orchestrator.resolve_temperature(request.temperature, profile)
        context_max = int(await orchestrator.get_setting("context_max_messages") or "20")
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
    """Execute a non-streaming chat turn through the tool loop (used by POST /)."""
    orchestrator = _get_chat_orchestrator()
    profile = _get_llm_profile(request.model_profile)
    _ensure_tool_capability(request, profile)

    tool_loop = orchestrator.make_tool_loop(profile, tool_adapter)
    messages = await _get_memory_adapter().load_short_term(session_id)
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
        await orchestrator.save_session_safe(session_id, session.get_messages())
        raise

    safe_messages = orchestrator.strip_dangling_tool_calls(session.get_messages())
    if orchestrator.needs_summary_fallback(safe_messages):
        temperature = await orchestrator.resolve_temperature(request.temperature, profile)
        hit_limit = (
            not tool_loop.unlimited
            and bool(safe_messages)
            and safe_messages[-1].role == MessageRole.TOOL
        )
        response = await orchestrator.get_llm_adapter(profile).generate(
            messages=orchestrator.build_summary_fallback_messages(
                safe_messages, hit_iteration_limit=hit_limit
            ),
            temperature=temperature,
        )
        if response.content.strip():
            session.add_message(Message(role=MessageRole.ASSISTANT, content=response.content))
        else:
            hint = "信息量较大，本轮分析已暂停。会话已保存，请发送「继续」让 AI 继续完成分析。"
            session.add_message(Message(role=MessageRole.ASSISTANT, content=hint))

    await orchestrator.save_session_safe(session_id, session.get_messages())
    last_assistant = next(
        (m for m in reversed(session.get_messages()) if m.role == MessageRole.ASSISTANT),
        None,
    )
    return last_assistant.content if last_assistant else ""


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: UUID) -> None:
    logger.info("删除会话: session_id=%s", session_id)
    await _get_memory_adapter().delete_session_memory(session_id)


@router.get("/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
async def get_session_messages(
    session_id: UUID,
    limit: int = 30,
    offset: int = 0,
) -> SessionMessagesResponse:
    all_msgs = await _get_memory_adapter().load_short_term(session_id)
    visible = [m for m in all_msgs if m.role in (MessageRole.USER, MessageRole.ASSISTANT)]

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

    run_meta: dict[str, list[ChatRunRow]] = {}
    for run in runs:
        if not run.assistant_content:
            continue
        run_meta.setdefault(run.user_message, []).append(run)

    folded: list[MessageItem] = []
    index = 0
    while index < len(visible):
        current = visible[index]
        if current.role != MessageRole.USER:
            folded.append(MessageItem(role=current.role.value, content=current.content))
            index += 1
            continue

        folded.append(MessageItem(role=current.role.value, content=current.content))
        next_user_index = index + 1
        while next_user_index < len(visible) and visible[next_user_index].role != MessageRole.USER:
            next_user_index += 1

        matches = run_meta.get(current.content)
        if matches:
            run = matches.pop(0)
            folded.append(
                MessageItem(
                    role=MessageRole.ASSISTANT.value,
                    content=run.assistant_content,
                    thinking_blocks=run.thinking_blocks or [],
                    tool_activity=run.tool_activity or [],
                )
            )
            index = next_user_index
            continue

        for message in visible[index + 1:next_user_index]:
            folded.append(MessageItem(role=message.role.value, content=message.content))
        index = next_user_index

    total = len(folded)
    end = total - offset
    start = max(0, end - limit)
    page = folded[start:end]

    return SessionMessagesResponse(
        messages=page,
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
            yield {"event": "run_state", "data": _json_event(_run_row_to_state(row).model_dump())}

        if row.status in _RUN_TERMINAL_STATUSES:
            yield {
                "event": "done" if row.status == "done" else "error",
                "data": _json_event({"message": row.error}),
            }
            return

        if active is None:
            row = await _update_run_row(rid, status="error", error="生成任务已中断，请重新发送")
            if row:
                yield {"event": "run_state", "data": _json_event(_run_row_to_state(row).model_dump())}
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
        active.update(
            status="cancelled",
            error="用户已停止生成",
            completed_at=datetime.now(UTC).isoformat(),
        )
    row = await _update_run_row(rid, status="cancelled", error="用户已停止生成")
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    _broadcast_snapshot(rid, row)
    _broadcast_run_event(rid, "error", {"message": "用户已停止生成"})
    return _run_row_to_state(row)


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest, http_request: Request) -> ChatResponse:
    """Simple (non-streaming) chat. Set use_tools=true to route through the tool loop."""
    session_id = request.session_id or uuid4()
    profile = _get_llm_profile(request.model_profile)

    try:
        if request.use_tools:
            tool_adapter = _resolve_tool_adapter(http_request)
            content = await _run_with_tools(request, session_id, tool_adapter)
            await _update_conversation_from_messages(session_id)
            return ChatResponse(
                session_id=session_id,
                message=content,
                model_profile=profile.id,
                model=profile.model,
            )

        orchestrator = _get_chat_orchestrator()
        temperature = await orchestrator.resolve_temperature(request.temperature, profile)
        use_case = _get_chat_use_case(profile.id)
        response_message = await use_case.execute(
            session_id=session_id,
            user_message=request.message,
            temperature=temperature,
        )
        await _update_conversation_from_messages(session_id)
        return ChatResponse(
            session_id=session_id,
            message=response_message.content,
            model_profile=profile.id,
            model=profile.model,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/stream")
async def chat_stream(request: ChatRequest, http_request: Request) -> EventSourceResponse:
    """Legacy streaming entry point: creates a background run and subscribes to it."""
    run = await create_chat_run(request, http_request)
    return await stream_chat_run(UUID(run.run_id))
