"""Hybrid memory adapter using Redis + SQLAlchemy (SQLite or PostgreSQL)."""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from astracore.core.domain.message import Message
from astracore.core.ports.memory import MemoryAdapter, MemoryEntry
from astracore.runtime.observability.logger import get_logger

logger = get_logger(__name__)


class HybridMemoryAdapter(MemoryAdapter):
    """Hybrid memory using Redis (short-term) and a SQL database (long-term).

    Read path: Redis → SQLite (restart persistence).
    When Redis is unavailable, falls back directly to SQLite.
    Safe for multi-process deployments — no in-process state.
    """

    def __init__(self, redis_url: str, db_url: str):
        self.redis_url = redis_url
        self.db_url = db_url
        self._redis: Any = None
        self._db_engine: Any = None
        self._redis_disabled = False

    def _get_redis(self) -> Any:
        """Lazy load Redis client."""
        if self._redis_disabled:
            return None
        if self._redis is None:
            try:
                from redis.asyncio import Redis

                self._redis = Redis.from_url(self.redis_url, decode_responses=True)
            except ImportError as e:
                raise ImportError(
                    "redis package not installed. Install with: pip install redis"
                ) from e
        return self._redis

    def _disable_redis(self) -> None:
        self._redis_disabled = True
        self._redis = None

    @staticmethod
    def _session_key(session_id: UUID) -> str:
        """Build stable short-term memory key."""
        return f"session:{session_id}:messages"

    @staticmethod
    def _deserialize_messages(messages_data: list[dict[str, Any]]) -> list[Message]:
        """Convert serialized message data to domain messages."""
        return [Message(**msg_data) for msg_data in messages_data]

    def _get_db(self) -> Any:
        """Lazy load database engine."""
        if self._db_engine is None:
            from astracore.adapters.db.session import get_engine

            self._db_engine = get_engine(self.db_url)
        return self._db_engine

    async def _save_short_term_to_db(
        self, session_id: UUID, messages_data: list[dict[str, Any]]
    ) -> None:
        """Upsert short-term messages to the DB for restart persistence."""
        try:
            from sqlalchemy.ext.asyncio import AsyncSession

            from astracore.adapters.db.models import ChatSessionRow

            engine = self._get_db()
            async with AsyncSession(engine) as db:
                existing = await db.get(ChatSessionRow, str(session_id))
                if existing:
                    existing.messages = messages_data
                    existing.updated_at = datetime.now(UTC)
                else:
                    db.add(
                        ChatSessionRow(
                            session_id=str(session_id),
                            messages=messages_data,
                            updated_at=datetime.now(UTC),
                        )
                    )
                await db.commit()
        except Exception:
            logger.exception("Failed to save short-term memory to DB for session %s", session_id)

    async def _load_short_term_from_db(
        self, session_id: UUID
    ) -> list[dict[str, Any]] | None:
        """Load short-term messages from DB; return None if not found."""
        try:
            from sqlalchemy.ext.asyncio import AsyncSession

            from astracore.adapters.db.models import ChatSessionRow

            engine = self._get_db()
            async with AsyncSession(engine) as db:
                row = await db.get(ChatSessionRow, str(session_id))
                return row.messages if row else None
        except Exception:
            logger.warning(
                "DB unavailable while loading short-term memory for session %s", session_id
            )
            return None

    async def save_short_term(
        self,
        session_id: UUID,
        messages: list[Message],
        ttl_seconds: int = 3600,
    ) -> None:
        """Save short-term memory to Redis + DB."""
        key = self._session_key(session_id)
        messages_data = [msg.model_dump(mode="json") for msg in messages]

        redis = self._get_redis()
        if redis is not None:
            try:
                await redis.setex(key, ttl_seconds, json.dumps(messages_data))
            except Exception:
                logger.warning(
                    "Redis write failed for session %s, disabling Redis", session_id, exc_info=True
                )
                self._disable_redis()

        await self._save_short_term_to_db(session_id, messages_data)

    async def load_short_term(
        self,
        session_id: UUID,
    ) -> list[Message]:
        """Load short-term memory: Redis → DB."""
        key = self._session_key(session_id)

        # 1. Try Redis
        redis = self._get_redis()
        if redis is not None:
            try:
                data = await redis.get(key)
                if data:
                    return self._deserialize_messages(json.loads(data))
            except Exception:
                logger.warning(
                    "Redis read failed for session %s, disabling Redis", session_id, exc_info=True
                )
                self._disable_redis()

        # 2. Fall back to DB (survives restarts)
        db_data = await self._load_short_term_from_db(session_id)
        return self._deserialize_messages(db_data) if db_data is not None else []

    async def save_long_term(
        self,
        session_id: UUID,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        """Save long-term memory to the database."""
        entry = MemoryEntry(
            session_id=session_id,
            content=summary,
            memory_type="long_term",
            metadata=metadata or {},
        )

        try:
            from sqlalchemy.ext.asyncio import AsyncSession

            from astracore.adapters.db.models import MemoryEntryRow

            engine = self._get_db()
            async with AsyncSession(engine) as db:
                row = MemoryEntryRow(
                    entry_id=str(entry.entry_id),
                    session_id=str(session_id),
                    content=summary,
                    memory_type="long_term",
                    meta=metadata or {},
                )
                db.add(row)
                await db.commit()
        except Exception:
            logger.exception("Failed to save long-term memory for session %s", session_id)

        return entry

    async def load_long_term(
        self,
        session_id: UUID,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """Load long-term memory from the database."""
        try:
            from sqlalchemy import select
            from sqlalchemy.ext.asyncio import AsyncSession

            from astracore.adapters.db.models import MemoryEntryRow

            engine = self._get_db()
            async with AsyncSession(engine) as db:
                result = await db.execute(
                    select(MemoryEntryRow)
                    .where(MemoryEntryRow.session_id == str(session_id))
                    .order_by(MemoryEntryRow.created_at.desc())
                    .limit(limit)
                )
                rows = result.scalars().all()
                return [
                    MemoryEntry(
                        session_id=session_id,
                        content=row.content,
                        memory_type=row.memory_type,
                        metadata=row.meta,
                        created_at=row.created_at,
                    )
                    for row in rows
                ]
        except Exception:
            logger.exception("Failed to load long-term memory for session %s", session_id)
            return []

    async def search_memory(
        self,
        query: str,
        session_id: UUID | None = None,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """Full-text search via ILIKE (PostgreSQL) or LIKE (SQLite)."""
        try:
            from sqlalchemy import select
            from sqlalchemy.ext.asyncio import AsyncSession

            from astracore.adapters.db.models import MemoryEntryRow

            engine = self._get_db()
            async with AsyncSession(engine) as db:
                stmt = select(MemoryEntryRow).where(
                    MemoryEntryRow.content.ilike(f"%{query}%")
                )
                if session_id is not None:
                    stmt = stmt.where(MemoryEntryRow.session_id == str(session_id))
                stmt = stmt.order_by(MemoryEntryRow.created_at.desc()).limit(limit)
                result = await db.execute(stmt)
                rows = result.scalars().all()
                return [
                    MemoryEntry(
                        session_id=UUID(row.session_id),
                        content=row.content,
                        memory_type=row.memory_type,
                        metadata=row.meta,
                        created_at=row.created_at,
                    )
                    for row in rows
                ]
        except Exception:
            logger.exception("Failed to search memory for query %r", query)
            return []

    async def delete_session_memory(
        self,
        session_id: UUID,
    ) -> None:
        """Delete all memory for a session (Redis + DB)."""
        key = self._session_key(session_id)

        redis = self._get_redis()
        if redis is not None:
            try:
                await redis.delete(key)
            except Exception:
                logger.warning(
                    "Redis delete failed for session %s, disabling Redis", session_id, exc_info=True
                )
                self._disable_redis()

        try:
            from sqlalchemy.ext.asyncio import AsyncSession

            from astracore.adapters.db.models import ChatSessionRow

            engine = self._get_db()
            async with AsyncSession(engine) as db:
                row = await db.get(ChatSessionRow, str(session_id))
                if row:
                    await db.delete(row)
                    await db.commit()
        except Exception:
            logger.exception("Failed to delete session memory from DB for session %s", session_id)

    async def ensure_schema(self) -> None:
        """Create database tables if they don't exist. Call at startup."""
        from astracore.adapters.db.models import Base

        engine = self._get_db()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
