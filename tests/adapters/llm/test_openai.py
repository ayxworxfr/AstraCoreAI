"""Tests for OpenAIAdapter protocol variants."""

import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astracore.infrastructure.llm.openai import OpenAIAdapter, _build_openai_user_content
from astracore.modules.attachments.domain import AttachmentProcessingError
from astracore.modules.chat.domain.message import Message, MessageRole
from astracore.modules.chat.domain.session_context import SessionContext
from astracore.shared.ports.llm import StreamEventType

_BJ = timezone(timedelta(hours=8))


@pytest.fixture
def adapter() -> OpenAIAdapter:
    return OpenAIAdapter(api_key="test-key")


def test_openai_adapter_passes_extra_headers_to_client(monkeypatch):
    pytest.importorskip("openai")
    created_kwargs = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            created_kwargs.update(kwargs)

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)
    adapter = OpenAIAdapter(
        api_key="test-key",
        base_url="https://proxy.example.com/v1",
        extra_headers={"x-proxy-route": "glm"},
    )

    adapter._get_client()

    assert created_kwargs == {
        "api_key": "test-key",
        "base_url": "https://proxy.example.com/v1",
        "default_headers": {"x-proxy-route": "glm"},
    }


def test_openai_adapter_passes_httpx_timeout_to_client(monkeypatch):
    """httpx.Timeout 应原样传入 AsyncOpenAI，治 stale stream。"""
    pytest.importorskip("openai")
    import httpx as _httpx  # noqa: PLC0415

    created_kwargs = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            created_kwargs.update(kwargs)

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)
    timeout = _httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=8.0)
    adapter = OpenAIAdapter(api_key="k", timeout=timeout)
    adapter._get_client()

    assert created_kwargs["timeout"] is timeout


def test_openai_adapter_omits_timeout_when_none(monkeypatch):
    pytest.importorskip("openai")
    created_kwargs = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            created_kwargs.update(kwargs)

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)
    adapter = OpenAIAdapter(api_key="k", timeout=None)
    adapter._get_client()
    assert "timeout" not in created_kwargs


class _FakeAsyncStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeResponsesStream:
    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return _FakeAsyncStream(self._events)

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _chunk_with_tool_delta(tool_deltas):
    delta = SimpleNamespace(content=None, tool_calls=tool_deltas)
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice])


def _tool_delta(
    *,
    index: int,
    call_id: str | None,
    name: str | None,
    arguments: str | None,
):
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(id=call_id, index=index, function=function)


@pytest.mark.asyncio
async def test_generate_stream_merges_tool_arguments_by_index_when_id_missing(adapter):
    """后续分片缺少 id 时，仍需按 index 继续拼接 arguments。"""
    chunks = [
        _chunk_with_tool_delta(
            [_tool_delta(index=0, call_id="call_1", name="directory_tree", arguments=None)]
        ),
        _chunk_with_tool_delta(
            [_tool_delta(index=0, call_id=None, name=None, arguments='{"path":"src"}')]
        ),
    ]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_FakeAsyncStream(chunks))
    adapter._client = mock_client

    events = []
    async for event in adapter.generate_stream(
        messages=[Message(role=MessageRole.USER, content="列目录")]
    ):
        events.append(event)

    tool_events = [e for e in events if e.event_type == StreamEventType.TOOL_CALL]
    assert len(tool_events) == 1
    assert tool_events[0].tool_call.name == "directory_tree"
    assert tool_events[0].tool_call.arguments == {"path": "src"}


