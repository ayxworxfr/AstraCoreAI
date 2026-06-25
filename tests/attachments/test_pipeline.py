"""Attachment pipeline tests — F2 (capability check) and attachment loading."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from astracore.modules.attachments.domain import AttachmentCapabilityError, AttachmentRef
from astracore.modules.attachments.ports import AttachmentStoragePort
from astracore.modules.chat.domain.chat_options import ChatOptions
from astracore.modules.chat.pipeline import ChatPipeline
from astracore.shared.policy.engine import PolicyEngine
from astracore.shared.policy.rules import CompactionRule


def _make_pipeline(storage: AttachmentStoragePort | None = None) -> ChatPipeline:
    cfg = MagicMock()
    cfg.storage.db_url = "sqlite+aiosqlite:///./test.db"
    cfg.agent.max_tool_iterations = 5
    cfg.agent.max_tool_result_chars = 4000
    cfg.policy.timeout.build_llm_httpx_timeout.return_value = MagicMock()
    cfg.policy.timeout.tool_timeout_s = 30
    cfg.policy.compaction = CompactionRule()

    return ChatPipeline(
        config=cfg,
        memory=AsyncMock(),
        rag_pipeline=AsyncMock(),
        policy=PolicyEngine(),
        tool_adapter=MagicMock(),
        attachment_storage=storage,
    )


def _make_vision_ref() -> AttachmentRef:
    return AttachmentRef(
        id="att-1",
        mime_type="image/png",
        filename="test.png",
        size_bytes=100,
        storage_key="user-1/abc.png",
    )


@pytest.mark.asyncio
async def test_pipeline_prepare_raises_on_vision_incapable():
    """F2: pipeline raises AttachmentCapabilityError for vision-incapable profiles."""
    pipeline = _make_pipeline()

    caps = MagicMock()
    caps.vision = False
    caps.tools = True
    caps.thinking = False
    caps.adaptive_thinking_only = False
    caps.temperature = True
    caps.reasoning_effort_capable = False
    caps.prompt_cache = False

    profile = MagicMock()
    profile.id = "deepseek-chat"
    profile.capabilities = caps
    profile.temperature = 0.7
    profile.thinking_mode = None
    profile.top_p = None
    profile.stop_sequences = []
    profile.enable_prompt_cache = False
    profile.service_tier = None
    profile.timeout_s = None
    profile.max_retries = None
    profile.max_tokens = 1000
    profile.protocol = "openai"

    pipeline._config.llm.get_profile.return_value = profile
    pipeline._prompt_builder = AsyncMock()
    pipeline._prompt_builder.build_static.return_value = None
    pipeline._build_turn_context = AsyncMock(return_value="")
    pipeline._resolve_temperature = AsyncMock(return_value=0.7)
    pipeline._get_setting = AsyncMock(return_value="")

    opts = ChatOptions(attachments=[_make_vision_ref()])
    with pytest.raises(AttachmentCapabilityError):
        await pipeline.prepare(
            message="describe this",
            session_id=uuid4(),
            options=opts,
            user_id="user-1",
        )


@pytest.mark.asyncio
async def test_pipeline_prepare_loads_attachment_bytes():
    """Vision-capable profile: pipeline loads bytes from storage."""
    storage = AsyncMock(spec=AttachmentStoragePort)
    storage.load.return_value = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    pipeline = _make_pipeline(storage=storage)

    caps = MagicMock()
    caps.vision = True
    caps.tools = True
    caps.thinking = False
    caps.adaptive_thinking_only = False
    caps.temperature = True
    caps.reasoning_effort_capable = False
    caps.prompt_cache = False

    profile = MagicMock()
    profile.id = "claude-sonnet"
    profile.capabilities = caps
    profile.temperature = 0.7
    profile.thinking_mode = None
    profile.top_p = None
    profile.stop_sequences = []
    profile.enable_prompt_cache = False
    profile.service_tier = None
    profile.timeout_s = None
    profile.max_retries = None
    profile.max_tokens = 1000
    profile.protocol = "anthropic"

    pipeline._config.llm.get_profile.return_value = profile
    pipeline._prompt_builder = AsyncMock()
    pipeline._prompt_builder.build_static.return_value = None
    pipeline._build_turn_context = AsyncMock(return_value="")
    pipeline._resolve_temperature = AsyncMock(return_value=0.7)
    pipeline._get_setting = AsyncMock(return_value="")

    ref = _make_vision_ref()
    opts = ChatOptions(attachments=[ref])
    ctx = await pipeline.prepare(
        message="describe this",
        session_id=uuid4(),
        options=opts,
        user_id="user-1",
    )

    assert len(ctx.attachment_refs) == 1
    assert ctx.attachment_refs[0].data is not None
    storage.load.assert_called_once_with(ref.storage_key)
