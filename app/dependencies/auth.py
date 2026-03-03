"""
IMPREZA Security: Авторизация через API Key и/или JWT.

Каждый эндпоинт получает Depends(...) из этого модуля:
  - require_auth         → API Key ИЛИ JWT (любая роль)
  - require_role("manager")  → JWT с ролью >= manager
  - require_admin        → JWT с ролью super
  - get_optional_auth    → если есть токен — парсим, если нет — None (для публичных)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

logger = logging.getLogger("impreza.security")

# ────────────────────────────────────────────
# Роли (от низшей к высшей)
# ────────────────────────────────────────────
ROLE_HIERARCHY = {
    "observer": 0,
    "country_manager": 1,
    "manager": 2,
    "scanner": 2,          # сканер ≈ менеджер по правам
    "super_observer": 3,
    "super": 4,
}

JWT_ALGORITHM = "HS256"

# Необязательный bearer — не бросает 403 если нет заголовка
_optional_bearer = HTTPBearer(auto_error=False)


# ────────────────────────────────────────────
# Внутренние хелперы
# ────────────────────────────────────────────

def _verify_api_key(request: Request) -> bool:
    """Проверяет X-API-Key заголовок."""
    api_key = request.headers.get("X-API-Key", "")
    if not api_key or not settings.INTERNAL_API_KEY:
        return False
    return api_key == settings.INTERNAL_API_KEY


def _decode_jwt(token: str) -> Optional[dict]:
    """Декодирует и валидирует JWT. Возвращает payload или None."""
    try:
        payload = jwt.decode(
            token,
            settings.API_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
        )
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


class AuthInfo:
    """Результат аутентификации — передаётся дальше в endpoint."""
    __slots__ = ("auth_type", "role", "name", "allowed_countries", "club_id")

    def __init__(
        self,
        auth_type: str,          # "api_key" | "jwt"
        role: str = "super",     # роль пользователя
        name: str | None = None,
        allowed_countries: list | None = None,
        club_id: int | None = None,
    ):
        self.auth_type = auth_type
        self.role = role
        self.name = name
        self.allowed_countries = allowed_countries
        self.club_id = club_id

    @property
    def role_level(self) -> int:
        return ROLE_HIERARCHY.get(self.role, 0)


# ────────────────────────────────────────────
# Public dependencies (для use в Depends())
# ────────────────────────────────────────────

async def require_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
) -> AuthInfo:
    """
    Требует ЛИБО валидный API Key ЛИБО валидный JWT.
    Возвращает AuthInfo с описанием вызывающего.
    """
    # 1. API Key
    if _verify_api_key(request):
        return AuthInfo(auth_type="api_key", role="super")

    # 2. JWT из Authorization: Bearer ...
    if credentials and credentials.credentials:
        payload = _decode_jwt(credentials.credentials)
        if payload:
            return AuthInfo(
                auth_type="jwt",
                role=payload.get("role", "observer"),
                name=payload.get("name"),
                allowed_countries=payload.get("allowed_countries"),
                club_id=payload.get("club_id"),
            )

    # 3. Ничего не подошло
    logger.warning(
        "Unauthorized access attempt: %s %s from %s",
        request.method,
        request.url.path,
        request.client.host if request.client else "unknown",
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required (API Key or JWT)",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_optional_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
) -> Optional[AuthInfo]:
    """Как require_auth, но НЕ бросает 401 — возвращает None если нет auth."""
    if _verify_api_key(request):
        return AuthInfo(auth_type="api_key", role="super")
    if credentials and credentials.credentials:
        payload = _decode_jwt(credentials.credentials)
        if payload:
            return AuthInfo(
                auth_type="jwt",
                role=payload.get("role", "observer"),
                name=payload.get("name"),
                allowed_countries=payload.get("allowed_countries"),
                club_id=payload.get("club_id"),
            )
    return None


def require_role(min_role: str):
    """
    Фабрика dependency: требует JWT (не API Key) с ролью >= min_role.
    API Key тоже пропускается (считается super).
    
    Использование:
        @router.delete("/things", dependencies=[Depends(require_role("super"))])
    """
    min_level = ROLE_HIERARCHY.get(min_role, 0)

    async def _check(auth: AuthInfo = Depends(require_auth)) -> AuthInfo:
        if auth.role_level < min_level:
            logger.warning(
                "Insufficient role: user role=%s, required=%s",
                auth.role,
                min_role,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role '{min_role}' or higher",
            )
        return auth

    return _check


# Удобные шорткаты
require_manager = require_role("manager")
require_admin = require_role("super")
