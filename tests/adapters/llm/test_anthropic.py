"""Tests for AnthropicAdapter — _convert_messages and generate_stream tool arg accumulation."""
import pytest
from unittest.mock import MagicMock

from astracore.adapters.llm.anthropic import AnthropicAdapter
from astracore.core.domain.message import Message, MessageRole, ToolCall, ToolResult
from astracore.core.ports.llm import StreamEventType


@pytest.fixture
def adapter():
    return AnthropicAdapter(api_key="test-key")


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
    result = adapter._convert_messages([
        Message(role=MessageRole.ASSISTANT, content="", tool_calls=[tc]),
        Message(role=MessageRole.TOOL, content="", tool_results=[tr]),
    ])

    assert result[1]["role"] == "user"
    content = result[1]["content"]
    assert content[0]["type"] == "tool_result"
    assert content[0]["content"] == "results here"
    assert content[0]["tool_use_id"] == "tc_1"


def test_convert_messages_skips_orphan_tool_results(adapter):
    tr = ToolResult(tool_call_id="missing_tool_use", name="search", content="results here")
    result = adapter._convert_messages([
        Message(role=MessageRole.TOOL, content="", tool_results=[tr]),
    ])
    assert result == []


def test_convert_messages_plain_assistant_message(adapter):
    msg = Message(role=MessageRole.ASSISTANT, content="Just text")
    result = adapter._convert_messages([msg])
    assert result[0]["content"] == "Just text"


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
