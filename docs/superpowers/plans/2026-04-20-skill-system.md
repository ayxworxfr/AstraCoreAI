# Skill System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-layer skill (system prompt) system with SQLite/PostgreSQL dual-DB support, skill CRUD API, and full frontend management UI.

**Architecture:** New `adapters/db/` module owns all SQLAlchemy models and session management. `MemoryConfig.postgres_url` replaced by `db_url` (defaults to SQLite). Skill resolution is injected into `_build_system_prompt()` in chat.py before every request.

**Tech Stack:** Python/FastAPI/SQLAlchemy async, aiosqlite, React/TypeScript/Ant Design, Zustand, @uiw/react-md-editor

---

## File Map

**Backend — New**
- `src/astracore/adapters/db/__init__.py`
- `src/astracore/adapters/db/models.py` — All ORM tables: MemoryEntryRow, SkillRow, UserSettingsRow
- `src/astracore/adapters/db/session.py` — `get_engine()`, `get_session()`, `init_db()`
- `src/astracore/service/api/skills.py` — CRUD endpoints for skills
- `src/astracore/service/api/settings.py` — GET/PUT user settings

**Backend — Modified**
- `src/astracore/sdk/config.py` — `MemoryConfig.postgres_url` → `db_url`
- `src/astracore/adapters/memory/models.py` — DELETE (absorbed into db/models.py)
- `src/astracore/adapters/memory/hybrid.py` — use `db_url`, import from `adapters.db`
- `src/astracore/sdk/client.py` — pass `db_url` instead of `postgres_url`
- `src/astracore/service/api/app.py` — register new routers, seed built-in skills at startup
- `src/astracore/service/api/chat.py` — add `skill_id`/`disable_skill` to ChatRequest, rewrite system prompt composition
- `.env` / `.env.example` — rename postgres_url → db_url

**Frontend — New**
- `frontend/src/types/skill.ts`
- `frontend/src/services/skillService.ts`
- `frontend/src/stores/skillStore.ts`
- `frontend/src/components/skills/SkillCard.tsx`
- `frontend/src/components/skills/SkillModal.tsx`
- `frontend/src/components/skills/SkillSelector.tsx`
- `frontend/src/pages/SkillsPage.tsx`

**Frontend — Modified**
- `frontend/src/types/api.ts` — add `skill_id`, `disable_skill` to ChatRequest
- `frontend/src/stores/chatStore.ts` — add `activeSkillId`, `setActiveSkillId`
- `frontend/src/app/router.tsx` — add `/skills` route
- `frontend/src/layouts/AppShell.tsx` — add 个性化 nav item
- `frontend/src/components/chat/ChatMain.tsx` — add SkillSelector to toolbar

---

## Task 1: DB adapter layer — models + session

**Files:**
- Create: `src/astracore/adapters/db/__init__.py`
- Create: `src/astracore/adapters/db/models.py`
- Create: `src/astracore/adapters/db/session.py`

- [ ] **Step 1: Create `adapters/db/__init__.py`**

```python
# empty
```

- [ ] **Step 2: Create `adapters/db/models.py`**

```python
"""SQLAlchemy ORM models (dialect-agnostic: SQLite + PostgreSQL)."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, JSON, String, Text
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
```

- [ ] **Step 3: Create `adapters/db/session.py`**

```python
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
```

- [ ] **Step 4: Commit**

```bash
git add src/astracore/adapters/db/
git commit -m "feat: add dialect-agnostic db adapter layer (models + session)"
```

---

## Task 2: Config + memory adapter migration

**Files:**
- Modify: `src/astracore/sdk/config.py`
- Modify: `src/astracore/adapters/memory/hybrid.py`
- Delete: `src/astracore/adapters/memory/models.py`
- Modify: `src/astracore/sdk/client.py`
- Modify: `.env`
- Modify: `.env.example`

- [ ] **Step 1: Update `sdk/config.py` — replace `postgres_url` with `db_url`**

In `MemoryConfig`, replace:
```python
postgres_url: str = "postgresql+asyncpg://localhost/astracore"
```
with:
```python
db_url: str = "sqlite+aiosqlite:///./astracore.db"
```

- [ ] **Step 2: Update `adapters/memory/hybrid.py`**

Replace constructor signature:
```python
def __init__(self, redis_url: str, db_url: str):
    self.redis_url = redis_url
    self.db_url = db_url
    self._redis: Any = None
    self._db_engine: Any = None
    self._redis_disabled = False
    self._in_memory_sessions: dict[str, list[dict[str, Any]]] = {}
    self._session_timestamps: dict[str, datetime] = {}
```

Replace `_get_db()`:
```python
def _get_db(self) -> Any:
    """Lazy load database engine."""
    if self._db_engine is None:
        from astracore.adapters.db.session import get_engine
        self._db_engine = get_engine(self.db_url)
    return self._db_engine
```

Replace all imports of `astracore.adapters.memory.models` with `astracore.adapters.db.models`:
```python
# in save_long_term:
from astracore.adapters.db.models import MemoryEntryRow

# in load_long_term:
from astracore.adapters.db.models import MemoryEntryRow

# in search_memory:
from astracore.adapters.db.models import MemoryEntryRow

# in ensure_schema:
from astracore.adapters.db.models import Base
```

- [ ] **Step 3: Delete `src/astracore/adapters/memory/models.py`**

```bash
rm src/astracore/adapters/memory/models.py
```

- [ ] **Step 4: Update `sdk/client.py` — fix constructor call**

In `_create_llm_adapter` and `__init__`, replace `postgres_url=config.memory.postgres_url` with `db_url=config.memory.db_url`:

```python
self._memory = HybridMemoryAdapter(
    redis_url=config.memory.redis_url,
    db_url=config.memory.db_url,
)
```

