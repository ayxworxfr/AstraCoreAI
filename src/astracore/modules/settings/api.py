"""User settings API endpoints."""

from datetime import UTC, datetime
from functools import lru_cache

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from astracore.infrastructure.db.models import UserRow, UserSettingsRow
from astracore.infrastructure.db.session import get_session
from astracore.modules.auth.dependencies import get_current_user
from astracore.sdk.config import AstraCoreConfig

router = APIRouter()

_SETTINGS_KEYS = {
    "global_instruction",
    "temperature",
    "rag_top_k",
    "context_max_messages",
    "ai_name",
    "owner_name",
    "timezone",
    "thinking_collapse_mode",
}

_SETTINGS_DEFAULTS: dict[str, str] = {
    "global_instruction": "",
    "temperature": "0.7",
    "rag_top_k": "4",
    "context_max_messages": "20",
    "ai_name": "小卡",
    "owner_name": "",
    "timezone": "Asia/Shanghai",
    "thinking_collapse_mode": "auto",
}


@lru_cache(maxsize=1)
def _db_url() -> str:
    return AstraCoreConfig().memory.db_url


class UserSettingsResponse(BaseModel):
    global_instruction: str = ""
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    rag_top_k: int = Field(default=4, ge=1, le=20)
    context_max_messages: int = Field(default=20, ge=4, le=200)
    ai_name: str = "小卡"
    owner_name: str = ""
    timezone: str = "Asia/Shanghai"
    thinking_collapse_mode: str = "auto"


class UserSettingsUpdate(BaseModel):
    global_instruction: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    rag_top_k: int | None = Field(default=None, ge=1, le=20)
    context_max_messages: int | None = Field(default=None, ge=4, le=200)
    ai_name: str | None = None
    owner_name: str | None = None
    timezone: str | None = None
    thinking_collapse_mode: str | None = None


async def _load_settings_map(db_url: str, user_id: str) -> dict[str, str]:
    async with get_session(db_url) as db:
        result = await db.execute(select(UserSettingsRow).where(UserSettingsRow.user_id == user_id))
        return {row.key: row.value for row in result.scalars().all()}


def _build_response(data: dict[str, str]) -> UserSettingsResponse:
    def _get(key: str) -> str:
        return data.get(key, _SETTINGS_DEFAULTS[key])

    return UserSettingsResponse(
        global_instruction=_get("global_instruction"),
        temperature=float(_get("temperature")),
        rag_top_k=int(_get("rag_top_k")),
        context_max_messages=int(_get("context_max_messages")),
        ai_name=_get("ai_name"),
        owner_name=_get("owner_name"),
        timezone=_get("timezone"),
        thinking_collapse_mode=_get("thinking_collapse_mode"),
    )


@router.get("/", response_model=UserSettingsResponse)
async def get_settings(
    current_user: UserRow = Depends(get_current_user),
) -> UserSettingsResponse:
    data = await _load_settings_map(_db_url(), current_user.id)
    return _build_response(data)


@router.put("/", response_model=UserSettingsResponse)
async def update_settings(
    body: UserSettingsUpdate,
    current_user: UserRow = Depends(get_current_user),
) -> UserSettingsResponse:
    patch: dict[str, str] = {
        k: str(v) for k, v in body.model_dump().items() if v is not None and k in _SETTINGS_KEYS
    }
    async with get_session(_db_url()) as db:
        for key, value in patch.items():
            row = await db.get(UserSettingsRow, {"user_id": current_user.id, "key": key})
            if row is None:
                db.add(
                    UserSettingsRow(
                        user_id=current_user.id,
                        key=key,
                        value=value,
                        updated_at=datetime.now(UTC),
                    )
                )
            else:
                row.value = value
                row.updated_at = datetime.now(UTC)
        await db.commit()

    data = await _load_settings_map(_db_url(), current_user.id)
    return _build_response(data)
