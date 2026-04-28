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


class LLMCapabilitiesInfo(BaseModel):
    tools: bool
    thinking: bool
    temperature: bool
    anthropic_blocks: bool


class LLMProfileInfo(BaseModel):
    id: str
    label: str | None
    provider: str
    model: str
    base_url: str | None
    api_key_configured: bool
    max_tokens: int
    capabilities: LLMCapabilitiesInfo


class LLMInfo(BaseModel):
    default_profile: str
    profiles: list[LLMProfileInfo]


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
            default_profile=cfg.llm.default_profile,
            profiles=[
                LLMProfileInfo(
                    id=profile.id,
                    label=profile.label,
                    provider=profile.provider,
                    model=profile.model,
                    base_url=profile.base_url,
                    api_key_configured=bool(profile.api_key),
                    max_tokens=profile.max_tokens,
                    capabilities=LLMCapabilitiesInfo(
                        tools=profile.capabilities.tools,
                        thinking=profile.capabilities.thinking,
                        temperature=profile.capabilities.temperature,
                        anthropic_blocks=profile.capabilities.anthropic_blocks,
                    ),
                )
                for profile in cfg.llm.profiles
            ],
        ),
        tavily_configured=bool(os.getenv("TAVILY_API_KEY", "").strip()),
        mcp_servers=[
            MCPServerInfo(name=entry.name, type=entry.type)
            for entry in cfg.mcp.servers
        ],
    )