- [ ] **Step 5: Update `.env`**

```env
# LLM Configuration（统一四元配置）
# ASTRACORE__LLM__PROVIDER: deepseek | anthropic
ASTRACORE__LLM__PROVIDER=anthropic
ASTRACORE__LLM__BASE_URL=https://llm-proxy.oa.com/aws
ASTRACORE__LLM__API_KEY=app-key-F1QWwWrSx3kfNTWw
ASTRACORE__LLM__MODEL=claude-sonnet-4-6

# Memory Configuration
ASTRACORE__MEMORY__REDIS_URL=redis://localhost:6379/0
ASTRACORE__MEMORY__DB_URL=sqlite+aiosqlite:///./astracore.db

# Retrieval Configuration
ASTRACORE__RETRIEVAL__COLLECTION_NAME=astracore
ASTRACORE__RETRIEVAL__PERSIST_DIRECTORY=./chroma_db
```

- [ ] **Step 6: Update `.env.example`**

```env
# LLM Configuration（统一四元配置）
# ASTRACORE__LLM__PROVIDER: deepseek | anthropic
ASTRACORE__LLM__PROVIDER=deepseek
ASTRACORE__LLM__BASE_URL=https://api.deepseek.com
ASTRACORE__LLM__API_KEY=
ASTRACORE__LLM__MODEL=deepseek-chat

# Memory Configuration
ASTRACORE__MEMORY__REDIS_URL=redis://localhost:6379/0
ASTRACORE__MEMORY__DB_URL=sqlite+aiosqlite:///./astracore.db

# Retrieval Configuration
ASTRACORE__RETRIEVAL__COLLECTION_NAME=astracore
ASTRACORE__RETRIEVAL__PERSIST_DIRECTORY=./chroma_db
```

- [ ] **Step 7: Update `service/api/chat.py` — fix memory adapter call**

In `_get_memory_adapter()`, replace:
```python
cfg = _get_settings().memory
return HybridMemoryAdapter(redis_url=cfg.redis_url, postgres_url=cfg.postgres_url)
```
with:
```python
cfg = _get_settings().memory
return HybridMemoryAdapter(redis_url=cfg.redis_url, db_url=cfg.db_url)
```

- [ ] **Step 8: Commit**

```bash
git add src/astracore/sdk/config.py src/astracore/adapters/memory/hybrid.py \
        src/astracore/sdk/client.py src/astracore/service/api/chat.py \
        .env .env.example
git commit -m "feat: replace postgres_url with db_url, support SQLite default"
```

---

## Task 3: Skills API

**Files:**
- Create: `src/astracore/service/api/skills.py`

- [ ] **Step 1: Create `service/api/skills.py`**

```python
"""Skills CRUD API endpoints."""

from datetime import UTC, datetime
from functools import lru_cache
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from astracore.adapters.db.models import SkillRow
from astracore.adapters.db.session import get_session
from astracore.sdk.config import AstraCoreConfig

router = APIRouter()


@lru_cache(maxsize=1)
def _db_url() -> str:
    return AstraCoreConfig().memory.db_url


class SkillResponse(BaseModel):
    id: str
    name: str
    description: str
    system_prompt: str
    is_builtin: bool
    created_at: datetime
    updated_at: datetime


class SkillCreate(BaseModel):
    name: str
    description: str = ""
    system_prompt: str


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None


def _to_response(row: SkillRow) -> SkillResponse:
    return SkillResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        system_prompt=row.system_prompt,
        is_builtin=row.is_builtin,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/", response_model=list[SkillResponse])
async def list_skills() -> list[SkillResponse]:
    """Return all skills ordered: built-ins first, then user-created by creation time."""
    async with get_session(_db_url()) as db:
        result = await db.execute(
            select(SkillRow).order_by(SkillRow.is_builtin.desc(), SkillRow.created_at)
        )
        return [_to_response(row) for row in result.scalars().all()]


@router.post("/", response_model=SkillResponse, status_code=201)
async def create_skill(body: SkillCreate) -> SkillResponse:
    async with get_session(_db_url()) as db:
        row = SkillRow(
            id=str(uuid4()),
            name=body.name,
            description=body.description,
            system_prompt=body.system_prompt,
            is_builtin=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return _to_response(row)


@router.put("/{skill_id}", response_model=SkillResponse)
async def update_skill(skill_id: str, body: SkillUpdate) -> SkillResponse:
    async with get_session(_db_url()) as db:
        row = await db.get(SkillRow, skill_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        if row.is_builtin:
            raise HTTPException(status_code=403, detail="内置 Skill 不可修改")
        if body.name is not None:
            row.name = body.name
        if body.description is not None:
            row.description = body.description
        if body.system_prompt is not None:
            row.system_prompt = body.system_prompt
        row.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        return _to_response(row)


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(skill_id: str) -> None:
    async with get_session(_db_url()) as db:
        row = await db.get(SkillRow, skill_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Skill not found")
        if row.is_builtin:
            raise HTTPException(status_code=403, detail="内置 Skill 不可删除")
        await db.delete(row)
        await db.commit()
```

- [ ] **Step 2: Commit**

```bash
git add src/astracore/service/api/skills.py
git commit -m "feat: add skills CRUD API endpoints"
```

---

## Task 4: Settings API

**Files:**
- Create: `src/astracore/service/api/settings.py`

- [ ] **Step 1: Create `service/api/settings.py`**

