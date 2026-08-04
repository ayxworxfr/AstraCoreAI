"""Append-only transcript 领域模型 —— 会话的唯一可审计事件流。

短期 Redis/JSON 快照是物化视图；本模块描述事件本身与重建逻辑。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from astracore.modules.chat.domain.message import Message, MessageRole, ToolCall, ToolResult
from astracore.shared.utils.json_utils import json_safe


class TranscriptKind(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    COMPACT = "compact"


class TranscriptEntry(BaseModel):
    """单条 append-only 事件。"""

    id: UUID = Field(default_factory=uuid4)
    kind: TranscriptKind
    content: str = ""
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_input: dict[str, Any] | None = None
    message_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def message_to_entries(message: Message) -> list[TranscriptEntry]:
    """把一条 Message 展开为 1..N 条 transcript 事件。"""
    mid = str(message.id)
    meta = json_safe(dict(message.metadata or {}))

    if meta.get("compacted"):
        return [
            TranscriptEntry(
                kind=TranscriptKind.COMPACT,
                content=message.content,
                message_id=mid,
                metadata=meta,
                created_at=message.created_at,
            )
        ]

    if message.role == MessageRole.USER:
        return [
            TranscriptEntry(
                kind=TranscriptKind.USER,
                content=message.content,
                message_id=mid,
                metadata=meta,
                created_at=message.created_at,
            )
        ]

    if message.role == MessageRole.ASSISTANT:
        entries: list[TranscriptEntry] = [
            TranscriptEntry(
                kind=TranscriptKind.ASSISTANT,
                content=message.content,
                message_id=mid,
                metadata=meta,
                created_at=message.created_at,
            )
        ]
        for tc in message.tool_calls:
            entries.append(
                TranscriptEntry(
                    kind=TranscriptKind.TOOL_USE,
                    content="",
                    tool_name=tc.name,
                    tool_call_id=tc.id,
                    tool_input=json_safe(tc.arguments),
                    message_id=mid,
                    created_at=tc.created_at,
                )
            )
        return entries

    if message.role == MessageRole.TOOL:
        return [
            TranscriptEntry(
                kind=TranscriptKind.TOOL_RESULT,
                content=tr.content,
                tool_name=tr.name,
                tool_call_id=tr.tool_call_id,
                message_id=mid,
                metadata=json_safe({"is_error": tr.is_error, **(tr.metadata or {})}),
                created_at=tr.created_at,
            )
            for tr in message.tool_results
        ]

    return []


def entries_to_messages(entries: list[TranscriptEntry]) -> list[Message]:
    """从事件流重建 LLM 可用的 Message 列表（含工具轨迹）。"""
    messages: list[Message] = []
    pending_assistant: Message | None = None

    def flush_assistant() -> None:
        nonlocal pending_assistant
        if pending_assistant is not None:
            messages.append(pending_assistant)
            pending_assistant = None

    for entry in entries:
        if entry.kind == TranscriptKind.COMPACT:
            flush_assistant()
            messages.append(
                Message(
                    role=MessageRole.USER,
                    content=entry.content,
                    metadata={**entry.metadata, "compacted": True, "synthetic": True},
                )
            )
        elif entry.kind == TranscriptKind.USER:
            flush_assistant()
            messages.append(
                Message(role=MessageRole.USER, content=entry.content, metadata=entry.metadata)
            )
        elif entry.kind == TranscriptKind.ASSISTANT:
            flush_assistant()
            pending_assistant = Message(
                role=MessageRole.ASSISTANT,
                content=entry.content,
                metadata=entry.metadata,
            )
        elif entry.kind == TranscriptKind.TOOL_USE:
            if pending_assistant is None:
                pending_assistant = Message(role=MessageRole.ASSISTANT, content="")
            pending_assistant.tool_calls.append(
                ToolCall(
                    id=entry.tool_call_id or str(uuid4()),
                    name=entry.tool_name or "",
                    arguments=entry.tool_input or {},
                )
            )
        elif entry.kind == TranscriptKind.TOOL_RESULT:
            flush_assistant()
            # 合并连续 tool_result 到同一 TOOL 消息
            if messages and messages[-1].role == MessageRole.TOOL:
                messages[-1].tool_results.append(
                    ToolResult(
                        tool_call_id=entry.tool_call_id or "",
                        name=entry.tool_name or "",
                        content=entry.content,
                        is_error=bool(entry.metadata.get("is_error")),
                    )
                )
            else:
                messages.append(
                    Message(
                        role=MessageRole.TOOL,
                        content="",
                        tool_results=[
                            ToolResult(
                                tool_call_id=entry.tool_call_id or "",
                                name=entry.tool_name or "",
                                content=entry.content,
                                is_error=bool(entry.metadata.get("is_error")),
                            )
                        ],
                    )
                )

    flush_assistant()
    return messages
