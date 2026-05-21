"""Tests for OpenAIAdapter protocol variants."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astracore.infrastructure.llm.openai import OpenAIAdapter
from astracore.modules.chat.domain.message import Message, MessageRole
from astracore.shared.ports.llm import StreamEventType


@pytest.fixture
def adapter() -> OpenAIAdapter:
    return OpenAIAdapter(api_key="test-key")


def test_openai_adapter_passes_extra_headers_to_client(monkeypatch):
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

    mock_client.responses.create.assert_called_once_with(
        model="gpt-5.5",
        input=[{"role": "user", "content": "hi"}],
        max_output_tokens=64,
        temperature=0.7,
        instructions="Be brief.",
    )
    assert result.content == "Hi there"
    assert result.usage == {"input_tokens": 3, "output_tokens": 4}


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

    mock_client.responses.stream.assert_called_once_with(
        model="gpt-5.5",
        input=[{"role": "user", "content": "hi"}],
        max_output_tokens=64,
        temperature=0.7,
    )
    assert [e.content for e in events if e.event_type == StreamEventType.TEXT_DELTA] == [
        "Hi",
        " there",
    ]
    assert events[-1].event_type == StreamEventType.DONE
