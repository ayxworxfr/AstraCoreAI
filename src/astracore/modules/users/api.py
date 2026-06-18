"""User management API (admin only)."""

from datetime import UTC, datetime
from functools import lru_cache
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from astracore.infrastructure.db.models import UserRow
from astracore.infrastructure.db.session import get_session
from astracore.modules.auth.dependencies import require_admin
from astracore.modules.auth.domain import hash_password
from astracore.sdk.config import AstraCoreConfig

router = APIRouter()


@lru_cache(maxsize=1)
def _db_url() -> str:
    return AstraCoreConfig().storage.db_url


class UserResponse(BaseModel):
    id: str
    username: str
    role: str
    is_active: bool
    created_at: str
    updated_at: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class PatchUserRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None


def _to_response(user: UserRow) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at.isoformat(),
        updated_at=user.updated_at.isoformat(),
    )


@router.get("/", response_model=list[UserResponse])
async def list_users(_: UserRow = Depends(require_admin)) -> list[UserResponse]:
    async with get_session(_db_url()) as db:
        result = await db.execute(select(UserRow).order_by(UserRow.created_at))
        return [_to_response(u) for u in result.scalars()]


@router.post("/", response_model=UserResponse, status_code=201)
async def create_user(body: CreateUserRequest, _: UserRow = Depends(require_admin)) -> UserResponse:
    async with get_session(_db_url()) as db:
        existing = await db.execute(select(UserRow).where(UserRow.username == body.username))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")

        now = datetime.now(UTC)
        user = UserRow(
            id=str(uuid4()),
            username=body.username,
            hashed_password=hash_password(body.password),
            role=body.role if body.role in {"admin", "user"} else "user",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        await db.commit()
    return _to_response(user)


@router.patch("/{user_id}", response_model=UserResponse)
async def patch_user(
    user_id: str,
    body: PatchUserRequest,
    _: UserRow = Depends(require_admin),
) -> UserResponse:
    async with get_session(_db_url()) as db:
        result = await db.execute(select(UserRow).where(UserRow.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")

        if body.role is not None and body.role in {"admin", "user"}:
            user.role = body.role
        if body.is_active is not None:
            user.is_active = body.is_active
        if body.password:
            user.hashed_password = hash_password(body.password)
        user.updated_at = datetime.now(UTC)
        await db.commit()
    return _to_response(user)


@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: str, admin: UserRow = Depends(require_admin)) -> None:
    if user_id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能删除自己的账户")
    async with get_session(_db_url()) as db:
        result = await db.execute(select(UserRow).where(UserRow.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        await db.delete(user)
        await db.commit()
