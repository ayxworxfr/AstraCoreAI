"""Structured memory API."""

from functools import lru_cache
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from astracore.infrastructure.db.models import UserRow
from astracore.infrastructure.memory.store import SQLMemoryStore
from astracore.infrastructure.memory.vector import MemoryVectorAdapter
from astracore.modules.auth.dependencies import get_current_user
from astracore.modules.memory.application.engine import MemoryEngine
from astracore.modules.memory.domain import MemoryScope, MemoryStatus, MemoryType, StructuredMemory
from astracore.sdk.config import AstraCoreConfig

router = APIRouter()


@lru_cache(maxsize=1)
def _get_db_url() -> str:
    return AstraCoreConfig().storage.db_url


@lru_cache(maxsize=1)
def _get_vector_adapter() -> MemoryVectorAdapter:
    cfg = AstraCoreConfig()
    return MemoryVectorAdapter(
        persist_directory=cfg.storage.vector.persist_directory,
        embedding_model=cfg.storage.vector.embedding_model,
    )


def _get_user_engine(user_id: str) -> MemoryEngine:
    return MemoryEngine(
        SQLMemoryStore(_get_db_url()),
        user_id=user_id,
        vector_adapter=_get_vector_adapter(),
    )


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
        "fact",
        "preference",
        "decision",
        "constraint",
        "state",
        "plan",
        "summary",
        "lesson",
        "procedure",
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
            "fact",
            "preference",
            "decision",
            "constraint",
            "state",
            "plan",
            "summary",
            "lesson",
            "procedure",
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
        "fact",
        "preference",
        "decision",
        "constraint",
        "state",
        "plan",
        "summary",
        "lesson",
        "procedure",
    ]
    | None = None,
    status: Literal["active", "stale", "archived", "rejected"] = "active",
    session_id: UUID | None = None,
    project_id: str | None = None,
    q: str | None = None,
    limit: int = 100,
    current_user: UserRow = Depends(get_current_user),
) -> MemoryListResponse:
    memories = await _get_user_engine(current_user.id).list_memories(
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
async def create_memory(
    body: MemoryCreate,
    current_user: UserRow = Depends(get_current_user),
) -> MemoryResponse:
    memory = await _get_user_engine(current_user.id).create_memory(
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
async def update_memory(
    memory_id: str,
    body: MemoryUpdate,
    current_user: UserRow = Depends(get_current_user),
) -> MemoryResponse:
    engine = _get_user_engine(current_user.id)
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
async def delete_memory(
    memory_id: str,
    current_user: UserRow = Depends(get_current_user),
) -> None:
    await _get_user_engine(current_user.id).delete_memory(memory_id)


class BatchDeleteRequest(BaseModel):
    ids: list[str] = Field(min_length=1)


class BatchDeleteResponse(BaseModel):
    deleted: int


@router.post("/batch-delete", response_model=BatchDeleteResponse)
async def batch_delete_memory(
    body: BatchDeleteRequest,
    current_user: UserRow = Depends(get_current_user),
) -> BatchDeleteResponse:
    deleted = await _get_user_engine(current_user.id).delete_memories_by_ids(body.ids)
    return BatchDeleteResponse(deleted=deleted)


# ------------------------------------------------------------------
# Pending promotion approvals (HITL)
# ------------------------------------------------------------------


class PendingPromotionResponse(BaseModel):
    id: str
    user_id: str
    source_memory_id: str
    target_scope: str
    reason: str
    candidate_content: str
    candidate_subject: str
    status: str
    created_at: str
    reviewed_at: str | None


class PendingPromotionListResponse(BaseModel):
    total: int
    items: list[PendingPromotionResponse]


class ReviewDecision(BaseModel):
    id: str
    action: Literal["approve", "reject"]


class BatchReviewRequest(BaseModel):
    decisions: list[ReviewDecision]


class BatchReviewResponse(BaseModel):
    approved: int
    rejected: int


def _to_promotion_response(row: Any) -> PendingPromotionResponse:
    return PendingPromotionResponse(
        id=row.id,
        user_id=row.user_id,
        source_memory_id=row.source_memory_id,
        target_scope=row.target_scope,
        reason=row.reason,
        candidate_content=row.candidate_content,
        candidate_subject=row.candidate_subject,
        status=row.status,
        created_at=row.created_at.isoformat(),
        reviewed_at=row.reviewed_at.isoformat() if row.reviewed_at else None,
    )


@router.get("/pending-approvals", response_model=PendingPromotionListResponse)
async def list_pending_approvals(
    limit: int = 20,
    offset: int = 0,
    current_user: UserRow = Depends(get_current_user),
) -> PendingPromotionListResponse:
    store = SQLMemoryStore(_get_db_url())
    try:
        total, items = (
            await store.count_pending_promotions(current_user.id),
            await store.list_pending_promotions(current_user.id, limit=limit, offset=offset),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return PendingPromotionListResponse(
        total=total,
        items=[_to_promotion_response(row) for row in items],
    )


@router.post("/pending-approvals/batch-review", response_model=BatchReviewResponse)
async def batch_review_approvals(
    body: BatchReviewRequest,
    current_user: UserRow = Depends(get_current_user),
) -> BatchReviewResponse:
    store = SQLMemoryStore(_get_db_url())
    approved = 0
    rejected = 0
    for decision in body.decisions:
        if decision.action not in ("approve", "reject"):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid action '{decision.action}': must be approve or reject",
            )
        try:
            result = await store.apply_promotion(decision.id, decision.action)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail=f"Promotion {decision.id!r} not found")
        if result.status == "approved":
            approved += 1
        elif result.status == "rejected":
            rejected += 1
    return BatchReviewResponse(approved=approved, rejected=rejected)
