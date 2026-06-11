"""Chat API endpoints."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm.attributes import flag_modified as _flag_modified
from sse_starlette.sse import EventSourceResponse

from astracore.infrastructure.db.models import ChatRunRow, ChatSessionRow, ConversationRow, UserRow
from astracore.infrastructure.db.session import get_session
from astracore.infrastructure.memory.hybrid import HybridMemoryAdapter
from astracore.infrastructure.memory.store import SQLMemoryStore
from astracore.infrastructure.memory.vector import MemoryVectorAdapter
from astracore.modules.auth.dependencies import get_current_user
from astracore.modules.chat.domain.chat_context import ChatContext
from astracore.modules.chat.domain.chat_options import ChatOptions
from astracore.modules.chat.domain.message import Message, MessageRole
from astracore.modules.chat.pipeline import ChatPipeline
from astracore.modules.memory.application.engine import MemoryEngine
from astracore.modules.rag import api as rag_api
from astracore.modules.tools.builtin import build_tool_adapter
from astracore.modules.tools.ports.tool import ToolAdapter
from astracore.sdk.config import AstraCoreConfig
from astracore.shared.observability.logger import get_logger
from astracore.shared.policy.engine import PolicyEngine
from astracore.shared.ports.llm import StreamEventType

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
def _get_vector_adapter() -> MemoryVectorAdapter:
    cfg = _get_settings()
    return MemoryVectorAdapter(
        persist_directory=cfg.retrieval.persist_directory,
        embedding_model=cfg.retrieval.embedding_model,
    )


@lru_cache(maxsize=1)
def _get_chat_pipeline() -> ChatPipeline:
    cfg = _get_settings()
    return ChatPipeline(
        config=cfg,
        memory=_get_memory_adapter(),
        rag_pipeline=rag_api._get_rag_pipeline(),
        policy=PolicyEngine(),
        tool_adapter=build_tool_adapter(db_url=cfg.memory.db_url),
        vector_adapter=_get_vector_adapter(),
    )


def _resolve_tool_adapter(http_request: Request) -> ToolAdapter:
    """Get the tool adapter from app.state (set by lifespan) or fall back to builtins."""
    adapter = getattr(http_request.app.state, "tool_adapter", None)
    return (
        adapter if adapter is not None else build_tool_adapter(db_url=_get_settings().memory.db_url)
    )


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
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None


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

    def to_options(self) -> ChatOptions:
        return ChatOptions(
            model_profile=self.model_profile,
            temperature=self.temperature,
            use_tools=self.use_tools,
            enable_thinking=self.enable_thinking,
            thinking_budget=self.thinking_budget,
            enable_rag=self.enable_rag,
            enable_web=self.enable_web,
        )


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


def _run_row_to_messages(row: ChatRunRow) -> list[MessageItem]:
    """Convert a persisted chat run into UI-visible chat messages."""
    messages = [
        MessageItem(
            id=row.id,
            role=MessageRole.USER.value,
            content=row.user_message,
            created_at=_utc_iso(row.created_at),
        )
    ]
    if row.assistant_content:
        messages.append(
            MessageItem(
                id=f"{row.id}:assistant",
                role=MessageRole.ASSISTANT.value,
                content=row.assistant_content,
                thinking_blocks=row.thinking_blocks or [],
                tool_activity=row.tool_activity or [],
                created_at=_utc_iso(row.completed_at or row.updated_at),
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                model=row.model,
            )
        )
    return messages


_VISIBLE_RUN_STATUSES = {"done", "cancelled", "error"}


async def _load_done_runs(session_id: UUID) -> list[ChatRunRow]:
    async with get_session(_get_settings().memory.db_url) as db:
        result = await db.execute(
            select(ChatRunRow)
            .where(
                ChatRunRow.session_id == str(session_id),
                ChatRunRow.status.in_(_VISIBLE_RUN_STATUSES),
            )
            .order_by(ChatRunRow.created_at.asc())
        )
        return list(result.scalars().all())


def _paginate_messages(
    messages: list[MessageItem], limit: int, offset: int
) -> SessionMessagesResponse:
    total = len(messages)
    end = max(0, total - offset)
    start = max(0, end - limit)
    return SessionMessagesResponse(
        messages=messages[start:end],
        total=total,
        has_more=start > 0,
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


async def _create_run_row(
    request: ChatRequest, session_id: UUID, user_id: str = "default"
) -> ChatRunRow:
    run_id = str(uuid4())
    now = datetime.now(UTC)
    row = ChatRunRow(
        id=run_id,
        session_id=str(session_id),
        user_id=user_id,
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
    """Update conversation metadata from completed chat runs.

    Returns the updated fields, or None if the conversation row does not exist.
    """
    runs = await _load_done_runs(session_id)
    visible = [message for run in runs for message in _run_row_to_messages(run)]
    preview = visible[-1].content[:256] if visible else ""
    async with get_session(_get_settings().memory.db_url) as db:
        row = await db.get(ConversationRow, str(session_id))
        if row is None:
            return None
        if row.title == "新会话" and row.message_count == 0 and visible:
            first_user = next((m for m in visible if m.role == MessageRole.USER.value), None)
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


async def _rebuild_short_term_from_runs(session_id: UUID) -> None:
    """Sync the short-term memory cache with current ChatRunRow state.

    Must be called after any message deletion so the LLM no longer sees
    deleted messages on the next turn. Rebuilds Message objects from the
    surviving done-runs and overwrites both Redis and DB caches.
    """
    runs = await _load_done_runs(session_id)
    messages: list[Message] = []
    for run in runs:
        messages.append(Message(role=MessageRole.USER, content=run.user_message))
        if run.assistant_content:
            messages.append(Message(role=MessageRole.ASSISTANT, content=run.assistant_content))
    await _get_memory_adapter().save_short_term(session_id, messages)


# ------------------------------------------------------------------
# Chat execution (background tasks)
# ------------------------------------------------------------------


async def _execute_run(*, run_id: str, ctx: ChatContext, user_id: str = "default") -> None:
    """Stream a fully-resolved ChatContext and broadcast SSE events for the run."""
    accumulated_content = ""
    thinking_blocks: list[str] = []
    tool_activity: list[dict[str, Any]] = []
    round_count = 0
    round_text_buffer: list[str] = []
    in_tool_round = False
    total_input_tokens = 0
    total_output_tokens = 0
    memory_saved_by_tool = False  # AI 本轮是否主动调用了 save_memory

    async for event in _get_chat_pipeline().stream(ctx):
        if event.event_type == StreamEventType.DONE:
            if event.metadata.get("source") == "tool_loop":
                # Phase boundary: closing round is about to begin.
                # Flush buffered intermediate text as a single event to avoid queue overflow.
                if round_text_buffer:
                    flushed = "".join(round_text_buffer)
                    accumulated_content += flushed
                    _broadcast_run_event(run_id, "message", {"text": flushed})
                round_text_buffer = []
                in_tool_round = False
            else:
                # Final DONE; extract usage, flush remaining buffer, and exit.
                _u = event.metadata.get("usage", {})
                total_input_tokens = int(_u.get("input_tokens", 0))
                total_output_tokens = int(_u.get("output_tokens", 0))
                if round_text_buffer:
                    flushed = "".join(round_text_buffer)
                    accumulated_content += flushed
                    _broadcast_run_event(run_id, "message", {"text": flushed})
                break
        elif event.event_type == StreamEventType.ROUND_START:
            if round_text_buffer:
                if not thinking_blocks:
                    thinking_blocks.append("")
                flushed = "".join(round_text_buffer)
                thinking_blocks[-1] += flushed
                _broadcast_run_event(run_id, "thinking", {"text": flushed})
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
        elif (
            event.event_type
            in {
                StreamEventType.TOOL_CALL,
                StreamEventType.TOOL_CALL_ERROR,
            }
            and event.tool_call
        ):
            if not in_tool_round:
                if not thinking_blocks:
                    thinking_blocks.append("")
                if round_text_buffer:
                    flushed = "".join(round_text_buffer)
                    thinking_blocks[-1] += flushed
                    _broadcast_run_event(run_id, "thinking", {"text": flushed})
                round_text_buffer = []
                in_tool_round = True
            if event.tool_call.name == "save_memory":
                memory_saved_by_tool = True
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

    row = await _update_run_row(
        run_id,
        assistant_content=accumulated_content,
        thinking_blocks=thinking_blocks,
        tool_activity=[{**item, "done": True} for item in tool_activity],
        status="done",
        input_tokens=total_input_tokens or None,
        output_tokens=total_output_tokens or None,
        model=ctx.profile.model or None,
    )
    conv_meta = await _update_conversation_from_messages(ctx.session_id)
    if total_input_tokens or total_output_tokens:
        _broadcast_run_event(
            run_id,
            "usage",
            {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "model": ctx.profile.model,
            },
        )
    if memory_saved_by_tool:
        logger.info("本轮已调用 save_memory，跳过自动提取: run_id=%s", run_id)
    else:
        logger.info("记忆自动提取: run_id=%s, content_len=%d", run_id, len(accumulated_content))
        try:
            cfg = _get_settings()
            user_engine = MemoryEngine(SQLMemoryStore(cfg.memory.db_url), user_id=user_id)
            await user_engine.extract_and_store(
                session_id=ctx.session_id,
                user_message=ctx.message,
                assistant_content=accumulated_content,
                source_run_id=run_id,
                llm_adapter=_get_chat_pipeline().get_llm_adapter(ctx.profile),
                model=ctx.profile.model,
            )
            logger.info("记忆自动提取完成: run_id=%s", run_id)
        except Exception:
            logger.exception("Memory 自动提取失败，run_id=%s", run_id)
    if row:
        _broadcast_snapshot(run_id, row)
    _broadcast_run_event(run_id, "done", {"conversation": conv_meta} if conv_meta else {})


async def _run_chat_in_background(
    *,
    run_id: str,
    request: ChatRequest,
    session_id: UUID,
    tool_adapter: ToolAdapter,
    user_id: str = "default",
) -> None:
    try:
        ctx = await _get_chat_pipeline().prepare(
            message=request.message,
            session_id=session_id,
            options=request.to_options(),
            tool_adapter=tool_adapter,
            user_id=user_id,
        )
        await _execute_run(run_id=run_id, ctx=ctx, user_id=user_id)
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
async def delete_session(
    session_id: UUID,
    current_user: UserRow = Depends(get_current_user),
) -> None:
    logger.info("删除会话: session_id=%s", session_id)
    cfg = _get_settings()
    await _get_memory_adapter().delete_session_memory(session_id)
    await MemoryEngine(
        SQLMemoryStore(cfg.memory.db_url),
        user_id=current_user.id,
        vector_adapter=_get_vector_adapter(),
    ).delete_conversation_memories(session_id)
    async with get_session(_get_settings().memory.db_url) as db:
        await db.execute(delete(ChatRunRow).where(ChatRunRow.session_id == str(session_id)))
        session_row = await db.get(ChatSessionRow, str(session_id))
        if session_row is not None:
            await db.delete(session_row)
        await db.commit()


@router.delete("/sessions/{session_id}/messages", status_code=204)
async def delete_session_message(
    session_id: UUID,
    role: Literal["user", "assistant"],
    message_id: str,
    current_user: UserRow = Depends(get_current_user),
) -> None:
    """Delete a visible history message using its run-based message id."""
    run_id = message_id.removesuffix(":assistant")
    async with get_session(_get_settings().memory.db_url) as db:
        row = await db.get(ChatRunRow, run_id)
        if row is None or row.session_id != str(session_id):
            raise HTTPException(status_code=404, detail="消息未找到")
        if role == "user":
            await db.delete(row)
        else:
            row.assistant_content = ""
            row.thinking_blocks = []
            row.tool_activity = []
            _flag_modified(row, "thinking_blocks")
            _flag_modified(row, "tool_activity")
            row.updated_at = datetime.now(UTC)
        await db.commit()
    await _update_conversation_from_messages(session_id)
    await _rebuild_short_term_from_runs(session_id)


@router.get("/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
async def get_session_messages(
    session_id: UUID,
    limit: int = 30,
    offset: int = 0,
    current_user: UserRow = Depends(get_current_user),
) -> SessionMessagesResponse:
    runs = await _load_done_runs(session_id)
    messages = [message for run in runs for message in _run_row_to_messages(run)]
    return _paginate_messages(messages, limit=limit, offset=offset)


@router.get("/sessions/{session_id}/runs/active", response_model=ChatRunStateResponse | None)
async def get_active_run(
    session_id: UUID,
    current_user: UserRow = Depends(get_current_user),
) -> ChatRunStateResponse | None:
    for active in _ACTIVE_RUNS.values():
        if active.state.get("session_id") == str(session_id):
            return ChatRunStateResponse(**active.payload())

    row = await _get_active_run_row(session_id)
    if row is None:
        return None
    return _run_row_to_state(row)


@router.post("/runs", response_model=ChatRunResponse, status_code=202)
async def create_chat_run(
    request: ChatRequest,
    http_request: Request,
    current_user: UserRow = Depends(get_current_user),
) -> ChatRunResponse:
    session_id = request.session_id or uuid4()
    active = await _get_active_run_row(session_id)
    if active is not None and active.id in _ACTIVE_RUNS:
        return ChatRunResponse(run_id=active.id, session_id=active.session_id, status=active.status)
    if active is not None:
        await _update_run_row(active.id, status="error", error="服务重启导致生成任务中断")

    row = await _create_run_row(request, session_id, user_id=current_user.id)
    tool_adapter = _resolve_tool_adapter(http_request)
    active_run = _ActiveRun(row)
    _ACTIVE_RUNS[row.id] = active_run
    active_run.task = asyncio.create_task(
        _run_chat_in_background(
            run_id=row.id,
            request=request,
            session_id=session_id,
            tool_adapter=tool_adapter,
            user_id=current_user.id,
        )
    )
    logger.info("创建后台 chat run: run_id=%s, session=%s", row.id, session_id)
    return ChatRunResponse(run_id=row.id, session_id=str(session_id), status=row.status)


@router.get("/runs/{run_id}/stream")
async def stream_chat_run(
    run_id: UUID,
    current_user: UserRow = Depends(get_current_user),
) -> EventSourceResponse:
    async def event_generator() -> AsyncIterator[dict[str, str]]:
        rid = str(run_id)
        row = await _get_run_row(rid)
        if row is None:
            yield {"event": "error", "data": _json_event({"message": "Run not found"})}
            return
        if row.user_id != current_user.id and current_user.role != "admin":
            yield {"event": "error", "data": _json_event({"message": "Access denied"})}
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
                yield {
                    "event": "run_state",
                    "data": _json_event(_run_row_to_state(row).model_dump()),
                }
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
async def cancel_chat_run(
    run_id: UUID,
    current_user: UserRow = Depends(get_current_user),
) -> ChatRunStateResponse:
    rid = str(run_id)
    row = await _get_run_row(rid)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if row.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
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
async def chat(
    request: ChatRequest,
    http_request: Request,
    current_user: UserRow = Depends(get_current_user),
) -> ChatResponse:
    """Simple (non-streaming) chat. Routing to tool loop is handled by pipeline.prepare()."""
    session_id = request.session_id or uuid4()
    row = await _create_run_row(request, session_id, user_id=current_user.id)
    try:
        ctx = await _get_chat_pipeline().prepare(
            message=request.message,
            session_id=session_id,
            options=request.to_options(),
            tool_adapter=_resolve_tool_adapter(http_request),
            user_id=current_user.id,
        )
        content = await _get_chat_pipeline().execute(ctx)
        await _update_run_row(
            row.id,
            assistant_content=content,
            thinking_blocks=[],
            tool_activity=[],
            status="done",
        )
        try:
            cfg = _get_settings()
            user_engine = MemoryEngine(SQLMemoryStore(cfg.memory.db_url), user_id=current_user.id)
            await user_engine.extract_and_store(
                session_id=session_id,
                user_message=request.message,
                assistant_content=content,
                source_run_id=row.id,
                llm_adapter=_get_chat_pipeline().get_llm_adapter(ctx.profile),
                model=ctx.profile.model,
            )
        except Exception:
            logger.exception("Memory 自动提取失败，run_id=%s", row.id)
        await _update_conversation_from_messages(session_id)
        return ChatResponse(
            session_id=session_id,
            message=content,
            model_profile=ctx.profile.id,
            model=ctx.profile.model,
        )
    except Exception as e:
        await _update_run_row(row.id, status="error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    http_request: Request,
    current_user: UserRow = Depends(get_current_user),
) -> EventSourceResponse:
    """Legacy streaming entry point: creates a background run and subscribes to it."""
    run = await create_chat_run(request, http_request, current_user)
    return await stream_chat_run(UUID(run.run_id), current_user)
