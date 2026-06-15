"""Structured Memory Engine."""

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from astracore.modules.chat.domain.message import Message, MessageRole
from astracore.modules.memory.domain import (
    ConversationProjectBinding,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    Project,
    StructuredMemory,
)
from astracore.modules.memory.ports.store import MemoryStore
from astracore.shared.ports.llm import LLMAdapter

if TYPE_CHECKING:
    from astracore.infrastructure.memory.vector import MemoryVectorAdapter

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


# ------------------------------------------------------------------
# LLM output schemas
# ------------------------------------------------------------------


class _MemoryItemDecision(BaseModel):
    """Single memory item within an extraction batch."""

    action: Literal["create", "update", "ignore"] = "create"
    scope: Literal["session", "project", "user", "global"] = "session"
    type: Literal[
        "fact",
        "preference",
        "decision",
        "constraint",
        "state",
        "plan",
        "summary",
        "lesson",
        "procedure",
    ] = "fact"
    subject: str = ""
    content: str = ""
    summary: str = ""
    importance: int = Field(default=3, ge=1, le=5)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    target_memory_id: str | None = None


class _ExtractionBatch(BaseModel):
    """Batch of memory extraction decisions from one turn (0-N items)."""

    memories: list[_MemoryItemDecision] = Field(default_factory=list)


class _PromotionDecision(BaseModel):
    """LLM decision for promoting a session memory to a broader scope."""

    action: Literal["promote_user", "promote_project", "keep", "archive"]
    reason: str = ""
    new_importance: int = Field(default=3, ge=1, le=5)


# ------------------------------------------------------------------
# Type metadata tables
# ------------------------------------------------------------------


@dataclass(frozen=True)
class _TypeMeta:
    order: int  # Tier-1 section sort order
    label: str  # Tier-2 short label
    title: str  # Tier-1 section heading


_TYPE_META: dict[MemoryType, _TypeMeta] = {
    MemoryType.PROCEDURE: _TypeMeta(0, "规范", "行为规范"),
    MemoryType.CONSTRAINT: _TypeMeta(1, "约束", "已确认约束"),
    MemoryType.DECISION: _TypeMeta(2, "决策", "已确认决策"),
    MemoryType.STATE: _TypeMeta(3, "状态", "当前状态"),
    MemoryType.PREFERENCE: _TypeMeta(4, "偏好", "用户偏好"),
    MemoryType.PLAN: _TypeMeta(5, "计划", "计划"),
    MemoryType.FACT: _TypeMeta(6, "事实", "已知事实"),
    MemoryType.LESSON: _TypeMeta(7, "教训", "经验教训"),
    MemoryType.SUMMARY: _TypeMeta(8, "摘要", "摘要"),
}

_CJK_RANGE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+")
_CJK_CHAR = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")

_DEFAULT_SCOPE_LIMITS: dict[MemoryScope, int] = {
    MemoryScope.SESSION: 6,
    MemoryScope.PROJECT: 6,
    MemoryScope.USER: 4,
    MemoryScope.GLOBAL: 4,
}


def _format_memory_doc(memory: StructuredMemory) -> str:
    """Format a single memory for Tier-2 context injection with type label and importance marker."""
    label = _TYPE_META[memory.type].label
    importance_marker = "⚑ " if memory.importance >= 4 else ""
    subject_part = f"{memory.subject}: " if memory.subject else ""
    return f"[{label}] {importance_marker}{subject_part}{memory.content}"


_SESSION_COMPACT_THRESHOLD = 12
_SIMILARITY_THRESHOLD = 0.72

# Heuristic thresholds for promotion eligibility
_PROMOTE_USE_COUNT = 5
_PROMOTE_HIGH_IMPORTANCE = 4
_PROMOTE_HIGH_USE_COUNT = 3


