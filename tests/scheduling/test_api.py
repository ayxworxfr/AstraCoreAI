"""Scheduling REST API tests — health endpoint, invalid cron, cross-user isolation."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from astracore.infrastructure.db.models import UserRow
from astracore.infrastructure.db.session import get_engine
from astracore.modules.auth.dependencies import get_current_user
from astracore.modules.scheduling import api as scheduling_api
from astracore.modules.scheduling.application.task_service import ScheduledTaskService
from astracore.modules.scheduling.domain.task import CreateTaskRequest, TriggerType
from tests.support.db import prepare_test_db


def _make_app(svc: ScheduledTaskService, current_user: UserRow) -> FastAPI:
    from astracore.sdk.config import AstraCoreConfig

    cfg = AstraCoreConfig()
    app = FastAPI()
    app.include_router(scheduling_api.router, prefix="/api/v1/scheduled-tasks")
    app.dependency_overrides[scheduling_api._get_svc] = lambda: svc
    app.dependency_overrides[scheduling_api._get_cfg] = lambda: cfg
    app.dependency_overrides[get_current_user] = lambda: current_user
    return app


@pytest.fixture
async def api_env(tmp_path):
    db_url = await prepare_test_db(tmp_path, name="api_test.db")
    svc = ScheduledTaskService(db_url, "Asia/Shanghai")
    user = UserRow(id="user-1", username="testuser", role="user", hashed_password="x")
    app = _make_app(svc, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, svc, user
    get_engine.cache_clear()


async def test_scheduler_health_endpoint(manual_test, api_env) -> None:
    """F1: /_health returns scheduler_running field regardless of scheduler state."""
    client, *_ = api_env
    resp = await client.get("/api/v1/scheduled-tasks/_health")
    assert resp.status_code == 200
    data = resp.json()
    assert "scheduler_running" in data
    assert isinstance(data["scheduler_running"], bool)


async def test_create_task_invalid_cron_returns_400(manual_test, api_env) -> None:
    """F5: invalid cron expression rejected with HTTP 400."""
    client, *_ = api_env
    resp = await client.post(
        "/api/v1/scheduled-tasks/",
        json={
            "prompt": "hello world",
            "trigger_type": "cron",
            "trigger_config": {"expr": "not-a-valid-cron"},
        },
    )
    assert resp.status_code == 400


async def test_create_task_valid_cron_returns_201(manual_test, api_env) -> None:
    client, *_ = api_env
    resp = await client.post(
        "/api/v1/scheduled-tasks/",
        json={
            "prompt": "daily job",
            "trigger_type": "cron",
            "trigger_config": {"expr": "0 9 * * *"},
            "name": "Daily",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Daily"
    assert data["status"] == "active"


async def test_get_task_cross_user_returns_404(manual_test, tmp_path) -> None:
    """F7: a user cannot read another user's task — service returns None → 404."""
    db_url = await prepare_test_db(tmp_path, name="cross.db")

    owner_svc = ScheduledTaskService(db_url, "Asia/Shanghai")
    task = await owner_svc.create_task(
        CreateTaskRequest(
            user_id="owner",
            prompt="private",
            trigger_type=TriggerType.CRON,
            trigger_config={"expr": "0 9 * * *"},
        )
    )

    attacker = UserRow(id="attacker", username="attacker", role="user", hashed_password="x")
    # Build app that returns the same svc but authenticates as attacker
    app = _make_app(owner_svc, attacker)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/scheduled-tasks/{task.id}")
        assert resp.status_code == 404

    get_engine.cache_clear()


async def test_list_tasks_returns_only_current_user_tasks(manual_test, api_env) -> None:
    client, svc, user = api_env

    # Create a task for the current user
    await svc.create_task(
        CreateTaskRequest(
            user_id=user.id,
            prompt="mine",
            trigger_type=TriggerType.CRON,
            trigger_config={"expr": "0 0 * * *"},
        )
    )
    # Create a task for another user
    await svc.create_task(
        CreateTaskRequest(
            user_id="other-user",
            prompt="not mine",
            trigger_type=TriggerType.CRON,
            trigger_config={"expr": "0 0 * * *"},
        )
    )

    resp = await client.get("/api/v1/scheduled-tasks/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["user_id"] == user.id
