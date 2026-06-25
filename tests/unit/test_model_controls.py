"""Tests for model_controls descriptor system.

Covers:
  - _build_controls: control descriptors generated correctly per profile
  - ChatOptions.top_p: three-level priority (opts > settings > profile)
  - reasoning_effort_protocol: correct field on LLMCapabilities
"""

import pytest

from astracore.sdk.model_capabilities import infer_model_capabilities

# ---------------------------------------------------------------------------
# infer_model_capabilities — reasoning_effort_protocol
# ---------------------------------------------------------------------------


def test_gpt5_capabilities_uses_responses_protocol():
    caps = infer_model_capabilities(protocol="openai", model="gpt-5")
    assert caps.reasoning_effort_protocol == "responses"
    assert caps.temperature is False


def test_deepseek_openai_protocol_uses_extra_body():
    caps = infer_model_capabilities(protocol="openai", model="deepseek-v4-flash")
    assert caps.reasoning_effort_protocol == "extra_body"


def test_deepseek_anthropic_protocol_no_reasoning_effort():
    caps = infer_model_capabilities(
        protocol="anthropic",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com/anthropic",
    )
    assert caps.reasoning_effort_protocol is None


def test_glm52_uses_extra_body():
    caps = infer_model_capabilities(protocol="openai", model="glm-5.2")
    assert caps.reasoning_effort_protocol == "extra_body"


def test_glm5_no_reasoning_effort():
    """GLM-5/5.1 do not have reasoning_effort; only GLM-5.2+ does."""
    for model in ("glm-5", "glm-5.1", "glm-5-plus"):
        caps = infer_model_capabilities(protocol="openai", model=model)
        assert caps.reasoning_effort_protocol is None, (
            f"{model} should not have reasoning_effort_protocol"
        )


def test_anthropic_claude_no_reasoning_effort():
    caps = infer_model_capabilities(protocol="anthropic", model="claude-sonnet-4-6")
    assert caps.reasoning_effort_protocol is None


# ---------------------------------------------------------------------------
# _build_controls
# ---------------------------------------------------------------------------


@pytest.fixture
def _make_profile():
    """Factory for minimal LLMProfileConfig-like objects."""
    from astracore.sdk.config import LLMProfileConfig

    def _factory(**kwargs):
        defaults = {
            "id": "test",
            "label": None,
            "api_key": "k",
            "protocol": "anthropic",
            "model": "claude-sonnet-4-6",
            "base_url": None,
            "max_tokens": 8192,
            "temperature": 0.7,
            "top_p": None,
        }
        defaults.update(kwargs)
        return LLMProfileConfig(**defaults)

    return _factory


def test_build_controls_sonnet_thinking_and_sampling(_make_profile):
    from astracore.modules.system.api import _build_controls

    profile = _make_profile(model="claude-sonnet-4-6", protocol="anthropic")
    profile.capabilities = infer_model_capabilities(protocol="anthropic", model="claude-sonnet-4-6")
    controls = _build_controls(profile)

    kinds = [c.kind for c in controls]
    assert "thinking" in kinds
    assert "temperature" in kinds
    assert "top_p" in kinds
    assert "reasoning_effort" not in kinds

    thinking = next(c for c in controls if c.kind == "thinking")
    assert "on" in thinking.modes
    assert "adaptive" in thinking.modes
    assert "off" in thinking.modes


def test_build_controls_opus47_no_on_mode(_make_profile):
    """Opus 4.7+ ThinkingControl must not contain 'on' — sending it causes 400."""
    from astracore.modules.system.api import _build_controls

    profile = _make_profile(model="claude-opus-4-7", protocol="anthropic")
    profile.capabilities = infer_model_capabilities(protocol="anthropic", model="claude-opus-4-7")
    controls = _build_controls(profile)

    thinking = next((c for c in controls if c.kind == "thinking"), None)
    assert thinking is not None
    assert "on" not in thinking.modes
    assert "adaptive" in thinking.modes


