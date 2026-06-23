"""FastAPI application factory."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse
from starlette.types import Scope

from astracore.app.middleware.logging import RequestLoggingMiddleware
from astracore.modules.auth import api as auth
from astracore.modules.chat import api as chat
from astracore.modules.chat import conversations_api as conversations
from astracore.modules.memory import api as memory
from astracore.modules.projects import api as projects
from astracore.modules.rag import api as rag
from astracore.modules.settings import api as settings
from astracore.modules.skills import api as skills
from astracore.modules.skills.seeds import seed_builtin_skills, seed_documents
from astracore.modules.system import api as system
from astracore.modules.system import health_api as health
from astracore.modules.tts import api as tts
from astracore.modules.users import api as users
from astracore.shared.observability.logger import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)


class SPAStaticFiles(StaticFiles):
    """Serve frontend routes from index.html while preserving backend 404s."""

    _BACKEND_PREFIXES = ("api/", "health", "docs", "redoc", "openapi.json")

    async def get_response(self, path: str, scope: Scope) -> Any:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            normalized_path = path.lstrip("/")
            is_backend_path = normalized_path == "api" or any(
                normalized_path == prefix.rstrip("/") or normalized_path.startswith(prefix)
                for prefix in self._BACKEND_PREFIXES
            )
            is_asset_path = "." in Path(normalized_path).name
            if (
                exc.status_code != 404
                or scope["method"] != "GET"
                or is_backend_path
                or is_asset_path
            ):
                raise
            return FileResponse(Path(str(self.directory)) / "index.html")


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    """Application lifespan manager."""
    from astracore.infrastructure.db.session import init_db
    from astracore.sdk.config import AstraCoreConfig

    cfg = AstraCoreConfig()

    try:
        await init_db(cfg.storage.db_url)
    except Exception:
        logger.exception("数据库初始化失败，不影响服务启动")

    try:
        await seed_builtin_skills(cfg.storage.db_url, extra_skill_dirs=cfg.skills.extra_dirs)
    except Exception:
        logger.exception("内置 Skill 种子写入失败，不影响服务启动")

    if cfg.storage.vector.enabled:
        try:
            pipeline = rag._get_rag_pipeline()
            # Run in background so slow model downloads don't block server startup
            asyncio.create_task(seed_documents(pipeline))
        except Exception:
            logger.exception("种子文档写入失败，不影响服务启动")

    if cfg.scheduling.enabled:
        try:
            from astracore.modules.chat.api import _get_chat_pipeline  # noqa: PLC0415
            from astracore.modules.scheduling.application.task_service import (  # noqa: PLC0415
                ScheduledTaskService,
            )
            from astracore.modules.scheduling.runner import init_runner  # noqa: PLC0415
            from astracore.modules.scheduling.scheduler import init_scheduler  # noqa: PLC0415

            scheduler = init_scheduler(
                db_url=cfg.storage.db_url,
                misfire_grace_seconds=cfg.scheduling.misfire_grace_seconds,
            )
            init_runner(
                pipeline_factory=_get_chat_pipeline,
                config_factory=lambda: cfg,
                max_concurrent_runs=cfg.scheduling.max_concurrent_runs,
            )
            svc = ScheduledTaskService(cfg.storage.db_url, cfg.scheduling.default_timezone)
            active_tasks = await svc.load_all_active_tasks()
            for task in active_tasks:
                svc._register_with_scheduler(task)
            scheduler.start()
            logger.info("Scheduler started with %d active job(s)", len(active_tasks))
        except Exception:
            logger.exception("Scheduler 初始化失败，不影响主服务启动")

    mcp_adapter = None
    if cfg.mcp.servers:
        try:
            from astracore.infrastructure.tools.composite import (
                CompositeToolAdapter,  # noqa: PLC0415
            )
            from astracore.infrastructure.tools.mcp import (  # noqa: PLC0415
                MCPToolAdapter,
                build_server_configs,
            )
            from astracore.modules.tools.builtin import build_tool_adapter  # noqa: PLC0415

            mcp_configs = build_server_configs(cfg.mcp.servers)
            mcp_adapter = MCPToolAdapter(mcp_configs)

            # 先挂内置工具，MCP 在后台启动，不阻塞服务就绪
            app.state.tool_adapter = build_tool_adapter(db_url=cfg.storage.db_url)

            async def _start_mcp() -> None:
                try:
                    await asyncio.wait_for(mcp_adapter.start(), timeout=30)
                    app.state.tool_adapter = CompositeToolAdapter(
                        [build_tool_adapter(db_url=cfg.storage.db_url), mcp_adapter]
                    )
                    logger.info("MCP tool adapter started with %d server(s)", len(mcp_configs))
                except Exception:
                    logger.exception("MCP 适配器后台启动失败，继续使用内置工具")

            asyncio.create_task(_start_mcp())
        except Exception:
            logger.exception("MCP 适配器初始化失败，回退到内置工具")

    yield

    if cfg.scheduling.enabled:
        try:
            from astracore.modules.scheduling.scheduler import get_scheduler  # noqa: PLC0415

            get_scheduler().shutdown(wait=False)
            logger.info("Scheduler stopped")
        except Exception:
            logger.exception("Scheduler 停止时出错")

    if mcp_adapter is not None:
        try:
            await mcp_adapter.stop()
            logger.info("MCP tool adapter stopped")
        except Exception:
            logger.exception("MCP 适配器停止时出错")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    from astracore.sdk.config import AstraCoreConfig  # noqa: PLC0415

    cfg = AstraCoreConfig()
    app = FastAPI(
        title="AstraCore AI",
        description="Enterprise-grade AI Framework API",
        version="0.1.0",
        lifespan=lifespan,
    )

    raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000")
    allowed_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]

    # 注意：FastAPI 中间件按注册逆序执行，RequestLoggingMiddleware 需最后注册，
    # 确保它在所有中间件最外层运行，计时和 request_id 覆盖完整请求生命周期。
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    )

    app.include_router(health.router, prefix="/health", tags=["health"])
    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
    app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])
    app.include_router(conversations.router, prefix="/api/v1/conversations", tags=["conversations"])
    if cfg.storage.vector.enabled:
        app.include_router(rag.router, prefix="/api/v1/rag", tags=["rag"])
    app.include_router(skills.router, prefix="/api/v1/skills", tags=["skills"])
    app.include_router(memory.router, prefix="/api/v1/memory", tags=["memory"])
    app.include_router(projects.router, prefix="/api/v1/projects", tags=["projects"])
    app.include_router(settings.router, prefix="/api/v1/settings", tags=["settings"])
    app.include_router(system.router, prefix="/api/v1/system", tags=["system"])
    app.include_router(tts.router, prefix="/api/v1/tts", tags=["tts"])

    if cfg.scheduling.enabled:
        from astracore.modules.scheduling import api as scheduling  # noqa: PLC0415

        app.include_router(
            scheduling.router,
            prefix="/api/v1/scheduled-tasks",
            tags=["scheduled-tasks"],
        )

    dist_dir = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if dist_dir.exists():
        app.mount("/", SPAStaticFiles(directory=str(dist_dir), html=True), name="static")

    return app