class MemoryEngine:
    """High-level service for project binding, memory retrieval, and extraction.

    Accepts an optional ``vector_adapter`` for semantic Tier-2 retrieval and Chroma
    synchronization on writes.  When absent, all operations degrade to SQL-only.
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        user_id: str = "default",
        vector_adapter: "MemoryVectorAdapter | None" = None,
    ) -> None:
        self._store = store
        self._user_id = user_id
        self._vector_adapter = vector_adapter

    # ------------------------------------------------------------------
    # Project management
    # ------------------------------------------------------------------

    async def create_project(
        self,
        *,
        name: str,
        root_paths: list[str] | None = None,
        description: str = "",
    ) -> Project:
        return await self._store.create_project(
            Project(name=name.strip(), root_paths=root_paths or [], description=description.strip())
        )

    async def list_projects(self) -> list[Project]:
        return await self._store.list_projects()

    async def get_project(self, project_id: str) -> Project | None:
        return await self._store.get_project(project_id)

    async def delete_project(self, project_id: str) -> bool:
        """Delete a project and cascade-remove its memories and conversation bindings."""
        return await self._store.delete_project(project_id)

    async def bind_conversation(
        self,
        *,
        conversation_id: UUID,
        project_id: str,
        locked: bool,
        source: str,
    ) -> ConversationProjectBinding:
        project = await self._store.get_project(project_id)
        if project is None:
            raise ValueError(f"Project not found: {project_id}")
        return await self._store.bind_conversation(
            ConversationProjectBinding(
                conversation_id=conversation_id,
                project_id=project_id,
                locked=locked,
                source=source,
            )
        )

    async def get_conversation_binding(
        self, conversation_id: UUID
    ) -> ConversationProjectBinding | None:
        return await self._store.get_conversation_binding(conversation_id)

    # ------------------------------------------------------------------
    # Memory CRUD (with Chroma sync)
    # ------------------------------------------------------------------

    async def create_memory(
        self,
        *,
        scope: MemoryScope,
        memory_type: MemoryType,
        content: str,
        subject: str = "",
        summary: str = "",
        session_id: UUID | None = None,
        conversation_id: UUID | None = None,
        project_id: str | None = None,
        source_run_id: str | None = None,
        importance: int = 3,
        confidence: float = 1.0,
        locked: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> StructuredMemory:
        memory = await self._store.create_memory(
            StructuredMemory(
                scope=scope,
                type=memory_type,
                subject=subject.strip(),
                content=content.strip(),
                summary=summary.strip(),
                session_id=session_id,
                conversation_id=conversation_id,
                project_id=project_id,
                user_id=self._user_id,
                source_run_id=source_run_id,
                importance=importance,
                confidence=confidence,
                locked=locked,
                metadata=metadata or {},
            )
        )
        if self._vector_adapter is not None:
            await self._vector_adapter.upsert(memory)
        return memory

    async def update_memory(self, memory: StructuredMemory) -> StructuredMemory:
        updated = await self._store.update_memory(memory)
        if self._vector_adapter is not None:
            await self._vector_adapter.upsert(updated)
        return updated

    async def get_memory(self, memory_id: str) -> StructuredMemory | None:
        return await self._store.get_memory(memory_id)

    async def list_memories(
        self,
        *,
        scope: MemoryScope | None = None,
        memory_type: MemoryType | None = None,
        session_id: UUID | None = None,
        project_id: str | None = None,
        query: str | None = None,
        status: MemoryStatus = MemoryStatus.ACTIVE,
        limit: int = 100,
    ) -> list[StructuredMemory]:
        return await self._store.list_memories(
            scope=scope,
            memory_type=memory_type,
            status=status,
            session_id=session_id,
            project_id=project_id,
            user_id=self._user_id,
            query=query,
            limit=limit,
        )

    async def delete_memory(self, memory_id: str) -> None:
        await self._store.delete_memory(memory_id)
        if self._vector_adapter is not None:
            await self._vector_adapter.delete(memory_id)

    async def delete_session_memories(self, session_id: UUID) -> int:
        return await self._store.delete_memories(
            scope=MemoryScope.SESSION,
            session_id=session_id,
        )

    async def delete_conversation_memories(self, conversation_id: UUID) -> int:
        deleted = await self._store.delete_memories(
            scope=MemoryScope.SESSION,
            session_id=conversation_id,
        )
        deleted += await self._store.delete_memories(conversation_id=conversation_id)
        if self._vector_adapter is not None:
            await self._vector_adapter.delete_by_conversation(
                str(conversation_id), user_id=self._user_id
            )
        return deleted

    # ------------------------------------------------------------------
    # Tier-1: stable user profile → System Prompt
    # ------------------------------------------------------------------

    async def build_profile_context(self, *, max_chars: int = 800) -> str:
        """Build Tier-1 context from user+global scope memories (SQL full load).

        Returns an empty string when no relevant memories exist.
        """
        user_memories = await self._store.list_memories(
            scope=MemoryScope.USER,
            user_id=self._user_id,
            status=MemoryStatus.ACTIVE,
            limit=50,
        )
        global_memories = await self._store.list_memories(
            scope=MemoryScope.GLOBAL,
            user_id=self._user_id,
            status=MemoryStatus.ACTIVE,
            limit=20,
        )

        all_memories = self._dedupe(user_memories + global_memories)
        if not all_memories:
            return ""

        grouped: dict[MemoryType, list[StructuredMemory]] = defaultdict(list)
        for memory in all_memories:
            grouped[memory.type].append(memory)

        lines = [
            "## 用户画像与行为规范",
            "",
            "以下来自长期记忆，请严格遵守 Constraints 和 Procedures；如用户明确纠正，以最新消息为准。",
        ]

        for memory_type in sorted(grouped, key=lambda t: _TYPE_META[t].order):
            title = _TYPE_META[memory_type].title
            lines.extend(["", f"### {title}"])
            for memory in grouped[memory_type]:
                lines.append(f"- {memory.content}")

        context = "\n".join(lines).strip()
        if len(context) > max_chars:
            context = context[:max_chars].rsplit("\n", 1)[0].rstrip()
        await self._store.touch_memories([m.id for m in all_memories])
        return context

    # ------------------------------------------------------------------
    # Tier-2: dynamic turn context → synthetic assistant message
    # ------------------------------------------------------------------

    async def build_turn_context(
        self,
        *,
        session_id: UUID,
        message: str,
        max_chars: int = 1200,
    ) -> str:
        """Build Tier-2 context from session+project scope memories (Chroma or SQL fallback).

        Returns an empty string when no relevant memories exist.
        """
        binding = await self._store.get_conversation_binding(session_id)
        session_docs: list[str] = []
        project_docs: list[str] = []

        if self._vector_adapter is not None:
            session_docs = await self._vector_adapter.query(
                message,
                user_id=self._user_id,
                scope_filter=["session"],
                session_id=str(session_id),
                n_results=6,
            )
            if binding is not None:
                project_docs = await self._vector_adapter.query(
                    message,
                    user_id=self._user_id,
                    scope_filter=["project"],
                    project_id=binding.project_id,
                    n_results=4,
                )

        # SQL fallback: always used when Chroma is unavailable OR returned empty results.
        # Covers memories created/updated without Chroma sync (e.g. via the REST API on an
        # older deployment) so they are never silently dropped from Tier-2 context.
        if not session_docs:
            session_memories = await self._store.list_memories(
                scope=MemoryScope.SESSION,
                session_id=session_id,
                user_id=self._user_id,
                status=MemoryStatus.ACTIVE,
                limit=_DEFAULT_SCOPE_LIMITS[MemoryScope.SESSION],
            )
            ranked_session = self._rank_memories(session_memories, message)
            session_docs = [_format_memory_doc(m) for m in ranked_session]
            await self._store.touch_memories([m.id for m in ranked_session])
        if binding is not None and not project_docs:
            project_memories = await self._store.list_memories(
                scope=MemoryScope.PROJECT,
                project_id=binding.project_id,
                user_id=self._user_id,
                status=MemoryStatus.ACTIVE,
                limit=_DEFAULT_SCOPE_LIMITS[MemoryScope.PROJECT],
            )
            ranked_project = self._rank_memories(project_memories, message)
            project_docs = [_format_memory_doc(m) for m in ranked_project]
            await self._store.touch_memories([m.id for m in ranked_project])

        if not session_docs and not project_docs:
            return ""

        lines = ["【记忆快照】"]
        if session_docs:
            lines.extend(["", "### 当前会话状态"])
            for doc in session_docs:
                lines.append(f"- {doc}")
        if project_docs:
            lines.extend(["", "### 项目上下文"])
            for doc in project_docs:
                lines.append(f"- {doc}")

        context = "\n".join(lines).strip()
        if len(context) > max_chars:
            context = context[:max_chars].rsplit("\n", 1)[0].rstrip()
        return context

    # ------------------------------------------------------------------
    # Memory extraction and compaction
    # ------------------------------------------------------------------

    async def extract_and_store(
        self,
        *,
        session_id: UUID,
        user_message: str,
        assistant_content: str,
        source_run_id: str,
        llm_adapter: LLMAdapter | None = None,
        model: str | None = None,
    ) -> list[StructuredMemory]:
        if llm_adapter is None:
            return []

        extracted = await self._extract_with_llm(
            session_id=session_id,
            user_message=user_message,
            assistant_content=assistant_content,
            source_run_id=source_run_id,
            llm_adapter=llm_adapter,
            model=model,
        )
        await self.compact_session_memories(
            session_id=session_id, llm_adapter=llm_adapter, model=model
        )
        await self._evaluate_and_promote(
            session_id=session_id, llm_adapter=llm_adapter, model=model
        )
        return extracted or []

    async def compact_session_memories(
        self,
        *,
        session_id: UUID,
        llm_adapter: LLMAdapter | None = None,
        model: str | None = None,
        threshold: int = _SESSION_COMPACT_THRESHOLD,
        force: bool = False,
    ) -> StructuredMemory | None:
        """压缩当前会话的短期记忆。

        force=True 时跳过条数阈值检查，仅保留 locked 记忆，其余全部压缩；
        适用于用户主动触发。force=False（默认）用于每轮对话后的自动触发，
        只有积累到 threshold 条以上才执行。
        """
        memories = await self._store.list_memories(
            scope=MemoryScope.SESSION,
            session_id=session_id,
            user_id=self._user_id,
            status=MemoryStatus.ACTIVE,
            limit=threshold + 20,
        )
        unlocked = [memory for memory in memories if not memory.locked]
        min_unlocked = 2 if force else 4
        if (not force and len(memories) <= threshold) or len(unlocked) < min_unlocked:
            return None

        protected = [memory for memory in memories if memory.locked]
        # force 时只保留 locked 记忆，让用户触发的压缩尽量彻底；
        # 自动触发时保留 top-3 以防高价值记忆被意外合并。
        keep_count = len(protected) if force else max(3, len(protected))
        ranked = self._rank_memories(memories, "")[:keep_count]
        keep_ids = {memory.id for memory in ranked}
        compressible = [memory for memory in unlocked if memory.id not in keep_ids]
        if len(compressible) < 2:
            return None

        summary = await self._summarize_memories(compressible, llm_adapter=llm_adapter, model=model)
        now = datetime.now(UTC)
        compressed_ids = [memory.id for memory in compressible]
        new_memory = await self.create_memory(
            scope=MemoryScope.SESSION,
            memory_type=MemoryType.SUMMARY,
            subject="session-summary",
            content=summary,
            summary=summary[:240],
            session_id=session_id,
            conversation_id=session_id,
            importance=3,
            confidence=0.8,
            metadata={
                "compressed_from_count": len(compressed_ids),
                "compressed_at": now.isoformat(),
                "source_memory_ids": compressed_ids,
                "retention_action": "deleted",
            },
        )
        for memory_id in compressed_ids:
            await self.delete_memory(memory_id)
        return new_memory

    # ------------------------------------------------------------------
    # LLM promotion: session → user/project
    # ------------------------------------------------------------------

    async def _evaluate_and_promote(
        self,
        *,
        session_id: UUID,
        llm_adapter: LLMAdapter,
        model: str | None,
    ) -> None:
        """Heuristic filter + LLM evaluation to promote high-value session memories."""
        session_memories = await self._store.list_memories(
            scope=MemoryScope.SESSION,
            session_id=session_id,
            user_id=self._user_id,
            status=MemoryStatus.ACTIVE,
            limit=50,
        )
        candidates = [
            m
            for m in session_memories
            if (
                m.use_count >= _PROMOTE_USE_COUNT
                or (
                    m.importance >= _PROMOTE_HIGH_IMPORTANCE
                    and m.use_count >= _PROMOTE_HIGH_USE_COUNT
                )
                or m.locked
            )
        ]
        if not candidates:
            return

        binding = await self._store.get_conversation_binding(session_id)
        for memory in candidates:
            try:
                await self._promote_one(
                    memory=memory,
                    binding=binding,
                    llm_adapter=llm_adapter,
                    model=model,
                )
            except Exception:
                pass  # promotion failures are non-critical; keep memory in session scope

    async def _promote_one(
        self,
        *,
        memory: StructuredMemory,
        binding: ConversationProjectBinding | None,
        llm_adapter: LLMAdapter,
        model: str | None,
    ) -> None:
        response = await llm_adapter.generate(
            messages=[
                Message(
                    role=MessageRole.SYSTEM,
                    content=(
                        "你是 AstraCoreAI 的记忆晋升评估器。判断一条 session 记忆是否值得晋升为长期记忆。\n"
                        "选项：\n"
                        "- promote_user：晋升为用户级永久记忆（偏好、稳定事实等）\n"
                        "- promote_project：晋升为项目级记忆（项目状态、决策等）\n"
                        "- keep：保留在 session，无需晋升\n"
                        "- archive：归档，已过时或无价值"
                    ),
                ),
                Message(
                    role=MessageRole.USER,
                    content=(
                        f"记忆内容：{memory.content}\n"
                        f"类型：{memory.type.value}\n"
                        f"使用次数：{memory.use_count}\n"
                        f"重要性：{memory.importance}\n"
                        f"创建于：{memory.created_at.isoformat()}"
                    ),
                ),
            ],
            model=model,
            temperature=0.0,
            response_format=_PromotionDecision,
        )
        decision = self._parse_memory_decision(response.content)
        if decision is None:
            return

        action = str(decision.get("action") or "keep").lower()
        new_importance = self._clamp_int(decision.get("new_importance"), 1, 5, memory.importance)
        now_iso = datetime.now(UTC).isoformat()

        if action == "promote_user":
            await self.create_memory(
                scope=MemoryScope.USER,
                memory_type=memory.type,
                content=memory.content,
                subject=memory.subject,
                summary=memory.summary,
                importance=new_importance,
                confidence=memory.confidence,
                metadata={"promoted_from": memory.id, "promoted_at": now_iso},
            )
            memory.status = MemoryStatus.ARCHIVED
            await self.update_memory(memory)

        elif action == "promote_project" and binding is not None:
            await self.create_memory(
                scope=MemoryScope.PROJECT,
                memory_type=memory.type,
                content=memory.content,
                subject=memory.subject,
                summary=memory.summary,
                project_id=binding.project_id,
                importance=new_importance,
                confidence=memory.confidence,
                metadata={"promoted_from": memory.id, "promoted_at": now_iso},
            )
            memory.status = MemoryStatus.ARCHIVED
            await self.update_memory(memory)

        elif action == "archive":
            memory.status = MemoryStatus.ARCHIVED
            await self.update_memory(memory)
        # "keep" → no action

    # ------------------------------------------------------------------
    # LLM extraction (batch)
    # ------------------------------------------------------------------

    async def _extract_with_llm(
        self,
        *,
        session_id: UUID,
        user_message: str,
        assistant_content: str,
        source_run_id: str,
        llm_adapter: LLMAdapter,
        model: str | None,
    ) -> list[StructuredMemory] | None:
        response = await llm_adapter.generate(
            messages=[
                Message(
                    role=MessageRole.SYSTEM,
                    content=(
                        "你是 AstraCoreAI 的记忆抽取器。从一轮对话中提取需要长期保留的信息。\n\n"
                        "**默认立场：提取。** 只有明确属于以下情形才跳过：\n"
                        "纯寒暄问候、一次性临时命令、完整代码块（过长）、敏感密钥、"
                        "与已有记忆完全重复的内容。\n\n"
                        "**必须提取的内容：**\n"
                        "- 用户明确说出的偏好（语言、工具、风格、习惯、喜好）\n"
                        "- 项目关键状态、决策、约束、计划、待办\n"
                        "- 用户确认的事实（项目名称、技术栈、目录路径、团队成员等）\n"
                        "- 经验教训（哪个方案有效/失败，原因是什么）\n"
                        "- AI 的行为规范（用户要求 AI 怎么做或不能做什么）\n\n"
                        "不确定是否值得保留时：填 importance=2, confidence=0.5，不要丢弃。\n\n"
                        "scope: session（本次会话）/ user（跨会话永久）/ project（项目级）\n"
                        "type: fact / preference / decision / constraint / state / plan / lesson / procedure\n"
                        "action 固定填 create（系统自动按 subject 去重合并，无需手动指定 update）。\n"
                        "content 须完整可独立理解，不能依赖上下文。subject 简短（< 20字）便于检索。\n\n"
                        '确实无内容可提取时才输出 {"memories": []}，这应是少数情况。'
                    ),
                ),
                Message(
                    role=MessageRole.USER,
                    content=(
                        "请从下面这轮对话中提取需要长期保留的信息。\n"
                        "积极提取，不要遗漏有价值的内容。\n\n"
                        f"用户消息：\n{user_message[:4000]}\n\n"
                        f"AI 回复：\n{assistant_content[:4000]}"
                    ),
                ),
            ],
            model=model,
            temperature=0.0,
            response_format=_ExtractionBatch,
        )

        raw = self._parse_memory_decision(response.content)
        if raw is None:
            return None

        memories_raw = raw.get("memories", [])
        if not isinstance(memories_raw, list):
            return []

        binding = await self._store.get_conversation_binding(session_id)
        results: list[StructuredMemory] = []

        for item in memories_raw:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "create").strip().lower()
            if action == "ignore":
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue

            scope = self._coerce_scope(item.get("scope"))
            memory_type = self._coerce_type(item.get("type"))
            project_id = (
                binding.project_id if binding is not None and scope == MemoryScope.PROJECT else None
            )
            if scope == MemoryScope.PROJECT and project_id is None:
                scope = MemoryScope.SESSION

            memory = await self._consolidate_candidate(
                scope=scope,
                memory_type=memory_type,
                subject=str(item.get("subject") or "").strip()[:128],
                content=content,
                summary=str(item.get("summary") or "").strip(),
                session_id=session_id,
                project_id=project_id,
                source_run_id=source_run_id,
                action=action,
                importance=self._clamp_int(item.get("importance"), 1, 5, 3),
                confidence=self._clamp_float(item.get("confidence"), 0.0, 1.0, 0.7),
            )
            results.append(memory)

        return results

    async def _consolidate_candidate(
        self,
        *,
        scope: MemoryScope,
        memory_type: MemoryType,
        subject: str,
        content: str,
        summary: str,
        session_id: UUID,
        project_id: str | None,
        source_run_id: str,
        action: str,
        importance: int,
        confidence: float,
    ) -> StructuredMemory:
        candidates = await self._store.list_memories(
            scope=scope,
            memory_type=memory_type,
            status=MemoryStatus.ACTIVE,
            session_id=session_id if scope == MemoryScope.SESSION else None,
            project_id=project_id if scope == MemoryScope.PROJECT else None,
            user_id=self._user_id,
            limit=50,
        )
        subject_key = self._normalize_key(subject)
        content_key = self._normalize_text(content)
        same_subject = [
            memory
            for memory in candidates
            if subject_key
            and self._subjects_match(subject_key, self._normalize_key(memory.subject))
        ]
        exact = [
            memory for memory in candidates if self._normalize_text(memory.content) == content_key
        ]
        target = self._best_candidate(same_subject or exact, content)
        if target is None:
            return await self._create_candidate_memory(
                scope=scope,
                memory_type=memory_type,
                subject=subject,
                content=content,
                summary=summary,
                session_id=session_id,
                project_id=project_id,
                source_run_id=source_run_id,
                importance=importance,
                confidence=confidence,
                status=MemoryStatus.ACTIVE,
                metadata={"extractor": "llm", "decision": "create"},
            )

        if target.locked and self._normalize_text(target.content) != content_key:
            return await self._create_candidate_memory(
                scope=scope,
                memory_type=memory_type,
                subject=subject,
                content=content,
                summary=summary,
                session_id=session_id,
                project_id=project_id,
                source_run_id=source_run_id,
                importance=importance,
                confidence=confidence,
                status=MemoryStatus.REJECTED,
                metadata={
                    "extractor": "llm",
                    "decision": "conflict",
                    "conflicts_with": target.id,
                },
            )

        should_update = action in {"update", "archive"} or self._should_replace(target, content)
        merged_content = content if should_update else self._merge_content(target.content, content)
        target.content = merged_content
        target.subject = subject or target.subject
        target.summary = summary or target.summary
        target.importance = max(target.importance, importance)
        target.confidence = max(target.confidence, confidence)
        target.source_run_id = source_run_id
        target.use_count += 1
        target.last_used_at = datetime.now(UTC)
        target.metadata = self._merged_metadata(
            target.metadata,
            decision="update" if should_update else "merge",
            source_run_id=source_run_id,
        )
        return await self.update_memory(target)

    async def _create_candidate_memory(
        self,
        *,
        scope: MemoryScope,
        memory_type: MemoryType,
        subject: str,
        content: str,
        summary: str,
        session_id: UUID,
        project_id: str | None,
        source_run_id: str,
        importance: int,
        confidence: float,
        status: MemoryStatus,
        metadata: dict[str, Any],
    ) -> StructuredMemory:
        memory = StructuredMemory(
            scope=scope,
            type=memory_type,
            subject=subject,
            content=content,
            summary=summary,
            session_id=session_id if scope == MemoryScope.SESSION else None,
            conversation_id=session_id,
            project_id=project_id if scope == MemoryScope.PROJECT else None,
            user_id=self._user_id,
            source_run_id=source_run_id,
            importance=importance,
            confidence=confidence,
            status=status,
            metadata=metadata,
        )
        created = await self._store.create_memory(memory)
        if self._vector_adapter is not None and status == MemoryStatus.ACTIVE:
            await self._vector_adapter.upsert(created)
        return created

    async def _summarize_memories(
        self,
        memories: list[StructuredMemory],
        *,
        llm_adapter: LLMAdapter | None,
        model: str | None,
    ) -> str:
        source = "\n".join(f"- [{memory.type.value}] {memory.content}" for memory in memories)
        if llm_adapter is None:
            return self._fallback_summary(memories)
        response = await llm_adapter.generate(
            messages=[
                Message(
                    role=MessageRole.SYSTEM,
                    content=(
                        "你是 AstraCoreAI 的会话记忆压缩器。把多条 session memory 压缩为一条简洁摘要。"
                        "只保留当前目标、关键决策、当前状态和后续计划。不要输出 Markdown 标题。"
                    ),
                ),
                Message(role=MessageRole.USER, content=f"请压缩这些记忆：\n{source[:6000]}"),
            ],
            model=model,
            temperature=0.0,
        )
        content = response.content.strip()
        return content or self._fallback_summary(memories)

    # ------------------------------------------------------------------
    # JSON parsing helpers
    # ------------------------------------------------------------------

    def _parse_memory_decision(self, raw: str) -> dict[str, Any] | None:
        text = _JSON_FENCE_RE.sub("", raw.strip()).strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            try:
                from json_repair import loads as repair_loads

                value = repair_loads(text)
            except Exception:
                return None
        if not isinstance(value, dict):
            return None
        return value

    def _coerce_scope(self, raw: object) -> MemoryScope:
        try:
            return MemoryScope(str(raw or MemoryScope.SESSION.value))
        except ValueError:
            return MemoryScope.SESSION

    def _coerce_type(self, raw: object) -> MemoryType:
        try:
            return MemoryType(str(raw or MemoryType.FACT.value))
        except ValueError:
            return MemoryType.FACT

    def _coerce_bool(self, raw: object) -> bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes", "y", "是", "需要"}
        return bool(raw)

    def _clamp_int(self, raw: object, low: int, high: int, default: int) -> int:
        if not isinstance(raw, str | bytes | bytearray | int | float):
            return default
        try:
            return min(high, max(low, int(raw)))
        except (TypeError, ValueError):
            return default

    def _clamp_float(self, raw: object, low: float, high: float, default: float) -> float:
        if not isinstance(raw, str | bytes | bytearray | int | float):
            return default
        try:
            return min(high, max(low, float(raw)))
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------
    # Text processing helpers
    # ------------------------------------------------------------------

    def _normalize_key(self, value: str) -> str:
        text = value.strip().lower()
        text = re.sub(r"[\s_\-:：/\\]+", "", text)
        return text

    def _normalize_text(self, value: str) -> str:
        text = value.strip().lower()
        text = re.sub(r"\s+", "", text)
        text = text.replace("\\", "/")
        return text

    def _subjects_match(self, left: str, right: str) -> bool:
        if not left or not right:
            return False
        if left == right:
            return True
        # Substring matching is unreliable for short ASCII strings (e.g. "ai" matches "astracoreai").
        # CJK subjects use a lower threshold since even 2-char CJK terms are semantically distinct.
        has_cjk = _CJK_CHAR.search(left) and _CJK_CHAR.search(right)
        min_len = 2 if has_cjk else 4
        if len(left) < min_len or len(right) < min_len:
            return False
        return left in right or right in left

    def _best_candidate(
        self, candidates: list[StructuredMemory], content: str
    ) -> StructuredMemory | None:
        if not candidates:
            return None
        content_key = self._normalize_text(content)

        def score(memory: StructuredMemory) -> tuple[int, float, int, str]:
            existing_key = self._normalize_text(memory.content)
            exact = 1 if existing_key == content_key else 0
            similarity = SequenceMatcher(None, existing_key, content_key).ratio()
            locked = 1 if memory.locked else 0
            return (exact, similarity, locked, memory.updated_at.isoformat())

        return max(candidates, key=score)

    def _should_replace(self, target: StructuredMemory, content: str) -> bool:
        if target.type in {MemoryType.DECISION, MemoryType.LESSON, MemoryType.SUMMARY}:
            return False
        existing = self._normalize_text(target.content)
        incoming = self._normalize_text(content)
        if existing == incoming:
            return False
        if existing in incoming:
            return True
        if incoming in existing:
            return False
        return SequenceMatcher(None, existing, incoming).ratio() < _SIMILARITY_THRESHOLD

    def _merge_content(self, existing: str, incoming: str) -> str:
        existing_text = existing.strip()
        incoming_text = incoming.strip()
        if not existing_text:
            return incoming_text
        if not incoming_text:
            return existing_text
        existing_key = self._normalize_text(existing_text)
        incoming_key = self._normalize_text(incoming_text)
        if existing_key == incoming_key or incoming_key in existing_key:
            return existing_text
        if existing_key in incoming_key:
            return incoming_text
        return f"{existing_text}；{incoming_text}"

    def _merged_metadata(
        self,
        metadata: dict[str, Any],
        *,
        decision: str,
        source_run_id: str,
    ) -> dict[str, Any]:
        merged = dict(metadata)
        merged["extractor"] = merged.get("extractor", "llm")
        merged["decision"] = decision
        history = list(merged.get("source_run_ids") or [])
        if source_run_id not in history:
            history.append(source_run_id)
        merged["source_run_ids"] = history[-10:]
        return merged

    def _fallback_summary(self, memories: list[StructuredMemory]) -> str:
        grouped: dict[MemoryType, list[str]] = defaultdict(list)
        for memory in memories:
            grouped[memory.type].append(memory.content.strip())
        lines: list[str] = []
        for memory_type in sorted(grouped, key=lambda item: _TYPE_META[item].order):
            for content in grouped[memory_type][:3]:
                if content:
                    lines.append(content)
        return "；".join(lines)[:1200]

    def _extract_keywords(self, message: str) -> set[str]:
        """Extract search keywords: ASCII words (len≥2) + CJK bigrams for Chinese matching."""
        words = {part for part in re.split(r"\W+", message.lower()) if len(part) >= 2}
        for chunk in _CJK_RANGE.findall(message):
            words.update(chunk[i : i + 2] for i in range(len(chunk) - 1))
        return words

    def _rank_memories(
        self, memories: list[StructuredMemory], message: str
    ) -> list[StructuredMemory]:
        keywords = self._extract_keywords(message)

        def _relevance(memory: StructuredMemory) -> int:
            text = ((memory.subject or "") + " " + memory.content).lower()
            return sum(1 for keyword in keywords if keyword in text)

        def _score(memory: StructuredMemory, relevance: int) -> tuple[int, int, float, str]:
            locked_bonus = 2 if memory.locked else 0
            return (
                _TYPE_META[memory.type].order,
                -(memory.importance + locked_bonus + relevance),
                -memory.confidence,
                memory.updated_at.isoformat(),
            )

        scored = [(m, _relevance(m)) for m in memories]

        # When there are keyword hits, suppress zero-hit low-importance memories.
        # Locked memories and high-importance (>=4) always pass regardless of relevance.
        if keywords and any(rel > 0 for _, rel in scored):
            scored = [(m, rel) for m, rel in scored if rel > 0 or m.locked or m.importance >= 4]

        return [m for m, _ in sorted(scored, key=lambda x: _score(x[0], x[1]))]

    def _dedupe(self, memories: list[StructuredMemory]) -> list[StructuredMemory]:
        seen: set[str] = set()
        unique: list[StructuredMemory] = []
        for memory in memories:
            if memory.id in seen:
                continue
            seen.add(memory.id)
            unique.append(memory)
        return unique