def test_build_controls_opus46_thinking_with_on_mode(_make_profile):
    """Opus 4.6 supports standard thinking (off/on/adaptive), same as Sonnet 4.6."""
    from astracore.modules.system.api import _build_controls

    profile = _make_profile(model="claude-opus-4-6", protocol="anthropic")
    profile.capabilities = infer_model_capabilities(protocol="anthropic", model="claude-opus-4-6")
    controls = _build_controls(profile)

    kinds = [c.kind for c in controls]
    assert "thinking" in kinds
    assert "temperature" in kinds
    assert "top_k" in kinds

    thinking = next(c for c in controls if c.kind == "thinking")
    assert "on" in thinking.modes
    assert "adaptive" in thinking.modes
    assert "off" in thinking.modes


def test_build_controls_gpt5_no_temperature(_make_profile):
    """GPT-5 must not produce TemperatureControl — Responses API rejects it."""
    from astracore.modules.system.api import _build_controls

    profile = _make_profile(model="gpt-5", protocol="responses")
    profile.capabilities = infer_model_capabilities(protocol="responses", model="gpt-5")
    controls = _build_controls(profile)

    kinds = [c.kind for c in controls]
    assert "temperature" not in kinds
    assert "top_p" not in kinds

    effort = next((c for c in controls if c.kind == "reasoning_effort"), None)
    assert effort is not None
    assert effort.levels == ["minimal", "low", "medium", "high"]


def test_build_controls_deepseek_openai_reasoning_effort(_make_profile):
    from astracore.modules.system.api import _build_controls

    profile = _make_profile(model="deepseek-v4-flash", protocol="openai")
    profile.capabilities = infer_model_capabilities(protocol="openai", model="deepseek-v4-flash")
    controls = _build_controls(profile)

    # DeepSeek supports thinking (on/off only, no adaptive)
    thinking = next((c for c in controls if c.kind == "thinking"), None)
    assert thinking is not None
    assert thinking.modes == ["off", "on"]
    assert "adaptive" not in thinking.modes

    effort = next((c for c in controls if c.kind == "reasoning_effort"), None)
    assert effort is not None
    assert effort.levels == ["high", "max"]


def test_build_controls_glm52_seven_levels(_make_profile):
    from astracore.modules.system.api import _build_controls

    profile = _make_profile(model="glm-5.2", protocol="openai")
    profile.capabilities = infer_model_capabilities(protocol="openai", model="glm-5.2")
    controls = _build_controls(profile)

    # GLM thinking: on/off only, no adaptive
    thinking = next((c for c in controls if c.kind == "thinking"), None)
    assert thinking is not None
    assert thinking.modes == ["off", "on"]
    assert "adaptive" not in thinking.modes

    effort = next((c for c in controls if c.kind == "reasoning_effort"), None)
    assert effort is not None
    assert len(effort.levels) == 7
    assert "none" in effort.levels
    assert "max" in effort.levels


def test_build_controls_glm5_no_reasoning_effort(_make_profile):
    """GLM-5/5.1 must not generate ReasoningEffortControl; thinking is on/off only."""
    from astracore.modules.system.api import _build_controls

    profile = _make_profile(model="glm-5", protocol="openai")
    profile.capabilities = infer_model_capabilities(protocol="openai", model="glm-5")
    controls = _build_controls(profile)

    assert not any(c.kind == "reasoning_effort" for c in controls)

    thinking = next((c for c in controls if c.kind == "thinking"), None)
    assert thinking is not None
    assert thinking.modes == ["off", "on"]
    assert "adaptive" not in thinking.modes


# ---------------------------------------------------------------------------
# ChatOptions.top_p — three-level priority
# ---------------------------------------------------------------------------


def test_chat_options_top_p_field_exists():
    import dataclasses

    from astracore.modules.chat.domain.chat_options import ChatOptions

    field_names = {f.name for f in dataclasses.fields(ChatOptions)}
    assert "top_p" in field_names


def test_chat_options_top_p_default_is_none():
    from astracore.modules.chat.domain.chat_options import ChatOptions

    opts = ChatOptions()
    assert opts.top_p is None


