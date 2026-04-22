"""FastAPI application factory."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from astracore.runtime.observability.logger import get_logger, setup_logging
from astracore.service.api import chat, health, rag, settings, skills, system
from astracore.service.seeds import seed_builtin_skills, seed_documents

setup_logging()
logger = get_logger(__name__)


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
        # Run in background so slow model downloads don't block server startup
        asyncio.create_task(seed_documents(pipeline))
    except Exception:
        logger.exception("种子文档写入失败，不影响服务启动")

    mcp_adapter = None
    if cfg.mcp.servers:
        try:
            from astracore.adapters.tools.composite import CompositeToolAdapter  # noqa: PLC0415
            from astracore.adapters.tools.mcp import (  # noqa: PLC0415
                MCPToolAdapter,
                build_server_configs,
            )
            from astracore.service.builtin_tools import build_tool_adapter  # noqa: PLC0415

            mcp_configs = build_server_configs(cfg.mcp.servers)
            mcp_adapter = MCPToolAdapter(mcp_configs)
            await asyncio.wait_for(mcp_adapter.start(), timeout=30)
            app.state.tool_adapter = CompositeToolAdapter([build_tool_adapter(), mcp_adapter])
            logger.info("MCP tool adapter started with %d server(s)", len(mcp_configs))
        except Exception:
            logger.exception("MCP 适配器启动失败，回退到内置工具")

    yield

    if mcp_adapter is not None:
        try:
            await mcp_adapter.stop()
            logger.info("MCP tool adapter stopped")
        except Exception:
            logger.exception("MCP 适配器停止时出错")


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
