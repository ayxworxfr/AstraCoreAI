"""System info API endpoint."""

import os
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from astracore.sdk.config import AstraCoreConfig, LLMProfileConfig

router = APIRouter()


@lru_cache(maxsize=1)
def _get_config() -> AstraCoreConfig:
    return AstraCoreConfig()


# ------------------------------------------------------------------
# ModelControl — discriminated union, 4 kinds
# ------------------------------------------------------------------


class ThinkingControl(BaseModel):
    kind: Literal["thinking"] = "thinking"
    modes: list[str]
    default: str


class ReasoningEffortControl(BaseModel):
    kind: Literal["reasoning_effort"] = "reasoning_effort"
    levels: list[str]
    default: str


class TemperatureControl(BaseModel):
    kind: Literal["temperature"] = "temperature"
    min: float
    max: float
    step: float
    profile_default: float


class TopPControl(BaseModel):
    kind: Literal["top_p"] = "top_p"
    min: float
    max: float
    step: float
    profile_default: float | None


class TopKControl(BaseModel):
    kind: Literal["top_k"] = "top_k"
    min: int
    max: int
    step: int


ModelControl = Annotated[
    ThinkingControl | ReasoningEffortControl | TemperatureControl | TopPControl | TopKControl,
    Field(discriminator="kind"),
]


def _build_controls(profile: LLMProfileConfig) -> list[ModelControl]:
    """Build per-turn UI control descriptors from a resolved LLM profile.

    Adding a new model only requires updating infer_model_capabilities(); this
    function derives controls purely from capability flags and the model name.
    """
    caps = profile.capabilities
    controls: list[ModelControl] = []

    # ── Thinking（主工具栏）────────────────────────────────────────────────
    # ThinkingControl.default 优先读 profile.thinking_mode；未配置时默认取该模型
    # 支持的最强思考模式（'on' 或 'adaptive'），使切换到 thinking 模型时自动开启。
    # 如需对某个 profile 默认关闭，在 config.yaml 中显式设置 thinking_mode: off。
    if caps.adaptive_thinking_only:
        modes = ["off", "adaptive"]
    elif caps.thinking:
        model_lower = profile.model.lower()
        # GLM / DeepSeek 的 thinking API 是二值开关（type=enabled），无自适应模式。
        # Anthropic Claude 支持三档：off / on（budget_tokens）/ adaptive（Opus 4.7 以下）。
        if "glm" in model_lower or "deepseek" in model_lower:
            modes = ["off", "on"]
        else:
            modes = ["off", "on", "adaptive"]
    else:
        modes = None

    if modes is not None:
        if profile.thinking_mode and profile.thinking_mode in modes:
            default_mode = profile.thinking_mode
        else:
            # Not explicitly configured: default to the strongest available thinking mode.
            default_mode = "on" if "on" in modes else "adaptive"
        controls.append(ThinkingControl(modes=modes, default=default_mode))

    # ── Reasoning effort（主工具栏）────────────────────────────────────────
    # levels encode per-provider options; kind stays unified so frontend never
    # needs to know the provider brand.
    if caps.reasoning_effort_protocol == "responses":
        # GPT-5/5.5 Responses API: 'minimal' was removed; 'xhigh' added.
        controls.append(
            ReasoningEffortControl(levels=["low", "medium", "high", "xhigh"], default="medium")
        )
    elif caps.reasoning_effort_protocol == "extra_body":
        model = profile.model.lower()
        if "deepseek" in model:
            controls.append(ReasoningEffortControl(levels=["high", "max"], default="high"))
        elif "glm" in model:
            controls.append(
                ReasoningEffortControl(
                    levels=["none", "minimal", "low", "medium", "high", "xhigh", "max"],
                    default="medium",
                )
            )

    # ── 采样参数（高级设置面板，默认折叠）─────────────────────────────────
    if caps.temperature:
        max_temp = 1.0 if profile.protocol == "anthropic" else 2.0
        controls.append(
            TemperatureControl(
                min=0.0, max=max_temp, step=0.01, profile_default=profile.temperature
            )
        )
        controls.append(TopPControl(min=0.0, max=1.0, step=0.01, profile_default=profile.top_p))

    if caps.top_k:
        controls.append(TopKControl(min=1, max=500, step=1))

    return controls


# ------------------------------------------------------------------
# API response models
# ------------------------------------------------------------------


class LLMCapabilitiesInfo(BaseModel):
    tools: bool
    thinking: bool
    temperature: bool
    top_k: bool
    anthropic_blocks: bool
    vision: bool
    reasoning_effort_protocol: Literal["responses", "extra_body"] | None


class LLMProfileInfo(BaseModel):
    id: str
    label: str | None
    protocol: str
    model: str
    base_url: str | None
    api_key_configured: bool
    max_tokens: int
    capabilities: LLMCapabilitiesInfo
    controls: list[ModelControl]


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
    rag_enabled: bool


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
                    protocol=profile.protocol,
                    model=profile.model,
                    base_url=profile.base_url,
                    api_key_configured=bool(profile.api_key),
                    max_tokens=profile.max_tokens,
                    capabilities=LLMCapabilitiesInfo(
                        tools=profile.capabilities.tools,
                        thinking=profile.capabilities.thinking,
                        temperature=profile.capabilities.temperature,
                        top_k=profile.capabilities.top_k,
                        anthropic_blocks=profile.capabilities.anthropic_blocks,
                        vision=profile.capabilities.vision,
                        reasoning_effort_protocol=profile.capabilities.reasoning_effort_protocol,
                    ),
                    controls=_build_controls(profile),
                )
                for profile in cfg.llm.profiles
            ],
        ),
        tavily_configured=bool(os.getenv("TAVILY_API_KEY", "").strip()),
        mcp_servers=[MCPServerInfo(name=entry.name, type=entry.type) for entry in cfg.mcp.servers],
        rag_enabled=cfg.storage.vector.enabled,
    )
