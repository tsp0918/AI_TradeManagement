"""JWT トークン発行・検証。"""

import uuid
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt

from platform_core.config import settings


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: str,
    expire_minutes: int | None = None,
) -> str:
    expire = _now() + timedelta(
        minutes=expire_minutes or settings.jwt_access_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "tenant": str(tenant_id),
        "role": role,
        "type": "access",
        "exp": expire,
        "iat": _now(),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: uuid.UUID, tenant_id: uuid.UUID) -> str:
    expire = _now() + timedelta(days=settings.jwt_refresh_token_expire_days)
    payload = {
        "sub": str(user_id),
        "tenant": str(tenant_id),
        "type": "refresh",
        "exp": expire,
        "iat": _now(),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """トークンを検証してペイロードを返す。失敗時は JWTError を raise。"""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


def verify_access_token(token: str) -> dict:
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise JWTError("Not an access token")
    return payload


def verify_refresh_token(token: str) -> dict:
    payload = decode_token(token)
    if payload.get("type") != "refresh":
        raise JWTError("Not a refresh token")
    return payload
