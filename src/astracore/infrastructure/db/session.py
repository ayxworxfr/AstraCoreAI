"""Database engine and session factory."""

from functools import lru_cache
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


@lru_cache(maxsize=1)
def get_engine(db_url: str) -> AsyncEngine:
    # SQLite 在 SSE 取消时更容易出现连接回收竞态，使用 NullPool 减少复用带来的终止冲突。
    if db_url.startswith("sqlite+"):
        return create_async_engine(db_url, echo=False, poolclass=NullPool)
    return create_async_engine(db_url, echo=False)


def get_session(db_url: str) -> AsyncSession:
    """Return a new AsyncSession. Use as an async context manager."""
    return AsyncSession(get_engine(db_url), expire_on_commit=False)


async def _apply_migrations(conn) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS _schema_migrations ("
            "  version TEXT PRIMARY KEY,"
            "  applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
    )

    result = await conn.execute(text("SELECT version FROM _schema_migrations"))
    applied = {row[0] for row in result.fetchall()}

    if not _MIGRATIONS_DIR.exists():
        return

    for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        version = sql_file.stem
        if version in applied:
            continue

        sql = sql_file.read_text(encoding="utf-8")
        for statement in sql.split(";"):
            # Strip comments and whitespace
            lines = [ln for ln in statement.splitlines() if not ln.strip().startswith("--")]
            statement = "\n".join(lines).strip()
            if statement:
                await conn.execute(text(statement))

        await conn.execute(
            text("INSERT INTO _schema_migrations (version) VALUES (:v)"),
            {"v": version},
        )


async def init_db(db_url: str) -> None:
    """Create all tables and apply pending migrations."""
    from astracore.infrastructure.db.models import Base

    engine = get_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_migrations(conn)

    # 种子：若无任何用户，自动创建默认 admin 账号 admin/admin123
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy import func as sa_func
    from sqlalchemy import select

    from astracore.infrastructure.db.models import UserRow
    from astracore.modules.auth.domain import hash_password

    async with AsyncSession(engine, expire_on_commit=False) as db:
        count_result = await db.execute(select(sa_func.count()).select_from(UserRow))
        if count_result.scalar_one() == 0:
            now = datetime.now(UTC)
            db.add(
                UserRow(
                    id=str(uuid4()),
                    username="admin",
                    hashed_password=hash_password("admin123"),
                    role="admin",
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
