"""Tests for AnthropicAdapter — _convert_messages and generate_stream tool arg accumulation."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from astracore.infrastructure.llm.anthropic import (
    AnthropicAdapter,
    _build_anthropic_attachment_blocks,
)
from astracore.modules.chat.domain.message import Message, MessageRole, ToolCall, ToolResult
from astracore.shared.ports.llm import StreamEventType


@pytest.fixture
def adapter():
    return AnthropicAdapter(api_key="test-key")


# ---------- timeout passthrough ----------


def test_adapter_passes_httpx_timeout_to_sdk():
    """httpx.Timeout 应原样传入 AsyncAnthropic，治 stale stream。"""
    timeout = httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=8.0)
    adapter = AnthropicAdapter(api_key="k", timeout=timeout)
    with patch("anthropic.AsyncAnthropic") as mock_client:
        mock_client.return_value = MagicMock(auth_token=None)
        adapter._get_client()
        kwargs = mock_client.call_args.kwargs
        assert kwargs["timeout"] is timeout


def test_adapter_omits_timeout_kwarg_when_none():
    """timeout=None 时不传 kwarg，让 SDK 用自身默认（600s）。"""
    adapter = AnthropicAdapter(api_key="k", timeout=None)
    with patch("anthropic.AsyncAnthropic") as mock_client:
        mock_client.return_value = MagicMock(auth_token=None)
        adapter._get_client()
        assert "timeout" not in mock_client.call_args.kwargs


# ---------- _convert_messages ----------


def test_convert_messages_skips_system_role(adapter):
    msgs = [
        Message(role=MessageRole.SYSTEM, content="You are helpful"),
        Message(role=MessageRole.USER, content="Hello"),
    ]
    result = adapter._convert_messages(msgs)
    assert len(result) == 1
    assert result[0]["role"] == "user"


def test_get_system_message_extracts_system(adapter):
    msgs = [
        Message(role=MessageRole.SYSTEM, content="System prompt"),
        Message(role=MessageRole.USER, content="Hi"),
    ]
    assert adapter._get_system_message(msgs) == "System prompt"


def test_get_system_message_returns_none_when_absent(adapter):
    msgs = [Message(role=MessageRole.USER, content="Hi")]
    assert adapter._get_system_message(msgs) is None


def test_convert_messages_formats_tool_calls(adapter):
    tc = ToolCall(id="tc_1", name="search", arguments={"q": "python"})
    msg = Message(role=MessageRole.ASSISTANT, content="Let me search", tool_calls=[tc])
    result = adapter._convert_messages([msg])

    assert result[0]["role"] == "assistant"
    content = result[0]["content"]
    assert isinstance(content, list)
    types = [block["type"] for block in content]
    assert "tool_use" in types
    tool_block = next(b for b in content if b["type"] == "tool_use")
    assert tool_block["name"] == "search"
    assert tool_block["input"] == {"q": "python"}


def test_convert_messages_formats_tool_results(adapter):
    tc = ToolCall(id="tc_1", name="search", arguments={"q": "python"})
    tr = ToolResult(tool_call_id="tc_1", name="search", content="results here")
    result = adapter._convert_messages(
        [
            Message(role=MessageRole.ASSISTANT, content="", tool_calls=[tc]),
            Message(role=MessageRole.TOOL, content="", tool_results=[tr]),
        ]
    )

    assert result[1]["role"] == "user"
    content = result[1]["content"]
    assert content[0]["type"] == "tool_result"
    assert content[0]["content"] == "results here"
    assert content[0]["tool_use_id"] == "tc_1"


def test_convert_messages_skips_orphan_tool_results(adapter):
    tr = ToolResult(tool_call_id="missing_tool_use", name="search", content="results here")
    result = adapter._convert_messages(
        [
            Message(role=MessageRole.TOOL, content="", tool_results=[tr]),
        ]
    )
    assert result == []


def test_convert_messages_plain_assistant_message(adapter):
    msg = Message(role=MessageRole.ASSISTANT, content="Just text")
    result = adapter._convert_messages([msg])
    assert result[0]["content"] == "Just text"


def test_convert_messages_replays_stored_thinking_blocks():
    adapter = AnthropicAdapter(api_key="test-key", use_anthropic_blocks=True)
    msg = Message(
        role=MessageRole.ASSISTANT,
        content="Final answer",
        metadata={
            "anthropic_content_blocks": [
                {
                    "type": "thinking",
                    "thinking": "private chain of thought",
                    "signature": "stale-signature",
                },
                {"type": "text", "text": "Final answer"},
            ],
        },
    )

    result = adapter._convert_messages([msg])

    assert result == [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "private chain of thought",
                    "signature": "stale-signature",
                },
                {"type": "text", "text": "Final answer"},
            ],
        }
    ]


def test_convert_messages_drops_stored_blocks_when_replay_disabled():
    adapter = AnthropicAdapter(api_key="test-key", use_anthropic_blocks=False)
    msg = Message(
        role=MessageRole.ASSISTANT,
        content="Final answer",
        metadata={
            "anthropic_content_blocks": [
                {"type": "text", "text": "Final answer"},
                {"type": "tool_use", "id": "tool_1", "name": "search", "input": {"q": "python"}},
            ],
        },
    )

    result = adapter._convert_messages([msg])

    assert result == [{"role": "assistant", "content": "Final answer"}]


def test_convert_messages_replays_text_and_tool_blocks_when_enabled():
    adapter = AnthropicAdapter(api_key="test-key", use_anthropic_blocks=True)
    msg = Message(
        role=MessageRole.ASSISTANT,
        content="Final answer",
        metadata={
            "anthropic_content_blocks": [
                {"type": "thinking", "thinking": "private", "signature": "stale"},
                {"type": "text", "text": "Final answer"},
                {"type": "tool_use", "id": "tool_1", "name": "search", "input": {"q": "python"}},
            ],
        },
    )

    result = adapter._convert_messages([msg])

    assert result == [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "private", "signature": "stale"},
                {"type": "text", "text": "Final answer"},
                {"type": "tool_use", "id": "tool_1", "name": "search", "input": {"q": "python"}},
            ],
        }
    ]


async def test_generate_omits_temperature_when_profile_disables_it():
    adapter = AnthropicAdapter(api_key="test-key", supports_temperature=False)
    create = AsyncMock()
    create.return_value = MagicMock(
        content=[MagicMock(type="text", text="ok")],
        usage=MagicMock(input_tokens=1, output_tokens=1),
    )
    adapter._client = MagicMock()
    adapter._client.messages.create = create

    await adapter.generate(
        messages=[Message(role=MessageRole.USER, content="Hi")],
        temperature=0.2,
    )

    assert "temperature" not in create.call_args.kwargs


async def test_generate_sends_only_one_sampling_parameter():
    adapter = AnthropicAdapter(api_key="test-key", supports_temperature=True)
    create = AsyncMock()
    create.return_value = MagicMock(
        content=[MagicMock(type="text", text="ok")],
        usage=MagicMock(input_tokens=1, output_tokens=1),
    )
    adapter._client = MagicMock()
    adapter._client.messages.create = create

    await adapter.generate(
        messages=[Message(role=MessageRole.USER, content="Hi")],
        temperature=0.2,
        top_p=0.8,
        top_k=20,
    )

    request = create.call_args.kwargs
    assert request["top_p"] == 0.8
    assert "temperature" not in request
    assert "top_k" not in request


# ---------- generate_stream — helpers ----------


def _event(type_: str, **kwargs) -> MagicMock:
    e = MagicMock()
    e.type = type_
    for k, v in kwargs.items():
        setattr(e, k, v)
    return e


def _delta(delta_type: str, **kwargs) -> MagicMock:
    d = MagicMock()
    d.type = delta_type
    for k, v in kwargs.items():
        setattr(d, k, v)
    return d


def _tool_block(tool_id: str, tool_name: str) -> MagicMock:
    b = MagicMock()
    b.type = "tool_use"
    b.id = tool_id
    b.name = tool_name
    return b


class _FakeStreamCtx:
    """Async context manager that yields a pre-defined sequence of events."""

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self._gen()

    async def __aexit__(self, *args):
        pass

    async def _gen(self):
        for e in self._events:
            yield e


# ---------- generate_stream — tests ----------


async def test_generate_stream_accumulates_tool_arguments(adapter):
    """input_json_delta chunks must be merged into a single complete ToolCall."""
    events = [
        _event(
            "content_block_start",
            index=0,
            content_block=_tool_block("tc_1", "get_weather"),
        ),
        _event(
            "content_block_delta",
            index=0,
            delta=_delta("input_json_delta", partial_json='{"city":'),
        ),
        _event(
            "content_block_delta",
            index=0,
            delta=_delta("input_json_delta", partial_json='"NYC"}'),
        ),
        _event("content_block_stop", index=0),
    ]
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = _FakeStreamCtx(events)
    adapter._client = mock_client

    collected = []
    async for ev in adapter.generate_stream(
        messages=[Message(role=MessageRole.USER, content="Weather?")]
    ):
        collected.append(ev)

    tool_events = [e for e in collected if e.event_type == StreamEventType.TOOL_CALL]
    assert len(tool_events) == 1
    tc = tool_events[0].tool_call
    assert tc.name == "get_weather"
    assert tc.arguments == {"city": "NYC"}


async def test_generate_stream_emits_text_delta(adapter):
    events = [
        _event(
            "content_block_delta",
            index=0,
            delta=_delta("text_delta", text="Hello world"),
        ),
    ]
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = _FakeStreamCtx(events)
    adapter._client = mock_client

    collected = []
    async for ev in adapter.generate_stream(
        messages=[Message(role=MessageRole.USER, content="Hi")]
    ):
        collected.append(ev)

    text_events = [e for e in collected if e.event_type == StreamEventType.TEXT_DELTA]
    assert len(text_events) == 1
    assert text_events[0].content == "Hello world"


async def test_generate_stream_always_ends_with_done_event(adapter):
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = _FakeStreamCtx([])
    adapter._client = mock_client

    events = []
    async for ev in adapter.generate_stream(
        messages=[Message(role=MessageRole.USER, content="Hi")]
    ):
        events.append(ev)

    assert events[-1].event_type == StreamEventType.DONE


# ---------- _convert_messages — multimodal attachment blocks ----------


def _make_attachment_ref(mime_type: str, data: bytes) -> dict:
    return {
        "id": "att-1",
        "mime_type": mime_type,
        "filename": "test",
        "storage_key": "user/test",
        "data_b64": base64.b64encode(data).decode("ascii"),
    }


def test_convert_messages_image_attachment_produces_image_block(adapter):
    """USER message with PNG attachment_ref → image content block."""
    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
    ref = _make_attachment_ref("image/png", png_data)
    msg = Message(
        role=MessageRole.USER,
        content="describe this",
        metadata={"attachment_refs": [ref]},
    )
    result = adapter._convert_messages([msg])
    assert len(result) == 1
    assert result[0]["role"] == "user"
    blocks = result[0]["content"]
    assert isinstance(blocks, list)
    text_block = next(b for b in blocks if b["type"] == "text")
    assert text_block["text"] == "describe this"
    image_block = next(b for b in blocks if b["type"] == "image")
    assert image_block["source"]["type"] == "base64"
    assert image_block["source"]["media_type"] == "image/png"
    assert image_block["source"]["data"] == base64.b64encode(png_data).decode("ascii")


def test_convert_messages_pdf_attachment_produces_document_block(adapter):
    """USER message with PDF attachment_ref → document content block."""
    pdf_data = b"%PDF-1.4\n" + b"\x00" * 20
    ref = _make_attachment_ref("application/pdf", pdf_data)
    msg = Message(
        role=MessageRole.USER,
        content="summarize",
        metadata={"attachment_refs": [ref]},
    )
    result = adapter._convert_messages([msg])
    blocks = result[0]["content"]
    doc_block = next(b for b in blocks if b["type"] == "document")
    assert doc_block["source"]["media_type"] == "application/pdf"
    assert doc_block["source"]["data"] == base64.b64encode(pdf_data).decode("ascii")


def test_convert_messages_skips_attachment_ref_without_data(adapter):
    """A ref with data_b64=None is silently skipped (file not loaded)."""
    msg = Message(
        role=MessageRole.USER,
        content="hi",
        metadata={"attachment_refs": [{"id": "x", "mime_type": "image/png", "data_b64": None}]},
    )
    result = adapter._convert_messages([msg])
    blocks = result[0]["content"]
    # Only the text block should remain; no image block for the missing data
    image_blocks = [b for b in blocks if b["type"] == "image"]
    assert image_blocks == []


def test_convert_messages_no_attachments_keeps_string_content(adapter):
    """Plain user message (no attachment_refs) → content is still a string."""
    msg = Message(role=MessageRole.USER, content="hello")
    result = adapter._convert_messages([msg])
    assert result[0]["content"] == "hello"


def test_build_anthropic_attachment_blocks_empty_text_omits_text_block():
    """If text is empty string, no text block is emitted."""
    png_data = b"\x89PNG" + b"\x00" * 4
    ref = _make_attachment_ref("image/png", png_data)
    blocks = _build_anthropic_attachment_blocks("", [ref])
    text_blocks = [b for b in blocks if b["type"] == "text"]
    assert text_blocks == []
    assert len(blocks) == 1


async def test_generate_stream_handles_multiple_tool_blocks(adapter):
    """Two separate tool_use blocks (different indices) each emit their own ToolCall."""
    events = [
        _event("content_block_start", index=0, content_block=_tool_block("tc_a", "tool_a")),
        _event(
            "content_block_delta",
            index=0,
            delta=_delta("input_json_delta", partial_json='{"x": 1}'),
        ),
        _event("content_block_stop", index=0),
        _event("content_block_start", index=1, content_block=_tool_block("tc_b", "tool_b")),
        _event(
            "content_block_delta",
            index=1,
            delta=_delta("input_json_delta", partial_json='{"y": 2}'),
        ),
        _event("content_block_stop", index=1),
    ]
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = _FakeStreamCtx(events)
    adapter._client = mock_client

    collected = []
    async for ev in adapter.generate_stream(
        messages=[Message(role=MessageRole.USER, content="Do things")]
    ):
        collected.append(ev)

    tool_events = [e for e in collected if e.event_type == StreamEventType.TOOL_CALL]
    assert len(tool_events) == 2
    names = {e.tool_call.name for e in tool_events}
    assert names == {"tool_a", "tool_b"}
