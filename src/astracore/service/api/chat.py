"""Chat API endpoints."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from astracore.adapters.db.models import ChatRunRow, ChatSessionRow, ConversationRow
from sqlalchemy.orm.attributes import flag_modified as _flag_modified
from astracore.adapters.db.session import get_session
from astracore.adapters.memory.hybrid import HybridMemoryAdapter
from astracore.core.domain.chat_context import ChatContext
from astracore.core.domain.message import MessageRole
from astracore.core.ports.llm import StreamEventType
from astracore.core.ports.tool import ToolAdapter
from astracore.runtime.observability.logger import get_logger
from astracore.runtime.policy.engine import PolicyEngine
from astracore.sdk.config import AstraCoreConfig
from astracore.service.api import rag as rag_api
from astracore.service.builtin_tools import build_tool_adapter
from astracore.service.chat_pipeline import ChatPipeline
from astracore.service.skill_router import SkillRouter

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
            "created_at": _utc_iso(row.created_at),
            "updated_at": _utc_iso(row.updated_at),
            "completed_at": _utc_iso(row.completed_at) if row.completed_at else None,
            "anchor_skill": None,
            "routed_skills": [],
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


@lru_cache(maxsize=1)
def _get_memory_adapter() -> HybridMemoryAdapter:
    cfg = _get_settings().memory
    return HybridMemoryAdapter(redis_url=cfg.redis_url, db_url=cfg.db_url)


@lru_cache(maxsize=1)
def _get_skill_router() -> SkillRouter:
    cfg = _get_settings()
    return SkillRouter(config=cfg, db_url=cfg.memory.db_url)


@lru_cache(maxsize=1)
def _get_chat_pipeline() -> ChatPipeline:
    cfg = _get_settings()
    return ChatPipeline(
        config=cfg,
        memory=_get_memory_adapter(),
        rag_pipeline=rag_api._get_rag_pipeline(),
        policy=PolicyEngine(),
        tool_adapter=build_tool_adapter(),
        skill_router=_get_skill_router() if cfg.skill_routing.mode != "off" else None,
    )


def _resolve_tool_adapter(http_request: Request) -> ToolAdapter:
    """Get the tool adapter from app.state (set by lifespan) or fall back to builtins."""
    adapter = getattr(http_request.app.state, "tool_adapter", None)
    return adapter if adapter is not None else build_tool_adapter()


# ------------------------------------------------------------------
# HTTP models
# ------------------------------------------------------------------


def _utc_iso(dt: datetime) -> str:
    """Return an ISO-8601 string with explicit UTC offset (+00:00).

    SQLite may return naive datetimes even with timezone=True columns;
    treat them as UTC so JavaScript always parses them correctly.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


class MessageItem(BaseModel):
    id: str = ""
    role: str
    content: str
    thinking_blocks: list[str] = Field(default_factory=list)
    tool_activity: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = ""


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
        created_at=_utc_iso(row.created_at),
        updated_at=_utc_iso(row.updated_at),
        completed_at=_utc_iso(row.completed_at) if row.completed_at else None,
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


