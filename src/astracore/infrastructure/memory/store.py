"""SQLAlchemy implementation for structured memory storage."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select

from astracore.infrastructure.db.models import (
    ConversationProjectBindingRow,
    ProjectRow,
    StructuredMemoryRow,
)
from astracore.infrastructure.db.session import get_session
from astracore.modules.memory.domain import (
    ConversationProjectBinding,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    Project,
    StructuredMemory,
)
from astracore.modules.memory.ports.store import MemoryStore


def _project_from_row(row: ProjectRow) -> Project:
    return Project(
        id=row.id,
        name=row.name,
        root_paths=list(row.root_paths or []),
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _binding_from_row(row: ConversationProjectBindingRow) -> ConversationProjectBinding:
    return ConversationProjectBinding(
        conversation_id=UUID(row.conversation_id),
        project_id=row.project_id,
        locked=row.locked,
        source=row.source,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _memory_from_row(row: StructuredMemoryRow) -> StructuredMemory:
    return StructuredMemory(
        id=row.id,
        scope=MemoryScope(row.scope),
        type=MemoryType(row.type),
        subject=row.subject,
        content=row.content,
        summary=row.summary,
        session_id=UUID(row.session_id) if row.session_id else None,
        conversation_id=UUID(row.conversation_id) if row.conversation_id else None,
        project_id=row.project_id,
        user_id=row.user_id,
        source_run_id=row.source_run_id,
        importance=row.importance,
        confidence=row.confidence,
        status=MemoryStatus(row.status),
        locked=row.locked,
        use_count=row.use_count,
        metadata=row.meta or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_used_at=row.last_used_at,
    )


class SQLMemoryStore(MemoryStore):
    """SQL-backed structured memory store."""

    def __init__(self, db_url: str) -> None:
        self._db_url = db_url

    async def create_project(self, project: Project) -> Project:
        now = datetime.now(UTC)
        row = ProjectRow(
            id=project.id,
            name=project.name,
            root_paths=project.root_paths,
            description=project.description,
            created_at=now,
            updated_at=now,
        )
        async with get_session(self._db_url) as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return _project_from_row(row)

    async def list_projects(self) -> list[Project]:
        async with get_session(self._db_url) as db:
            result = await db.execute(select(ProjectRow).order_by(ProjectRow.updated_at.desc()))
            return [_project_from_row(row) for row in result.scalars().all()]

    async def get_project(self, project_id: str) -> Project | None:
        async with get_session(self._db_url) as db:
            row = await db.get(ProjectRow, project_id)
            return _project_from_row(row) if row else None

    async def delete_project(self, project_id: str) -> bool:
        async with get_session(self._db_url) as db:
            row = await db.get(ProjectRow, project_id)
            if row is None:
                return False
            await db.execute(
                delete(StructuredMemoryRow).where(StructuredMemoryRow.project_id == project_id)
            )
            await db.execute(
                delete(ConversationProjectBindingRow).where(
                    ConversationProjectBindingRow.project_id == project_id
                )
            )
            await db.delete(row)
            await db.commit()
            return True

    async def bind_conversation(
        self, binding: ConversationProjectBinding
    ) -> ConversationProjectBinding:
        now = datetime.now(UTC)
        async with get_session(self._db_url) as db:
            row = await db.get(ConversationProjectBindingRow, str(binding.conversation_id))
            if row is None:
                row = ConversationProjectBindingRow(
                    conversation_id=str(binding.conversation_id),
                    project_id=binding.project_id,
                    locked=binding.locked,
                    source=binding.source,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.project_id = binding.project_id
                row.locked = binding.locked
                row.source = binding.source
                row.updated_at = now
            await db.commit()
            await db.refresh(row)
            return _binding_from_row(row)

    async def get_conversation_binding(
        self, conversation_id: UUID
    ) -> ConversationProjectBinding | None:
        async with get_session(self._db_url) as db:
            row = await db.get(ConversationProjectBindingRow, str(conversation_id))
            return _binding_from_row(row) if row else None

    async def create_memory(self, memory: StructuredMemory) -> StructuredMemory:
        now = datetime.now(UTC)
        row = StructuredMemoryRow(
            id=memory.id,
            scope=memory.scope.value,
            type=memory.type.value,
            subject=memory.subject,
            content=memory.content,
            summary=memory.summary,
            session_id=str(memory.session_id) if memory.session_id else None,
            conversation_id=str(memory.conversation_id) if memory.conversation_id else None,
            project_id=memory.project_id,
            user_id=memory.user_id,
            source_run_id=memory.source_run_id,
            importance=memory.importance,
            confidence=memory.confidence,
            status=memory.status.value,
            locked=memory.locked,
            use_count=memory.use_count,
            meta=memory.metadata,
            created_at=now,
            updated_at=now,
            last_used_at=memory.last_used_at,
        )
        async with get_session(self._db_url) as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return _memory_from_row(row)

    async def update_memory(self, memory: StructuredMemory) -> StructuredMemory:
        async with get_session(self._db_url) as db:
            row = await db.get(StructuredMemoryRow, memory.id)
            if row is None:
                raise ValueError(f"Memory not found: {memory.id}")
            row.scope = memory.scope.value
            row.type = memory.type.value
            row.subject = memory.subject
            row.content = memory.content
            row.summary = memory.summary
            row.session_id = str(memory.session_id) if memory.session_id else None
            row.conversation_id = str(memory.conversation_id) if memory.conversation_id else None
            row.project_id = memory.project_id
            row.user_id = memory.user_id
            row.source_run_id = memory.source_run_id
            row.importance = memory.importance
            row.confidence = memory.confidence
            row.status = memory.status.value
            row.locked = memory.locked
            row.use_count = memory.use_count
            row.meta = memory.metadata
            row.updated_at = datetime.now(UTC)
            row.last_used_at = memory.last_used_at
            await db.commit()
            await db.refresh(row)
            return _memory_from_row(row)

    async def get_memory(self, memory_id: str) -> StructuredMemory | None:
        async with get_session(self._db_url) as db:
            row = await db.get(StructuredMemoryRow, memory_id)
            return _memory_from_row(row) if row else None

    async def list_memories(
        self,
        *,
        scope: MemoryScope | None = None,
        memory_type: MemoryType | None = None,
        status: MemoryStatus = MemoryStatus.ACTIVE,
        session_id: UUID | None = None,
        project_id: str | None = None,
        user_id: str = "default",
        query: str | None = None,
        limit: int = 100,
    ) -> list[StructuredMemory]:
        stmt = select(StructuredMemoryRow).where(StructuredMemoryRow.status == status.value)
        if scope is not None:
            stmt = stmt.where(StructuredMemoryRow.scope == scope.value)
        if memory_type is not None:
            stmt = stmt.where(StructuredMemoryRow.type == memory_type.value)
        if session_id is not None:
            stmt = stmt.where(StructuredMemoryRow.session_id == str(session_id))
        if project_id is not None:
            stmt = stmt.where(StructuredMemoryRow.project_id == project_id)
        if scope == MemoryScope.USER:
            stmt = stmt.where(StructuredMemoryRow.user_id == user_id)
        if query:
            stmt = stmt.where(StructuredMemoryRow.content.ilike(f"%{query}%"))
        stmt = stmt.order_by(
            StructuredMemoryRow.importance.desc(),
            StructuredMemoryRow.updated_at.desc(),
        ).limit(limit)
        async with get_session(self._db_url) as db:
            result = await db.execute(stmt)
            return [_memory_from_row(row) for row in result.scalars().all()]

    async def delete_memory(self, memory_id: str) -> None:
        async with get_session(self._db_url) as db:
            row = await db.get(StructuredMemoryRow, memory_id)
            if row is not None:
                await db.delete(row)
                await db.commit()

    async def delete_memories(
        self,
        *,
        scope: MemoryScope | None = None,
        session_id: UUID | None = None,
        conversation_id: UUID | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        status: MemoryStatus | None = None,
    ) -> int:
        stmt = delete(StructuredMemoryRow)
        if scope is not None:
            stmt = stmt.where(StructuredMemoryRow.scope == scope.value)
        if session_id is not None:
            stmt = stmt.where(StructuredMemoryRow.session_id == str(session_id))
        if conversation_id is not None:
            stmt = stmt.where(StructuredMemoryRow.conversation_id == str(conversation_id))
        if project_id is not None:
            stmt = stmt.where(StructuredMemoryRow.project_id == project_id)
        if user_id is not None:
            stmt = stmt.where(StructuredMemoryRow.user_id == user_id)
        if status is not None:
            stmt = stmt.where(StructuredMemoryRow.status == status.value)
        async with get_session(self._db_url) as db:
            result = await db.execute(stmt)
            await db.commit()
            return int(getattr(result, "rowcount", 0) or 0)
