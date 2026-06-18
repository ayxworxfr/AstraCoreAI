# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Backend
make setup          # One-shot init (hatch + deps)
make api            # Start FastAPI service on :8000
make test           # Run pytest
make test-cov       # Run pytest with coverage report
make lint           # ruff check
make type-check     # mypy
make check          # lint + type-check (run before commits)
make fmt            # ruff format

# Single test
hatch run pytest tests/path/to/test_file.py::TestClass::test_method -v

# Frontend
make fe-install     # Install node deps
make fe-dev         # Dev server on :5173
cd frontend && npm run build        # Type-check + build
cd frontend && npm run typecheck    # Type-check only
```

The test suite has 190 passing tests. Run `make check` before every commit.

## Architecture

AstraCoreAI is an AI assistant platform with a FastAPI backend and React SPA frontend. Two usage modes share identical business logic:

- **SDK mode**: `AstraCoreClient` in `src/astracore/sdk/client.py` — used in scripts and examples
- **HTTP service**: FastAPI app factory in `src/astracore/app/factory.py` — started by `make api`

### Core Chat Execution (most important file)

`src/astracore/modules/chat/pipeline.py` — `ChatPipeline` runs all chat:

1. `prepare()` — batch DB queries → immutable `ChatContext` (zero side-effects)
2. `stream()` — pure execution, two paths:
   - `tool_loop` mode: `ToolLoopUseCase` drives iterative tool execution
   - `normal` mode: single LLM call, streamed

`stream()` accepts a `hitl_callback` kwarg (injected by the HTTP layer) that tools use to pause execution and send a `PendingQuestion` to the frontend. The callback resolves when the user submits their answer.

### Module Map

```
src/astracore/
├── modules/          # Business capabilities (Clean Architecture per module)
│   ├── auth/         # JWT auth: register/login/me, bcrypt, jose, admin/user roles
│   ├── chat/         # Pipeline, session, conversation CRUD, HistoryCompactor
│   ├── memory/       # MemoryEngine: Tier-1/Tier-2 injection, extraction, promotion
│   ├── rag/          # RAG pipeline: retrieve → citations, knowledge base docs
│   ├── skills/       # Skill CRUD, SKILL.md loader, skill tools
│   ├── tools/        # Native tool registration (builtin.py)
│   ├── agent/        # Multi-agent DAG workflow
│   ├── scheduling/   # APScheduler-backed cron/interval/date task scheduling
│   ├── projects/     # Project CRUD and conversation bindings
│   ├── settings/     # Per-user settings (ai_name, owner_name, global_instruction)
│   ├── system/       # Health / system info endpoints
│   └── users/        # User CRUD (admin operations)
├── infrastructure/   # External adapters (never imported by modules directly)
│   ├── llm/          # AnthropicAdapter, OpenAIAdapter
│   ├── memory/       # SQLMemoryStore, HybridMemoryAdapter, MemoryVectorAdapter (Chroma)
│   ├── retrieval/    # ChromaDB retrieval adapter
│   ├── tools/        # NativeToolAdapter, MCPToolAdapter, ParallelAgentTool
│   ├── tokenizer/    # Token estimation utilities
│   └── workflow/     # NativeWorkflowOrchestrator (Kahn DAG + asyncio.gather)
├── shared/           # Cross-cutting: ports (interfaces), policy, observability
│   ├── ports/        # LLMAdapter, ToolAdapter, MemoryAdapter (abstract)
│   ├── policy/       # PolicyEngine (tenacity retry + asyncio timeout), CircuitBreaker
│   ├── security/     # SecurityValidator (XSS, length), external_data (wrap_external)
│   └── observability/# HookRegistry (before/after_llm/tool), Tracer
├── app/              # FastAPI factory, routers, SSE endpoints
└── sdk/              # AstraCoreClient, public API surface
```

### Key Patterns

**Ports & Adapters**: All external deps (LLM, DB, vector store) are behind abstract port interfaces in `shared/ports/`. Implementations live in `infrastructure/`.

**System Prompt Assembly** (in `pipeline.py` → `_build_system_prompt()`), layers joined with `\n\n---\n\n`:
1. `injection_guard` — static security declaration: treat `<external_data>` tags as data, not instructions
2. Identity block (ai_name, owner_name, time, global_instruction)
3. Skill manifest (lists available skills by category)
4. HITL guideline (if `hitl.enabled`) — explains `ask_user` tool semantics
5. Tier-1 memory (user/global scope, full-load from SQL)
6. RAG context (if `enable_rag` and query hits something)

**Prompt Injection Defense**: All external content (RAG results, Tier-2 memory, tool results) is wrapped via `shared/security/external_data.py`:
```python
wrap_external(content, source="rag")   # → <external_data trust="untrusted" source="rag">…</external_data>
```
The injection_guard in the system prompt instructs the LLM to treat tagged content as data only.

**Hybrid Memory** (`modules/memory/`):
- **Tier-1** (system prompt): user/global scope memories injected as permanent context
- **Tier-2** (synthetic message pair): session/project memories retrieved via Chroma vector search, injected as `[记忆同步]`/`[记忆快照]` synthetic messages before the real user message. These are filtered out before persisting (`_prepare_for_save`).
- Auto-extraction runs post-turn: LLM extracts 0-N structured memories; high-value session memories are promoted to user/project scope after LLM judgment. Promotion requires user approval when `hitl.require_memory_promotion_approval = true` (creates a `pending_promotion` record instead of promoting immediately).

**HistoryCompactor** (`modules/chat/application/compactor.py`): Called at `stream()` entry. Estimates token count; triggers at 50% of `context_window` (200k chars default). Summarizes oldest 60% of messages via LLM, persists the summary to MemoryEngine, falls back to tail-truncation on LLM failure.

**HITL (Human-in-the-Loop)**:
- Tools declare `requires_confirmation=True` in `register_tool()` → execution pauses for user approval
- `ask_user` built-in tool pauses the tool loop and sends a `PendingQuestion` to the frontend via `hitl_callback`
- Memory promotion with `require_memory_promotion_approval = true` creates a pending record instead of promoting
- Timeout: `hitl.inline_question_timeout` seconds (default 300), then auto-resumes

**Scheduled Tasks** (`modules/scheduling/`): APScheduler drives `cron`, `interval`, and one-shot `date` triggers. Each task stores a prompt; when fired, it runs through `ChatPipeline` and the resulting `conversation_id` is written back to the task row. The service layer (`task_service.py`) syncs every create/update/delete/pause/resume with the in-process APScheduler instance. Batch delete uses `DELETE WHERE id IN (...)` + `.returning()` to collect deleted IDs before removing the corresponding APScheduler jobs.

**Active Skill Enforcement**: `_detect_active_skill()` scans recent messages for `load_skill` calls (via `metadata["skill_loaded"]` after save, or live `tool_calls` in-session). If found, appends a mandatory reload instruction to the system prompt.

**Backend Run + SSE Subscribe**: Each generation creates a `ChatRunRow`. The asyncio Task runs independently; the `/stream` SSE endpoint subscribes to an in-process event store. Browser reconnect → same `run_id`, no data loss. **Known limitation**: `_ACTIVE_RUNS` is in-process; breaks with multiple gunicorn workers.

### Configuration

`config/config.yaml` — structured config. `config/README.md` documents all sections.
`.env` — secrets only (`ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, etc.).

