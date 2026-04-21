"""User settings API endpoints."""

from datetime import UTC, datetime
from functools import lru_cache

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from astracore.adapters.db.models import UserSettingsRow
from astracore.adapters.db.session import get_session
from astracore.sdk.config import AstraCoreConfig

router = APIRouter()

_SETTINGS_KEYS = {
    "default_skill_id",
    "global_instruction",
    "temperature",
    "rag_top_k",
    "context_max_messages",
}

_SETTINGS_DEFAULTS: dict[str, str] = {
    "default_skill_id": "",
    "global_instruction": "",
    "temperature": "0.7",
    "rag_top_k": "4",
    "context_max_messages": "20",
}


@lru_cache(maxsize=1)
def _db_url() -> str:
    return AstraCoreConfig().memory.db_url


class UserSettingsResponse(BaseModel):
    default_skill_id: str = ""
    global_instruction: str = ""
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    rag_top_k: int = Field(default=4, ge=1, le=20)
    context_max_messages: int = Field(default=20, ge=4, le=200)


class UserSettingsUpdate(BaseModel):
    default_skill_id: str | None = None
    global_instruction: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    rag_top_k: int | None = Field(default=None, ge=1, le=20)
    context_max_messages: int | None = Field(default=None, ge=4, le=200)


async def _load_settings_map(db_url: str) -> dict[str, str]:
    async with get_session(db_url) as db:
        result = await db.execute(select(UserSettingsRow))
        return {row.key: row.value for row in result.scalars().all()}


def _build_response(data: dict[str, str]) -> UserSettingsResponse:
    def _get(key: str) -> str:
        return data.get(key, _SETTINGS_DEFAULTS[key])

    return UserSettingsResponse(
        default_skill_id=_get("default_skill_id"),
        global_instruction=_get("global_instruction"),
        temperature=float(_get("temperature")),
        rag_top_k=int(_get("rag_top_k")),
        context_max_messages=int(_get("context_max_messages")),
    )


@router.get("/", response_model=UserSettingsResponse)
async def get_settings() -> UserSettingsResponse:
    data = await _load_settings_map(_db_url())
    return _build_response(data)


@router.put("/", response_model=UserSettingsResponse)
async def update_settings(body: UserSettingsUpdate) -> UserSettingsResponse:
    patch: dict[str, str] = {
        k: str(v)
        for k, v in body.model_dump().items()
        if v is not None and k in _SETTINGS_KEYS
    }
    async with get_session(_db_url()) as db:
        for key, value in patch.items():
            row = await db.get(UserSettingsRow, key)
            if row is None:
                db.add(UserSettingsRow(key=key, value=value, updated_at=datetime.now(UTC)))
            else:
                row.value = value
                row.updated_at = datetime.now(UTC)
        await db.commit()

    data = await _load_settings_map(_db_url())
    return _build_response(data)
