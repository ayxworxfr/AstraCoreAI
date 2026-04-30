"""Shared chat execution engine used by both HTTP service and embedded SDK.

Encapsulates: system-prompt assembly, session management, LLM adapter caching,
tool-loop execution, summary fallback, and session persistence.

HTTP-specific concerns (SSE broadcasting, run tracking) remain in the API layer;
SDK-specific concerns (async context manager, MCP lifecycle) remain in the SDK client.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from astracore.adapters.db.models import SkillRow, UserSettingsRow
from astracore.adapters.db.session import get_session
from astracore.adapters.llm.anthropic import AnthropicAdapter
from astracore.adapters.llm.openai import OpenAIAdapter
from astracore.adapters.memory.hybrid import HybridMemoryAdapter
from astracore.core.application.rag import RAGPipeline
from astracore.core.application.tool_loop import ToolLoopUseCase
from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import LLMAdapter, StreamEvent, StreamEventType
from astracore.core.ports.tool import ToolAdapter
from astracore.runtime.observability.logger import get_logger
from astracore.runtime.policy.engine import PolicyEngine
from astracore.sdk.config import AstraCoreConfig, LLMProfileConfig
from astracore.service.prompt_utils import render_skill_prompt

logger = get_logger(__name__)

_ANTHROPIC_BLOCKS_KEY = "anthropic_content_blocks"


class ChatOrchestrator:
    """Shared chat execution engine.

    Callers pass context-specific dependencies (tool_adapter) per call so that
    both the HTTP service (which resolves tool_adapter from app.state) and the
    embedded SDK (which manages its own tool_adapter lifecycle) can reuse the
    same pipeline without coupling.
    """

    def __init__(
        self,
        config: AstraCoreConfig,
        memory: HybridMemoryAdapter,
        rag_pipeline: RAGPipeline,
        policy: PolicyEngine,
    ) -> None:
        self._config = config
        self._memory = memory
        self._rag_pipeline = rag_pipeline
        self._policy = policy
        self._llm_adapters: dict[str, LLMAdapter] = {}

    # ------------------------------------------------------------------
    # LLM / tool-loop factories
    # ------------------------------------------------------------------

    def get_llm_adapter(self, profile: LLMProfileConfig) -> LLMAdapter:
        if profile.id not in self._llm_adapters:
            if profile.provider == "anthropic":
                self._llm_adapters[profile.id] = AnthropicAdapter(
                    api_key=profile.api_key,
                    default_model=profile.model,
                    base_url=profile.base_url,
                    max_tokens=profile.max_tokens,
                    supports_temperature=profile.capabilities.temperature,
                    use_anthropic_blocks=profile.capabilities.anthropic_blocks,
                )
            else:
                self._llm_adapters[profile.id] = OpenAIAdapter(
                    api_key=profile.api_key,
                    default_model=profile.model,
                    base_url=profile.base_url,
                    max_tokens=profile.max_tokens,
                )
        return self._llm_adapters[profile.id]

    def make_tool_loop(
        self, profile: LLMProfileConfig, tool_adapter: ToolAdapter
    ) -> ToolLoopUseCase:
        cfg = self._config.agent
        return ToolLoopUseCase(
            llm_adapter=self.get_llm_adapter(profile),
            tool_adapter=tool_adapter,
            policy_engine=self._policy,
            max_iterations=cfg.max_tool_iterations,
            max_tool_result_chars=cfg.max_tool_result_chars,
            tool_timeout_s=cfg.tool_timeout_s,
        )

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def get_setting(self, key: str) -> str:
        async with get_session(self._config.memory.db_url) as db:
            row = await db.get(UserSettingsRow, key)
            return row.value if row else ""

    async def load_skill(self, skill_id: str) -> SkillRow | None:
        async with get_session(self._config.memory.db_url) as db:
            return await db.get(SkillRow, skill_id)

    # ------------------------------------------------------------------
    # Prompt composition
    # ------------------------------------------------------------------

    async def build_rag_context(self, query: str) -> str | None:
        try:
            top_k = int(await self.get_setting("rag_top_k") or "4")
            chunks = await self._rag_pipeline.retrieve_with_citations(query=query, top_k=top_k)
            if not chunks:
                return None
            parts = [
                f"[来源: {c.citation.title or c.citation.source_id}]\n{c.content}"
                for c in chunks
            ]
            context = "\n\n---\n\n".join(parts)
            return (
                "以下是从知识库检索到的相关内容，请优先基于这些内容回答用户问题，"
                "并在回答中注明引用的来源：\n\n" + context
            )
        except Exception:
            return None

    async def build_system_prompt(
        self,
        skill_id: UUID | None,
        disable_skill: bool,
        enable_rag: bool,
        message: str,
    ) -> str | None:
        """Compose the three-layer system prompt: skill → global instruction → RAG context."""
        parts: list[str] = []

        if not disable_skill:
            resolved_id = (
                str(skill_id) if skill_id else await self.get_setting("default_skill_id")
            )
            if resolved_id:
                skill = await self.load_skill(resolved_id)
                if skill and skill.system_prompt:
                    ai_name = await self.get_setting("ai_name") or "小卡"
                    owner_name = await self.get_setting("owner_name")
                    parts.append(render_skill_prompt(skill.system_prompt, ai_name, owner_name))

        instruction = await self.get_setting("global_instruction")
        if instruction:
            parts.append(instruction)

        if enable_rag:
            rag_ctx = await self.build_rag_context(message)
            if rag_ctx:
                parts.append(rag_ctx)

        return "\n\n---\n\n".join(parts) or None

    async def resolve_temperature(
        self, temperature: float | None, profile: LLMProfileConfig
    ) -> float:
        if temperature is not None:
            return temperature
        saved = await self.get_setting("temperature")
        return float(saved) if saved else profile.temperature

    # ------------------------------------------------------------------
    # Message helpers (static, no I/O)
    # ------------------------------------------------------------------

    @staticmethod
    def strip_dangling_tool_calls(messages: list[Message]) -> list[Message]:
        """Remove trailing ASSISTANT messages that have tool_calls but no following results."""
        msgs = list(messages)
        while msgs and msgs[-1].role == MessageRole.ASSISTANT and msgs[-1].tool_calls:
            msgs.pop()
        return msgs

    @staticmethod
    def prepare_for_save(messages: list[Message]) -> list[Message]:
        """Drop SYSTEM messages and trailing dangling tool calls before persisting."""
        msgs = [m for m in messages if m.role != MessageRole.SYSTEM]
        return ChatOrchestrator.strip_dangling_tool_calls(msgs)

    @staticmethod
    def needs_summary_fallback(messages: list[Message]) -> bool:
        """Return True when the tool loop ended without producing visible assistant text."""
        visible = [m for m in messages if m.role != MessageRole.SYSTEM]
        if not visible:
            return False
        last = visible[-1]
        if last.role == MessageRole.TOOL and last.has_tool_results():
            return True
        return last.role == MessageRole.ASSISTANT and not last.content.strip()

    @staticmethod
    def build_summary_fallback_messages(
        messages: list[Message], *, hit_iteration_limit: bool
    ) -> list[Message]:
        """Construct a message list that instructs the LLM to summarise without tool calls."""
        prompt = (
            "你现在处于工具调用收尾阶段。请只基于已有对话和工具结果给出最终回答，"
            "不要继续调用工具，也不要继续规划下一步。"
            "如果信息不足，请明确说明已确认内容和仍然缺失的信息。"
        )
        if hit_iteration_limit:
            prompt = (
                "你已达到工具循环最大轮次，请停止继续探索，直接基于当前工具结果完成总结。"
                + prompt
            )
        copied = [m.model_copy(deep=True) for m in messages]
        if copied and copied[0].role == MessageRole.SYSTEM:
            copied[0] = copied[0].model_copy(
                update={"content": f"{copied[0].content}\n\n---\n\n{prompt}"}
            )
        else:
            copied.insert(0, Message(role=MessageRole.SYSTEM, content=prompt))
        return copied

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    async def save_session_safe(self, session_id: UUID, messages: list[Message]) -> None:
        """Persist session, shielding against cancellation during cleanup."""
        try:
            await asyncio.shield(
                self._memory.save_short_term(
                    session_id=session_id,
                    messages=self.prepare_for_save(messages),
                )
            )
        except asyncio.CancelledError:
            logger.warning("会话保存被取消，session_id=%s", session_id)
        except Exception:
            logger.exception("会话保存失败，session_id=%s", session_id)

    # ------------------------------------------------------------------
    # Core streaming
    # ------------------------------------------------------------------

    async def stream_normal(
        self,
        *,
        session_id: UUID,
        message: str,
        profile: LLMProfileConfig,
        inject_system: str | None,
        temperature: float,
        context_max: int,
        llm_kwargs: dict[str, Any],
    ) -> AsyncIterator[StreamEvent]:
        """Stream a single LLM call without tool execution."""
        stored = [
            m
            for m in await self._memory.load_short_term(session_id)
            if m.role != MessageRole.SYSTEM
        ]
        session = SessionState(session_id=session_id)
        session.restore_messages(stored)
        session.add_message(Message(role=MessageRole.USER, content=message))

        llm_messages = session.get_messages()
        if context_max and len(llm_messages) > context_max:
            llm_messages = llm_messages[-context_max:]
        if inject_system:
            llm_messages = [Message(role=MessageRole.SYSTEM, content=inject_system)] + llm_messages

        accumulated_content = ""
        assistant_metadata: dict[str, Any] = {}
        try:
            async for event in self.get_llm_adapter(profile).generate_stream(
                messages=llm_messages,
                temperature=temperature,
                **llm_kwargs,
            ):
                if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                    accumulated_content += event.content
                elif event.event_type == StreamEventType.DONE:
                    raw_blocks = event.metadata.get(_ANTHROPIC_BLOCKS_KEY)
                    if isinstance(raw_blocks, list) and raw_blocks:
                        assistant_metadata[_ANTHROPIC_BLOCKS_KEY] = raw_blocks
                yield event
        finally:
            session.add_message(
                Message(
                    role=MessageRole.ASSISTANT,
                    content=accumulated_content,
                    metadata=assistant_metadata,
                )
            )
            await self.save_session_safe(session_id, session.get_messages())

    async def stream_with_tools(
        self,
        *,
        session_id: UUID,
        message: str,
        profile: LLMProfileConfig,
        tool_adapter: ToolAdapter,
        inject_system: str | None,
        temperature: float,
        context_max: int,
        enable_rag: bool,
        enable_web: bool,
        llm_kwargs: dict[str, Any],
    ) -> AsyncIterator[StreamEvent]:
        """Stream a multi-round tool-loop execution.

        Event sequence emitted:
          1. All events from the tool loop (ROUND_START, TEXT_DELTA, TOOL_CALL, etc.)
          2. If summary fallback is needed:
               StreamEvent(DONE, metadata={"source": "tool_loop"})   ← phase boundary
               TEXT_DELTA* from the summary call
          3. StreamEvent(DONE)   ← always emitted last

        The intermediate DONE with source="tool_loop" lets consumers (e.g., the HTTP
        SSE layer) distinguish "thinking/intermediate text" from "final assistant text"
        without needing knowledge of the tool-loop internals.
        """
        tool_loop = self.make_tool_loop(profile, tool_adapter)
        all_tools = {d.name for d in tool_adapter.get_definitions()}
        allowed_tools = all_tools
        if not enable_rag:
            allowed_tools = allowed_tools - {"search_knowledge_base"}
        if not enable_web:
            allowed_tools = allowed_tools - {"web_search"}

        stored = [
            m
            for m in await self._memory.load_short_term(session_id)
            if m.role != MessageRole.SYSTEM
        ]
        if context_max and len(stored) > context_max:
            stored = stored[-context_max:]

        session = SessionState(session_id=session_id)
        initial: list[Message] = []
        if inject_system:
            initial.append(Message(role=MessageRole.SYSTEM, content=inject_system))
        initial.extend(stored)
        session.restore_messages(initial)
        session.add_message(Message(role=MessageRole.USER, content=message))

        round_count = 0
        completed = False
        try:
            async for event in tool_loop.execute_stream_with_tools(
                session, allowed_tools=allowed_tools, **llm_kwargs
            ):
                if event.event_type == StreamEventType.ROUND_START:
                    round_count = int(event.metadata.get("round", round_count + 1))
                # 过滤掉 LLM 适配器每轮生成结束时发出的 DONE 事件。
                # HTTP service 的 _execute_tool_run 用 DONE 事件来判断"最终完成"，
                # 若把 LLM 级 DONE 透传出去，_execute_tool_run 会在第一轮工具调用后
                # 误判为完成并提前 break，导致后续轮次的文本无法广播给前端。
                # orchestrator 自身会在合适时机发出 phase-boundary DONE 和 final DONE。
                if event.event_type == StreamEventType.DONE:
                    continue
                yield event
            completed = True
        finally:
            if not completed:
                await self.save_session_safe(session_id, session.get_messages())

        # Normal completion path: optional summary fallback, then save and final DONE
        safe_messages = self.strip_dangling_tool_calls(session.get_messages())
        if self.needs_summary_fallback(safe_messages):
            hit_limit = (
                not tool_loop.unlimited
                and round_count >= tool_loop.max_iterations
                and bool(safe_messages)
                and safe_messages[-1].role == MessageRole.TOOL
            )
            # Phase boundary: consumers can use this to separate tool-phase text from summary text
            yield StreamEvent(event_type=StreamEventType.DONE, metadata={"source": "tool_loop"})

            summary_text = ""
            async for event in self.get_llm_adapter(profile).generate_stream(
                messages=self.build_summary_fallback_messages(
                    safe_messages, hit_iteration_limit=hit_limit
                ),
                temperature=temperature,
            ):
                if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                    summary_text += event.content
                # 同样过滤 LLM 级 DONE，确保 save_session_safe 在 final DONE 之前执行。
                if event.event_type == StreamEventType.DONE:
                    continue
                yield event

            if summary_text.strip():
                session.add_message(Message(role=MessageRole.ASSISTANT, content=summary_text))
            else:
                hint = "信息量较大，本轮分析已暂停。会话已保存，请发送「继续」让 AI 继续完成分析。"
                session.add_message(Message(role=MessageRole.ASSISTANT, content=hint))
                yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content=hint)

        await self.save_session_safe(session_id, session.get_messages())
        yield StreamEvent(event_type=StreamEventType.DONE)
