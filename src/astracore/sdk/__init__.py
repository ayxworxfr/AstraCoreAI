"""SDK client for AstraCore AI."""

from typing import TYPE_CHECKING

# AstraCoreConfig and ChatOptions are always safe to import eagerly (no circular dependency).
from astracore.modules.chat.domain.chat_options import ChatOptions
from astracore.sdk.config import AstraCoreConfig

if TYPE_CHECKING:
    from astracore.sdk.client import (
        AstraCoreClient,
        ChatResult,
        Conversation,
        MemoryClient,
        ProjectClient,
        WorkflowClient,
    )

# AstraCoreClient and ChatResult are loaded lazily to avoid a circular import:
#   chat_pipeline  →  sdk.config  →  sdk.__init__  →  sdk.client  →  chat_pipeline
# With __getattr__, importing the package itself does not trigger client.py until it is
# actually accessed, by which time chat_pipeline is fully initialised.


def __getattr__(name: str) -> object:
    _client_names = {
        "AstraCoreClient",
        "ChatResult",
        "Conversation",
        "MemoryClient",
        "ProjectClient",
        "WorkflowClient",
    }
    if name in _client_names:
        from astracore.sdk.client import (  # noqa: PLC0415
            AstraCoreClient,
            ChatResult,
            Conversation,
            MemoryClient,
            ProjectClient,
            WorkflowClient,
        )

        globals()["AstraCoreClient"] = AstraCoreClient
        globals()["ChatResult"] = ChatResult
        globals()["Conversation"] = Conversation
        globals()["MemoryClient"] = MemoryClient
        globals()["ProjectClient"] = ProjectClient
        globals()["WorkflowClient"] = WorkflowClient
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AstraCoreClient",
    "AstraCoreConfig",
    "ChatOptions",
    "ChatResult",
    "Conversation",
    "MemoryClient",
    "ProjectClient",
    "WorkflowClient",
]
