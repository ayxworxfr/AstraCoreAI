# Docker Containerization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package AstraCoreAI (FastAPI backend + React frontend) into a Docker Compose setup that any user can run with a single `docker-compose up` after filling in their API key.

**Architecture:** Multi-stage Dockerfile: Stage 1 builds the React frontend with Node.js; Stage 2 is the final Python 3.12-slim image that installs Node.js runtime (for MCP), copies the built frontend dist, and serves it via FastAPI StaticFiles. docker-compose orchestrates the `app` service and a `redis:7-alpine` service with a health check, using named volumes for SQLite and ChromaDB persistence.

**Tech Stack:** Python 3.12-slim, Node.js 20 (LTS), FastAPI StaticFiles, Redis 7, Docker Compose v2, ChromaDB, SQLite/aiosqlite

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `Dockerfile` | Multi-stage build: frontend → final app image |
| Create | `docker-compose.yml` | Orchestrate app + redis, volumes, env injection |
| Create | `.dockerignore` | Exclude .env, node_modules, data files, .git |
| Modify | `pyproject.toml` | Add `aiofiles` dependency (required by FastAPI StaticFiles) |
| Modify | `src/astracore/service/api/app.py` | Mount `frontend/dist` as StaticFiles at `/` |

---

### Task 1: Create `.dockerignore`

**Files:**
- Create: `.dockerignore`

- [ ] **Step 1: Create `.dockerignore`**

```
.git
.github
.env
*.env
node_modules
frontend/node_modules
__pycache__
*.pyc
*.pyo
.pytest_cache
.mypy_cache
.ruff_cache
.hatch
.cache
astracore.db
chroma_db/
logs/
dist/
*.egg-info
docs/
tests/
```

- [ ] **Step 2: Commit**

```bash
git add .dockerignore
git commit -m "chore: add .dockerignore for Docker builds"
```

---

### Task 2: Add `aiofiles` dependency to `pyproject.toml`

**Files:**
- Modify: `pyproject.toml`

FastAPI's `StaticFiles` (from Starlette) requires the `aiofiles` package to serve static files. It is not currently listed in dependencies.

- [ ] **Step 1: Add `aiofiles` to `pyproject.toml` dependencies**

In `pyproject.toml`, find the `dependencies` list and add `"aiofiles>=23.0.0"` after the last existing entry before the closing `]`:

```toml
dependencies = [
    "pydantic>=2.8.0",
    "pydantic-settings>=2.3.0",
    "httpx>=0.27.0",
    "tenacity>=8.5.0",
    "sqlalchemy>=2.0.31",
    "greenlet>=3.0.3",
    "alembic>=1.13.2",
    "aiosqlite>=0.20.0",
    "asyncpg>=0.29.0",
    "redis>=5.0.7",
    "fastapi>=0.111.1",
    "uvicorn[standard]>=0.30.1",
    "python-multipart>=0.0.9",
    "sse-starlette>=2.1.2",
    "aiofiles>=23.0.0",
]
```

- [ ] **Step 2: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add aiofiles dependency for FastAPI StaticFiles support"
```

---

### Task 3: Add StaticFiles mount to FastAPI app

**Files:**
- Modify: `src/astracore/service/api/app.py`
- Depends on: Task 2 (`aiofiles` installed)

The current `create_app()` ends at line 97. We need to mount `frontend/dist` as static files at `/` **after** all API routers, so API routes take priority. We guard with `Path.exists()` so the dev workflow (`make api`) is unaffected when `dist/` doesn't exist.

- [ ] **Step 1: Modify `create_app()` in `src/astracore/service/api/app.py`**

Add the import at the top of the file (after the existing imports):

```python
from pathlib import Path

from fastapi.staticfiles import StaticFiles
```

Then append these lines at the end of `create_app()`, just before `return app`:

```python
    dist_dir = Path(__file__).parent.parent.parent.parent.parent / "frontend" / "dist"
    if dist_dir.exists():
        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="static")

    return app
```

The full updated `create_app()` function should look like:

```python
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
    app.include_router(system.router, prefix="/api/v1/system", tags=["system"])

    dist_dir = Path(__file__).parent.parent.parent.parent.parent / "frontend" / "dist"
    if dist_dir.exists():
        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="static")

    return app
