"""Database engine and session factory."""

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool


@lru_cache(maxsize=1)
def get_engine(db_url: str) -> AsyncEngine:
    """Return a cached async engine for the given URL."""
    # SQLite 在 SSE 取消时更容易出现连接回收竞态，使用 NullPool 减少复用带来的终止冲突。
    if db_url.startswith("sqlite+"):
        return create_async_engine(db_url, echo=False, poolclass=NullPool)
    return create_async_engine(db_url, echo=False)


def get_session(db_url: str) -> AsyncSession:
    """Return a new AsyncSession. Use as an async context manager."""
    return AsyncSession(get_engine(db_url), expire_on_commit=False)


async def init_db(db_url: str) -> None:
    """Create all tables if they don't exist (idempotent)."""
    from sqlalchemy import text

    from astracore.infrastructure.db.models import Base

    engine = get_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 迁移：为旧版本的 chat_runs 表补充 token 用量列
        for col_sql in (
            "ALTER TABLE chat_runs ADD COLUMN input_tokens INTEGER",
            "ALTER TABLE chat_runs ADD COLUMN output_tokens INTEGER",
            "ALTER TABLE chat_runs ADD COLUMN model TEXT",
        ):
            try:
                await conn.execute(text(col_sql))
            except Exception:
                pass  # 列已存在时 SQLite/PostgreSQL 均会抛异常，忽略即可
