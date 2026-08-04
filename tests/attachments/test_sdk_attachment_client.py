"""SDK AttachmentClient — upload, delete, auto-resolve (Slice 7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from astracore.infrastructure.attachments.local_fs import LocalFSAttachmentStorage
from astracore.infrastructure.db.session import get_engine
from astracore.modules.attachments.domain import AttachmentRef
from astracore.sdk.client import AttachmentClient
from tests.support.db import prepare_test_db

_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
    b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
    b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
async def client(tmp_path: Path) -> AttachmentClient:
    db_url = await prepare_test_db(tmp_path)
    storage = LocalFSAttachmentStorage(base_path=tmp_path / "attachments")
    yield AttachmentClient(storage=storage, db_url=db_url)
    get_engine.cache_clear()


@pytest.mark.asyncio
async def test_upload_bytes_returns_attachment_ref(client: AttachmentClient):
    ref = await client.upload(_PNG_1PX, filename="img.png", mime_type="image/png")
    assert isinstance(ref, AttachmentRef)
    assert ref.mime_type == "image/png"
    assert ref.filename == "img.png"
    assert ref.size_bytes == len(_PNG_1PX)
    assert ref.data == _PNG_1PX
    assert ref.storage_key


@pytest.mark.asyncio
async def test_upload_path_infers_mime_type(client: AttachmentClient, tmp_path: Path):
    png_file = tmp_path / "photo.png"
    png_file.write_bytes(_PNG_1PX)
    ref = await client.upload(png_file)
    assert ref.mime_type == "image/png"
    assert ref.filename == "photo.png"


@pytest.mark.asyncio
async def test_delete_removes_record_and_file(client: AttachmentClient, tmp_path: Path):
    ref = await client.upload(_PNG_1PX, filename="img.png", mime_type="image/png")
    file_path = tmp_path / "attachments" / ref.storage_key
    assert file_path.exists()

    await client.delete(ref.id)
    assert not file_path.exists()


@pytest.mark.asyncio
async def test_delete_shared_file_only_after_last_reference(
    client: AttachmentClient,
    tmp_path: Path,
):
    first = await client.upload(_PNG_1PX, filename="a.png", mime_type="image/png")
    second = await client.upload(_PNG_1PX, filename="b.png", mime_type="image/png")
    assert first.id != second.id
    assert first.storage_key == second.storage_key

    file_path = tmp_path / "attachments" / first.storage_key
    await client.delete(first.id)
    assert file_path.exists()

    await client.delete(second.id)
    assert not file_path.exists()


@pytest.mark.asyncio
async def test_delete_nonexistent_is_noop(client: AttachmentClient):
    await client.delete("does-not-exist")  # must not raise


@pytest.mark.asyncio
async def test_resolve_path_objects_auto_upload(client: AttachmentClient, tmp_path: Path):
    png_file = tmp_path / "photo.png"
    png_file.write_bytes(_PNG_1PX)
    refs = await client._resolve([png_file])
    assert len(refs) == 1
    assert refs[0].mime_type == "image/png"
    assert refs[0].data == _PNG_1PX


@pytest.mark.asyncio
async def test_resolve_attachment_ref_passes_through(client: AttachmentClient):
    existing = AttachmentRef(
        id="att-x",
        mime_type="image/png",
        filename="x.png",
        size_bytes=100,
        storage_key="u/x.png",
        data=_PNG_1PX,
    )
    refs = await client._resolve([existing])
    assert refs == [existing]
