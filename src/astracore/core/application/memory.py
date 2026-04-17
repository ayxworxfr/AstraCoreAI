"""Memory pipeline implementation."""

from uuid import UUID

from astracore.core.domain.message import Message
from astracore.core.domain.session import SessionState
from astracore.core.ports.memory import MemoryAdapter, MemoryEntry


class MemoryPipeline:
    """Memory management pipeline."""

    def __init__(self, memory_adapter: MemoryAdapter):
        self.memory = memory_adapter

    async def load_context(self, session_id: UUID) -> SessionState:
        """Load full context including short and long-term memory."""
        session = SessionState(session_id=session_id)

        short_term = await self.memory.load_short_term(session_id)
        for msg in short_term:
            session.add_message(msg)

        long_term = await self.memory.load_long_term(session_id, limit=5)
        if long_term:
            summary_parts = [entry.content for entry in long_term]
            session.context_window.summary = " | ".join(summary_parts)

        return session

    async def save_context(self, session: SessionState) -> None:
        """Save session context to memory."""
        await self.memory.save_short_term(
            session_id=session.session_id,
            messages=session.get_messages(),
        )

        if session.context_window.should_summarize():
            summary = self._create_summary(session.get_messages())
            await self.memory.save_long_term(
                session_id=session.session_id,
                summary=summary,
            )

    def _create_summary(self, messages: list[Message]) -> str:
        """Create summary of messages."""
        if not messages:
            return ""

        key_messages = messages[-10:]
        summary_parts = []

        for msg in key_messages:
            if msg.content:
                summary_parts.append(f"{msg.role.value}: {msg.content[:100]}")

        return " | ".join(summary_parts)

    async def search_relevant_memory(
        self,
        session_id: UUID,
        query: str,
        limit: int = 3,
    ) -> list[MemoryEntry]:
        """Search for relevant memories."""
        return await self.memory.search_memory(
            query=query,
            session_id=session_id,
            limit=limit,
        )

    async def clear_session(self, session_id: UUID) -> None:
        """Clear all session memory."""
        await self.memory.delete_session_memory(session_id)
