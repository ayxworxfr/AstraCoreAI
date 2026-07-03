"""History compactor — token-based context window management."""

import logging
from datetime import UTC, datetime
from uuid import UUID

from astracore.modules.chat.domain.message import Message, MessageRole
from astracore.modules.memory.application.engine import MemoryEngine
from astracore.modules.memory.domain import MemoryScope, MemoryType
from astracore.shared.policy.rules import CompactionRule
from astracore.shared.ports.llm import LLMAdapter

logger = logging.getLogger(__name__)


class HistoryCompactor:
    """Compacts conversation history when the estimated token count exceeds a threshold.

    Uses an LLM to summarize the oldest messages, persists the summary to MemoryEngine,
    and replaces the compacted messages with a single system summary message.

    On LLM failure the compactor falls back to plain tail-truncation so the conversation
    never stalls.
    """

    def __init__(
        self,
        llm_adapter: LLMAdapter,
        memory_engine: MemoryEngine,
        model: str | None = None,
        rule: CompactionRule | None = None,
    ) -> None:
        self._llm = llm_adapter
        self._memory_engine = memory_engine
        self._model = model
        self._rule = rule or CompactionRule()

    def _estimate_message_tokens(self, msg: Message) -> int:
        """Rough token count for a single message (CJK + English mixed)."""
        text = msg.content or ""
        for tc in msg.tool_calls:
            text += str(tc.arguments)
        for tr in msg.tool_results:
            text += tr.content or ""
        return max(1, int(len(text) * self._rule.chars_per_token))

    def estimate_tokens(self, messages: list[Message]) -> int:
        """Estimate total token count for a list of messages."""
        return sum(self._estimate_message_tokens(m) for m in messages)

    async def maybe_compact(
        self,
        messages: list[Message],
        session_id: UUID,
        trim_limit: int | None = None,
    ) -> list[Message]:
        """Compact history if token estimate exceeds the threshold.

        Returns a new message list. On LLM failure, falls back to tail-trim using
        ``trim_limit`` (or :attr:`CompactionRule.default_max_messages`).
        """
        rule = self._rule
        context_window = rule.context_window_tokens
        threshold = int(context_window * rule.trigger_ratio)
        estimated = self.estimate_tokens(messages)

        logger.info(
            "上下文 token 统计: 估算 %d tokens / 阈值 %d (window=%d, 消息数=%d)",
            estimated,
            threshold,
            context_window,
            len(messages),
        )

        if estimated <= threshold:
            logger.info("上下文未超阈值，无需压缩")
            return messages

        logger.info(
            "上下文压缩触发: 估算 %d tokens > 阈值 %d (context_window=%d)",
            estimated,
            threshold,
            context_window,
        )

        # Split: compact the oldest batch, keep the most recent portion.
        non_system = [m for m in messages if m.role != MessageRole.SYSTEM]
        system_msgs = [m for m in messages if m.role == MessageRole.SYSTEM]

        batch_size = max(2, int(len(non_system) * rule.compact_batch_ratio))
        to_compact = non_system[:batch_size]
        to_keep = non_system[batch_size:]

        try:
            summary = await self._summarize(to_compact)
        except Exception:
            logger.warning("上下文压缩失败，回退到尾部裁剪", exc_info=True)
            # Fallback: tail-trim to trim_limit (configured default if not provided).
            limit = trim_limit if trim_limit is not None else rule.default_max_messages
            if limit > 0 and len(messages) > limit:
                return messages[-limit:]
            return messages

        await self._persist_summary(summary, len(to_compact), session_id)

        summary_msg = Message(
            role=MessageRole.SYSTEM,
            content=f"【对话摘要】\n{summary}",
            metadata={"synthetic": True, "compacted": True},
        )
        result = system_msgs + [summary_msg] + to_keep
        estimated_after = self.estimate_tokens(result)
        logger.info(
            "压缩完成: %d 条消息 → %d 条，估算 tokens %d → %d (压缩率 %.1f%%)",
            len(messages),
            len(result),
            estimated,
            estimated_after,
            (1 - estimated_after / estimated) * 100 if estimated else 0,
        )
        return result

    async def _summarize(self, messages: list[Message]) -> str:
        """Call LLM to summarize a batch of messages."""
        lines: list[str] = []
        for msg in messages:
            role_label = {
                MessageRole.USER: "用户",
                MessageRole.ASSISTANT: "助手",
                MessageRole.TOOL: "工具结果",
                MessageRole.SYSTEM: "系统",
            }.get(msg.role, msg.role.value)
            content = msg.content or ""
            if content:
                lines.append(f"[{role_label}]: {content[:1000]}")

        source = "\n".join(lines)
        response = await self._llm.generate(
            messages=[
                Message(
                    role=MessageRole.SYSTEM,
                    content=(
                        "你是 AstraCoreAI 的对话历史压缩器。"
                        "把以下多轮对话压缩为一段简洁的中文摘要，"
                        "保留关键事实、用户意图、重要决策和当前状态。"
                        "不要输出 Markdown 标题，直接给出摘要段落。"
                    ),
                ),
                Message(
                    role=MessageRole.USER,
                    content=f"请压缩以下对话历史：\n\n{source[:8000]}",
                ),
            ],
            model=self._model,
            temperature=0.0,
        )
        return response.content.strip() or _fallback_summary(messages)

    async def _persist_summary(self, summary: str, compacted_count: int, session_id: UUID) -> None:
        """Persist the summary to MemoryEngine as MemoryType.SUMMARY."""
        try:
            await self._memory_engine.create_memory(
                scope=MemoryScope.SESSION,
                memory_type=MemoryType.SUMMARY,
                subject="history-compaction",
                content=summary,
                summary=summary[:240],
                session_id=session_id,
                conversation_id=session_id,
                importance=3,
                confidence=0.9,
                metadata={
                    "compacted_message_count": compacted_count,
                    "compacted_at": datetime.now(UTC).isoformat(),
                    "source": "history_compactor",
                },
            )
        except Exception:
            logger.warning("压缩摘要持久化失败，继续运行", exc_info=True)


def _fallback_summary(messages: list[Message]) -> str:
    """Plain-text fallback when LLM summarization fails."""
    parts = [f"[{m.role.value}] {(m.content or '')[:200]}" for m in messages if m.content]
    return "【对话历史摘要（简化版）】\n" + "\n".join(parts[:10])