def test_chat_options_top_p_zero_is_not_falsy():
    """opts.top_p=0.0 must NOT be skipped by a falsy check — it is a valid override."""
    from astracore.modules.chat.domain.chat_options import ChatOptions

    opts = ChatOptions(top_p=0.0)
    assert opts.top_p == 0.0

    # Simulate the pipeline three-level expression
    saved_top_p_from_db = "0.9"
    profile_top_p = 0.8

    effective_top_p = (
        opts.top_p
        if opts.top_p is not None
        else (float(saved_top_p_from_db) if saved_top_p_from_db else profile_top_p)
    )
    assert effective_top_p == 0.0, "top_p=0.0 from opts must not be overridden by settings/profile"


# ---------------------------------------------------------------------------
# OpenAI adapter — extra_body reasoning_effort routing
# ---------------------------------------------------------------------------


def _make_chat_completions_mock(captured: dict):
    """Returns a fake AsyncOpenAI client that records request_params."""
    from types import SimpleNamespace

    class FakeChoice:
        message = SimpleNamespace(content="ok", tool_calls=None)
        finish_reason = "stop"

    class FakeUsage:
        prompt_tokens = 1
        completion_tokens = 1
        prompt_tokens_details = None
        prompt_cache_hit_tokens = None

    class FakeResponse:
        choices = [FakeChoice()]
        usage = FakeUsage()

    class FakeChatCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return FakeResponse()

    class FakeChat:
        completions = FakeChatCompletions()

    class FakeOpenAI:
        chat = FakeChat()

    return SimpleNamespace(chat=FakeChat())


@pytest.mark.asyncio
async def test_openai_adapter_extra_body_reasoning_effort(monkeypatch):
    """When reasoning_effort is provided, it must appear in extra_body for Chat Completions."""
    from astracore.infrastructure.llm.openai import OpenAIAdapter
    from astracore.modules.chat.domain.message import Message, MessageRole

    captured: dict = {}

    from types import SimpleNamespace

    class FakeChoice:
        message = SimpleNamespace(content="ok", tool_calls=None)

    class FakeUsage:
        prompt_tokens = 1
        completion_tokens = 1
        prompt_tokens_details = None
        prompt_cache_hit_tokens = None

    class FakeResponse:
        choices = [FakeChoice()]
        usage = FakeUsage()

    class FakeChatCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return FakeResponse()

    class FakeChat:
        completions = FakeChatCompletions()

    class FakeOpenAI:
        chat = FakeChat()

    adapter = OpenAIAdapter(api_key="k", protocol="openai")
    adapter._client = FakeOpenAI()

    messages = [Message(role=MessageRole.USER, content="hi")]
    await adapter.generate(messages, model="deepseek-v4-flash", reasoning_effort="max")

    assert captured.get("extra_body") == {"reasoning_effort": "max"}


@pytest.mark.asyncio
async def test_openai_adapter_glm_thinking_and_reasoning_effort(monkeypatch):
    """GLM stream: thinking + reasoning_effort must both appear in extra_body."""
    from astracore.infrastructure.llm.openai import OpenAIAdapter
    from astracore.modules.chat.domain.message import Message, MessageRole

    captured: dict = {}

    class FakeDelta:
        content = "hi"
        tool_calls = None
        reasoning_content = None

    class FakeChoice:
        delta = FakeDelta()
        finish_reason = None

    class FakeChunk:
        choices = [FakeChoice()]
        usage = None

    class FakeUsageChunk:
        choices = []

        class _Usage:
            prompt_tokens = 1
            completion_tokens = 1
            prompt_tokens_details = None
            prompt_cache_hit_tokens = None

        usage = _Usage()

    class FakeStream:
        def __aiter__(self):
            return self

            async def __anext__(self):
                raise StopAsyncIteration

        async def __anext__(self):
            raise StopAsyncIteration

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeStream()

    class FakeChatCompletions:
        create = staticmethod(fake_create)

    class FakeChat:
        completions = FakeChatCompletions()

    class FakeOpenAI:
        chat = FakeChat()

    adapter = OpenAIAdapter(api_key="k", protocol="openai")
    adapter._client = FakeOpenAI()

    messages = [Message(role=MessageRole.USER, content="hi")]
    events = []
    async for event in adapter.generate_stream(
        messages,
        model="glm-5.2",
        thinking_mode="on",
        reasoning_effort="high",
    ):
        events.append(event)

    extra = captured.get("extra_body", {})
    assert extra.get("thinking") == {"type": "enabled"}
    assert extra.get("reasoning_effort") == "high"
