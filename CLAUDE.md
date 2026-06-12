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

The test suite has 162 passing tests. Run `make check` before every commit.

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

Both modes build `effective_system` from `ctx.system_prompt + active_skill_note` and feed the same stored history.

### Module Map

```
src/astracore/
├── modules/          # Business capabilities (Clean Architecture per module)
│   ├── chat/         # Pipeline, session, conversation CRUD
│   ├── memory/       # MemoryEngine: extraction, injection, Tier-1/Tier-2 context
│   ├── rag/          # RAG pipeline: retrieve → rerank → citations
│   ├── skills/       # Skill CRUD, SKILL.md loader, skill tools
│   ├── tools/        # Native tool registration (builtin.py)
│   ├── agent/        # Multi-agent DAG workflow
│   └── users/auth/   # Auth, user management
├── infrastructure/   # External adapters (never imported by modules directly)
│   ├── llm/          # AnthropicAdapter, OpenAIAdapter
│   ├── memory/       # SQLMemoryStore, HybridMemoryAdapter, Chroma vector
│   ├── tools/        # NativeToolAdapter, MCPToolAdapter, ParallelAgentTool
│   └── workflow/     # NativeWorkflowOrchestrator (Kahn DAG + asyncio.gather)
├── shared/           # Cross-cutting: ports (interfaces), policy, observability
│   ├── ports/        # LLMAdapter, ToolAdapter, MemoryAdapter (abstract)
│   ├── policy/       # PolicyEngine (tenacity retry + asyncio timeout)
│   └── observability/# HookRegistry (before/after_llm/tool), Tracer
├── app/              # FastAPI factory, routers, SSE endpoints
└── sdk/              # AstraCoreClient, public API surface
```

### Key Patterns

**Ports & Adapters**: All external deps (LLM, DB, vector store) are behind abstract port interfaces in `shared/ports/`. Implementations live in `infrastructure/`.

**Three-Layer System Prompt** (assembled in `pipeline.py` / `prompt_utils.py`):
1. Identity block (name, owner, time, global instruction)
2. Skill manifest (lists available skills by category)
3. On-demand: RAG context + memory snapshot

**Hybrid Memory** (`modules/memory/`):
- **Tier-1** (system prompt): user/global scope memories injected as permanent context
- **Tier-2** (synthetic message pair): session/project memories retrieved via Chroma vector search, injected as `[记忆同步]`/`[记忆快照]` synthetic messages
- Auto-extraction runs post-turn: LLM extracts 0-N structured memories, heuristic filter promotes to durable scope

**Active Skill Enforcement**: `_detect_active_skill()` scans recent messages for `load_skill` calls (via `metadata["skill_loaded"]` after save, or live `tool_calls` in-session). If found, `_build_active_skill_system_note()` appends a mandatory reload instruction to the system prompt.

**Backend Run + SSE Subscribe**: Each generation creates a `ChatRunRow`. The asyncio Task runs independently; the `/stream` SSE endpoint subscribes to an in-process event store. Browser reconnect → same `run_id`, no data loss.

### Configuration

`config/config.yaml` — structured config (LLM profiles, agent limits, RAG, MCP servers).
`.env` — secrets only (`ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, etc.).

LLM profiles are referenced by string ID (`claude-sonnet`, `claude-opus`, etc.). Never pass raw model names in business logic — use profile IDs.

### Frontend

`frontend/src/` uses React 18 + Vite + Zustand + Ant Design. Feature-based structure mirrors backend modules (`features/chat/`, `features/memory/`, `features/skills/`, etc.). State management: Zustand stores per feature. HTTP: axios. Streaming: EventSource SSE.

### Skills System

Skills are YAML-frontmatter + Markdown files in `src/astracore/modules/skills/builtin/`. The model self-routes via `load_skill(skill_id)` tool. After loading, every subsequent turn must re-call `load_skill` (enforced by system prompt injection). Reference files loaded on-demand via `get_skill_reference()`.

### Adding New Built-in Tools

Register in `src/astracore/modules/tools/builtin.py` → `build_tool_adapter()`. Tools with `_context` parameter receive session/user context automatically.
