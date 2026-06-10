"""Chat history pagination tests."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from astracore.infrastructure.db.models import ChatRunRow
from astracore.infrastructure.db.session import get_engine, get_session, init_db
from astracore.modules.chat import api as chat
from astracore.modules.chat.domain.chat_context import ChatContext
from astracore.modules.chat.domain.message import Message, MessageRole


class _MemoryStub:
    def __init__(self, messages: list[Message]) -> None:
        self.messages = messages

    async def load_short_term(self, _session_id):
        return self.messages

    async def save_short_term(self, _session_id, messages):
        self.messages = messages


@pytest.fixture
async def chat_history_db(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'history.db'}"
    get_engine.cache_clear()
    await init_db(db_url)

    monkeypatch.setattr(
        chat,
        "_get_settings",
        lambda: SimpleNamespace(memory=SimpleNamespace(db_url=db_url)),
    )

    yield db_url

    get_engine.cache_clear()


async def _insert_done_runs(db_url: str, session_id, count: int) -> list[str]:
    now = datetime.now(UTC)
    run_ids: list[str] = []
    async with get_session(db_url) as db:
        for index in range(count):
            run_id = str(uuid4())
            run_ids.append(run_id)
            created_at = now + timedelta(seconds=index)
            db.add(
                ChatRunRow(
                    id=run_id,
                    session_id=str(session_id),
                    status="done",
                    request={},
                    user_message=f"user {index:02d}",
                    assistant_content=f"assistant {index:02d}",
                    thinking_blocks=[f"thinking {index:02d}"],
                    tool_activity=[{"name": "tool", "done": True}],
                    created_at=created_at,
                    updated_at=created_at,
                    completed_at=created_at + timedelta(milliseconds=100),
                )
            )
        await db.commit()
    return run_ids


async def test_session_messages_page_from_chat_runs_when_short_term_is_trimmed(
    chat_history_db, monkeypatch
) -> None:
    session_id = uuid4()
    await _insert_done_runs(chat_history_db, session_id, 35)

    trimmed = [
        Message(role=MessageRole.USER, content="only recent user"),
        Message(role=MessageRole.ASSISTANT, content="only recent assistant"),
    ]
    monkeypatch.setattr(chat, "_get_memory_adapter", lambda: _MemoryStub(trimmed))

    first_page = await chat.get_session_messages(session_id, limit=30, offset=0)
    second_page = await chat.get_session_messages(session_id, limit=30, offset=30)

    assert first_page.total == 70
    assert first_page.has_more is True
    assert len(first_page.messages) == 30
    assert first_page.messages[0].content == "user 20"
    assert first_page.messages[-1].content == "assistant 34"

    assert second_page.total == 70
    assert second_page.has_more is True
    assert len(second_page.messages) == 30
    assert second_page.messages[0].content == "user 05"
    assert second_page.messages[-1].content == "assistant 19"


async def test_delete_session_message_deletes_run_by_run_based_message_id(
    chat_history_db, monkeypatch
) -> None:
    session_id = uuid4()
    run_ids = await _insert_done_runs(chat_history_db, session_id, 3)
    monkeypatch.setattr(chat, "_get_memory_adapter", lambda: _MemoryStub([]))

    await chat.delete_session_message(session_id, role="user", message_id=run_ids[1])

    page = await chat.get_session_messages(session_id, limit=30, offset=0)

    assert page.total == 4
    assert [message.content for message in page.messages] == [
        "user 00",
        "assistant 00",
        "user 02",
        "assistant 02",
    ]


async def test_non_streaming_chat_persists_done_run_for_history(
    chat_history_db, monkeypatch
) -> None:
    session_id = uuid4()

    class _PipelineStub:
        async def prepare(self, **kwargs):
            return ChatContext(
                session_id=kwargs["session_id"],
                user_id=kwargs.get("user_id", "default"),
                message=kwargs["message"],
                profile=SimpleNamespace(id="profile-a", model="model-a"),
                temperature=0.7,
                system_prompt=None,
                context_max_messages=20,
                mode="normal",
            )

        async def execute(self, _ctx):
            return "assistant response"

    monkeypatch.setattr(chat, "_get_chat_pipeline", lambda: _PipelineStub())
    monkeypatch.setattr(chat, "_resolve_tool_adapter", lambda _request: None)

    response = await chat.chat(
        chat.ChatRequest(message="user request", session_id=session_id),
        SimpleNamespace(),
        SimpleNamespace(id="default"),
    )
    page = await chat.get_session_messages(session_id, limit=30, offset=0)

    assert response.message == "assistant response"
    assert page.total == 2
    assert [message.content for message in page.messages] == [
        "user request",
        "assistant response",
    ]
