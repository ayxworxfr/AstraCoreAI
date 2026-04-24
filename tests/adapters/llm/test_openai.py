"""Tests for OpenAIAdapter streaming tool-call assembly."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astracore.adapters.llm.openai import OpenAIAdapter
from astracore.core.domain.message import Message, MessageRole
from astracore.core.ports.llm import StreamEventType


@pytest.fixture
def adapter() -> OpenAIAdapter:
    return OpenAIAdapter(api_key="test-key")


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
"""Tests for OpenAIAdapter streaming tool-call argument assembly."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astracore.adapters.llm.openai import OpenAIAdapter
from astracore.core.domain.message import Message, MessageRole
from astracore.core.ports.llm import StreamEventType


@pytest.fixture
def adapter() -> OpenAIAdapter:
    return OpenAIAdapter(api_key="test-key")


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
