"""System info API endpoint."""

import os
from functools import lru_cache

from fastapi import APIRouter
from pydantic import BaseModel

from astracore.sdk.config import AstraCoreConfig

router = APIRouter()


@lru_cache(maxsize=1)
def _get_config() -> AstraCoreConfig:
    return AstraCoreConfig()


class LLMInfo(BaseModel):
    provider: str
    model: str
    base_url: str | None
    api_key_configured: bool


class MCPServerInfo(BaseModel):
    name: str
    type: str


class SystemInfoResponse(BaseModel):
    llm: LLMInfo
    tavily_configured: bool
    mcp_servers: list[MCPServerInfo]


@router.get("/", response_model=SystemInfoResponse)
async def get_system_info() -> SystemInfoResponse:
    cfg = _get_config()
    return SystemInfoResponse(
        llm=LLMInfo(
            provider=cfg.llm.provider,
            model=cfg.llm.model,
            base_url=cfg.llm.base_url,
            api_key_configured=bool(cfg.llm.api_key),
        ),
        tavily_configured=bool(os.getenv("TAVILY_API_KEY", "").strip()),
        mcp_servers=[
            MCPServerInfo(name=entry.name, type=entry.type)
            for entry in cfg.mcp.servers
        ],
    )
