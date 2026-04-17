"""Hybrid memory adapter using Redis + PostgreSQL."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from astracore.core.domain.message import Message
from astracore.core.ports.memory import MemoryAdapter, MemoryEntry


class HybridMemoryAdapter(MemoryAdapter):
    """Hybrid memory using Redis (short-term) and PostgreSQL (long-term).

    When Redis is unavailable, falls back to an in-process dict with TTL eviction
    and a max-session cap to prevent unbounded memory growth.
    The in-memory fallback is NOT shared across processes and is lost on restart.
    """

    _MAX_IN_MEMORY_SESSIONS: int = 1_000
    _SESSION_TTL: timedelta = timedelta(hours=1)

    def __init__(self, redis_url: str, postgres_url: str):
        self.redis_url = redis_url
        self.postgres_url = postgres_url
        self._redis: Any = None
        self._db_engine: Any = None
        self._redis_disabled = False
        self._in_memory_sessions: dict[str, list[dict[str, Any]]] = {}
        self._session_timestamps: dict[str, datetime] = {}

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
        """Disable Redis after connection failures to avoid repeated blocking retries."""
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
            try:
                from sqlalchemy.ext.asyncio import create_async_engine

                self._db_engine = create_async_engine(self.postgres_url)
            except ImportError as e:
                raise ImportError(
                    "sqlalchemy and asyncpg required. Install with: pip install sqlalchemy asyncpg"
                ) from e
        return self._db_engine

    def _evict_stale(self) -> None:
        """Remove expired sessions and enforce the max-session cap.

        Called on every write to keep memory bounded without a background task.
        """
        now = datetime.now(UTC)
        stale = [
            k
            for k, ts in self._session_timestamps.items()
            if now - ts > self._SESSION_TTL
        ]
        for k in stale:
            self._in_memory_sessions.pop(k, None)
            self._session_timestamps.pop(k, None)

        # Enforce cap: evict oldest entries first (LRU approximation)
        while len(self._in_memory_sessions) > self._MAX_IN_MEMORY_SESSIONS:
            oldest = min(self._session_timestamps, key=lambda k: self._session_timestamps[k])
            self._in_memory_sessions.pop(oldest, None)
            self._session_timestamps.pop(oldest, None)

    async def save_short_term(
        self,
        session_id: UUID,
        messages: list[Message],
        ttl_seconds: int = 3600,
    ) -> None:
        """Save short-term memory to Redis, with in-memory fallback."""
        key = self._session_key(session_id)
        messages_data = [msg.model_dump(mode="json") for msg in messages]

        self._evict_stale()
        self._in_memory_sessions[key] = messages_data
        self._session_timestamps[key] = datetime.now(UTC)

        redis = self._get_redis()
        if redis is None:
            return

        try:
            await redis.setex(key, ttl_seconds, json.dumps(messages_data))
        except Exception:
            self._disable_redis()

    async def load_short_term(
        self,
        session_id: UUID,
    ) -> list[Message]:
        """Load short-term memory from Redis, falling back to in-memory."""
        key = self._session_key(session_id)

        redis = self._get_redis()
        if redis is None:
            return self._deserialize_messages(self._in_memory_sessions.get(key, []))

        try:
            data = await redis.get(key)
        except Exception:
            self._disable_redis()
            return self._deserialize_messages(self._in_memory_sessions.get(key, []))

        if not data:
            return self._deserialize_messages(self._in_memory_sessions.get(key, []))

        messages_data = json.loads(data)
        # Keep in-memory cache in sync with Redis
        self._in_memory_sessions[key] = messages_data
        self._session_timestamps[key] = datetime.now(UTC)
        return self._deserialize_messages(messages_data)

    async def save_long_term(
        self,
        session_id: UUID,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        """Save long-term memory to PostgreSQL."""
        entry = MemoryEntry(
            session_id=session_id,
            content=summary,
            memory_type="long_term",
            metadata=metadata or {},
        )

        try:
            from sqlalchemy.ext.asyncio import AsyncSession

            from astracore.adapters.memory.models import MemoryEntryRow

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
            pass  # Degrade gracefully if DB unavailable

        return entry

    async def load_long_term(
        self,
        session_id: UUID,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """Load long-term memory from PostgreSQL."""
        try:
            from sqlalchemy import select
            from sqlalchemy.ext.asyncio import AsyncSession

            from astracore.adapters.memory.models import MemoryEntryRow

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
            return []

    async def search_memory(
        self,
        query: str,
        session_id: UUID | None = None,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """Full-text search via ILIKE. For production, consider pg_trgm or a vector index."""
        try:
            from sqlalchemy import select
            from sqlalchemy.ext.asyncio import AsyncSession

            from astracore.adapters.memory.models import MemoryEntryRow

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
            return []

    async def delete_session_memory(
        self,
        session_id: UUID,
    ) -> None:
        """Delete all memory for a session."""
        key = self._session_key(session_id)

        self._in_memory_sessions.pop(key, None)
        self._session_timestamps.pop(key, None)

        redis = self._get_redis()
        if redis is None:
            return
        try:
            await redis.delete(key)
        except Exception:
            self._disable_redis()

    async def ensure_schema(self) -> None:
        """Create database tables if they don't exist. Call at startup."""
        from astracore.adapters.memory.models import Base

        engine = self._get_db()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
