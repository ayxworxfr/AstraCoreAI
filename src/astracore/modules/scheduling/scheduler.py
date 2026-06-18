"""APScheduler singleton — AsyncIOScheduler with SQLAlchemyJobStore.

A sync SQLAlchemy engine is created alongside the existing async engine so that
APScheduler 3.x can persist job state to the same SQLite file without interfering
with the async session pool.  The sync engine is used exclusively by APScheduler.
"""

from __future__ import annotations

import re

from apscheduler.schedulers.asyncio import AsyncIOScheduler

_scheduler: AsyncIOScheduler | None = None


def _sync_db_url(async_url: str) -> str:
    """Convert an async DB URL to its synchronous equivalent."""
    url = re.sub(r"^\s*sqlite\+aiosqlite", "sqlite", async_url)
    url = re.sub(r"\+asyncpg", "", url)
    return url


def get_scheduler() -> AsyncIOScheduler:
    """Return the module-level scheduler singleton (must call init_scheduler first)."""
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialised — call init_scheduler() in lifespan first")
    return _scheduler


def init_scheduler(db_url: str, misfire_grace_seconds: int = 300) -> AsyncIOScheduler:
    """Create (or return) the AsyncIOScheduler singleton with a SQLAlchemy job store."""
    global _scheduler  # noqa: PLW0603

    if _scheduler is not None:
        return _scheduler

    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore  # noqa: PLC0415
    from sqlalchemy import create_engine  # noqa: PLC0415

    sync_url = _sync_db_url(db_url)
    sync_engine = create_engine(sync_url, echo=False)

    _scheduler = AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(engine=sync_engine)},
        job_defaults={"misfire_grace_time": misfire_grace_seconds, "coalesce": True},
    )
    return _scheduler


def reset_scheduler() -> None:
    """Tear down the singleton (used in tests)."""
    global _scheduler  # noqa: PLW0603
    _scheduler = None
