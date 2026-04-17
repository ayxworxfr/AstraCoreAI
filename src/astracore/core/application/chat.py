"""Chat use case implementation."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import LLMAdapter, StreamEvent, StreamEventType
from astracore.core.ports.memory import MemoryAdapter
from astracore.runtime.policy.engine import PolicyEngine


class ChatUseCase:
    """Core chat use case with streaming support."""

    def __init__(
        self,
        llm_adapter: LLMAdapter,
        memory_adapter: MemoryAdapter,
        policy_engine: PolicyEngine,
    ):
        self.llm = llm_adapter
        self.memory = memory_adapter
        self.policy = policy_engine

    async def execute(
        self,
        session_id: UUID,
        user_message: str,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> Message:
        """Execute a complete chat interaction with retry and timeout from policy."""
        session = await self._load_session(session_id)

        user_msg = Message(role=MessageRole.USER, content=user_message)
        session.add_message(user_msg)

        session = self.policy.apply_budget_policy(session)

        async def _call_llm() -> Any:
            return await self.llm.generate(
                messages=session.get_messages(),
                model=model,
                temperature=temperature,
            )

        # Outer timeout wraps the entire retry loop.
        response = await self.policy.apply_timeout_policy(
            lambda: self.policy.apply_retry_policy(_call_llm),
            timeout_type="llm",
        )

        assistant_msg = Message(
            role=MessageRole.ASSISTANT,
            content=response.content,
            tool_calls=response.tool_calls,
        )
        session.add_message(assistant_msg)

        await self._save_session(session)

        return assistant_msg

    async def execute_stream(
        self,
        session_id: UUID,
        user_message: str,
        model: str | None = None,
        temperature: float = 0.7,
        inject_system: str | None = None,
        **llm_kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Execute a streaming chat interaction.

        inject_system: 在会话消息前注入额外系统提示（用于 RAG 上下文）。
        llm_kwargs: 透传给 LLM 适配器的额外参数（如 enable_thinking）。
        """
        session = await self._load_session(session_id)

        user_msg = Message(role=MessageRole.USER, content=user_message)
        session.add_message(user_msg)

        session = self.policy.apply_budget_policy(session)

        messages = session.get_messages()
        if inject_system:
            messages = [Message(role=MessageRole.SYSTEM, content=inject_system)] + messages

        accumulated_content = ""
        accumulated_tool_calls = []

        async for event in self.llm.generate_stream(
            messages=messages,
            model=model,
            temperature=temperature,
            **llm_kwargs,
        ):
            if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                accumulated_content += event.content

            if event.tool_call:
                accumulated_tool_calls.append(event.tool_call)

            yield event

        assistant_msg = Message(
            role=MessageRole.ASSISTANT,
            content=accumulated_content,
            tool_calls=accumulated_tool_calls,
        )
        session.add_message(assistant_msg)

        await self._save_session(session)

    async def _load_session(self, session_id: UUID) -> SessionState:
        """Load or create session. Uses restore_messages to avoid token double-counting."""
        messages = await self.memory.load_short_term(session_id)
        session = SessionState(session_id=session_id)
        if messages:
            session.restore_messages(messages)
        return session

    async def _save_session(self, session: SessionState) -> None:
        """Save session to memory."""
        await self.memory.save_short_term(
            session_id=session.session_id,
            messages=session.get_messages(),
        )
