"""Conversation metadata CRUD API."""

from datetime import UTC, datetime
from functools import lru_cache
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select

from astracore.infrastructure.db.models import (
    ChatRunRow,
    ChatSessionRow,
    ConversationProjectBindingRow,
    ConversationRow,
)
from astracore.infrastructure.db.session import get_session
from astracore.infrastructure.memory.store import SQLMemoryStore
from astracore.modules.memory.application.engine import MemoryEngine
from astracore.sdk.config import AstraCoreConfig

router = APIRouter()


@lru_cache(maxsize=1)
def _get_db_url() -> str:
    return AstraCoreConfig().memory.db_url


@lru_cache(maxsize=1)
def _get_memory_engine() -> MemoryEngine:
    return MemoryEngine(SQLMemoryStore(_get_db_url()))


def _row_to_item(row: ConversationRow) -> "ConversationItem":
    return ConversationItem(
        id=row.id,
        title=row.title,
        pinned=row.pinned,
        model_id=row.model_id,
        last_message_preview=row.last_message_preview,
        message_count=row.message_count,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


class ConversationItem(BaseModel):
    id: str
    title: str
    pinned: bool
    model_id: str | None
    last_message_preview: str
    message_count: int
    created_at: str
    updated_at: str


class CreateConversationRequest(BaseModel):
    id: str
    title: str = "新会话"
    model_id: str | None = None


class PatchConversationRequest(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    model_id: str | None = None
    last_message_preview: str | None = None
    message_count: int | None = None


@router.get("/", response_model=list[ConversationItem])
async def list_conversations() -> list[ConversationItem]:
    async with get_session(_get_db_url()) as db:
        result = await db.execute(
            select(ConversationRow).order_by(
                ConversationRow.pinned.desc(),
                ConversationRow.updated_at.desc(),
            )
        )
        return [_row_to_item(row) for row in result.scalars()]


@router.post("/", response_model=ConversationItem, status_code=status.HTTP_201_CREATED)
async def create_conversation(body: CreateConversationRequest) -> ConversationItem:
    row = ConversationRow(
        id=body.id,
        title=body.title,
        model_id=body.model_id,
    )
    async with get_session(_get_db_url()) as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return _row_to_item(row)


@router.patch("/{conversation_id}", response_model=ConversationItem)
async def patch_conversation(
    conversation_id: UUID,
    body: PatchConversationRequest,
) -> ConversationItem:
    async with get_session(_get_db_url()) as db:
        row = await db.get(ConversationRow, str(conversation_id))
        if row is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        provided = body.model_fields_set
        if "title" in provided and body.title is not None:
            row.title = body.title
        if "pinned" in provided and body.pinned is not None:
            row.pinned = body.pinned
        if "model_id" in provided:
            row.model_id = body.model_id
        if "last_message_preview" in provided and body.last_message_preview is not None:
            row.last_message_preview = body.last_message_preview
        if "message_count" in provided and body.message_count is not None:
            row.message_count = body.message_count
        row.updated_at = datetime.now(UTC)

        await db.commit()
        await db.refresh(row)
        return _row_to_item(row)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: UUID) -> None:
    """Delete conversation metadata, message history, runs, bindings, and scoped memory."""
    cid = str(conversation_id)
    await _get_memory_engine().delete_conversation_memories(conversation_id)
    async with get_session(_get_db_url()) as db:
        row = await db.get(ConversationRow, cid)
        if row is not None:
            await db.delete(row)
        session_row = await db.get(ChatSessionRow, cid)
        if session_row is not None:
            await db.delete(session_row)
        binding_row = await db.get(ConversationProjectBindingRow, cid)
        if binding_row is not None:
            await db.delete(binding_row)
        await db.execute(delete(ChatRunRow).where(ChatRunRow.session_id == cid))
        await db.commit()
