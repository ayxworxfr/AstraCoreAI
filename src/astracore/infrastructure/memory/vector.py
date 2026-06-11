"""Chroma vector adapter for structured memories — lazy init + graceful degradation.

Chroma is the index; SQLite is source of truth.  All sync Chroma calls are wrapped
in run_in_executor so the event loop is never blocked.  If chromadb is absent or
initialization fails, all methods degrade to no-ops / empty results.
"""

import asyncio
import logging
from typing import Any

from astracore.modules.memory.domain import StructuredMemory

logger = logging.getLogger(__name__)


class MemoryVectorAdapter:
    """Semantic retrieval adapter for structured memories via Chroma.

    Usage pattern (mirrors ChromaRetrieverAdapter from the RAG layer):
      - ``upsert(memory)``  — write/overwrite a memory vector
      - ``delete(memory_id)`` — remove a single memory vector
      - ``delete_by_conversation(conversation_id, user_id)`` — bulk remove by conversation
      - ``query(text, ...)`` — return top-N relevant document strings
    """

    _COLLECTION_NAME = "astracore_memory"

    def __init__(
        self,
        persist_directory: str | None = None,
        embedding_model: str = "all-MiniLM-L6-v2",
    ) -> None:
        self._persist_directory = persist_directory
        self._embedding_model = embedding_model
        self._client: Any = None
        self._collection: Any = None
        self._available: bool | None = None  # None = not yet attempted

    # ------------------------------------------------------------------
    # Lazy initialisation (runs once in executor on first call)
    # ------------------------------------------------------------------

    def _init_sync(self) -> None:
        try:
            import chromadb
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

            if self._persist_directory:
                client: Any = chromadb.PersistentClient(path=self._persist_directory)
            else:
                client = chromadb.Client()

            ef = SentenceTransformerEmbeddingFunction(model_name=self._embedding_model)
            self._collection = client.get_or_create_collection(
                name=self._COLLECTION_NAME,
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )
            self._client = client
            self._available = True
        except ImportError:
            logger.warning(
                "chromadb not installed; MemoryVectorAdapter degraded to no-op. "
                "Install with: pip install chromadb"
            )
            self._available = False
        except Exception:
            logger.exception("MemoryVectorAdapter init failed; degrading to no-op")
            self._available = False

    async def _ensure_init(self) -> None:
        if self._available is None:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._init_sync)

    # ------------------------------------------------------------------
    # Document helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _document(memory: StructuredMemory) -> str:
        if memory.subject:
            return f"{memory.subject}: {memory.content}"
        return memory.content

    @staticmethod
    def _metadata(memory: StructuredMemory) -> dict[str, Any]:
        return {
            "memory_id": memory.id,
            "user_id": memory.user_id,
            "scope": memory.scope.value,
            "session_id": str(memory.session_id) if memory.session_id else "",
            "conversation_id": str(memory.conversation_id) if memory.conversation_id else "",
            "project_id": memory.project_id or "",
            "type": memory.type.value,
            "importance": memory.importance,
            "locked": memory.locked,
            "status": memory.status.value,
        }

    # ------------------------------------------------------------------
    # Synchronous helpers (run in executor)
    # ------------------------------------------------------------------

    def _upsert_sync(self, memory: StructuredMemory) -> None:
        self._collection.upsert(
            ids=[memory.id],
            documents=[self._document(memory)],
            metadatas=[self._metadata(memory)],
        )

    def _delete_sync(self, memory_id: str) -> None:
        try:
            self._collection.delete(ids=[memory_id])
        except Exception:
            pass

    def _delete_by_conversation_sync(self, conversation_id: str, user_id: str) -> None:
        try:
            self._collection.delete(
                where={
                    "$and": [
                        {"user_id": {"$eq": user_id}},
                        {"conversation_id": {"$eq": conversation_id}},
                    ]
                }
            )
        except Exception:
            pass

    def _query_sync(
        self,
        text: str,
        *,
        user_id: str,
        scope_filter: list[str],
        session_id: str | None,
        project_id: str | None,
        n_results: int,
    ) -> list[str]:
        conditions: list[dict[str, Any]] = [
            {"user_id": {"$eq": user_id}},
            {"scope": {"$in": scope_filter}},
            {"status": {"$eq": "active"}},
        ]
        if session_id:
            conditions.append({"session_id": {"$eq": session_id}})
        if project_id:
            conditions.append({"project_id": {"$eq": project_id}})

        where: dict[str, Any] = {"$and": conditions}

        try:
            results = self._collection.query(
                query_texts=[text],
                n_results=n_results,
                where=where,
            )
        except Exception:
            # Chroma raises if the collection is empty or filter returns zero candidates
            return []

        docs = results.get("documents", [[]])[0]
        return [d for d in docs if d]

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def upsert(self, memory: StructuredMemory) -> None:
        """Upsert a memory into the vector index (no-op if unavailable)."""
        await self._ensure_init()
        if not self._available:
            return
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, lambda: self._upsert_sync(memory))
        except Exception:
            logger.exception("MemoryVectorAdapter.upsert failed for memory_id=%s", memory.id)

    async def delete(self, memory_id: str) -> None:
        """Delete a single memory from the vector index (no-op if unavailable)."""
        await self._ensure_init()
        if not self._available:
            return
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, lambda: self._delete_sync(memory_id))
        except Exception:
            logger.exception("MemoryVectorAdapter.delete failed for memory_id=%s", memory_id)

    async def delete_by_conversation(self, conversation_id: str, *, user_id: str) -> None:
        """Delete all memories linked to a conversation (no-op if unavailable)."""
        await self._ensure_init()
        if not self._available:
            return
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None, lambda: self._delete_by_conversation_sync(conversation_id, user_id)
            )
        except Exception:
            logger.exception(
                "MemoryVectorAdapter.delete_by_conversation failed for conversation_id=%s",
                conversation_id,
            )

    async def query(
        self,
        text: str,
        *,
        user_id: str,
        scope_filter: list[str],
        session_id: str | None = None,
        project_id: str | None = None,
        n_results: int = 8,
    ) -> list[str]:
        """Return top-N semantically relevant memory document strings.

        Returns an empty list when the adapter is unavailable or on error.
        """
        await self._ensure_init()
        if not self._available:
            return []
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None,
                lambda: self._query_sync(
                    text,
                    user_id=user_id,
                    scope_filter=scope_filter,
                    session_id=session_id,
                    project_id=project_id,
                    n_results=n_results,
                ),
            )
        except Exception:
            logger.exception("MemoryVectorAdapter.query failed")
            return []
