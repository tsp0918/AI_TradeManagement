"""認証ルーター。ローカルJWT + Google/Microsoft SSO対応。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from jose import JWTError
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.auth.jwt import (
    create_access_token,
    create_refresh_token,
    verify_refresh_token,
)
from platform_core.auth.password import hash_password, verify_password
from platform_core.config import settings
from platform_core.db.session import get_db
from platform_core.models.user import AuthProvider, User

router = APIRouter(prefix="/auth", tags=["auth"])


# --- Schemas ---

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


# --- Local Auth ---

@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None or user.hashed_password is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    user.last_login_at = datetime.now(UTC)
    await db.commit()

    return TokenResponse(
        access_token=create_access_token(user.id, user.tenant_id, user.role),
        refresh_token=create_refresh_token(user.id, user.tenant_id),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    try:
        payload = verify_refresh_token(body.refresh_token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    import uuid
    user_id = uuid.UUID(payload["sub"])
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
        )

    return TokenResponse(
        access_token=create_access_token(user.id, user.tenant_id, user.role),
        refresh_token=create_refresh_token(user.id, user.tenant_id),
    )


# --- Google OAuth2 SSO ---

@router.get("/google/login")
async def google_login() -> RedirectResponse:
    """Google OAuth2 認証フローを開始する。"""
    if not settings.google_client_id:
        raise HTTPException(status_code=501, detail="Google SSO not configured")

    params = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.google_client_id}"
        f"&redirect_uri={settings.google_redirect_uri}"
        "&response_type=code"
        "&scope=openid%20email%20profile"
        "&access_type=offline"
        "&prompt=select_account"
    )
    return RedirectResponse(url=params)


@router.get("/google/callback")
async def google_callback(code: str, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """Google から認可コードを受け取り、ユーザーを取得またはプロビジョニングする。"""
    import httpx

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()

        userinfo_resp = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        userinfo_resp.raise_for_status()
        userinfo = userinfo_resp.json()

    user = await _get_or_provision_sso_user(
        db=db,
        email=userinfo["email"],
        full_name=userinfo.get("name", userinfo["email"]),
        oauth_sub=userinfo["sub"],
        provider=AuthProvider.GOOGLE,
    )

    return TokenResponse(
        access_token=create_access_token(user.id, user.tenant_id, user.role),
        refresh_token=create_refresh_token(user.id, user.tenant_id),
    )


# --- Microsoft OAuth2 SSO ---

@router.get("/microsoft/login")
async def microsoft_login() -> RedirectResponse:
    if not settings.microsoft_client_id:
        raise HTTPException(status_code=501, detail="Microsoft SSO not configured")

    base = f"https://login.microsoftonline.com/{settings.microsoft_tenant_id}/oauth2/v2.0/authorize"
    params = (
        f"{base}"
        f"?client_id={settings.microsoft_client_id}"
        f"&redirect_uri={settings.microsoft_redirect_uri}"
        "&response_type=code"
        "&scope=openid%20email%20profile%20User.Read"
        "&response_mode=query"
    )
    return RedirectResponse(url=params)


@router.get("/microsoft/callback")
async def microsoft_callback(code: str, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    import httpx

    token_url = f"https://login.microsoftonline.com/{settings.microsoft_tenant_id}/oauth2/v2.0/token"

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            token_url,
            data={
                "code": code,
                "client_id": settings.microsoft_client_id,
                "client_secret": settings.microsoft_client_secret,
                "redirect_uri": settings.microsoft_redirect_uri,
                "grant_type": "authorization_code",
                "scope": "openid email profile User.Read",
            },
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()

        userinfo_resp = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        userinfo_resp.raise_for_status()
        userinfo = userinfo_resp.json()

    email = userinfo.get("mail") or userinfo.get("userPrincipalName", "")
    user = await _get_or_provision_sso_user(
        db=db,
        email=email,
        full_name=userinfo.get("displayName", email),
        oauth_sub=userinfo["id"],
        provider=AuthProvider.MICROSOFT,
    )

    return TokenResponse(
        access_token=create_access_token(user.id, user.tenant_id, user.role),
        refresh_token=create_refresh_token(user.id, user.tenant_id),
    )


# --- Helper ---

async def _get_or_provision_sso_user(
    db: AsyncSession,
    email: str,
    full_name: str,
    oauth_sub: str,
    provider: AuthProvider,
) -> User:
    """SSO ユーザーを取得。存在しなければ新規プロビジョニング。"""
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None:
        # 新規ユーザー: テナントは最初のアクティブなテナントに仮紐付け
        # 実運用では招待フローやドメインマッチングで決定する
        from platform_core.models.tenant import Tenant
        tenant_result = await db.execute(
            select(Tenant).where(Tenant.is_active == True).limit(1)
        )
        tenant = tenant_result.scalar_one_or_none()
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No active tenant found. Contact your administrator.",
            )

        user = User(
            tenant_id=tenant.id,
            email=email,
            full_name=full_name,
            oauth_sub=oauth_sub,
            auth_provider=provider,
        )
        db.add(user)
        await db.flush()

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    user.last_login_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(user)
    return user
