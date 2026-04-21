"""Database engine and session factory."""

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine


@lru_cache(maxsize=1)
def get_engine(db_url: str) -> AsyncEngine:
    """Return a cached async engine for the given URL."""
    return create_async_engine(db_url, echo=False)


def get_session(db_url: str) -> AsyncSession:
    """Return a new AsyncSession. Use as an async context manager."""
    return AsyncSession(get_engine(db_url), expire_on_commit=False)


async def init_db(db_url: str) -> None:
    """Create all tables if they don't exist (idempotent)."""
    from astracore.adapters.db.models import Base

    engine = get_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
