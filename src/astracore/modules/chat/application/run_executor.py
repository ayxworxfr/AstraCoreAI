"""Shared chat run execution loop — used by HTTP and scheduler layers.

The executor streams a resolved ChatContext through ChatPipeline, accumulates
results, persists them to the DB, updates conversation metadata, and triggers
memory auto-extraction.

HTTP-specific concerns (SSE broadcasting, in-memory active-run state, HITL
prompt/response wiring) are injected via callbacks so this module has no
dependency on FastAPI, SSE, or ``_ACTIVE_RUNS``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

from astracore.infrastructure.db.models import ChatRunRow, ConversationRow
from astracore.infrastructure.db.session import get_session
from astracore.infrastructure.memory.store import SQLMemoryStore
from astracore.modules.memory.application.engine import MemoryEngine
from astracore.shared.observability.logger import get_logger
from astracore.shared.ports.llm import StreamEventType

if TYPE_CHECKING:
    from astracore.modules.chat.domain.chat_context import ChatContext
    from astracore.modules.chat.pipeline import ChatPipeline

logger = get_logger(__name__)

_TERMINAL_STATUSES = {"done", "error", "cancelled"}
_VISIBLE_STATUSES = {"done", "cancelled", "error"}

# Callable types for the HTTP layer's event hooks
EventSink = Callable[[str, dict[str, Any]], None]
StateSink = Callable[..., None]
SnapshotSink = Callable[[ChatRunRow], None]
HitlCallback = Callable[..., Any]


async def update_run_row(db_url: str, run_id: str, **patch: Any) -> ChatRunRow | None:
    """Persist field updates to a ChatRunRow; sets completed_at on terminal status."""
    async with get_session(db_url) as db:
        row = await db.get(ChatRunRow, run_id)
        if row is None:
            return None
        for key, value in patch.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(UTC)
        if row.status in _TERMINAL_STATUSES and row.completed_at is None:
            row.completed_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        return row


async def update_conversation_meta(db_url: str, session_id: UUID) -> dict[str, Any] | None:
    """Update ConversationRow preview/count/title from completed ChatRunRows.

    Returns the updated fields dict, or None when the conversation does not exist.
    """
    async with get_session(db_url) as db:
        result = await db.execute(
            select(ChatRunRow)
            .where(
                ChatRunRow.session_id == str(session_id),
                ChatRunRow.status.in_(_VISIBLE_STATUSES),
            )
            .order_by(ChatRunRow.created_at.asc())
        )
        runs = list(result.scalars().all())

        count = 0
        preview = ""
        first_user_content = ""
        for run in runs:
            if run.user_message:
                if not first_user_content:
                    first_user_content = run.user_message
                count += 1
                preview = run.user_message[:256]
            if run.assistant_content:
                count += 1
                preview = run.assistant_content[:256]

        conv = await db.get(ConversationRow, str(session_id))
        if conv is None:
            return None
        if conv.title == "新会话" and conv.message_count == 0 and first_user_content:
            conv.title = first_user_content[:24] or "新会话"
        conv.last_message_preview = preview
        conv.message_count = count
        conv.updated_at = datetime.now(UTC)
        await db.commit()
        return {
            "title": conv.title,
            "last_message_preview": conv.last_message_preview,
            "message_count": conv.message_count,
            "updated_at": conv.updated_at.isoformat(),
        }


async def execute_run_loop(
    *,
    run_id: str,
    ctx: ChatContext,
    user_id: str,
    pipeline: ChatPipeline,
    db_url: str,
    event_sink: EventSink | None = None,
    state_sink: StateSink | None = None,
    snapshot_sink: SnapshotSink | None = None,
    hitl_callback: HitlCallback | None = None,
) -> ChatRunRow | None:
    """Stream a ChatContext, accumulate output, persist to DB, and extract memories.

    Parameters
    ----------
    event_sink:
        Called as ``event_sink(event_name, data)`` for every SSE-level event.
        When ``None``, events are silently dropped (scheduler path).
    state_sink:
        Called as ``state_sink(assistant_content=..., thinking_blocks=..., tool_activity=...)``
        after each meaningful event to update in-memory active-run state.
        When ``None``, no in-memory state is updated (scheduler path).
    snapshot_sink:
        Called as ``snapshot_sink(row)`` with the final ChatRunRow for the
        HTTP layer to emit a ``run_state`` SSE snapshot.  No-op when ``None``.
    hitl_callback:
        Passed directly to ``pipeline.stream()``'s extra_context.
        When ``None``, HITL interactions are not available (scheduler path).
    """
    _sink: EventSink = event_sink or (lambda _n, _d: None)
    _state: StateSink = state_sink or (lambda **_kw: None)
    _snap: SnapshotSink = snapshot_sink or (lambda _r: None)

    accumulated_content = ""
    thinking_blocks: list[str] = []
    tool_activity: list[dict[str, Any]] = []
    round_text_buffer: list[str] = []
    in_tool_round = False
    round_count = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read_input_tokens = 0
    total_cache_creation_input_tokens = 0
    memory_saved_by_tool = False

    extra: dict[str, Any] = {}
    if hitl_callback is not None:
        extra["hitl_callback"] = hitl_callback

    async for event in pipeline.stream(ctx, extra_context=extra or None):
        if event.event_type == StreamEventType.DONE:
            if event.metadata.get("source") == "tool_loop":
                if round_text_buffer:
                    flushed = "".join(round_text_buffer)
                    accumulated_content += flushed
                    _sink("message", {"text": flushed})
                round_text_buffer = []
                in_tool_round = False
            else:
                _u = event.metadata.get("usage", {})
                total_input_tokens = int(_u.get("input_tokens", 0))
                total_output_tokens = int(_u.get("output_tokens", 0))
                total_cache_read_input_tokens = int(_u.get("cache_read_input_tokens", 0))
                total_cache_creation_input_tokens = int(_u.get("cache_creation_input_tokens", 0))
                if round_text_buffer:
                    flushed = "".join(round_text_buffer)
                    accumulated_content += flushed
                    _sink("message", {"text": flushed})
                break

        elif event.event_type == StreamEventType.ROUND_START:
            if round_text_buffer:
                if not thinking_blocks:
                    thinking_blocks.append("")
                flushed = "".join(round_text_buffer)
                thinking_blocks[-1] += flushed
                _sink("thinking", {"text": flushed})
            round_text_buffer = []
            in_tool_round = False
            round_count = int(event.metadata.get("round", round_count + 1))
            thinking_blocks.append("")
            _sink("thinking_start", {"round": round_count})

        elif event.event_type == StreamEventType.TEXT_DELTA and event.content:
            if in_tool_round:
                if not thinking_blocks:
                    thinking_blocks.append("")
                thinking_blocks[-1] += event.content
                _sink("thinking", {"text": event.content})
            elif ctx.mode == "normal":
                accumulated_content += event.content
                _sink("message", {"text": event.content})
            else:
                round_text_buffer.append(event.content)

        elif event.event_type == StreamEventType.THINKING_DELTA and event.content:
            if not thinking_blocks:
                thinking_blocks.append("")
                if ctx.mode == "normal":
                    _sink("thinking_start", {"round": 1})
            thinking_blocks[-1] += event.content
            _sink("thinking", {"text": event.content})

        elif event.event_type == StreamEventType.THINKING_STOP:
            _sink("thinking_stop", {"duration_ms": event.metadata.get("duration_ms", 0)})

        elif (
            event.event_type in {StreamEventType.TOOL_CALL, StreamEventType.TOOL_CALL_ERROR}
            and event.tool_call
        ):
            if not in_tool_round:
                if not thinking_blocks:
                    thinking_blocks.append("")
                if round_text_buffer:
                    flushed = "".join(round_text_buffer)
                    thinking_blocks[-1] += flushed
                    _sink("thinking", {"text": flushed})
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
            _sink(
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
            _sink(
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
            _sink(
                "agent_start",
                {
                    "agent_id": event.metadata.get("agent_id"),
                    "task": event.metadata.get("task"),
                    "model": event.metadata.get("model"),
                },
            )
        elif event.event_type == StreamEventType.AGENT_TEXT_DELTA and event.content:
            _sink(
                "agent_message",
                {"agent_id": event.metadata.get("agent_id"), "text": event.content},
            )
        elif event.event_type == StreamEventType.AGENT_THINKING_DELTA and event.content:
            _sink(
                "agent_thinking",
                {"agent_id": event.metadata.get("agent_id"), "text": event.content},
            )
        elif event.event_type == StreamEventType.AGENT_TOOL_CALL and event.tool_call:
            _sink(
                "agent_tool_start",
                {
                    "agent_id": event.metadata.get("agent_id"),
                    "tool": event.tool_call.name,
                    "tool_call_id": event.tool_call.id,
                    "input": event.tool_call.arguments,
                },
            )
        elif event.event_type == StreamEventType.AGENT_TOOL_RESULT:
            _sink(
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
            _sink(
                "agent_done",
                {
                    "agent_id": event.metadata.get("agent_id"),
                    "duration_ms": event.metadata.get("duration_ms", 0),
                    "error": event.metadata.get("error"),
                },
            )

        _state(
            assistant_content=accumulated_content,
            thinking_blocks=list(thinking_blocks),
            tool_activity=list(tool_activity),
        )

    row = await update_run_row(
        db_url,
        run_id,
        assistant_content=accumulated_content,
        thinking_blocks=thinking_blocks,
        tool_activity=[{**item, "done": True} for item in tool_activity],
        status="done",
        input_tokens=total_input_tokens or None,
        output_tokens=total_output_tokens or None,
        cache_read_input_tokens=total_cache_read_input_tokens or None,
        cache_creation_input_tokens=total_cache_creation_input_tokens or None,
        model=ctx.profile.model or None,
    )

    conv_meta = await update_conversation_meta(db_url, ctx.session_id)

    if total_input_tokens or total_output_tokens:
        _sink(
            "usage",
            {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "cache_read_input_tokens": total_cache_read_input_tokens,
                "cache_creation_input_tokens": total_cache_creation_input_tokens,
                "model": ctx.profile.model,
            },
        )

    if row:
        _snap(row)

    _sink("done", {"conversation": conv_meta} if conv_meta else {})

    # 取消自动提取，节约 token
    memory_saved_by_tool = True
    if memory_saved_by_tool:
        logger.info("本轮已调用 save_memory，跳过自动提取: run_id=%s", run_id)
    else:
        logger.info("记忆自动提取: run_id=%s, content_len=%d", run_id, len(accumulated_content))
        try:
            engine = MemoryEngine(SQLMemoryStore(db_url), user_id=user_id)
            await engine.extract_and_store(
                session_id=ctx.session_id,
                user_message=ctx.message,
                assistant_content=accumulated_content,
                source_run_id=run_id,
                llm_adapter=pipeline.get_llm_adapter(ctx.profile),
                model=ctx.profile.model,
                session_only=True,
            )
            logger.info("记忆自动提取完成: run_id=%s", run_id)
        except Exception:
            logger.exception("Memory 自动提取失败，run_id=%s", run_id)

    return row


async def run_pipeline_background(
    *,
    run_id: str,
    prompt: str,
    session_id: UUID,
    pipeline: ChatPipeline,
    db_url: str,
    user_id: str = "default",
    model_profile: str | None = None,
    use_tools: bool = True,
    enable_web: bool = False,
    event_sink: EventSink | None = None,
    state_sink: StateSink | None = None,
    snapshot_sink: SnapshotSink | None = None,
    hitl_callback: HitlCallback | None = None,
) -> ChatRunRow | None:
    """Build ChatContext from scratch and run execute_run_loop.

    Used by the scheduler runner which has no incoming HTTP request to draw
    options from.  Returns the final ChatRunRow or None on failure; never raises.
    """
    from astracore.modules.chat.domain.chat_options import ChatOptions  # noqa: PLC0415

    options = ChatOptions(
        model_profile=model_profile,
        use_tools=use_tools,
        enable_rag=False,
        enable_web=enable_web,
    )
    try:
        ctx = await pipeline.prepare(
            message=prompt,
            session_id=session_id,
            options=options,
            user_id=user_id,
        )
        return await execute_run_loop(
            run_id=run_id,
            ctx=ctx,
            user_id=user_id,
            pipeline=pipeline,
            db_url=db_url,
            event_sink=event_sink,
            state_sink=state_sink,
            snapshot_sink=snapshot_sink,
            hitl_callback=hitl_callback,
        )
    except asyncio.CancelledError:
        await update_run_row(db_url, run_id, status="cancelled", error="任务被取消")
        raise
    except Exception as exc:
        logger.exception("run_pipeline_background 失败: run_id=%s", run_id)
        await update_run_row(db_url, run_id, status="error", error=str(exc))
        return None
