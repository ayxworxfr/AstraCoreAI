# Skill System Design

**Date:** 2026-04-20
**Status:** Approved

## Overview

A two-layer system prompt injection mechanism that lets users configure "skills" (named system prompts) to shape AI conversation behavior. Layer 1 is a selectable skill (role), Layer 2 is a persistent global instruction appended to every conversation.

## Goals

- User can select a preset or self-created skill per conversation
- User can set a global instruction that is always appended
- Skills can be created, edited, and deleted from the frontend
- Built-in preset skills are read-only
- Architecture is designed for single-user now, extendable to multi-user later

## System Prompt Composition

Every chat request resolves to the following system prompt structure:

```
[Skill system_prompt]          ← Layer 1: selected skill (optional)
---
[Global instruction]           ← Layer 2: always-on personal rule (optional)
---
[RAG context]                  ← Layer 3: existing, injected when enable_rag=true
```

Layers are omitted when empty. If no skill is selected and no global instruction is set, no system prompt is injected.

## Database Layer

### Dual-driver support

Replace `ASTRACORE__MEMORY__POSTGRES_URL` with a single `ASTRACORE__MEMORY__DB_URL` field:

| Environment | Value |
|-------------|-------|
| Default (zero-config) | `sqlite+aiosqlite:///./astracore.db` |
| Production | `postgresql+asyncpg://user:pass@host/dbname` |

SQLAlchemy selects the driver based on the URL scheme. No code branching needed.

### New module: `adapters/db/`

```
src/astracore/adapters/db/
├── session.py     ← get_engine(), get_async_session() — shared across all repos
└── models.py      ← SQLAlchemy ORM: Skill, UserSettings (+ migrated memory models)
```

Existing memory ORM models in `adapters/memory/models.py` migrate into `adapters/db/models.py` to unify table management.

## Data Models

### Skill

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID (PK) | |
| `name` | str | e.g. "代码助手" |
| `description` | str | Short summary shown in UI |
| `system_prompt` | str | Full prompt content, supports Markdown |
| `is_builtin` | bool | True = read-only preset, seeded on startup |
| `created_at` | datetime | |
| `updated_at` | datetime | |

### UserSettings

Key-value store. Extending to multi-user: add `user_id` FK column.

| Key | Value | Description |
|-----|-------|-------------|
| `default_skill_id` | UUID string or `""` | Skill applied to every new conversation |
| `global_instruction` | string | Always appended to system prompt |

### Built-in Presets (seeded on first startup)

| Name | Description |
|------|-------------|
| 通用助手 | Default mode, no special constraints |
| 代码助手 | Programming-focused, prioritizes code examples |
| 写作助手 | Article writing, editing, rewriting |
| 翻译官 | Chinese-English translation, preserving meaning |
| 数据分析师 | Data analysis and statistical interpretation |

Built-in skills are never deleted. Seed logic checks `is_builtin=True` records before inserting to avoid duplicates on restart.

## Backend API

### New router: `/api/skills`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/skills` | List all skills (builtin + user-created) |
| POST | `/api/skills` | Create a new skill |
| PUT | `/api/skills/{id}` | Update a skill (non-builtin only) |
| DELETE | `/api/skills/{id}` | Delete a skill (non-builtin only) |

### New router: `/api/settings`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/settings` | Get all user settings |
| PUT | `/api/settings` | Update one or more settings keys |

### ChatRequest changes

Add one optional field:

```python
skill_id: UUID | None = None   # per-session skill override
```

Resolution order:
1. `request.skill_id` if provided → use that skill
2. `default_skill_id` from UserSettings if set → use that skill
3. No skill → skip Layer 1

### System prompt composition (chat.py)

```python
async def _build_system_prompt(skill_id, enable_rag, message) -> str | None:
    parts = []

    skill = await resolve_skill(skill_id)          # Layer 1
    if skill:
        parts.append(skill.system_prompt)

    instruction = await get_setting("global_instruction")  # Layer 2
    if instruction:
        parts.append(instruction)

    if enable_rag:
        rag_ctx = await _build_rag_context(message)        # Layer 3
        if rag_ctx:
            parts.append(rag_ctx)

    return "\n\n---\n\n".join(parts) or None
```

## Config Changes

`MemoryConfig` in `sdk/config.py`:

```python
class MemoryConfig(BaseModel):
    redis_url: str = "redis://localhost:6379/0"
    db_url: str = "sqlite+aiosqlite:///./astracore.db"
    # postgres_url removed
```

`.env` / `.env.example`:

```
# before
ASTRACORE__MEMORY__POSTGRES_URL=postgresql+asyncpg://user:password@localhost/astracore

# after
ASTRACORE__MEMORY__DB_URL=sqlite+aiosqlite:///./astracore.db
```

## Frontend

### Navigation

Add new nav item to `AppShell.tsx`:

```
对话 | RAG | 个性化 | 系统
```

New route: `/skills` → `SkillsPage`

### SkillsPage (`/skills`)

Two sections:

**1. 全局设置**
- Textarea: global instruction (auto-save on blur or explicit save button)
- Dropdown: default skill selector (all skills + "无")
- Save button

**2. Skill 列表**
- Card grid (3 columns)
- Built-in skills: grey badge "内置", click to view (read-only modal)
- User skills: show edit/delete icons on hover
- "+ 新建" button top-right

### Skill Create/Edit Modal

Fields:
- Name (Input)
- Description (Input)
- System Prompt — reuse `<RagMarkdownEditor height={300} />` for Markdown support with live preview, auto dark/light theme

### Chat Toolbar (ChatMain.tsx)

Add skill selector as the leftmost button in the toolbar row:

```
[💡 代码助手 ▼]   思考   RAG   工具   联网
```

- Shows current session skill name (or "通用" if none)
- Dropdown lists all skills + "无 (不使用)"
- Disabled during streaming
- Session-scoped only — does not change global default

### New Files

```
frontend/src/
├── pages/SkillsPage.tsx
├── components/skills/
│   ├── SkillCard.tsx
│   ├── SkillModal.tsx         ← create/edit modal
│   └── SkillSelector.tsx      ← chat toolbar dropdown
├── stores/skillStore.ts       ← Zustand: skills list, settings, activeSkillId
└── types/skill.ts             ← Skill, UserSettings types
```

## Error Handling

- Skill not found (deleted while in use): fall back to no skill, do not error
- DB unavailable on startup: log warning, skill features disabled, chat still works
- PUT/DELETE on builtin skill: 403 with clear message

## Out of Scope

- Tool-level skill extensions (e.g. a skill that auto-enables web search)
- Skill import/export
- Multi-user authentication
