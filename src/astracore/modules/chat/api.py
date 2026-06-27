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

from astracore.infrastructure.attachments.local_fs import LocalFSAttachmentStorage
from astracore.infrastructure.db.models import AttachmentRow, ChatRunRow, ChatSessionRow, UserRow
from astracore.infrastructure.db.session import get_session
from astracore.infrastructure.memory.hybrid import HybridMemoryAdapter
from astracore.infrastructure.memory.store import SQLMemoryStore
from astracore.infrastructure.memory.vector import MemoryVectorAdapter
from astracore.modules.attachments.domain import AttachmentCapabilityError, AttachmentRef
from astracore.modules.auth.dependencies import get_current_user
from astracore.modules.chat.application.run_executor import (
    execute_run_loop,
    update_conversation_meta,
    update_run_row,
)
from astracore.modules.chat.application.run_factory import create_chat_run_row
from astracore.modules.chat.domain.chat_context import ChatContext
from astracore.modules.chat.domain.chat_options import ChatOptions
from astracore.modules.chat.domain.message import Message, MessageRole
from astracore.modules.chat.pipeline import ChatPipeline
from astracore.modules.memory.application.engine import MemoryEngine
from astracore.modules.rag import api as rag_api
from astracore.modules.tools.builtin import build_tool_adapter
from astracore.modules.tools.ports.tool import ToolAdapter
from astracore.sdk.config import AstraCoreConfig
from astracore.shared.domain.hitl import HITLAnswer, PendingQuestion
from astracore.shared.observability.logger import get_logger
from astracore.shared.policy.engine import PolicyConfig as _EnginePolicyConfig
from astracore.shared.policy.engine import PolicyEngine

router = APIRouter()
logger = get_logger(__name__)

_RUN_TERMINAL_STATUSES = {"done", "error", "cancelled"}


class _ActiveRun:
    """In-process run state and subscriber queues; hot token path writes here, not the DB."""

    def __init__(self, row: ChatRunRow):
        self.task: asyncio.Task[None] | None = None
        self.subscribers: set[asyncio.Queue[tuple[str, str]]] = set()
        # HITL: one pending question at a time per run; future resolved by POST /answer
        self._hitl_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}
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
            "pending_question": None,
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
    cfg = _get_settings().storage
    return HybridMemoryAdapter(redis_url=cfg.redis_url, db_url=cfg.db_url)


@lru_cache(maxsize=1)
def _get_vector_adapter() -> MemoryVectorAdapter | None:
    cfg = _get_settings()
    if not cfg.storage.vector.enabled:
        return None
    return MemoryVectorAdapter(
        persist_directory=cfg.storage.vector.persist_directory,
        embedding_model=cfg.storage.vector.embedding_model,
    )


@lru_cache(maxsize=1)
def _get_attachment_storage() -> LocalFSAttachmentStorage:
    from pathlib import Path  # noqa: PLC0415

    return LocalFSAttachmentStorage(base_path=Path("data/attachments"))


@lru_cache(maxsize=1)
def _get_chat_pipeline() -> ChatPipeline:
    cfg = _get_settings()
    rag_pipeline = rag_api._get_rag_pipeline() if cfg.storage.vector.enabled else None
    return ChatPipeline(
        config=cfg,
        memory=_get_memory_adapter(),
        rag_pipeline=rag_pipeline,
        policy=PolicyEngine(
            config=_EnginePolicyConfig(
                retry=cfg.policy.retry,
                timeout=cfg.policy.timeout,
                compaction=cfg.policy.compaction,
            )
        ),
        tool_adapter=build_tool_adapter(db_url=cfg.storage.db_url),
        vector_adapter=_get_vector_adapter(),
        attachment_storage=_get_attachment_storage(),
    )


async def _resolve_attachment_refs(
    attachment_ids: list[str], user_id: str, db_url: str
) -> list[AttachmentRef]:
    """Load AttachmentRow records from DB and build AttachmentRef list (without bytes).

    Pipeline.prepare() will load the bytes via AttachmentStoragePort.
    """
    if not attachment_ids:
        return []
    refs: list[AttachmentRef] = []
    async with get_session(db_url) as db:
        result = await db.execute(select(AttachmentRow).where(AttachmentRow.id.in_(attachment_ids)))
        rows = result.scalars().all()

    row_map = {r.id: r for r in rows}
    for aid in attachment_ids:
        row = row_map.get(aid)
        if row is None:
            continue
        if row.user_id != user_id:
            raise HTTPException(status_code=403, detail=f"无权访问附件 {aid}")
        refs.append(
            AttachmentRef(
                id=row.id,
                mime_type=row.mime_type,
                filename=row.filename,
                size_bytes=row.size_bytes,
                storage_key=row.storage_key,
            )
        )
    return refs


