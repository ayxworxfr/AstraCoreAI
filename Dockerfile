# ============================================================
# Stage 1: Build React frontend
# ============================================================
FROM node:20-slim AS frontend-builder

WORKDIR /frontend

ARG NPM_REGISTRY=https://registry.npmjs.org/

COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --include=dev --prefer-offline \
    --registry="${NPM_REGISTRY}" \
    --fetch-retries=5 \
    --fetch-retry-mintimeout=20000 \
    --fetch-retry-maxtimeout=120000

COPY frontend/ ./
RUN npm run build

# ============================================================
# Stage 2: Final application image
# ============================================================
FROM python:3.12-slim AS final

# Copy Node.js runtime from builder stage (avoids re-downloading; required for MCP servers)
COPY --from=frontend-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-builder /usr/local/bin/npm /usr/local/bin/npm
COPY --from=frontend-builder /usr/local/bin/npx /usr/local/bin/npx
COPY --from=frontend-builder /usr/local/lib/node_modules /usr/local/lib/node_modules

WORKDIR /app

# 为网络较慢的服务器配置 pip 源，可通过 build arg 覆盖。
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

# 先安装依赖再复制源码，代码级改动不会让依赖层失效。
COPY pyproject.toml README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p src/astracore \
    && touch src/astracore/__init__.py \
    && pip install -e ".[anthropic,openai,vector]"

COPY src/ ./src/
COPY config/ ./config/

# Copy built frontend from Stage 1
COPY --from=frontend-builder /frontend/dist ./frontend/dist

# Data directory for volumes (SQLite + ChromaDB)
RUN mkdir -p /app/data

# Non-root user for security
RUN useradd -m -u 1000 appuser

# Pre-download ChromaDB ONNX model using urllib (no httpx read-timeout issues).
# Failures are non-fatal: the model will fall back to runtime download.
# Copy locally-cached model if available (run scripts/cache_chroma_model.py once to populate)
COPY docker/chroma_model/ /home/appuser/.cache/chroma/onnx_models/all-MiniLM-L6-v2/

COPY scripts/predownload_chroma_model.py /tmp/predownload_chroma_model.py
RUN python /tmp/predownload_chroma_model.py /home/appuser/.cache/chroma/onnx_models/all-MiniLM-L6-v2 \
    && chown -R appuser:appuser /app /home/appuser/.cache \
    && rm /tmp/predownload_chroma_model.py
USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "astracore.app.factory:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