```python
"""User settings API endpoints."""

from datetime import UTC, datetime
from functools import lru_cache

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from astracore.adapters.db.models import UserSettingsRow
from astracore.adapters.db.session import get_session
from astracore.sdk.config import AstraCoreConfig

router = APIRouter()

_SETTINGS_KEYS = {"default_skill_id", "global_instruction"}


@lru_cache(maxsize=1)
def _db_url() -> str:
    return AstraCoreConfig().memory.db_url


class UserSettingsResponse(BaseModel):
    default_skill_id: str = ""
    global_instruction: str = ""


class UserSettingsUpdate(BaseModel):
    default_skill_id: str | None = None
    global_instruction: str | None = None


async def _load_settings_map(db_url: str) -> dict[str, str]:
    async with get_session(db_url) as db:
        result = await db.execute(select(UserSettingsRow))
        return {row.key: row.value for row in result.scalars().all()}


@router.get("/", response_model=UserSettingsResponse)
async def get_settings() -> UserSettingsResponse:
    data = await _load_settings_map(_db_url())
    return UserSettingsResponse(
        default_skill_id=data.get("default_skill_id", ""),
        global_instruction=data.get("global_instruction", ""),
    )


@router.put("/", response_model=UserSettingsResponse)
async def update_settings(body: UserSettingsUpdate) -> UserSettingsResponse:
    patch = {
        k: v
        for k, v in body.model_dump().items()
        if v is not None and k in _SETTINGS_KEYS
    }
    async with get_session(_db_url()) as db:
        for key, value in patch.items():
            row = await db.get(UserSettingsRow, key)
            if row is None:
                db.add(UserSettingsRow(key=key, value=value, updated_at=datetime.now(UTC)))
            else:
                row.value = value
                row.updated_at = datetime.now(UTC)
        await db.commit()

    data = await _load_settings_map(_db_url())
    return UserSettingsResponse(
        default_skill_id=data.get("default_skill_id", ""),
        global_instruction=data.get("global_instruction", ""),
    )
```

- [ ] **Step 2: Commit**

```bash
git add src/astracore/service/api/settings.py
git commit -m "feat: add user settings API endpoints"
```

---

## Task 5: App startup — register routers + seed skills

**Files:**
- Modify: `src/astracore/service/api/app.py`
- Modify: `src/astracore/service/seeds.py`

- [ ] **Step 1: Add `seed_builtin_skills()` to `service/seeds.py`**

Append to the end of `seeds.py`:

```python
_BUILTIN_SKILLS = [
    {
        "name": "通用助手",
        "description": "默认模式，平衡各类任务",
        "system_prompt": "你是一个有帮助、准确、诚实的 AI 助手。",
    },
    {
        "name": "代码助手",
        "description": "专注编程，优先提供代码示例",
        "system_prompt": (
            "你是一名专业的软件工程师。回答编程问题时优先给出可运行的代码示例，"
            "并简要解释关键逻辑。如果问题不涉及编程，礼貌说明并尝试帮助。"
        ),
    },
    {
        "name": "写作助手",
        "description": "文章写作、润色、改写",
        "system_prompt": (
            "你是一名专业写作助手。帮助用户撰写、润色和改写文章。"
            "注重语言表达的准确性和可读性，保持用户原有的语气和风格。"
        ),
    },
    {
        "name": "翻译官",
        "description": "中英文互译，保持原意",
        "system_prompt": (
            "你是一名专业翻译。在中文和英文之间进行高质量互译，"
            "忠实于原文含义，同时保证译文自然流畅。直接给出译文，不加额外解释。"
        ),
    },
    {
        "name": "数据分析师",
        "description": "数据分析、统计解读",
        "system_prompt": (
            "你是一名数据分析专家。帮助用户理解数据、解读统计结果、设计分析方案。"
            "回答时注重逻辑严谨，必要时说明数据局限性和分析假设。"
        ),
    },
]


async def seed_builtin_skills(db_url: str) -> None:
    """Insert built-in skills if they don't already exist. Idempotent."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy import select

    from astracore.adapters.db.models import SkillRow
    from astracore.adapters.db.session import get_session

    async with get_session(db_url) as db:
        result = await db.execute(select(SkillRow).where(SkillRow.is_builtin == True))  # noqa: E712
        existing_names = {row.name for row in result.scalars().all()}

        for skill in _BUILTIN_SKILLS:
            if skill["name"] in existing_names:
                continue
            now = datetime.now(UTC)
            db.add(
                SkillRow(
                    id=str(uuid4()),
                    name=skill["name"],
                    description=skill["description"],
                    system_prompt=skill["system_prompt"],
                    is_builtin=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        await db.commit()

    logger.info("内置 Skill 种子写入完成")
```

- [ ] **Step 2: Update `service/api/app.py`**

```python
"""FastAPI application factory."""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from astracore.service.api import chat, health, rag, settings, skills
from astracore.service.seeds import seed_builtin_skills, seed_documents

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    """Application lifespan manager."""
    from astracore.adapters.db.session import init_db
    from astracore.sdk.config import AstraCoreConfig

    cfg = AstraCoreConfig()
    try:
        await init_db(cfg.memory.db_url)
    except Exception:
        logger.exception("数据库初始化失败，不影响服务启动")

    try:
        await seed_builtin_skills(cfg.memory.db_url)
    except Exception:
        logger.exception("内置 Skill 种子写入失败，不影响服务启动")

    try:
        pipeline = rag._get_rag_pipeline()
        await seed_documents(pipeline)
    except Exception:
        logger.exception("种子文档写入失败，不影响服务启动")

    yield


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title="AstraCore AI",
        description="Enterprise-grade AI Framework API",
        version="0.1.0",
        lifespan=lifespan,
    )

    raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000")
    allowed_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    )

    app.include_router(health.router, prefix="/health", tags=["health"])
    app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])
    app.include_router(rag.router, prefix="/api/v1/rag", tags=["rag"])
    app.include_router(skills.router, prefix="/api/v1/skills", tags=["skills"])
    app.include_router(settings.router, prefix="/api/v1/settings", tags=["settings"])

    return app
```

- [ ] **Step 3: Commit**

