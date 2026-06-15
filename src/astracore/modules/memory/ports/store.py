"""Structured memory store port."""

from abc import ABC, abstractmethod
from uuid import UUID

from astracore.modules.memory.domain import (
    ConversationProjectBinding,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    Project,
    StructuredMemory,
)


class MemoryStore(ABC):
    """Persistence port for structured memories and projects."""

    @abstractmethod
    async def create_project(self, project: Project) -> Project:
        pass

    @abstractmethod
    async def list_projects(self) -> list[Project]:
        pass

    @abstractmethod
    async def get_project(self, project_id: str) -> Project | None:
        pass

    @abstractmethod
    async def delete_project(self, project_id: str) -> bool:
        """Delete a project and cascade-remove its memories and conversation bindings.

        Returns True if the project existed and was deleted, False if not found.
        """
        pass

    @abstractmethod
    async def bind_conversation(
        self, binding: ConversationProjectBinding
    ) -> ConversationProjectBinding:
        pass

    @abstractmethod
    async def get_conversation_binding(
        self, conversation_id: UUID
    ) -> ConversationProjectBinding | None:
        pass

    @abstractmethod
    async def create_memory(self, memory: StructuredMemory) -> StructuredMemory:
        pass

    @abstractmethod
    async def update_memory(self, memory: StructuredMemory) -> StructuredMemory:
        pass

    @abstractmethod
    async def get_memory(self, memory_id: str) -> StructuredMemory | None:
        pass

    @abstractmethod
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
        pass

    @abstractmethod
    async def delete_memory(self, memory_id: str) -> None:
        pass

    @abstractmethod
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
        pass

    @abstractmethod
    async def touch_memories(self, memory_ids: list[str]) -> None:
        """Increment use_count and update last_used_at for the given memories in bulk."""
        pass
