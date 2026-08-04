"""会话历史：物化视图过滤 + transcript 回放重建。"""

from __future__ import annotations

from uuid import UUID

from astracore.infrastructure.chat.transcript_store import SQLTranscriptStore
from astracore.infrastructure.memory.hybrid import HybridMemoryAdapter
from astracore.modules.chat.domain.message import Message, MessageRole
from astracore.shared.observability.logger import get_logger

logger = get_logger(__name__)


def strip_dangling_tool_calls(messages: list[Message]) -> list[Message]:
    """Remove trailing ASSISTANT messages that have tool_calls but no following results."""
    msgs = list(messages)
    while msgs and msgs[-1].role == MessageRole.ASSISTANT and msgs[-1].tool_calls:
        msgs.pop()
    return msgs


def prepare_for_save(messages: list[Message]) -> list[Message]:
    """Drop SYSTEM / tool-loop-internal / synthetic messages before persisting chat history.

    Exception: assistant messages that contain a ``load_skill`` tool call are replaced with a
    thin text record (``metadata["skill_loaded"] = skill_id``) so the skill-state tracker can
    detect an active skill even after the full tool-call pair is stripped.

    Exception: ``metadata["compacted"]=True`` 的摘要消息必须保留，否则跨轮上下文 reinject 失效。
    """
    msgs: list[Message] = []
    for m in messages:
        if m.metadata.get("compacted"):
            msgs.append(m)
            continue
        if m.role == MessageRole.SYSTEM:
            continue
        if m.role == MessageRole.TOOL:
            continue
        if m.metadata.get("synthetic"):
            continue
        if m.role == MessageRole.ASSISTANT and m.tool_calls:
            load_skill_calls = [tc for tc in m.tool_calls if tc.name == "load_skill"]
            if load_skill_calls:
                skill_id = str(load_skill_calls[-1].arguments.get("skill_id", "")).strip()
                if skill_id:
                    msgs.append(
                        Message(
                            role=MessageRole.ASSISTANT,
                            content=m.content,
                            metadata={"skill_loaded": skill_id},
                        )
                    )
            continue
        msgs.append(m)
    return strip_dangling_tool_calls(msgs)


def materialize_for_llm(messages: list[Message]) -> list[Message]:
    """从完整 transcript 重建「可喂给 LLM / 可写短term」的视图。"""
    return prepare_for_save(messages)


async def load_history(
    memory: HybridMemoryAdapter,
    transcript: SQLTranscriptStore,
    session_id: UUID,
) -> list[Message]:
    """优先读 short-term；为空则从 transcript replay 并回填 short-term。"""
    loaded = [m for m in await memory.load_short_term(session_id) if m.role != MessageRole.SYSTEM]
    if loaded:
        return loaded

    try:
        replayed = await transcript.load_messages(session_id)
    except Exception:
        logger.warning("transcript replay 失败 session=%s", session_id, exc_info=True)
        return []

    if not replayed:
        return []

    materialized = materialize_for_llm(replayed)
    # 回填物化视图，避免每轮都全量 replay
    try:
        await memory.save_short_term(session_id, materialized)
        logger.info(
            "transcript replay 重建 short-term: session=%s messages=%d",
            session_id,
            len(materialized),
        )
    except Exception:
        logger.warning("replay 后写回 short-term 失败 session=%s", session_id, exc_info=True)
    return [m for m in materialized if m.role != MessageRole.SYSTEM]