```bash
git add src/astracore/service/seeds.py src/astracore/service/api/app.py
git commit -m "feat: register skills/settings routers, seed built-in skills at startup"
```

---

## Task 6: Chat API — skill resolution + system prompt composition

**Files:**
- Modify: `src/astracore/service/api/chat.py`

- [ ] **Step 1: Update `chat.py` — add skill resolution helpers and rewrite `_build_system_prompt`**

Replace the entire file with the following (preserving all existing logic, adding skill support):

```python
"""Chat API endpoints."""

from functools import lru_cache
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from astracore.adapters.db.models import SkillRow, UserSettingsRow
from astracore.adapters.db.session import get_session
from astracore.adapters.llm.anthropic import AnthropicAdapter
from astracore.adapters.llm.openai import OpenAIAdapter
from astracore.adapters.memory.hybrid import HybridMemoryAdapter
from astracore.core.application.chat import ChatUseCase
from astracore.core.application.tool_loop import ToolLoopUseCase
from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import LLMAdapter, StreamEventType
from astracore.runtime.policy.engine import PolicyEngine
from astracore.sdk.config import AstraCoreConfig
from astracore.service.api import rag as rag_api
from astracore.service.builtin_tools import build_tool_adapter

router = APIRouter()


@lru_cache(maxsize=1)
def _get_settings() -> AstraCoreConfig:
    return AstraCoreConfig()


@lru_cache(maxsize=1)
def _get_llm_adapter() -> LLMAdapter:
    cfg = _get_settings().llm
    if cfg.provider == "anthropic":
        return AnthropicAdapter(
            api_key=cfg.api_key,
            default_model=cfg.model,
            base_url=cfg.base_url,
        )
    return OpenAIAdapter(
        api_key=cfg.api_key,
        default_model=cfg.model,
        base_url=cfg.base_url,
    )


@lru_cache(maxsize=1)
def _get_memory_adapter() -> HybridMemoryAdapter:
    cfg = _get_settings().memory
    return HybridMemoryAdapter(redis_url=cfg.redis_url, db_url=cfg.db_url)


@lru_cache(maxsize=1)
def _get_chat_use_case() -> ChatUseCase:
    return ChatUseCase(
        llm_adapter=_get_llm_adapter(),
        memory_adapter=_get_memory_adapter(),
        policy_engine=PolicyEngine(),
    )


@lru_cache(maxsize=1)
def _get_tool_loop_use_case() -> ToolLoopUseCase:
    return ToolLoopUseCase(
        llm_adapter=_get_llm_adapter(),
        tool_adapter=build_tool_adapter(),
        policy_engine=PolicyEngine(),
    )


async def _load_skill(skill_id: str) -> SkillRow | None:
    """Fetch a skill by id; return None if missing."""
    db_url = _get_settings().memory.db_url
    async with get_session(db_url) as db:
        return await db.get(SkillRow, skill_id)


async def _get_setting_value(key: str) -> str:
    """Fetch a single user-settings value; return '' if not set."""
    db_url = _get_settings().memory.db_url
    async with get_session(db_url) as db:
        row = await db.get(UserSettingsRow, key)
        return row.value if row else ""


class ChatRequest(BaseModel):
    """Chat request model."""

    message: str
    session_id: UUID | None = None
    model: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    use_tools: bool = False
    enable_thinking: bool = False
    thinking_budget: int = Field(default=8000, ge=1000, le=32000)
    enable_rag: bool = False
    enable_web: bool = False
    skill_id: UUID | None = None
    disable_skill: bool = False


class ChatResponse(BaseModel):
    """Chat response model."""

    session_id: UUID
    message: str
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


async def _build_system_prompt(
    skill_id: UUID | None,
    disable_skill: bool,
    enable_rag: bool,
    message: str,
) -> str | None:
    """Compose the three-layer system prompt."""
    parts: list[str] = []

    # Layer 1: Skill
    if not disable_skill:
        resolved_id = str(skill_id) if skill_id else await _get_setting_value("default_skill_id")
        if resolved_id:
            skill = await _load_skill(resolved_id)
            if skill and skill.system_prompt:
                parts.append(skill.system_prompt)

    # Layer 2: Global instruction
    instruction = await _get_setting_value("global_instruction")
    if instruction:
        parts.append(instruction)

    # Layer 3: RAG context
    if enable_rag:
        rag_ctx = await _build_rag_context(message)
        if rag_ctx:
            parts.append(rag_ctx)

    return "\n\n---\n\n".join(parts) or None


async def _run_with_tools(request: ChatRequest, session_id: UUID) -> str:
    memory = _get_memory_adapter()
    tool_loop = _get_tool_loop_use_case()

    messages = await memory.load_short_term(session_id)
    session = SessionState(session_id=session_id)
    if messages:
        session.restore_messages(messages)

    session.add_message(Message(role=MessageRole.USER, content=request.message))
    session = await tool_loop.execute_with_tools(session, model=request.model)
    await memory.save_short_term(session_id, session.get_messages())

    last_assistant = next(
        (m for m in reversed(session.get_messages()) if m.role == MessageRole.ASSISTANT),
        None,
    )
    return last_assistant.content if last_assistant else ""


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Chat endpoint."""
    session_id = request.session_id or uuid4()

    try:
        if request.use_tools:
            content = await _run_with_tools(request, session_id)
            return ChatResponse(session_id=session_id, message=content, model=request.model)

        use_case = _get_chat_use_case()
        response_message = await use_case.execute(
            session_id=session_id,
            user_message=request.message,
            model=request.model,
            temperature=request.temperature,
        )
        return ChatResponse(
            session_id=session_id,
            message=response_message.content,
            model=request.model,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _build_rag_context(query: str) -> str | None:
    """检索相关文档，构建 RAG 上下文系统提示。"""
    try:
        pipeline = rag_api._get_rag_pipeline()
        chunks = await pipeline.retrieve_with_citations(query=query, top_k=4)
        if not chunks:
            return None
        parts = [
            f"[来源: {c.citation.title or c.citation.source_id}]\n{c.content}"
            for c in chunks
        ]
        context = "\n\n---\n\n".join(parts)
        return (
            "以下是从知识库检索到的相关内容，请优先基于这些内容回答用户问题，"
            "并在回答中注明引用的来源：\n\n" + context
        )
    except Exception:
        return None


@router.post("/stream")
async def chat_stream(request: ChatRequest) -> EventSourceResponse:
    """Streaming chat endpoint."""
    session_id = request.session_id or uuid4()

    async def event_generator() -> Any:
        try:
            inject_system = await _build_system_prompt(
                skill_id=request.skill_id,
                disable_skill=request.disable_skill,
                enable_rag=request.enable_rag,
                message=request.message,
            )

            llm_kwargs: dict[str, Any] = {}
            if request.enable_thinking:
                llm_kwargs["enable_thinking"] = True
                llm_kwargs["thinking_budget"] = request.thinking_budget

            if request.use_tools or request.enable_web:
                _BASE_TOOLS = {"get_current_time", "calculate", "search_knowledge_base"}
                allowed_tools = _BASE_TOOLS | ({"web_search"} if request.enable_web else set())

                memory = _get_memory_adapter()
                tool_loop = _get_tool_loop_use_case()

                messages = await memory.load_short_term(session_id)
                session = SessionState(session_id=session_id)
                if messages:
                    session.restore_messages(messages)

                if inject_system:
                    session.add_message(
                        Message(role=MessageRole.SYSTEM, content=inject_system)
                    )
                session.add_message(
                    Message(role=MessageRole.USER, content=request.message)
                )

                async for event in tool_loop.execute_stream_with_tools(
                    session,
                    model=request.model,
                    allowed_tools=allowed_tools,
                    **llm_kwargs,
                ):
                    if event.event_type == StreamEventType.ROUND_START:
                        yield {"event": "thinking_start", "data": str(event.metadata.get("round", 1))}
                    elif event.event_type == StreamEventType.TEXT_DELTA:
                        yield {"event": "message", "data": event.content}
                    elif event.event_type == StreamEventType.THINKING_DELTA:
                        yield {"event": "thinking", "data": event.content}
                    elif event.event_type == StreamEventType.TOOL_CALL and event.tool_call:
                        yield {"event": "tool_use", "data": event.tool_call.name}

                await memory.save_short_term(session_id, session.get_messages())
                yield {"event": "done", "data": "[DONE]"}

            else:
                use_case = _get_chat_use_case()
                async for event in use_case.execute_stream(
                    session_id=session_id,
                    user_message=request.message,
                    model=request.model,
                    temperature=request.temperature,
                    inject_system=inject_system,
                    **llm_kwargs,
                ):
                    if event.event_type == StreamEventType.TEXT_DELTA:
                        yield {"event": "message", "data": event.content}
                    elif event.event_type == StreamEventType.THINKING_DELTA:
                        yield {"event": "thinking", "data": event.content}
                    elif event.event_type == StreamEventType.DONE:
                        yield {"event": "done", "data": "[DONE]"}

        except Exception as e:
            detail = str(e)
            if e.__cause__ is not None:
                detail = f"{detail} — {e.__cause__!s}"
            yield {"event": "error", "data": detail}

    return EventSourceResponse(event_generator())
```