@pytest.mark.asyncio
async def test_generate_uses_responses_api_when_configured():
    adapter = OpenAIAdapter(api_key="test-key", protocol="responses")
    mock_client = MagicMock()
    mock_client.responses.create = AsyncMock(
        return_value=SimpleNamespace(
            output_text="Hi there",
            usage=SimpleNamespace(input_tokens=3, output_tokens=4),
        )
    )
    adapter._client = mock_client

    result = await adapter.generate(
        messages=[
            Message(role=MessageRole.SYSTEM, content="Be brief."),
            Message(role=MessageRole.USER, content="hi"),
        ],
        model="gpt-5.5",
        max_tokens=64,
    )

    # Responses API (GPT-5 / o-series) does not accept temperature; assert it is absent.
    mock_client.responses.create.assert_called_once_with(
        model="gpt-5.5",
        input=[{"role": "user", "content": "hi"}],
        max_output_tokens=64,
        instructions="Be brief.",
    )
    assert result.content == "Hi there"
    assert result.usage == {
        "input_tokens": 3,
        "output_tokens": 4,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


@pytest.mark.asyncio
async def test_generate_stream_uses_responses_api_when_configured():
    adapter = OpenAIAdapter(api_key="test-key", protocol="responses")
    mock_client = MagicMock()
    mock_client.responses.stream.return_value = _FakeResponsesStream(
        [
            SimpleNamespace(type="response.output_text.delta", delta="Hi"),
            SimpleNamespace(type="response.output_text.delta", delta=" there"),
        ]
    )
    adapter._client = mock_client

    events = []
    async for event in adapter.generate_stream(
        messages=[Message(role=MessageRole.USER, content="hi")],
        model="gpt-5.5",
        max_tokens=64,
    ):
        events.append(event)

    # Responses API (GPT-5 / o-series) does not accept temperature; assert it is absent.
    mock_client.responses.stream.assert_called_once_with(
        model="gpt-5.5",
        input=[{"role": "user", "content": "hi"}],
        max_output_tokens=64,
    )
    assert [e.content for e in events if e.event_type == StreamEventType.TEXT_DELTA] == [
        "Hi",
        " there",
    ]
    assert events[-1].event_type == StreamEventType.DONE


# ---------- multimodal attachment blocks ----------


def _make_ref(mime_type: str, data: bytes, filename: str = "file") -> dict:
    return {
        "id": "att-1",
        "mime_type": mime_type,
        "filename": filename,
        "storage_key": "u/k",
        "data_b64": base64.b64encode(data).decode("ascii"),
    }


def test_build_openai_user_content_image_produces_image_url_block():
    """PNG attachment → image_url content block."""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
    ref = _make_ref("image/png", png)
    result = _build_openai_user_content("describe", [ref])
    assert isinstance(result, list)
    text_block = next(b for b in result if b["type"] == "text")
    assert text_block["text"] == "describe"
    img_block = next(b for b in result if b["type"] == "image_url")
    expected_url = f"data:image/png;base64,{base64.b64encode(png).decode()}"
    assert img_block["image_url"]["url"] == expected_url


def test_build_openai_user_content_no_images_returns_string():
    """No image refs → plain string (unchanged)."""
    result = _build_openai_user_content("hello", [])
    assert result == "hello"


def test_convert_messages_image_attachment_injects_image_url(adapter):
    """USER message with PNG attachment_ref → image_url in converted content."""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
    msg = Message(
        role=MessageRole.USER,
        content="what is this?",
        metadata={"attachment_refs": [_make_ref("image/png", png)]},
    )
    result = adapter._convert_messages([msg])
    content = result[0]["content"]
    assert isinstance(content, list)
    img_block = next(b for b in content if b["type"] == "image_url")
    assert img_block["image_url"]["url"].startswith("data:image/png;base64,")


def test_convert_messages_no_attachments_keeps_string_content(adapter):
    """Plain user message (no attachment_refs) → content stays string."""
    msg = Message(role=MessageRole.USER, content="hello")
    result = adapter._convert_messages([msg])
    assert result[0]["content"] == "hello"


def test_build_openai_user_content_pdf_error_propagates(monkeypatch):
    """AttachmentProcessingError from _extract_pdf_text must propagate out of _build_openai_user_content."""
    import astracore.infrastructure.llm.openai as openai_mod  # noqa: PLC0415

    def always_raise(data_b64: str, filename: str) -> str:
        raise AttachmentProcessingError(f"PDF '{filename}' 已加密，无法提取文本")

    monkeypatch.setattr(openai_mod, "_extract_pdf_text", always_raise)
    ref = _make_ref("application/pdf", b"%PDF-1.4\n", "secret.pdf")
    with pytest.raises(AttachmentProcessingError, match="已加密"):
        _build_openai_user_content("summarize", [ref])


# ---------- prompt-cache friendly session_context placement ----------


def test_append_session_context_keeps_system_stable(adapter):
    """动态 session_context 必须挂在末尾 user，不能污染 system 前缀。"""
    base = [
        {"role": "system", "content": "STATIC"},
        {"role": "user", "content": "hello"},
    ]
    round1 = adapter._append_session_context(base, "<session_context>\nround 1\n</session_context>")
    round2 = adapter._append_session_context(base, "<session_context>\nround 2\n</session_context>")
    assert round1[0] == {"role": "system", "content": "STATIC"}
    assert round2[0] == {"role": "system", "content": "STATIC"}
    assert round1[0] == round2[0]
    assert round1[1] == round2[1] == {"role": "user", "content": "hello"}
    assert round1[-1]["role"] == "user"
    assert "round 1" in round1[-1]["content"]
    assert "round 2" in round2[-1]["content"]


def test_append_session_context_puts_stable_after_system(adapter):
    base = [
        {"role": "system", "content": "STATIC"},
        {"role": "user", "content": "hello"},
    ]
    ctx = SessionContext.build(
        active_skill="mini-game",
        turn_context="进度：第3题",
        now=datetime(2026, 8, 15, 16, 0, tzinfo=_BJ),
    )
    out = adapter._append_session_context(base, ctx)
    assert out[0] == {"role": "system", "content": "STATIC"}
    assert out[1]["role"] == "system"
    assert "mini-game" in out[1]["content"]
    assert "进度：第3题" not in out[1]["content"]
    assert out[2] == {"role": "user", "content": "hello"}
    assert "进度：第3题" in out[-1]["content"]


def test_append_session_context_noop_when_empty(adapter):
    msgs = [{"role": "user", "content": "hi"}]
    assert adapter._append_session_context(msgs, None) == msgs
    assert adapter._append_session_context(msgs, "   ") == msgs


def test_responses_input_keeps_instructions_static_with_session_at_end(adapter):
    """Responses：instructions=静态 system；session_context 进 input 末尾。"""
    msgs = [
        Message(role=MessageRole.SYSTEM, content="STATIC_SYS"),
        Message(role=MessageRole.USER, content="q"),
    ]
    instructions, input_messages = adapter._responses_input(msgs)
    with_ctx = adapter._append_session_context(
        input_messages, "<session_context>dyn</session_context>"
    )
    assert instructions == "STATIC_SYS"
    assert with_ctx[-1] == {
        "role": "user",
        "content": "<session_context>dyn</session_context>",
    }
    # instructions 本身不被 session 污染
    assert "dyn" not in (instructions or "")


@pytest.mark.asyncio
async def test_generate_stream_passes_prompt_cache_key(adapter, monkeypatch):
    pytest.importorskip("openai")
    captured: dict = {}

    class FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return FakeStream()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(adapter, "_get_client", lambda: FakeClient())
    _ = [
        e
        async for e in adapter.generate_stream(
            [
                Message(role=MessageRole.SYSTEM, content="S"),
                Message(role=MessageRole.USER, content="u"),
            ],
            session_context="<session_context>x</session_context>",
            prompt_cache_key="user1:deepseek-v4-pro",
        )
    ]
    assert captured["prompt_cache_key"] == "user1:deepseek-v4-pro"
    assert captured["messages"][0] == {"role": "system", "content": "S"}
    assert captured["messages"][-1]["content"] == "<session_context>x</session_context>"
