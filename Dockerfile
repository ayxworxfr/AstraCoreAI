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

# Copy Node.js runtime from builder stage (avoids re-downloading; required for MCP servers)
COPY --from=frontend-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-builder /usr/local/bin/npm /usr/local/bin/npm
COPY --from=frontend-builder /usr/local/bin/npx /usr/local/bin/npx
COPY --from=frontend-builder /usr/local/lib/node_modules /usr/local/lib/node_modules

WORKDIR /app

# Install Python dependencies first (layer cache friendly)
COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e ".[anthropic,openai]" \
    && pip install --no-cache-dir chromadb

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

CMD ["python", "-m", "uvicorn", "astracore.service.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