- [ ] **Step 2: Commit**

```bash
git add src/astracore/service/api/chat.py
git commit -m "feat: add skill_id/disable_skill to ChatRequest, compose three-layer system prompt"
```

---

## Task 7: Frontend types + service

**Files:**
- Create: `frontend/src/types/skill.ts`
- Modify: `frontend/src/types/api.ts`
- Create: `frontend/src/services/skillService.ts`

- [ ] **Step 1: Create `types/skill.ts`**

```typescript
export type Skill = {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  is_builtin: boolean;
  created_at: string;
  updated_at: string;
};

export type CreateSkillRequest = {
  name: string;
  description: string;
  system_prompt: string;
};

export type UpdateSkillRequest = {
  name?: string;
  description?: string;
  system_prompt?: string;
};

export type UserSettings = {
  default_skill_id: string;
  global_instruction: string;
};
```

- [ ] **Step 2: Update `types/api.ts` — add skill fields to ChatRequest**

Add two fields to the `ChatRequest` type:
```typescript
export type ChatRequest = {
  message: string;
  session_id?: string;
  model?: string;
  temperature?: number;
  enable_thinking?: boolean;
  thinking_budget?: number;
  enable_rag?: boolean;
  use_tools?: boolean;
  enable_web?: boolean;
  skill_id?: string;
  disable_skill?: boolean;
};
```

- [ ] **Step 3: Create `services/skillService.ts`**

```typescript
import type { CreateSkillRequest, Skill, UpdateSkillRequest, UserSettings } from '../types/skill';
import { apiClient } from './apiClient';

export async function listSkills(): Promise<Skill[]> {
  const { data } = await apiClient.get<Skill[]>('/api/v1/skills/');
  return data;
}

export async function createSkill(req: CreateSkillRequest): Promise<Skill> {
  const { data } = await apiClient.post<Skill>('/api/v1/skills/', req);
  return data;
}

export async function updateSkill(id: string, req: UpdateSkillRequest): Promise<Skill> {
  const { data } = await apiClient.put<Skill>(`/api/v1/skills/${id}`, req);
  return data;
}

export async function deleteSkill(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/skills/${id}`);
}

export async function getSettings(): Promise<UserSettings> {
  const { data } = await apiClient.get<UserSettings>('/api/v1/settings/');
  return data;
}