Key sections beyond LLM profiles:
- `auth`: `secret_key` (must change in production), `allow_registration`, `token_expire_days`
- `hitl`: `enabled`, `require_tool_approval`, `require_memory_promotion_approval`, `inline_question_timeout`
- `debug.log_prompts`: prints full LLM prompt to stdout before each call — do not enable in production

LLM profiles are referenced by string ID (`claude-sonnet`, etc.). Never pass raw model names in business logic — use profile IDs.

### Frontend

`frontend/src/` uses React 18 + Vite + Zustand + Ant Design. Feature-based structure mirrors backend modules (`features/chat/`, `features/memory/`, `features/skills/`, etc.). State management: Zustand stores per feature. HTTP: axios with JWT Bearer token. Streaming: EventSource SSE.

Authentication state lives in `features/auth/`. All API calls attach `Authorization: Bearer <token>` via an axios interceptor. The frontend detects 401 responses and redirects to login.

### Skills System

Skills are YAML-frontmatter + Markdown files in `src/astracore/modules/skills/builtin/`. The model self-routes via `load_skill(skill_id)` tool. After loading, every subsequent turn must re-call `load_skill` (enforced by system prompt injection). Reference files loaded on-demand via `get_skill_reference()`.

### Adding New Built-in Tools

Register in `src/astracore/modules/tools/builtin.py` → `build_tool_adapter()`. Tools with a `_context` parameter receive session/user context automatically (includes `session_id`, `user_id`, `llm_adapter`, `hitl_callback`). Add `requires_confirmation=True` to pause for user approval before execution.