async def _execute_run(*, run_id: str, ctx: ChatContext) -> None:
    """Stream a fully-resolved ChatContext and broadcast SSE events for the run."""
    accumulated_content = ""
    thinking_blocks: list[str] = []
    tool_activity: list[dict[str, Any]] = []
    round_count = 0
    round_text_buffer: list[str] = []
    in_tool_round = False

    async for event in _get_chat_pipeline().stream(ctx):
        if event.event_type == StreamEventType.DONE:
            if event.metadata.get("source") == "tool_loop":
                # Phase boundary: tool loop ended, summary phase begins.
                # Flush any buffered intermediate text as final assistant content.
                for text in round_text_buffer:
                    accumulated_content += text
                    _broadcast_run_event(run_id, "message", {"text": text})
                round_text_buffer = []
                in_tool_round = False
            else:
                # Final DONE; flush remaining buffer and exit.
                for text in round_text_buffer:
                    accumulated_content += text
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
            elif ctx.mode == "normal":
                # Normal mode: directly emit as message text.
                accumulated_content += event.content
                _broadcast_run_event(run_id, "message", {"text": event.content})
            else:
                # Tool-loop mode, between tool rounds: buffer until flush point.
                round_text_buffer.append(event.content)
        elif event.event_type == StreamEventType.THINKING_DELTA and event.content:
            if not thinking_blocks:
                thinking_blocks.append("")
                if ctx.mode == "normal":
                    _broadcast_run_event(run_id, "thinking_start", {"round": 1})
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
                "tool_call_id": event.tool_call.id,
                "done": False,
                "input": event.tool_call.arguments,
            }
            tool_activity.append(item)
            _broadcast_run_event(
                run_id,
                "tool_start",
                {
                    "tool": event.tool_call.name,
                    "tool_call_id": event.tool_call.id,
                    "input": event.tool_call.arguments,
                },
            )
        elif event.event_type == StreamEventType.TOOL_RESULT:
            tool_name = str(event.metadata.get("tool", ""))
            tool_call_id = str(event.metadata.get("tool_call_id", ""))
            result_text = str(event.metadata.get("result", ""))
            for item in tool_activity:
                if item.get("tool_call_id") == tool_call_id and not item.get("done"):
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
                    "tool_call_id": tool_call_id,
                    "input": event.metadata.get("input", {}),
                    "result": result_text,
                    "is_error": event.metadata.get("is_error", False),
                    "duration_ms": event.metadata.get("duration_ms", 0),
                },
            )
        elif event.event_type == StreamEventType.AGENT_START:
            _broadcast_run_event(
                run_id,
                "agent_start",
                {
                    "agent_id": event.metadata.get("agent_id"),
                    "task": event.metadata.get("task"),
                    "model": event.metadata.get("model"),
                },
            )
        elif event.event_type == StreamEventType.AGENT_TEXT_DELTA and event.content:
            _broadcast_run_event(
                run_id,
                "agent_message",
                {"agent_id": event.metadata.get("agent_id"), "text": event.content},
            )
        elif event.event_type == StreamEventType.AGENT_THINKING_DELTA and event.content:
            _broadcast_run_event(
                run_id,
                "agent_thinking",
                {"agent_id": event.metadata.get("agent_id"), "text": event.content},
            )
        elif event.event_type == StreamEventType.AGENT_TOOL_CALL and event.tool_call:
            _broadcast_run_event(
                run_id,
                "agent_tool_start",
                {
                    "agent_id": event.metadata.get("agent_id"),
                    "tool": event.tool_call.name,
                    "tool_call_id": event.tool_call.id,
                    "input": event.tool_call.arguments,
                },
            )
        elif event.event_type == StreamEventType.AGENT_TOOL_RESULT:
            _broadcast_run_event(
                run_id,
                "agent_tool_result",
                {
                    "agent_id": event.metadata.get("agent_id"),
                    "tool": event.metadata.get("tool"),
                    "tool_call_id": event.metadata.get("tool_call_id", ""),
                    "result": event.metadata.get("result"),
                    "is_error": event.metadata.get("is_error", False),
                    "duration_ms": event.metadata.get("duration_ms", 0),
                },
            )
        elif event.event_type == StreamEventType.AGENT_DONE:
            _broadcast_run_event(
                run_id,
                "agent_done",
                {
                    "agent_id": event.metadata.get("agent_id"),
                    "duration_ms": event.metadata.get("duration_ms", 0),
                    "error": event.metadata.get("error"),
                },
            )

        _update_active_run_state(
            run_id,
            assistant_content=accumulated_content,
            thinking_blocks=list(thinking_blocks),
            tool_activity=list(tool_activity),
        )

    conv_meta = await _update_conversation_from_messages(ctx.session_id)
    row = await _update_run_row(
        run_id,
        assistant_content=accumulated_content,
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
        ctx = await _get_chat_pipeline().prepare(
            message=request.message,
            session_id=session_id,
            tool_adapter=tool_adapter,
            model_profile=request.model_profile,
            temperature=request.temperature,
            use_tools=request.use_tools,
            enable_thinking=request.enable_thinking,
            thinking_budget=request.thinking_budget,
            enable_rag=request.enable_rag,
            enable_web=request.enable_web,
            skill_id=request.skill_id,
            disable_skill=request.disable_skill,
        )
        if ctx.anchor_skill or ctx.routed_skills:
            logger.info(
                "active skills for run %s: anchor=%s routed=%s",
                run_id,
                ctx.anchor_skill,
                ctx.routed_skills,
            )
            _update_active_run_state(
                run_id,
                anchor_skill=ctx.anchor_skill,
                routed_skills=list(ctx.routed_skills),
            )
            _broadcast_run_event(
                run_id,
                "auto_skills",
                {"anchor": ctx.anchor_skill, "routed": list(ctx.routed_skills)},
            )
        await _execute_run(run_id=run_id, ctx=ctx)
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


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: UUID) -> None:
    logger.info("删除会话: session_id=%s", session_id)
    await _get_memory_adapter().delete_session_memory(session_id)


