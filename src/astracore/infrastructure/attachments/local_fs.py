"""Local filesystem attachment storage adapter."""

import hashlib
from pathlib import Path

from astracore.modules.attachments.ports import AttachmentStoragePort


class LocalFSAttachmentStorage(AttachmentStoragePort):
    """Stores attachments as files under ``base_path/<user_id>/<sha256>.<ext>``.

    The storage_key is the relative path ``<user_id>/<sha256>.<ext>``, which is
    opaque to callers and safe to persist in the DB.
    """

    def __init__(self, base_path: Path) -> None:
        self._base = base_path

    async def save(self, data: bytes, ext: str, user_id: str) -> str:
        sha = hashlib.sha256(data).hexdigest()
        clean_ext = ext.lstrip(".").lower()
        storage_key = f"{user_id}/{sha}.{clean_ext}"
        dest = self._base / storage_key
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write via sibling temp file to avoid partial reads.
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        try:
            tmp.write_bytes(data)
            tmp.replace(dest)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return storage_key

    async def load(self, storage_key: str) -> bytes:
        path = self._base / storage_key
        if not path.exists():
            raise FileNotFoundError(f"Attachment not found: {storage_key}")
        return path.read_bytes()

    async def delete(self, storage_key: str) -> None:
        path = self._base / storage_key
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass
