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
        try:
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
            return assistant_msg
        finally:
            await self._save_session(session)

    async def execute_stream(
        self,
        session_id: UUID,
        user_message: str,
        model: str | None = None,
        temperature: float = 0.7,
        inject_system: str | None = None,
        context_max_messages: int = 0,
        **llm_kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Execute a streaming chat interaction.

        inject_system: 在会话消息前注入额外系统提示（用于 RAG 上下文）。
        context_max_messages: 发给 LLM 的历史消息条数上限，0 表示不限制。
        llm_kwargs: 透传给 LLM 适配器的额外参数（如 enable_thinking）。
        """
        session = await self._load_session(session_id, context_max_messages)

        user_msg = Message(role=MessageRole.USER, content=user_message)
        session.add_message(user_msg)

        session = self.policy.apply_budget_policy(session)

        messages = session.get_messages()
        if inject_system:
            messages = [Message(role=MessageRole.SYSTEM, content=inject_system)] + messages

        accumulated_content = ""
        accumulated_tool_calls = []
        _saved = False

        try:
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
            _saved = True
        finally:
            if not _saved:
                # 出错时保留至少用户消息，若有部分回复也一并保存
                if accumulated_content or accumulated_tool_calls:
                    session.add_message(
                        Message(
                            role=MessageRole.ASSISTANT,
                            content=accumulated_content,
                            tool_calls=accumulated_tool_calls,
                        )
                    )
                await self._save_session(session)

    async def _load_session(self, session_id: UUID, context_max_messages: int = 0) -> SessionState:
        """Load or create session. Uses restore_messages to avoid token double-counting.

        context_max_messages: 0 = no limit; >0 = keep only the last N messages.
        """
        messages = await self.memory.load_short_term(session_id)
        if context_max_messages > 0 and len(messages) > context_max_messages:
            messages = messages[-context_max_messages:]
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
