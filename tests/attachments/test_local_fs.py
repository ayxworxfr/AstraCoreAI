"""Unit tests for LocalFSAttachmentStorage."""

import pytest

from astracore.infrastructure.attachments.local_fs import LocalFSAttachmentStorage


@pytest.fixture
def storage(tmp_path):
    return LocalFSAttachmentStorage(base_path=tmp_path / "attachments")


@pytest.mark.asyncio
async def test_save_returns_storage_key(storage):
    key = await storage.save(b"hello", "txt", "user-1")
    assert "user-1/" in key
    assert key.endswith(".txt")


@pytest.mark.asyncio
async def test_load_round_trip(storage):
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    key = await storage.save(data, "png", "user-1")
    loaded = await storage.load(key)
    assert loaded == data


@pytest.mark.asyncio
async def test_same_content_same_key(storage):
    data = b"identical"
    key1 = await storage.save(data, "bin", "user-1")
    key2 = await storage.save(data, "bin", "user-1")
    assert key1 == key2


@pytest.mark.asyncio
async def test_load_missing_raises_file_not_found(storage):
    with pytest.raises(FileNotFoundError):
        await storage.load("user-1/nonexistent.png")


@pytest.mark.asyncio
async def test_delete_removes_file(storage):
    key = await storage.save(b"to-delete", "txt", "user-1")
    await storage.delete(key)
    with pytest.raises(FileNotFoundError):
        await storage.load(key)


@pytest.mark.asyncio
async def test_delete_nonexistent_is_noop(storage):
    # Must not raise.
    await storage.delete("user-1/does-not-exist.png")
