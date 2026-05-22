"""Structured Memory Engine."""

import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any, Literal
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

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class _MemoryDecision(BaseModel):
    """LLM 结构化输出：记忆抽取决策。"""

    should_remember: bool
    scope: Literal["session", "project", "user", "global"] = "session"
    type: Literal[
        "fact", "preference", "decision", "constraint", "state", "plan", "summary", "lesson"
    ] = "fact"
    subject: str = ""
    content: str = ""
    summary: str = ""
    importance: int = Field(default=3, ge=1, le=5)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    action: str = ""


_TYPE_TITLES: dict[MemoryType, str] = {
    MemoryType.CONSTRAINT: "Constraints",
    MemoryType.STATE: "Current Project State",
    MemoryType.PREFERENCE: "User Preferences",
    MemoryType.DECISION: "Recent Decisions",
    MemoryType.PLAN: "Plans",
    MemoryType.FACT: "Facts",
    MemoryType.LESSON: "Lessons",
    MemoryType.SUMMARY: "Summaries",
}

_TYPE_ORDER: dict[MemoryType, int] = {
    MemoryType.CONSTRAINT: 0,
    MemoryType.DECISION: 1,
    MemoryType.STATE: 2,
    MemoryType.PREFERENCE: 3,
    MemoryType.PLAN: 4,
    MemoryType.FACT: 5,
    MemoryType.LESSON: 6,
    MemoryType.SUMMARY: 7,
}

_DEFAULT_SCOPE_LIMITS: dict[MemoryScope, int] = {
    MemoryScope.SESSION: 6,
    MemoryScope.PROJECT: 6,
    MemoryScope.USER: 4,
    MemoryScope.GLOBAL: 4,
}

_SESSION_COMPACT_THRESHOLD = 12
_SIMILARITY_THRESHOLD = 0.72


