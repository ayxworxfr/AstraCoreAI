"""Conversation metadata CRUD API."""

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from astracore.infrastructure.attachments.local_fs import LocalFSAttachmentStorage
from astracore.infrastructure.db.models import (
    AttachmentRow,
    ChatRunRow,
    ChatSessionRow,
    ConversationProjectBindingRow,
    ConversationRow,
    UserRow,
)
from astracore.infrastructure.db.session import get_session
from astracore.infrastructure.memory.store import SQLMemoryStore
from astracore.infrastructure.memory.vector import MemoryVectorAdapter
from astracore.modules.auth.dependencies import get_current_user
from astracore.modules.memory.application.engine import MemoryEngine
from astracore.sdk.config import AstraCoreConfig

router = APIRouter()


@lru_cache(maxsize=1)
def _get_db_url() -> str:
    return AstraCoreConfig().storage.db_url


@lru_cache(maxsize=1)
def _get_vector_adapter() -> MemoryVectorAdapter | None:
    cfg = AstraCoreConfig()
    if not cfg.storage.vector.enabled:
        return None
    return MemoryVectorAdapter(
        persist_directory=cfg.storage.vector.persist_directory,
        embedding_model=cfg.storage.vector.embedding_model,
    )


@lru_cache(maxsize=1)
def _get_attachment_storage() -> LocalFSAttachmentStorage:
    return LocalFSAttachmentStorage(base_path=Path("data/attachments"))


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


def _attachment_ids_from_run(row: ChatRunRow) -> list[str]:
    raw_ids = row.request.get("attachment_ids", [])
    if not isinstance(raw_ids, list):
        return []
    return [str(attachment_id) for attachment_id in raw_ids]


async def _delete_run_attachments(
    db: AsyncSession,
    runs: list[ChatRunRow],
    user_id: str,
) -> set[str]:
    """Delete attachment DB rows referenced by runs and return their storage keys."""
    attachment_ids = {
        attachment_id for run in runs for attachment_id in _attachment_ids_from_run(run)
    }
    if not attachment_ids:
        return set()

    attachment_result = await db.execute(
        select(AttachmentRow).where(
            AttachmentRow.id.in_(attachment_ids),
            AttachmentRow.user_id == user_id,
        )
    )
    attachment_rows = list(attachment_result.scalars().all())
    deleting_ids = {row.id for row in attachment_rows}
    if not deleting_ids:
        return set()

    storage_keys = {row.storage_key for row in attachment_rows}
    await db.execute(delete(AttachmentRow).where(AttachmentRow.id.in_(deleting_ids)))
    return storage_keys


@router.get("/", response_model=list[ConversationItem])
async def list_conversations(
    current_user: UserRow = Depends(get_current_user),
) -> list[ConversationItem]:
    async with get_session(_get_db_url()) as db:
        result = await db.execute(
            select(ConversationRow)
            .where(ConversationRow.user_id == current_user.id)
            .order_by(
                ConversationRow.pinned.desc(),
                ConversationRow.updated_at.desc(),
            )
        )
        return [_row_to_item(row) for row in result.scalars()]


@router.post("/", response_model=ConversationItem, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: CreateConversationRequest,
    current_user: UserRow = Depends(get_current_user),
) -> ConversationItem:
    row = ConversationRow(
        id=body.id,
        user_id=current_user.id,
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
    current_user: UserRow = Depends(get_current_user),
) -> ConversationItem:
    async with get_session(_get_db_url()) as db:
        result = await db.execute(
            select(ConversationRow).where(
                ConversationRow.id == str(conversation_id),
                ConversationRow.user_id == current_user.id,
            )
        )
        row = result.scalar_one_or_none()
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
async def delete_conversation(
    conversation_id: UUID,
    current_user: UserRow = Depends(get_current_user),
) -> None:
    """Delete conversation metadata, message history, runs, bindings, and scoped memory."""
    cid = str(conversation_id)
    async with get_session(_get_db_url()) as db:
        result = await db.execute(
            select(ConversationRow).where(
                ConversationRow.id == cid,
                ConversationRow.user_id == current_user.id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

    await MemoryEngine(
        SQLMemoryStore(_get_db_url()),
        user_id=current_user.id,
        vector_adapter=_get_vector_adapter(),
    ).delete_conversation_memories(conversation_id)

    storage_keys_to_delete: set[str] = set()
    async with get_session(_get_db_url()) as db:
        runs_result = await db.execute(select(ChatRunRow).where(ChatRunRow.session_id == cid))
        runs: list[ChatRunRow] = list(runs_result.scalars().all())
        storage_keys_to_delete = await _delete_run_attachments(db, runs, current_user.id)

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

    storage = _get_attachment_storage()
    for storage_key in storage_keys_to_delete:
        await storage.delete(storage_key)
