"""Conversation deletion cleans up referenced attachments."""

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from astracore.infrastructure.attachments.local_fs import LocalFSAttachmentStorage
from astracore.infrastructure.db.models import AttachmentRow, ChatRunRow, ConversationRow, UserRow
from astracore.infrastructure.db.session import get_engine, get_session, init_db
from astracore.modules.auth.dependencies import get_current_user
from astracore.modules.chat import conversations_api


async def _noop_delete_memories(self, conversation_id):
    return 0


def _make_app(
    monkeypatch: pytest.MonkeyPatch,
    db_url: str,
    storage: LocalFSAttachmentStorage,
    current_user: UserRow,
) -> FastAPI:
    app = FastAPI()
    app.include_router(conversations_api.router, prefix="/api/v1/conversations")
    app.dependency_overrides[get_current_user] = lambda: current_user
    monkeypatch.setattr(conversations_api, "_get_db_url", lambda: db_url)
    monkeypatch.setattr(conversations_api, "_get_attachment_storage", lambda: storage)
    monkeypatch.setattr(conversations_api, "_get_vector_adapter", lambda: None)
    monkeypatch.setattr(
        conversations_api.MemoryEngine,
        "delete_conversation_memories",
        _noop_delete_memories,
    )
    return app


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    get_engine.cache_clear()
    await init_db(db_url)
    storage = LocalFSAttachmentStorage(base_path=tmp_path / "attachments")
    user = UserRow(id="user-a", username="alice", role="user", hashed_password="x")
    app = _make_app(monkeypatch, db_url, storage, user)
    yield db_url, storage, user, app
    get_engine.cache_clear()


async def _seed_conversation_attachment(
    db_url: str,
    conversation_id: str,
    attachment_id: str,
    storage_key: str,
) -> None:
    async with get_session(db_url) as db:
        db.add(ConversationRow(id=conversation_id, user_id="user-a", title="with file"))
        db.add(
            AttachmentRow(
                id=attachment_id,
                user_id="user-a",
                filename="photo.png",
                mime_type="image/png",
                size_bytes=67,
                storage_key=storage_key,
            )
        )
        db.add(
            ChatRunRow(
                id=str(uuid4()),
                session_id=conversation_id,
                user_id="user-a",
                status="done",
                request={"attachment_ids": [attachment_id]},
                user_message="看图",
                assistant_content="ok",
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_delete_conversation_removes_referenced_attachment_record_and_file(env):
    db_url, storage, _user, app = env
    conversation_id = str(uuid4())
    attachment_id = str(uuid4())
    storage_key = "user-a/photo.png"
    file_path = storage._base / storage_key
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"image")
    await _seed_conversation_attachment(db_url, conversation_id, attachment_id, storage_key)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/conversations/{conversation_id}")

    assert resp.status_code == 204
    assert not file_path.exists()
    assert not file_path.parent.exists()
    async with get_session(db_url) as db:
        assert await db.get(AttachmentRow, attachment_id) is None
