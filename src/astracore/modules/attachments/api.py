"""Attachment upload/download/delete endpoints."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select

from astracore.infrastructure.attachments.local_fs import LocalFSAttachmentStorage
from astracore.infrastructure.db.models import AttachmentRow, UserRow
from astracore.infrastructure.db.session import get_session
from astracore.modules.auth.dependencies import get_current_user

router = APIRouter()

_IMAGE_SIZE_LIMIT = 20 * 1024 * 1024  # 20 MB
_PDF_SIZE_LIMIT = 32 * 1024 * 1024  # 32 MB

_ALLOWED_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "application/pdf",
    }
)

_MAGIC_BYTES: dict[str, list[tuple[bytes, int]]] = {
    "image/jpeg": [(b"\xff\xd8\xff", 0)],
    "image/png": [(b"\x89PNG\r\n\x1a\n", 0)],
    "image/gif": [(b"GIF87a", 0), (b"GIF89a", 0)],
    "image/webp": [(b"RIFF", 0), (b"WEBP", 8)],
    "application/pdf": [(b"%PDF-", 0)],
}

_EXT_MAP: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


class AttachmentUploadResponse(BaseModel):
    attachment_id: str
    filename: str
    mime_type: str
    size_bytes: int


def _detect_mime_type(data: bytes) -> str | None:
    """Return the MIME type detected from magic bytes, or None if unrecognised."""
    for mime, checks in _MAGIC_BYTES.items():
        if mime == "image/webp":
            if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                return mime
        else:
            for magic, offset in checks:
                end = offset + len(magic)
                if len(data) >= end and data[offset:end] == magic:
                    return mime
    return None


@lru_cache(maxsize=1)
def _get_db_url() -> str:
    from astracore.sdk.config import AstraCoreConfig  # noqa: PLC0415

    return AstraCoreConfig().storage.db_url


@lru_cache(maxsize=1)
def _get_storage() -> LocalFSAttachmentStorage:
    return LocalFSAttachmentStorage(base_path=Path("data/attachments"))


@router.post("", response_model=AttachmentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    file: UploadFile,
    current_user: UserRow = Depends(get_current_user),
    db_url: str = Depends(_get_db_url),
    storage: LocalFSAttachmentStorage = Depends(_get_storage),
) -> AttachmentUploadResponse:
    """Upload an image or PDF attachment; return the attachment_id for chat requests."""
    claimed_mime = (file.content_type or "").lower().split(";")[0].strip()
    if claimed_mime not in _ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"不支持的文件类型：{claimed_mime}。支持：{', '.join(sorted(_ALLOWED_MIME_TYPES))}",
        )

    size_limit = _PDF_SIZE_LIMIT if claimed_mime == "application/pdf" else _IMAGE_SIZE_LIMIT
    # Read exactly size_limit+1 bytes to detect overflow without loading full stream.
    data = await file.read(size_limit + 1)
    if len(data) > size_limit:
        limit_mb = size_limit // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件超过 {limit_mb} MB 限制",
        )

    actual_mime = _detect_mime_type(data)
    if actual_mime != claimed_mime:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="文件内容与声明的 Content-Type 不匹配",
        )

    ext = _EXT_MAP.get(claimed_mime, "bin")
    storage_key = await storage.save(data, ext, current_user.id)

    attachment_id = str(uuid4())
    filename = file.filename or f"upload.{ext}"

    async with get_session(db_url) as db:
        db.add(
            AttachmentRow(
                id=attachment_id,
                user_id=current_user.id,
                filename=filename,
                mime_type=claimed_mime,
                size_bytes=len(data),
                storage_key=storage_key,
            )
        )
        await db.commit()

    return AttachmentUploadResponse(
        attachment_id=attachment_id,
        filename=filename,
        mime_type=claimed_mime,
        size_bytes=len(data),
    )


@router.get("/{attachment_id}")
async def download_attachment(
    attachment_id: str,
    current_user: UserRow = Depends(get_current_user),
    db_url: str = Depends(_get_db_url),
    storage: LocalFSAttachmentStorage = Depends(_get_storage),
) -> StreamingResponse:
    """Download an attachment.  Only the owning user may access it."""
    async with get_session(db_url) as db:
        result = await db.execute(select(AttachmentRow).where(AttachmentRow.id == attachment_id))
        row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="附件不存在")
    if row.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该附件")

    try:
        data = await storage.load(row.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="附件文件已删除") from exc

    async def _iter() -> AsyncGenerator[bytes, None]:
        yield data

    return StreamingResponse(
        _iter(),
        media_type=row.mime_type,
        headers={"Content-Disposition": f'inline; filename="{row.filename}"'},
    )


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: str,
    current_user: UserRow = Depends(get_current_user),
    db_url: str = Depends(_get_db_url),
    storage: LocalFSAttachmentStorage = Depends(_get_storage),
) -> None:
    """Delete an attachment.  Only the owning user may delete it."""
    should_delete_file = False
    async with get_session(db_url) as db:
        result = await db.execute(select(AttachmentRow).where(AttachmentRow.id == attachment_id))
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="附件不存在")
        if row.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权删除该附件")

        storage_key = row.storage_key
        await db.execute(delete(AttachmentRow).where(AttachmentRow.id == attachment_id))
        remaining = await db.scalar(
            select(func.count())
            .select_from(AttachmentRow)
            .where(AttachmentRow.storage_key == storage_key)
        )
        should_delete_file = (remaining or 0) == 0
        await db.commit()

    if should_delete_file:
        await storage.delete(storage_key)
