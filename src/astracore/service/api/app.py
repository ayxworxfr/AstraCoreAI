"""FastAPI application factory."""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from astracore.service.api import chat, health, rag
from astracore.service.seeds import seed_documents

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    """Application lifespan manager."""
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

    # Read allowed origins from environment.
    # Default covers local frontend dev servers.
    # Production: set ALLOWED_ORIGINS=https://your-domain.com
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

    return app
