"""Authentication API: register, login, me."""

from datetime import UTC, datetime
from functools import lru_cache
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import func, select

from astracore.infrastructure.db.models import UserRow
from astracore.infrastructure.db.session import get_session
from astracore.modules.auth.dependencies import get_current_user
from astracore.modules.auth.domain import create_access_token, hash_password, verify_password
from astracore.sdk.config import AstraCoreConfig

router = APIRouter()


@lru_cache(maxsize=1)
def _cfg() -> AstraCoreConfig:
    return AstraCoreConfig()


@lru_cache(maxsize=1)
def _db_url() -> str:
    return _cfg().memory.db_url


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RegisterRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: str
    username: str
    role: str
    is_active: bool
    created_at: str


def _user_to_response(user: UserRow) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at.isoformat(),
    )


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(body: RegisterRequest) -> UserResponse:
    cfg = _cfg()
    async with get_session(_db_url()) as db:
        user_count_result = await db.execute(select(func.count()).select_from(UserRow))
        user_count: int = user_count_result.scalar_one()

        if user_count > 0 and not cfg.auth.allow_registration:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="注册已关闭，请联系管理员创建账户",
            )

        existing = await db.execute(select(UserRow).where(UserRow.username == body.username))
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="用户名已存在",
            )

        role = "admin" if user_count == 0 else "user"
        now = datetime.now(UTC)
        user = UserRow(
            id=str(uuid4()),
            username=body.username,
            hashed_password=hash_password(body.password),
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        await db.commit()
    return _user_to_response(user)


@router.post("/login", response_model=TokenResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()) -> TokenResponse:
    cfg = _cfg()
    async with get_session(_db_url()) as db:
        result = await db.execute(select(UserRow).where(UserRow.username == form_data.username))
        user = result.scalar_one_or_none()

    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账户已停用")

    token = create_access_token(user.id, user.username, user.role, cfg.auth)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: UserRow = Depends(get_current_user)) -> UserResponse:
    return _user_to_response(current_user)