@router.delete("/sessions/{session_id}/messages", status_code=204)
async def delete_session_message(
    session_id: UUID,
    role: Literal["user", "assistant"],
    message_id: str,
) -> None:
    """从会话历史中按轮次删除消息。

    通过 message_id（USER 消息 UUID）精确定位轮次，避免重复内容时误删。
    USER role：删除整个轮次（USER + 其后所有 ASSISTANT/TOOL，直到下一条 USER）。
    ASSISTANT role：仅删除该轮次的 ASSISTANT/TOOL 消息，保留 USER。
    """
    memory = _get_memory_adapter()
    messages = await memory.load_short_term(session_id)
    # 通过 UUID 精确定位轮次的 USER 消息
    idx = next(
        (i for i, m in enumerate(messages) if m.role == MessageRole.USER and str(m.id) == message_id),
        None,
    )
    if idx is None:
        raise HTTPException(status_code=404, detail="消息未找到")

    user_msg_content = messages[idx].content
    # 该轮次在同内容 USER 消息中的出现次序（0-based），用于定位对应的 ChatRunRow
    occurrence = sum(
        1 for m in messages[:idx] if m.role == MessageRole.USER and m.content == user_msg_content
    )

    # 该轮次的结束位置（下一条 USER 或列表末尾）
    end = idx + 1
    while end < len(messages) and messages[end].role != MessageRole.USER:
        end += 1
    new_messages = list(messages)
    if role == "user":
        new_messages = new_messages[:idx] + new_messages[end:]
    else:
        new_messages = new_messages[: idx + 1] + new_messages[end:]
    messages_data = [m.model_dump(mode="json") for m in new_messages]
    # 原子性地持久化到 DB：更新 ChatSessionRow + 删除对应 ChatRunRow 在同一事务中
    async with get_session(_get_settings().memory.db_url) as db:
        session_row = await db.get(ChatSessionRow, str(session_id))
        if session_row:
            session_row.messages = messages_data
            _flag_modified(session_row, "messages")
            session_row.updated_at = datetime.now(UTC)
        else:
            db.add(
                ChatSessionRow(
                    session_id=str(session_id),
                    messages=messages_data,
                    updated_at=datetime.now(UTC),
                )
            )
        # 只删除第 occurrence 条同内容的 ChatRunRow（按创建时间排序）
        run_result = await db.execute(
            select(ChatRunRow)
            .where(
                ChatRunRow.session_id == str(session_id),
                ChatRunRow.user_message == user_msg_content,
            )
            .order_by(ChatRunRow.created_at.asc())
        )
        matching_runs = run_result.scalars().all()
        if occurrence < len(matching_runs):
            await db.delete(matching_runs[occurrence])
        await db.commit()
    # DB 事务提交成功后 best-effort 更新 Redis（失败不影响已持久化的 DB 状态）
    try:
        await memory.save_short_term(session_id, new_messages)
    except Exception:
        pass


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
            folded.append(MessageItem(id=str(current.id), role=current.role.value, content=current.content, created_at=_utc_iso(current.created_at)))
            index += 1
            continue

        folded.append(MessageItem(id=str(current.id), role=current.role.value, content=current.content, created_at=_utc_iso(current.created_at)))
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
                    created_at=_utc_iso(run.completed_at or run.created_at),
                )
            )
            index = next_user_index
            continue

        for message in visible[index + 1:next_user_index]:
            folded.append(MessageItem(id=str(message.id), role=message.role.value, content=message.content, created_at=_utc_iso(message.created_at)))
        index = next_user_index

    total = len(folded)
    end = max(0, total - offset)
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
    """Simple (non-streaming) chat. Routing to tool loop is handled by pipeline.prepare()."""
    session_id = request.session_id or uuid4()
    try:
        ctx = await _get_chat_pipeline().prepare(
            message=request.message,
            session_id=session_id,
            tool_adapter=_resolve_tool_adapter(http_request),
            model_profile=request.model_profile,
            temperature=request.temperature,
            use_tools=request.use_tools,
            enable_thinking=request.enable_thinking,
            thinking_budget=request.thinking_budget,
            enable_rag=request.enable_rag,
            enable_web=request.enable_web,
            skill_id=request.skill_id,
            disable_skill=request.disable_skill,
        )
        content = await _get_chat_pipeline().execute(ctx)
        await _update_conversation_from_messages(session_id)
        return ChatResponse(
            session_id=session_id,
            message=content,
            model_profile=ctx.profile.id,
            model=ctx.profile.model,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/stream")
async def chat_stream(request: ChatRequest, http_request: Request) -> EventSourceResponse:
    """Legacy streaming entry point: creates a background run and subscribes to it."""
    run = await create_chat_run(request, http_request)
    return await stream_chat_run(UUID(run.run_id))