class MemoryEngine:
    """High-level service for project binding, memory retrieval, and extraction."""

    def __init__(self, store: MemoryStore, *, user_id: str = "default") -> None:
        self._store = store
        self._user_id = user_id

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
        return await self._store.create_memory(
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

    async def update_memory(self, memory: StructuredMemory) -> StructuredMemory:
        return await self._store.update_memory(memory)

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

    async def delete_session_memories(self, session_id: UUID) -> int:
        return await self._store.delete_memories(
            scope=MemoryScope.SESSION,
            session_id=session_id,
        )

    async def delete_conversation_memories(
        self,
        conversation_id: UUID,
    ) -> int:
        deleted = await self._store.delete_memories(
            scope=MemoryScope.SESSION,
            session_id=conversation_id,
        )
        deleted += await self._store.delete_memories(conversation_id=conversation_id)
        return deleted

    async def build_memory_context(
        self,
        *,
        session_id: UUID,
        message: str,
        max_items: int = 12,
        max_chars: int = 4000,
    ) -> str:
        binding = await self._store.get_conversation_binding(session_id)
        memories: list[StructuredMemory] = []
        memories.extend(
            await self._store.list_memories(
                scope=MemoryScope.SESSION,
                session_id=session_id,
                status=MemoryStatus.ACTIVE,
                limit=min(max_items, _DEFAULT_SCOPE_LIMITS[MemoryScope.SESSION]),
            )
        )
        if binding is not None:
            memories.extend(
                await self._store.list_memories(
                    scope=MemoryScope.PROJECT,
                    project_id=binding.project_id,
                    status=MemoryStatus.ACTIVE,
                    limit=min(max_items, _DEFAULT_SCOPE_LIMITS[MemoryScope.PROJECT]),
                )
            )
        memories.extend(
            await self._store.list_memories(
                scope=MemoryScope.USER,
                user_id=self._user_id,
                status=MemoryStatus.ACTIVE,
                limit=min(max_items, _DEFAULT_SCOPE_LIMITS[MemoryScope.USER]),
            )
        )
        memories.extend(
            await self._store.list_memories(
                scope=MemoryScope.GLOBAL,
                status=MemoryStatus.ACTIVE,
                limit=min(max_items, _DEFAULT_SCOPE_LIMITS[MemoryScope.GLOBAL]),
            )
        )
        if not memories:
            return ""

        ranked = self._rank_memories(self._dedupe(memories), message)[:max_items]
        grouped: dict[MemoryType, list[StructuredMemory]] = defaultdict(list)
        for memory in ranked:
            grouped[memory.type].append(memory)

        lines = [
            "## Relevant Memory",
            "",
            "以下记忆来自系统长期记忆。请优先遵守 Constraints 和 Recent Decisions；如果用户明确纠正，以用户最新消息为准。",
        ]
        for memory_type in sorted(grouped, key=lambda t: _TYPE_ORDER[t]):
            title = _TYPE_TITLES[memory_type]
            lines.extend(["", f"### {title}"])
            for memory in grouped[memory_type]:
                lines.append(f"- {memory.content}")

        context = "\n".join(lines).strip()
        return context[:max_chars].rstrip()

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
        return extracted or []

    async def compact_session_memories(
        self,
        *,
        session_id: UUID,
        llm_adapter: LLMAdapter | None = None,
        model: str | None = None,
        threshold: int = _SESSION_COMPACT_THRESHOLD,
    ) -> StructuredMemory | None:
        memories = await self._store.list_memories(
            scope=MemoryScope.SESSION,
            session_id=session_id,
            status=MemoryStatus.ACTIVE,
            limit=threshold + 20,
        )
        unlocked = [memory for memory in memories if not memory.locked]
        if len(memories) <= threshold or len(unlocked) < 4:
            return None

        protected = [memory for memory in memories if memory.locked]
        keep_count = max(3, len(protected))
        ranked = self._rank_memories(memories, "")[:keep_count]
        keep_ids = {memory.id for memory in ranked}
        compressible = [memory for memory in unlocked if memory.id not in keep_ids]
        if len(compressible) < 2:
            return None

        summary = await self._summarize_memories(compressible, llm_adapter=llm_adapter, model=model)
        now = datetime.now(UTC)
        compressed_ids = [memory.id for memory in compressible]
        memory = await self.create_memory(
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
            await self._store.delete_memory(memory_id)
        return memory

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
                        "你是 AstraCoreAI 的长期记忆抽取器。判断一轮对话是否值得写入长期记忆。\n"
                        "只记住未来仍有用的信息，例如：用户稳定偏好、项目路径、项目状态、明确决策、"
                        "长期约束、后续计划、重要事实或可复用经验。\n"
                        "不要记住寒暄、一次性问题、临时命令、普通解释、敏感密钥、完整代码块或低价值细节。\n"
                        "如果能判断写入动作，action 字段填 create|update|merge|ignore|archive|conflict。\n"
                        "如果不需要记忆，将 should_remember 设为 false，其余字段可留空。"
                    ),
                ),
                Message(
                    role=MessageRole.USER,
                    content=(
                        "请判断下面这轮对话是否需要写入长期记忆。\n\n"
                        f"用户消息：\n{user_message[:4000]}\n\n"
                        f"AI 回复：\n{assistant_content[:4000]}"
                    ),
                ),
            ],
            model=model,
            temperature=0.0,
            response_format=_MemoryDecision,
        )
        decision = self._parse_memory_decision(response.content)
        if decision is None:
            return None
        if not self._coerce_bool(decision.get("should_remember")):
            return []

        content = str(decision.get("content") or "").strip()
        if not content:
            return []

        binding = await self._store.get_conversation_binding(session_id)
        scope = self._coerce_scope(decision.get("scope"))
        memory_type = self._coerce_type(decision.get("type"))
        project_id = (
            binding.project_id if binding is not None and scope == MemoryScope.PROJECT else None
        )
        if scope == MemoryScope.PROJECT and project_id is None:
            scope = MemoryScope.SESSION

        memory = await self._consolidate_candidate(
            scope=scope,
            memory_type=memory_type,
            subject=str(decision.get("subject") or "").strip()[:128],
            content=content,
            summary=str(decision.get("summary") or "").strip(),
            session_id=session_id,
            project_id=project_id,
            source_run_id=source_run_id,
            action=str(decision.get("action") or "").strip().lower(),
            importance=self._clamp_int(decision.get("importance"), 1, 5, 3),
            confidence=self._clamp_float(decision.get("confidence"), 0.0, 1.0, 0.7),
        )
        return [memory]

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
        return await self._store.update_memory(target)

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
        return await self._store.create_memory(memory)

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
        return left == right or left in right or right in left

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
        for memory_type in sorted(grouped, key=lambda item: _TYPE_ORDER[item]):
            for content in grouped[memory_type][:3]:
                if content:
                    lines.append(content)
        return "；".join(lines)[:1200]

    def _rank_memories(
        self, memories: list[StructuredMemory], message: str
    ) -> list[StructuredMemory]:
        keywords = {part for part in re.split(r"\W+", message.lower()) if len(part) >= 2}

        def score(memory: StructuredMemory) -> tuple[int, int, float, str]:
            text = memory.content.lower()
            relevance = sum(1 for keyword in keywords if keyword in text)
            locked_bonus = 2 if memory.locked else 0
            return (
                _TYPE_ORDER[memory.type],
                -(memory.importance + locked_bonus + relevance),
                -memory.confidence,
                memory.updated_at.isoformat(),
            )

        return sorted(memories, key=score)

    def _dedupe(self, memories: list[StructuredMemory]) -> list[StructuredMemory]:
        seen: set[str] = set()
        unique: list[StructuredMemory] = []
        for memory in memories:
            if memory.id in seen:
                continue
            seen.add(memory.id)
            unique.append(memory)
        return unique
