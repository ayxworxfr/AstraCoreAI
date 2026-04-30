"""SDK client for AstraCore AI."""

# AstraCoreConfig is always safe to import eagerly (no circular dependency).
from astracore.sdk.config import AstraCoreConfig

# AstraCoreClient and ChatResult are loaded lazily to avoid a circular import:
#   chat_orchestrator  →  sdk.config  →  sdk.__init__  →  sdk.client  →  chat_orchestrator
# With __getattr__, importing the package itself does not trigger client.py until it is
# actually accessed, by which time chat_orchestrator is fully initialised.


def __getattr__(name: str) -> object:
    if name in ("AstraCoreClient", "ChatResult"):
        from astracore.sdk.client import AstraCoreClient, ChatResult  # noqa: PLC0415

        globals()["AstraCoreClient"] = AstraCoreClient
        globals()["ChatResult"] = ChatResult
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["AstraCoreClient", "AstraCoreConfig", "ChatResult"]
