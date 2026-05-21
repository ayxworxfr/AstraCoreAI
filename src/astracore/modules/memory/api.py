"""Structured memory API."""

from functools import lru_cache
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from astracore.infrastructure.memory.store import SQLMemoryStore
from astracore.modules.memory.application.engine import MemoryEngine
from astracore.modules.memory.domain import MemoryScope, MemoryStatus, MemoryType, StructuredMemory
from astracore.sdk.config import AstraCoreConfig

router = APIRouter()


@lru_cache(maxsize=1)
def _get_db_url() -> str:
    return AstraCoreConfig().memory.db_url


@lru_cache(maxsize=1)
def _get_memory_engine() -> MemoryEngine:
    return MemoryEngine(SQLMemoryStore(_get_db_url()))


class MemoryResponse(BaseModel):
    id: str
    scope: str
    type: str
    subject: str
    content: str
    summary: str
    session_id: str | None
    conversation_id: str | None
    project_id: str | None
    user_id: str
    source_run_id: str | None
    importance: int
    confidence: float
    status: str
    locked: bool
    use_count: int
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    last_used_at: str | None


class MemoryListResponse(BaseModel):
    items: list[MemoryResponse]
    total: int


class MemoryCreate(BaseModel):
    scope: Literal["session", "project", "user", "global"]
    type: Literal[
        "fact", "preference", "decision", "constraint", "state", "plan", "summary", "lesson"
    ]
    content: str = Field(min_length=1)
    subject: str = ""
    summary: str = ""
    session_id: UUID | None = None
    conversation_id: UUID | None = None
    project_id: str | None = None
    source_run_id: str | None = None
    importance: int = Field(default=3, ge=1, le=5)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    locked: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryUpdate(BaseModel):
    scope: Literal["session", "project", "user", "global"] | None = None
    type: (
        Literal[
            "fact", "preference", "decision", "constraint", "state", "plan", "summary", "lesson"
        ]
        | None
    ) = None
    content: str | None = None
    subject: str | None = None
    summary: str | None = None
    session_id: UUID | None = None
    conversation_id: UUID | None = None
    project_id: str | None = None
    importance: int | None = Field(default=None, ge=1, le=5)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: Literal["active", "stale", "archived", "rejected"] | None = None
    locked: bool | None = None
    metadata: dict[str, Any] | None = None


def _to_response(memory: StructuredMemory) -> MemoryResponse:
    return MemoryResponse(
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
        metadata=memory.metadata,
        created_at=memory.created_at.isoformat(),
        updated_at=memory.updated_at.isoformat(),
        last_used_at=memory.last_used_at.isoformat() if memory.last_used_at else None,
    )


@router.get("/", response_model=MemoryListResponse)
async def list_memory(
    scope: Literal["session", "project", "user", "global"] | None = None,
    type: Literal[
        "fact", "preference", "decision", "constraint", "state", "plan", "summary", "lesson"
    ]
    | None = None,
    status: Literal["active", "stale", "archived", "rejected"] = "active",
    session_id: UUID | None = None,
    project_id: str | None = None,
    q: str | None = None,
    limit: int = 100,
) -> MemoryListResponse:
    memories = await _get_memory_engine().list_memories(
        scope=MemoryScope(scope) if scope else None,
        memory_type=MemoryType(type) if type else None,
        session_id=session_id,
        project_id=project_id,
        query=q,
        status=MemoryStatus(status),
        limit=limit,
    )
    return MemoryListResponse(
        items=[_to_response(memory) for memory in memories], total=len(memories)
    )


@router.post("/", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(body: MemoryCreate) -> MemoryResponse:
    memory = await _get_memory_engine().create_memory(
        scope=MemoryScope(body.scope),
        memory_type=MemoryType(body.type),
        content=body.content,
        subject=body.subject,
        summary=body.summary,
        session_id=body.session_id,
        conversation_id=body.conversation_id,
        project_id=body.project_id,
        source_run_id=body.source_run_id,
        importance=body.importance,
        confidence=body.confidence,
        locked=body.locked,
        metadata=body.metadata,
    )
    return _to_response(memory)


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def update_memory(memory_id: str, body: MemoryUpdate) -> MemoryResponse:
    engine = _get_memory_engine()
    memory = await engine.get_memory(memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    data = body.model_dump(exclude_unset=True)
    if "scope" in data and data["scope"] is not None:
        memory.scope = MemoryScope(data["scope"])
    if "type" in data and data["type"] is not None:
        memory.type = MemoryType(data["type"])
    if "status" in data and data["status"] is not None:
        memory.status = MemoryStatus(data["status"])
    for field_name in (
        "content",
        "subject",
        "summary",
        "session_id",
        "conversation_id",
        "project_id",
        "importance",
        "confidence",
        "locked",
    ):
        if field_name in data:
            setattr(memory, field_name, data[field_name])
    if "metadata" in data and data["metadata"] is not None:
        memory.metadata = data["metadata"]
    return _to_response(await engine.update_memory(memory))


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: str) -> None:
    await _get_memory_engine().delete_memory(memory_id)
