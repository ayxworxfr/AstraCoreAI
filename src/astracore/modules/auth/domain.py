"""Authentication domain: password hashing and JWT token management."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status

from astracore.sdk.config import AuthConfig

_ALGORITHM = "HS256"


def hash_password(plain: str) -> str:
    import bcrypt  # noqa: PLC0415

    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    import bcrypt  # noqa: PLC0415

    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(user_id: str, username: str, role: str, cfg: AuthConfig) -> str:
    from jose import jwt  # type: ignore[import-untyped]  # noqa: PLC0415

    expire = datetime.now(UTC) + timedelta(days=cfg.token_expire_days)
    payload: dict[str, Any] = {
        "sub": user_id,
        "username": username,
        "role": role,
        "exp": expire,
    }
    return str(jwt.encode(payload, cfg.secret_key, algorithm=_ALGORITHM))


def decode_token(token: str, cfg: AuthConfig) -> dict[str, Any]:
    from jose import JWTError, jwt  # noqa: PLC0415

    try:
        return dict(jwt.decode(token, cfg.secret_key, algorithms=[_ALGORITHM]))
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或已过期的令牌",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