def _resolve_tool_adapter(http_request: Request) -> ToolAdapter:
    """Get the tool adapter from app.state (set by lifespan) or fall back to builtins."""
    adapter = getattr(http_request.app.state, "tool_adapter", None)
    return (
        adapter
        if adapter is not None
        else build_tool_adapter(db_url=_get_settings().storage.db_url)
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
    metadata: dict[str, Any] = Field(default_factory=dict)
    thinking_blocks: list[str] = Field(default_factory=list)
    tool_activity: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
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
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=0)
    use_tools: bool = False
    thinking_mode: str | None = None
    thinking_budget: int = Field(default=8000, ge=1000, le=32000)
    reasoning_effort: str | None = None
    verbosity: str | None = None
    enable_rag: bool = False
    enable_web: bool = False
    attachment_ids: list[str] = Field(default_factory=list)

    def to_options(self) -> ChatOptions:
        return ChatOptions(
            model_profile=self.model_profile,
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            use_tools=self.use_tools,
            thinking_mode=self.thinking_mode,
            thinking_budget=self.thinking_budget,
            reasoning_effort=self.reasoning_effort,
            verbosity=self.verbosity,
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
    pending_question: dict[str, Any] | None = None


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


def _attachment_metadata_from_run(
    row: ChatRunRow,
    attachment_rows: dict[str, AttachmentRow],
) -> dict[str, Any]:
    attachment_ids = _attachment_ids_from_run(row)
    if not isinstance(attachment_ids, list) or not attachment_ids:
        return {}

    refs: list[dict[str, Any]] = []
    for raw_id in attachment_ids:
        attachment_id = str(raw_id)
        attachment = attachment_rows.get(attachment_id)
        if attachment is None:
            continue
        refs.append(
            {
                "id": attachment.id,
                "mime_type": attachment.mime_type,
                "filename": attachment.filename,
                "size_bytes": attachment.size_bytes,
            }
        )
    return {"attachment_refs": refs} if refs else {}


def _attachment_ids_from_run(row: ChatRunRow) -> list[str]:
    raw_ids = row.request.get("attachment_ids", [])
    if not isinstance(raw_ids, list):
        return []
    return [str(attachment_id) for attachment_id in raw_ids]


def _run_row_to_messages(
    row: ChatRunRow,
    attachment_rows: dict[str, AttachmentRow] | None = None,
) -> list[MessageItem]:
    """Convert a persisted chat run into UI-visible chat messages."""
    messages = [
        MessageItem(
            id=row.id,
            role=MessageRole.USER.value,
            content=row.user_message,
            metadata=_attachment_metadata_from_run(row, attachment_rows or {}),
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
                cache_read_input_tokens=row.cache_read_input_tokens,
                cache_creation_input_tokens=row.cache_creation_input_tokens,
                model=row.model,
            )
        )
    return messages


_VISIBLE_RUN_STATUSES = {"done", "cancelled", "error"}


async def _load_done_runs(session_id: UUID) -> list[ChatRunRow]:
    async with get_session(_get_settings().storage.db_url) as db:
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
    async with get_session(_get_settings().storage.db_url) as db:
        return await db.get(ChatRunRow, run_id)


async def _get_active_run_row(session_id: UUID) -> ChatRunRow | None:
    async with get_session(_get_settings().storage.db_url) as db:
        result = await db.execute(
            select(ChatRunRow)
            .where(
                ChatRunRow.session_id == str(session_id),
                ChatRunRow.status == "running",
            )
            .order_by(ChatRunRow.created_at.desc())
        )
        for row in result.scalars():
            trigger_source = (row.request or {}).get("trigger_source", "user")
            if trigger_source == "user":
                return row
        return None


async def _update_run_row(run_id: str, **patch: Any) -> ChatRunRow | None:
    return await update_run_row(_get_settings().storage.db_url, run_id, **patch)


async def _create_run_row(
    request: ChatRequest, session_id: UUID, user_id: str = "default"
) -> ChatRunRow:
    return await create_chat_run_row(
        db_url=_get_settings().storage.db_url,
        session_id=session_id,
        prompt=request.message,
        user_id=user_id,
        trigger_source="user",
        request_payload=request.model_dump(mode="json"),
    )


async def _update_conversation_from_messages(session_id: UUID) -> dict[str, Any] | None:
    """Update conversation metadata from completed chat runs.

    Returns the updated fields, or None if the conversation row does not exist.
    """
    return await update_conversation_meta(_get_settings().storage.db_url, session_id)


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
    cfg = _get_settings()

    async def _hitl_callback(q: PendingQuestion) -> dict[str, Any]:
        """Suspend the run, broadcast a question to the frontend, and await the answer."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        active = _ACTIVE_RUNS.get(run_id)
        if active is None:
            raise RuntimeError("run not found in active runs")
        active._hitl_futures[q.question_id] = fut
        active.update(status="awaiting_input", pending_question=q.model_dump(mode="json"))
        _broadcast_run_event(
            run_id,
            "user_input_required",
            {
                **active.payload(),
                "pending_question": q.model_dump(mode="json"),
            },
        )
        try:
            answer = await asyncio.wait_for(fut, timeout=cfg.hitl.inline_question_timeout)
        except TimeoutError:
            return {"selected": [], "freeform": None, "error": "timeout"}
        finally:
            active._hitl_futures.pop(q.question_id, None)
            active.update(status="running", pending_question=None)
            _broadcast_run_event(run_id, "user_input_resolved", {"question_id": q.question_id})
        return answer

    await execute_run_loop(
        run_id=run_id,
        ctx=ctx,
        user_id=user_id,
        pipeline=_get_chat_pipeline(),
        db_url=cfg.storage.db_url,
        event_sink=lambda name, data: _broadcast_run_event(run_id, name, data),
        state_sink=lambda **patch: _update_active_run_state(run_id, **patch),
        snapshot_sink=lambda row: _broadcast_snapshot(run_id, row),
        hitl_callback=_hitl_callback,
    )


async def _run_chat_in_background(
    *,
    run_id: str,
    request: ChatRequest,
    session_id: UUID,
    tool_adapter: ToolAdapter,
    user_id: str = "default",
) -> None:
    try:
        cfg = _get_settings()
        attachment_refs = await _resolve_attachment_refs(
            request.attachment_ids, user_id, cfg.storage.db_url
        )
        opts = request.to_options().apply(attachments=attachment_refs)
        ctx = await _get_chat_pipeline().prepare(
            message=request.message,
            session_id=session_id,
            options=opts,
            tool_adapter=tool_adapter,
            user_id=user_id,
        )
        await _execute_run(run_id=run_id, ctx=ctx, user_id=user_id)
    except AttachmentCapabilityError as e:
        row = await _update_run_row(run_id, status="error", error=str(e))
        if row:
            _broadcast_snapshot(run_id, row)
        _broadcast_run_event(run_id, "error", {"message": str(e)})
        return
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
        SQLMemoryStore(cfg.storage.db_url),
        user_id=current_user.id,
        vector_adapter=_get_vector_adapter(),
    ).delete_conversation_memories(session_id)
    async with get_session(_get_settings().storage.db_url) as db:
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
    async with get_session(_get_settings().storage.db_url) as db:
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
    attachment_ids = {
        str(attachment_id) for run in runs for attachment_id in _attachment_ids_from_run(run)
    }
    attachment_rows: dict[str, AttachmentRow] = {}
    if attachment_ids:
        async with get_session(_get_settings().storage.db_url) as db:
            result = await db.execute(
                select(AttachmentRow).where(
                    AttachmentRow.id.in_(attachment_ids),
                    AttachmentRow.user_id == current_user.id,
                )
            )
            attachment_rows = {row.id: row for row in result.scalars().all()}
    messages = [message for run in runs for message in _run_row_to_messages(run, attachment_rows)]
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


@router.post("/runs/{run_id}/answer")
async def answer_hitl_question(
    run_id: UUID,
    answer: HITLAnswer,
    current_user: UserRow = Depends(get_current_user),
) -> dict[str, bool]:
    """Submit the user's answer to a pending HITL question, unblocking the run."""
    rid = str(run_id)
    active = _ACTIVE_RUNS.get(rid)
    if active is None:
        raise HTTPException(status_code=404, detail="Run not found or not in active state")
    fut = active._hitl_futures.get(answer.question_id)
    if fut is None:
        raise HTTPException(status_code=409, detail="No pending question with that question_id")
    if fut.done():
        raise HTTPException(status_code=409, detail="Question already answered or timed out")
    fut.set_result({"selected": answer.selected, "freeform": answer.freeform})
    return {"ok": True}


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
        cfg = _get_settings()
        attachment_refs = await _resolve_attachment_refs(
            request.attachment_ids, current_user.id, cfg.storage.db_url
        )
        opts = request.to_options().apply(attachments=attachment_refs)
        ctx = await _get_chat_pipeline().prepare(
            message=request.message,
            session_id=session_id,
            options=opts,
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
            user_engine = MemoryEngine(SQLMemoryStore(cfg.storage.db_url), user_id=current_user.id)
            await user_engine.extract_and_store(
                session_id=session_id,
                user_message=request.message,
                assistant_content=content,
                source_run_id=row.id,
                llm_adapter=_get_chat_pipeline().get_llm_adapter(ctx.profile),
                model=ctx.profile.model,
                session_only=True,
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
    except AttachmentCapabilityError as e:
        await _update_run_row(row.id, status="error", error=str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e
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
