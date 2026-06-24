"""Attachment storage port interface."""

from abc import ABC, abstractmethod


class AttachmentStoragePort(ABC):
    """Abstract port for attachment persistence.

    Implementations must be fully replaceable (local FS, S3, MinIO, etc.)
    without touching any business logic above this interface.
    """

    @abstractmethod
    async def save(self, data: bytes, ext: str, user_id: str) -> str:
        """Persist attachment bytes and return a unique storage_key."""

    @abstractmethod
    async def load(self, storage_key: str) -> bytes:
        """Load attachment bytes by storage_key.

        Raises FileNotFoundError if the key does not exist.
        """

    @abstractmethod
    async def delete(self, storage_key: str) -> None:
        """Delete attachment by storage_key.

        No-op if the key does not exist.
        """
