"""Attachment HTTP endpoint tests — F1 (size limit) and F4 (IDOR)."""

from __future__ import annotations

import io

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from astracore.infrastructure.attachments.local_fs import LocalFSAttachmentStorage
from astracore.infrastructure.db.models import AttachmentRow, UserRow
from astracore.infrastructure.db.session import get_engine, get_session
from astracore.modules.attachments import api as attachments_api
from astracore.modules.auth.dependencies import get_current_user
from tests.support.db import prepare_test_db

# Smallest valid PNG (1×1 px, 67 bytes)
_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
    b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
    b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)

_PDF_HEADER = b"%PDF-1.4\n"


def _make_app(db_url: str, storage: LocalFSAttachmentStorage, current_user: UserRow) -> FastAPI:
    app = FastAPI()
    app.include_router(attachments_api.router, prefix="/api/v1/attachments")
    app.dependency_overrides[attachments_api._get_db_url] = lambda: db_url
    app.dependency_overrides[attachments_api._get_storage] = lambda: storage
    app.dependency_overrides[get_current_user] = lambda: current_user
    return app


@pytest.fixture
async def env(tmp_path):
    db_url = await prepare_test_db(tmp_path)
    storage = LocalFSAttachmentStorage(base_path=tmp_path / "attachments")
    user_a = UserRow(id="user-a", username="alice", role="user", hashed_password="x")
    user_b = UserRow(id="user-b", username="bob", role="user", hashed_password="x")
    yield db_url, storage, user_a, user_b
    get_engine.cache_clear()


@pytest.mark.asyncio
async def test_upload_valid_png(env, tmp_path):
    db_url, storage, user_a, _ = env
    app = _make_app(db_url, storage, user_a)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/attachments",
            files={"file": ("photo.png", io.BytesIO(_PNG_1PX), "image/png")},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["mime_type"] == "image/png"
    assert body["attachment_id"]


@pytest.mark.asyncio
async def test_upload_same_png_twice_reuses_physical_file(env):
    db_url, storage, user_a, _ = env
    app = _make_app(db_url, storage, user_a)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/attachments",
            files={"file": ("photo.png", io.BytesIO(_PNG_1PX), "image/png")},
        )
        second = await client.post(
            "/api/v1/attachments",
            files={"file": ("photo.png", io.BytesIO(_PNG_1PX), "image/png")},
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["attachment_id"] != second.json()["attachment_id"]

    async with get_session(db_url) as db:
        result = await db.execute(
            select(AttachmentRow).where(
                AttachmentRow.id.in_(
                    [first.json()["attachment_id"], second.json()["attachment_id"]]
                )
            )
        )
        rows = list(result.scalars().all())

    assert len(rows) == 2
    assert len({row.storage_key for row in rows}) == 1
    assert len(list((storage._base / user_a.id).iterdir())) == 1


@pytest.mark.asyncio
async def test_delete_shared_attachment_file_only_after_last_reference(env):
    db_url, storage, user_a, _ = env
    app = _make_app(db_url, storage, user_a)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/attachments",
            files={"file": ("photo.png", io.BytesIO(_PNG_1PX), "image/png")},
        )
        second = await client.post(
            "/api/v1/attachments",
            files={"file": ("photo.png", io.BytesIO(_PNG_1PX), "image/png")},
        )

        async with get_session(db_url) as db:
            result = await db.execute(
                select(AttachmentRow).where(AttachmentRow.id == first.json()["attachment_id"])
            )
            row = result.scalar_one()
            file_path = storage._base / row.storage_key

        assert file_path.exists()
        delete_first = await client.delete(f"/api/v1/attachments/{first.json()['attachment_id']}")
        assert delete_first.status_code == 204
        assert file_path.exists()

        delete_second = await client.delete(f"/api/v1/attachments/{second.json()['attachment_id']}")
        assert delete_second.status_code == 204
        assert not file_path.exists()


@pytest.mark.asyncio
async def test_attachment_upload_size_limit_image(env, tmp_path):
    """F1: image exceeding 20 MB must return 413."""
    db_url, storage, user_a, _ = env
    app = _make_app(db_url, storage, user_a)
    # Build oversized PNG: use valid PNG header + padding
    oversized = _PNG_1PX + b"\x00" * (20 * 1024 * 1024 + 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/attachments",
            files={"file": ("big.png", io.BytesIO(oversized), "image/png")},
        )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_attachment_upload_size_limit_pdf(env, tmp_path):
    """F1: PDF exceeding 32 MB must return 413."""
    db_url, storage, user_a, _ = env
    app = _make_app(db_url, storage, user_a)
    oversized = _PDF_HEADER + b"\x00" * (32 * 1024 * 1024 + 1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/attachments",
            files={"file": ("big.pdf", io.BytesIO(oversized), "application/pdf")},
        )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_attachment_spoofed_content_type_rejected(env):
    """Magic bytes mismatch must return 415."""
    db_url, storage, user_a, _ = env
    app = _make_app(db_url, storage, user_a)
    # Claim PNG but send PDF bytes
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/attachments",
            files={"file": ("fake.png", io.BytesIO(_PDF_HEADER + b"\x00" * 100), "image/png")},
        )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_attachment_cross_user_access(env):
    """F4: user_b must not read user_a's attachment (IDOR → 403)."""
    db_url, storage, user_a, user_b = env

    # Upload as user_a
    app_a = _make_app(db_url, storage, user_a)
    async with AsyncClient(transport=ASGITransport(app=app_a), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/attachments",
            files={"file": ("photo.png", io.BytesIO(_PNG_1PX), "image/png")},
        )
    assert resp.status_code == 201
    attachment_id = resp.json()["attachment_id"]

    # Try to read as user_b
    app_b = _make_app(db_url, storage, user_b)
    async with AsyncClient(transport=ASGITransport(app=app_b), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/attachments/{attachment_id}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_attachment_cross_user_delete(env):
    """F4: user_b must not delete user_a's attachment (IDOR → 403)."""
    db_url, storage, user_a, user_b = env

    app_a = _make_app(db_url, storage, user_a)
    async with AsyncClient(transport=ASGITransport(app=app_a), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/attachments",
            files={"file": ("photo.png", io.BytesIO(_PNG_1PX), "image/png")},
        )
    attachment_id = resp.json()["attachment_id"]

    app_b = _make_app(db_url, storage, user_b)
    async with AsyncClient(transport=ASGITransport(app=app_b), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/attachments/{attachment_id}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_download_and_delete_own_attachment(env):
    """Owner can download and then delete their own attachment."""
    db_url, storage, user_a, _ = env
    app = _make_app(db_url, storage, user_a)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/attachments",
            files={"file": ("photo.png", io.BytesIO(_PNG_1PX), "image/png")},
        )
        attachment_id = resp.json()["attachment_id"]

        dl = await client.get(f"/api/v1/attachments/{attachment_id}")
        assert dl.status_code == 200
        assert dl.content == _PNG_1PX

        rm = await client.delete(f"/api/v1/attachments/{attachment_id}")
        assert rm.status_code == 204