```

- [ ] **Step 2: Verify the path calculation**

`app.py` is at `src/astracore/service/api/app.py`.
- `.parent` → `src/astracore/service/api/`
- `.parent.parent` → `src/astracore/service/`
- `.parent.parent.parent` → `src/astracore/`
- `.parent.parent.parent.parent` → `src/`
- `.parent.parent.parent.parent.parent` → project root

So `dist_dir` resolves to `<project_root>/frontend/dist`. Correct.

- [ ] **Step 3: Commit**

```bash
git add src/astracore/service/api/app.py
git commit -m "feat: serve frontend dist via FastAPI StaticFiles when dist/ exists"
```

---

### Task 4: Write the Dockerfile (multi-stage)

**Files:**
- Create: `Dockerfile`

- [ ] **Step 1: Create `Dockerfile`**

```dockerfile
# ============================================================
# Stage 1: Build React frontend
# ============================================================
FROM node:20-slim AS frontend-builder

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --prefer-offline

COPY frontend/ ./
RUN npm run build

# ============================================================
# Stage 2: Final application image
# ============================================================
FROM python:3.12-slim AS final

# Install system deps + Node.js runtime (required for MCP servers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache friendly)
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e ".[anthropic,openai]" \
    && pip install --no-cache-dir chromadb

# Copy built frontend from Stage 1
COPY --from=frontend-builder /frontend/dist ./frontend/dist

# Data directory for volumes (SQLite + ChromaDB)
RUN mkdir -p /app/data

# Non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "astracore.service.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Commit**

```bash
git add Dockerfile
git commit -m "feat: add multi-stage Dockerfile (frontend builder + Python/Node final)"
```

---

### Task 5: Write `docker-compose.yml`

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Create `docker-compose.yml`**

```yaml
services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  app:
    build: .
    restart: unless-stopped
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      # Override localhost values from .env with container-network addresses
      ASTRACORE__MEMORY__REDIS_URL: "redis://redis:6379/0"
      ASTRACORE__MEMORY__DB_URL: "sqlite+aiosqlite:////app/data/astracore.db"
      ASTRACORE__RETRIEVAL__PERSIST_DIRECTORY: "/app/data/chroma_db"
    volumes:
      - astracore_data:/app/data
    depends_on:
      redis:
        condition: service_healthy

volumes:
  astracore_data:
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add docker-compose.yml with app + redis services and named volume"
```

---

### Task 6: Smoke test — build and run

**Prerequisites:** Docker Desktop must be running.

- [ ] **Step 1: Copy `.env.example` to `.env` and fill in your API key**

```bash
cp .env.example .env
# Edit .env: set ASTRACORE__LLM__API_KEY to your actual key
```

- [ ] **Step 2: Build the image**

```bash
docker-compose build
```

Expected: build completes without error. Stage 1 runs `npm run build`, Stage 2 installs Python deps. Total time: ~3-5 minutes on first build.

- [ ] **Step 3: Start the services**

```bash
docker-compose up -d
```

Expected output:
```
[+] Running 2/2
 ✔ Container astracore-redis-1  Started
 ✔ Container astracore-app-1    Started
```

- [ ] **Step 4: Check health endpoint**

```bash
curl http://localhost:8000/health
```

Expected: HTTP 200 with a JSON response like `{"status": "ok"}` (or similar).

- [ ] **Step 5: Check frontend is served**

Open `http://localhost:8000` in a browser. Expected: the AstraCore AI frontend UI loads.

- [ ] **Step 6: Check logs if anything fails**

```bash
docker-compose logs app --tail=50
docker-compose logs redis --tail=20
```

- [ ] **Step 7: Stop services**

```bash
docker-compose down
```

---

### Task 7: Update `.env.example` with Docker instructions

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add Docker section comment to `.env.example`**

Append the following block to the end of `.env.example`:

```bash
# ──────────────────────────────────────────────────────────────
# Docker 部署说明
# ──────────────────────────────────────────────────────────────
# 1. 复制本文件: cp .env.example .env
# 2. 填入 ASTRACORE__LLM__API_KEY
# 3. 启动: docker-compose up -d
# 4. 访问: http://localhost:8000
#
# 注意: Docker 运行时以下两项会被 docker-compose.yml 自动覆盖，
#       无需手动修改：
#   ASTRACORE__MEMORY__REDIS_URL  → redis://redis:6379/0
#   ASTRACORE__MEMORY__DB_URL     → sqlite+aiosqlite:////app/data/astracore.db
#   ASTRACORE__RETRIEVAL__PERSIST_DIRECTORY → /app/data/chroma_db
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: add Docker deployment instructions to .env.example"
```
