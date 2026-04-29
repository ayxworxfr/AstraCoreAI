"""SQLAlchemy ORM models (dialect-agnostic: SQLite + PostgreSQL)."""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MemoryEntryRow(Base):
    """Persistent long-term memory entry."""

    __tablename__ = "memory_entries"

    entry_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    memory_type: Mapped[str] = mapped_column(String(64), nullable=False, default="long_term")
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_memory_entries_session_created", "session_id", "created_at"),
    )


class SkillRow(Base):
    """User-defined or built-in skill (named system prompt)."""

    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    # MD 文件名（不含扩展名），作为内置 Skill 的稳定标识符，用于跨重启的 upsert 和孤儿清理
    source_key: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ChatSessionRow(Base):
    """Persisted short-term conversation history (survives backend restarts)."""

    __tablename__ = "chat_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    messages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ChatRunRow(Base):
    """Background chat generation run, decoupled from browser SSE connections."""

    __tablename__ = "chat_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    request: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    thinking_blocks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tool_activity: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_chat_runs_session_status_updated", "session_id", "status", "updated_at"),
    )


class ConversationRow(Base):
    """Persisted conversation metadata (title, pin status, skill/model preferences)."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="新会话")
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    skill_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_message_preview: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class UserSettingsRow(Base):
    """Key-value store for user preferences."""

    __tablename__ = "user_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
