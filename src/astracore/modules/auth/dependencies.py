"""FastAPI dependencies for authentication and authorization."""

from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select

from astracore.infrastructure.db.models import UserRow
from astracore.infrastructure.db.session import get_session
from astracore.modules.auth.domain import decode_token
from astracore.sdk.config import AstraCoreConfig, AuthConfig


@lru_cache(maxsize=1)
def _get_auth_config() -> AuthConfig:
    return AstraCoreConfig().auth


@lru_cache(maxsize=1)
def _get_db_url() -> str:
    return AstraCoreConfig().memory.db_url


async def get_current_user(
    authorization: str | None = Header(default=None),
) -> UserRow:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_token(token, _get_auth_config())
    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效令牌")

    async with get_session(_get_db_url()) as db:
        result = await db.execute(select(UserRow).where(UserRow.id == user_id))
        user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已停用",
        )
    return user


async def require_admin(
    current_user: UserRow = Depends(get_current_user),
) -> UserRow:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return current_user