export async function saveSettings(patch: Partial<UserSettings>): Promise<UserSettings> {
  const { data } = await apiClient.put<UserSettings>('/api/v1/settings/', patch);
  return data;
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/skill.ts frontend/src/types/api.ts \
        frontend/src/services/skillService.ts
git commit -m "feat: add skill types and service layer"
```

---

## Task 8: Frontend store

**Files:**
- Create: `frontend/src/stores/skillStore.ts`
- Modify: `frontend/src/stores/chatStore.ts`

- [ ] **Step 1: Create `stores/skillStore.ts`**

```typescript
import { create } from 'zustand';
import { normalizeError } from '../services/apiClient';
import {
  createSkill,
  deleteSkill,
  getSettings,
  listSkills,
  saveSettings,
  updateSkill,
} from '../services/skillService';
import type { CreateSkillRequest, Skill, UpdateSkillRequest, UserSettings } from '../types/skill';

type SkillStore = {
  skills: Skill[];
  settings: UserSettings;
  isLoading: boolean;
  error: string | null;

  fetchSkills: () => Promise<void>;
  fetchSettings: () => Promise<void>;
  createSkill: (req: CreateSkillRequest) => Promise<void>;
  updateSkill: (id: string, req: UpdateSkillRequest) => Promise<void>;
  deleteSkill: (id: string) => Promise<void>;
  saveSettings: (patch: Partial<UserSettings>) => Promise<void>;
  clearError: () => void;
};

export const useSkillStore = create<SkillStore>()((set) => ({
  skills: [],
  settings: { default_skill_id: '', global_instruction: '' },
  isLoading: false,
  error: null,

  fetchSkills: async () => {
    set({ isLoading: true, error: null });
    try {
      const skills = await listSkills();
      set({ skills, isLoading: false });
    } catch (e) {
      set({ error: normalizeError(e), isLoading: false });
    }
  },

  fetchSettings: async () => {
    try {
      const settings = await getSettings();
      set({ settings });
    } catch (e) {
      set({ error: normalizeError(e) });
    }
  },

  createSkill: async (req) => {
    const skill = await createSkill(req);
    set((s) => ({ skills: [...s.skills, skill] }));
  },

  updateSkill: async (id, req) => {
    const updated = await updateSkill(id, req);
    set((s) => ({ skills: s.skills.map((sk) => (sk.id === id ? updated : sk)) }));
  },

  deleteSkill: async (id) => {
    await deleteSkill(id);
    set((s) => ({ skills: s.skills.filter((sk) => sk.id !== id) }));
  },

  saveSettings: async (patch) => {
    const settings = await saveSettings(patch);
    set({ settings });
  },

  clearError: () => set({ error: null }),
}));
```

- [ ] **Step 2: Update `stores/chatStore.ts` — add `activeSkillId`**

Add to `ChatStore` type:
```typescript
activeSkillId: string | null;  // null = use default, 'none' = explicitly disabled, uuid = specific skill
setActiveSkillId: (id: string | null) => void;
```

Add to initial state inside `create()`:
```typescript
activeSkillId: null,
```

Add action:
```typescript
setActiveSkillId: (id) => set({ activeSkillId: id }),
```

In `sendMessage`, read `activeSkillId` from state and pass it to `sendChatStream`:
```typescript
const { activeConversationId, useStream, enableThinking, enableRag, enableTools, enableWeb, activeSkillId, conversations } = get();
```

Update the `sendChatStream` call payload:
```typescript
await sendChatStream(
  {
    message: trimmed,
    session_id: activeConversationId,
    enable_thinking: enableThinking,
    enable_rag: enableRag,
    use_tools: enableTools || enableWeb,
    enable_web: enableWeb,
    skill_id: activeSkillId !== null && activeSkillId !== 'none' ? activeSkillId : undefined,
    disable_skill: activeSkillId === 'none',
  },
  ...
```

Add `activeSkillId` to the `partialize` persist list:
```typescript
activeSkillId: s.activeSkillId,
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/stores/skillStore.ts frontend/src/stores/chatStore.ts
git commit -m "feat: add skill store and activeSkillId to chat store"
```

---

## Task 9: Frontend components

**Files:**
- Create: `frontend/src/components/skills/SkillCard.tsx`
- Create: `frontend/src/components/skills/SkillModal.tsx`
- Create: `frontend/src/components/skills/SkillSelector.tsx`

- [ ] **Step 1: Create `components/skills/SkillCard.tsx`**

```tsx
import { EditOutlined, DeleteOutlined, LockOutlined } from '@ant-design/icons';
import { Card, Tag, Tooltip, Popconfirm, Typography } from 'antd';
import type { Skill } from '../../types/skill';

type Props = {
  skill: Skill;
  onEdit: (skill: Skill) => void;
  onDelete: (id: string) => void;
  onView: (skill: Skill) => void;
};

export default function SkillCard({ skill, onEdit, onDelete, onView }: Props): JSX.Element {
  const actions = skill.is_builtin
    ? [
        <Tooltip title="查看内置 Skill" key="view">
          <LockOutlined onClick={() => onView(skill)} />
        </Tooltip>,
      ]
    : [
        <Tooltip title="编辑" key="edit">
          <EditOutlined onClick={() => onEdit(skill)} />
        </Tooltip>,
        <Popconfirm
          key="delete"
          title="确认删除此 Skill？"
          onConfirm={() => onDelete(skill.id)}
          okText="删除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
        >
          <Tooltip title="删除">
            <DeleteOutlined />
          </Tooltip>
        </Popconfirm>,
      ];

  return (
    <Card
      size="small"
      hoverable
      actions={actions}
      style={{ height: '100%' }}
    >
      <Card.Meta
        title={
          <Typography.Text ellipsis style={{ maxWidth: 160 }}>
            {skill.name}
          </Typography.Text>
        }
        description={
          <Typography.Text type="secondary" ellipsis={{ tooltip: skill.description }}>
            {skill.description || '暂无描述'}
          </Typography.Text>
        }
      />
      {skill.is_builtin && (
        <Tag color="default" style={{ marginTop: 8 }}>
          内置
        </Tag>
      )}
    </Card>
  );
}
```

- [ ] **Step 2: Create `components/skills/SkillModal.tsx`**

```tsx
import { useEffect } from 'react';
import { Modal, Form, Input, Alert } from 'antd';
import RagMarkdownEditor from '../rag/RagMarkdownEditor';
import type { CreateSkillRequest, Skill } from '../../types/skill';

type Props = {
  open: boolean;
  skill: Skill | null;   // null = create mode; non-null = edit or view mode
  readOnly?: boolean;
  onClose: () => void;
  onSave: (req: CreateSkillRequest) => Promise<void>;
};

export default function SkillModal({ open, skill, readOnly, onClose, onSave }: Props): JSX.Element {
  const [form] = Form.useForm<CreateSkillRequest>();

  useEffect(() => {
    if (open) {
      form.setFieldsValue(
        skill
          ? { name: skill.name, description: skill.description, system_prompt: skill.system_prompt }
          : { name: '', description: '', system_prompt: '' },
      );
    }
  }, [open, skill, form]);

  const handleOk = async () => {
    const values = await form.validateFields();
    await onSave(values);
    onClose();
  };

  const title = readOnly ? '查看 Skill' : skill ? '编辑 Skill' : '新建 Skill';

  return (
    <Modal
      title={title}
      open={open}
      onOk={readOnly ? undefined : handleOk}
      onCancel={onClose}
      okText="保存"
      cancelText={readOnly ? '关闭' : '取消'}
      footer={readOnly ? null : undefined}
      width={720}
      destroyOnClose
    >
      {readOnly && (
        <Alert message="内置 Skill 不可修改" type="info" showIcon style={{ marginBottom: 16 }} />
      )}
      <Form form={form} layout="vertical" disabled={readOnly}>
        <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
          <Input placeholder="代码助手" maxLength={128} />
        </Form.Item>
        <Form.Item name="description" label="描述">
          <Input placeholder="简短说明这个 Skill 的用途" maxLength={200} />
        </Form.Item>
        <Form.Item
          name="system_prompt"
          label="System Prompt"
          rules={[{ required: true, message: '请输入 System Prompt' }]}
        >
          <Form.Item noStyle shouldUpdate>
            {({ getFieldValue, setFieldValue }) => (
              <RagMarkdownEditor
                value={getFieldValue('system_prompt') ?? ''}
                onChange={(v) => setFieldValue('system_prompt', v)}
                height={300}
              />
            )}
          </Form.Item>
        </Form.Item>
      </Form>
    </Modal>
  );
}
```

- [ ] **Step 3: Create `components/skills/SkillSelector.tsx`**

```tsx
import { BookOutlined, DownOutlined } from '@ant-design/icons';
import { Button, Dropdown, Spin } from 'antd';
import type { MenuProps } from 'antd';
import { useEffect } from 'react';
import { useChatStore } from '../../stores/chatStore';
import { useSkillStore } from '../../stores/skillStore';

export default function SkillSelector({ disabled }: { disabled: boolean }): JSX.Element {
  const { skills, fetchSkills } = useSkillStore();
  const { activeSkillId, setActiveSkillId } = useChatStore();

  useEffect(() => {
    if (skills.length === 0) fetchSkills();
  }, []);

  const activeSkill =
    activeSkillId && activeSkillId !== 'none'
      ? skills.find((s) => s.id === activeSkillId)
      : null;

  const label =
    activeSkillId === 'none' ? '无' : activeSkill ? activeSkill.name : '默认';

  const items: MenuProps['items'] = [
    {
      key: '__default__',
      label: '使用默认',
      onClick: () => setActiveSkillId(null),
    },
    {
      key: '__none__',
      label: '无 (不使用)',
      onClick: () => setActiveSkillId('none'),
    },
    { type: 'divider' },
    ...skills.map((s) => ({
      key: s.id,
      label: s.name,
      onClick: () => setActiveSkillId(s.id),
    })),
  ];

  return (
    <Dropdown menu={{ items, selectedKeys: [activeSkillId ?? '__default__'] }} disabled={disabled}>
      <Button
        size="small"
        style={{ borderRadius: 20, fontSize: 12, height: 26, padding: '0 10px' }}
        icon={<BookOutlined />}
      >
        {label} <DownOutlined style={{ fontSize: 10 }} />
      </Button>
    </Dropdown>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/skills/
git commit -m "feat: add SkillCard, SkillModal, SkillSelector components"
```

---

## Task 10: Frontend SkillsPage

**Files:**
- Create: `frontend/src/pages/SkillsPage.tsx`

- [ ] **Step 1: Create `pages/SkillsPage.tsx`**

```tsx
import { useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Col,
  Flex,
  Form,
  Input,
  Row,
  Select,
  Spin,
  Typography,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import SkillCard from '../components/skills/SkillCard';
import SkillModal from '../components/skills/SkillModal';
import { useSkillStore } from '../stores/skillStore';
import type { CreateSkillRequest, Skill, UpdateSkillRequest } from '../types/skill';

export default function SkillsPage(): JSX.Element {
  const {
    skills,
    settings,
    isLoading,
    error,
    fetchSkills,
    fetchSettings,
    createSkill,
    updateSkill,
    deleteSkill,
    saveSettings,
    clearError,
  } = useSkillStore();

  const [modalOpen, setModalOpen] = useState(false);
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [viewOnly, setViewOnly] = useState(false);
  const [settingsForm] = Form.useForm<{ default_skill_id: string; global_instruction: string }>();

  useEffect(() => {
    fetchSkills();
    fetchSettings();
  }, []);

  useEffect(() => {
    settingsForm.setFieldsValue({
      default_skill_id: settings.default_skill_id,
      global_instruction: settings.global_instruction,
    });
  }, [settings, settingsForm]);

  const handleSaveSettings = async () => {
    const values = settingsForm.getFieldsValue();
    await saveSettings(values);
  };

  const handleOpenCreate = () => {
    setEditingSkill(null);
    setViewOnly(false);
    setModalOpen(true);
  };

  const handleOpenEdit = (skill: Skill) => {
    setEditingSkill(skill);
    setViewOnly(false);
    setModalOpen(true);
  };

  const handleOpenView = (skill: Skill) => {
    setEditingSkill(skill);
    setViewOnly(true);
    setModalOpen(true);
  };

  const handleSave = async (req: CreateSkillRequest | UpdateSkillRequest) => {
    if (editingSkill) {
      await updateSkill(editingSkill.id, req as UpdateSkillRequest);
    } else {
      await createSkill(req as CreateSkillRequest);
    }
  };

  return (
    <Flex vertical style={{ height: '100%', overflow: 'auto', padding: 24 }} gap={24}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        个性化
      </Typography.Title>

      {error && (
        <Alert type="error" message={error} closable onClose={clearError} />
      )}

      {/* Global settings */}
      <div>
        <Typography.Title level={5} style={{ marginTop: 0 }}>
          全局设置
        </Typography.Title>
        <Form form={settingsForm} layout="vertical" style={{ maxWidth: 600 }}>
          <Form.Item name="global_instruction" label="全局指令">
            <Input.TextArea
              rows={3}
              placeholder="每次对话都会追加的个人偏好，例如：回答请用中文，保持简洁"
              maxLength={1000}
              showCount
            />
          </Form.Item>
          <Form.Item name="default_skill_id" label="默认 Skill">
            <Select
              placeholder="不使用默认 Skill"
              allowClear
              options={[
                ...skills.map((s) => ({ value: s.id, label: s.name })),
              ]}
            />
          </Form.Item>
          <Button type="primary" onClick={handleSaveSettings}>
            保存设置
          </Button>
        </Form>
      </div>

      {/* Skill list */}
      <div>
        <Flex justify="space-between" align="center" style={{ marginBottom: 16 }}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            我的 Skill
          </Typography.Title>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleOpenCreate}>
            新建
          </Button>
        </Flex>

        {isLoading ? (
          <Flex justify="center" style={{ padding: 40 }}>
            <Spin />
          </Flex>
        ) : (
          <Row gutter={[16, 16]}>
            {skills.map((skill) => (
              <Col key={skill.id} xs={24} sm={12} md={8}>
                <SkillCard
                  skill={skill}
                  onEdit={handleOpenEdit}
                  onDelete={deleteSkill}
                  onView={handleOpenView}
                />
              </Col>
            ))}
          </Row>
        )}
      </div>

      <SkillModal
        open={modalOpen}
        skill={editingSkill}
        readOnly={viewOnly}
        onClose={() => setModalOpen(false)}
        onSave={handleSave}
      />
    </Flex>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/SkillsPage.tsx
git commit -m "feat: add SkillsPage with global settings and skill management"
```

---

## Task 11: Frontend wiring

**Files:**
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/layouts/AppShell.tsx`
- Modify: `frontend/src/components/chat/ChatMain.tsx`

- [ ] **Step 1: Update `router.tsx`**

```tsx
import { createBrowserRouter, Navigate } from 'react-router-dom';
import AppShell from '../layouts/AppShell';
import ChatPage from '../pages/ChatPage';
import RagPage from '../pages/RagPage';
import SkillsPage from '../pages/SkillsPage';
import SystemPage from '../pages/SystemPage';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: 'chat', element: <ChatPage /> },
      { path: 'rag', element: <RagPage /> },
      { path: 'skills', element: <SkillsPage /> },
      { path: 'system', element: <SystemPage /> },
    ],
  },
]);
```

- [ ] **Step 2: Update `AppShell.tsx` — add 个性化 nav item**

Replace `NAV_ITEMS`:
```tsx
const NAV_ITEMS = [
  { key: '/chat', label: <NavLink to="/chat">对话</NavLink> },
  { key: '/rag', label: <NavLink to="/rag">RAG</NavLink> },
  { key: '/skills', label: <NavLink to="/skills">个性化</NavLink> },
  { key: '/system', label: <NavLink to="/system">系统</NavLink> },
];
```

- [ ] **Step 3: Update `ChatMain.tsx` — add SkillSelector to toolbar**

Add import at top:
```tsx
import SkillSelector from '../skills/SkillSelector';
```

In the toolbar `<Flex>` that contains the 思考/RAG/工具/联网 buttons, add `<SkillSelector>` as the first child:
```tsx
<Flex gap={6} align="center" wrap="wrap">
  <SkillSelector disabled={isStreaming} />
  {/* existing buttons: 思考, RAG, 工具, 联网 */}
  ...
</Flex>
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/router.tsx frontend/src/layouts/AppShell.tsx \
        frontend/src/components/chat/ChatMain.tsx
git commit -m "feat: wire up skills page in router, nav, and chat toolbar"
```

---

## Task 12: Install aiosqlite dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add aiosqlite to dependencies in `pyproject.toml`**

In the `[project] dependencies` list, add:
```toml
"aiosqlite>=0.20.0",
```

- [ ] **Step 2: Install**

```bash
cd /d/project/study/AstraCoreAI
pip install aiosqlite
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add aiosqlite dependency for SQLite async support"
```
