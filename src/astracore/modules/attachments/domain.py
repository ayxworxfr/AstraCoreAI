"""Attachment domain models."""

import dataclasses


class AttachmentCapabilityError(Exception):
    """Raised when the active LLM profile does not support the requested attachment type."""


class AttachmentProcessingError(Exception):
    """Raised when an attachment cannot be processed (e.g. encrypted PDF)."""


@dataclasses.dataclass
class AttachmentRef:
    """Reference to a stored attachment, optionally carrying loaded bytes.

    ``data`` is None when the ref is used as a pointer (e.g. in ChatOptions
    after HTTP request parsing).  ``ChatPipeline.prepare()`` populates it by
    reading from AttachmentStoragePort before passing to LLM adapters.
    """

    id: str
    mime_type: str
    filename: str
    size_bytes: int
    storage_key: str
    data: bytes | None = dataclasses.field(default=None, repr=False, compare=False)
