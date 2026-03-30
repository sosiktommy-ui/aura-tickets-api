"""
IMPREZA Admin Panel - Server-side authentication
JWT-based login for the web admin panel.
Passwords loaded from ADMIN_PASSWORDS env variable.
"""

import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt

from app.config import settings
from app.dependencies.auth import require_auth, require_role, AuthInfo

logger = logging.getLogger("impreza.security")

router = APIRouter(prefix="/api/admin", tags=["admin-auth"])

# ─── Password -> Role mapping loaded from env ───
# Env ADMIN_PASSWORDS format (JSON):
# {
#   "super": ["pass1", "pass2"],
#   "super_observer": ["pass3"],
#   "manager": ["pass4"],
#   "observer": ["pass5", "pass6"],
#   "country_manager": {
#     "pass7": {"name": "Катя", "countries": ["BG", "NL", "DE"]},
#     "pass8": {"name": "Фёдор", "countries": ["PL"]}
#   }
# }

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24


def _get_passwords() -> dict:
    """Load passwords from env. Returns parsed dict."""
    return settings.get_admin_passwords()


# Заблокированные пароли — НИКОГДА не принимаются, даже если остались в env
DENIED_PASSWORDS = {
    "ImprezaMaster2025",
}


def check_role(password: str):
    """Определить роль по паролю из env-переменной ADMIN_PASSWORDS."""
    if password in DENIED_PASSWORDS:
        logger.warning("Blocked login attempt with revoked password")
        return None

    pw_config = _get_passwords()
    if not pw_config:
        return None

    # super passwords
    if password in pw_config.get("super", []):
        return "super"

    # super_observer
    if password in pw_config.get("super_observer", []):
        return "super_observer"

    # manager
    if password in pw_config.get("manager", []):
        return "manager"

    # observer
    if password in pw_config.get("observer", []):
        return "observer"

    # country_manager (dict of password -> {name, countries})
    cm = pw_config.get("country_manager", {})
    if isinstance(cm, dict) and password in cm:
        return "country_manager"

    return None


def _get_all_manager_passwords() -> list[str]:
    """Return list of all passwords that count as manager-level (for check-password)."""
    pw_config = _get_passwords()
    result = []
    result.extend(pw_config.get("super", []))
    result.extend(pw_config.get("manager", []))
    cm = pw_config.get("country_manager", {})
    if isinstance(cm, dict):
        result.extend(cm.keys())
    return [p for p in result if p not in DENIED_PASSWORDS]


def create_jwt_token(payload: dict) -> str:
    """Create a JWT token with expiry."""
    data = payload.copy()
    data["exp"] = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS)
    data["iat"] = datetime.now(timezone.utc)
    return jwt.encode(data, settings.API_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_jwt_token(token: str):
    """Verify and decode a JWT token. Returns payload or None."""
    try:
        payload = jwt.decode(token, settings.API_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# Schemas

class AdminLoginRequest(BaseModel):
    password: str


class AdminLoginResponse(BaseModel):
    role: str
    token: str
    name: Optional[str] = None
    allowed_countries: Optional[list] = None


class AdminVerifyResponse(BaseModel):
    valid: bool
    role: Optional[str] = None
    name: Optional[str] = None
    allowed_countries: Optional[list] = None


class PasswordCheckRequest(BaseModel):
    password: str


class PasswordCheckResponse(BaseModel):
    valid: bool
    is_manager: bool = False


# Endpoints

@router.post("/login", response_model=AdminLoginResponse)
def admin_login(req: AdminLoginRequest):
    """
    Authenticate admin user by password.
    Returns JWT token + role + optional country manager metadata.
    """
    role = check_role(req.password)
    if role is None:
        logger.warning("Failed admin login attempt from password: ***")
        raise HTTPException(status_code=401, detail="Неверный пароль")

    payload = {"role": role}
    name = None
    allowed_countries = None

    if role == "country_manager":
        pw_config = _get_passwords()
        cm = pw_config.get("country_manager", {})
        account = cm.get(req.password, {})
        name = account.get("name")
        allowed_countries = account.get("countries")
        payload["name"] = name
        payload["allowed_countries"] = allowed_countries

    token = create_jwt_token(payload)

    return AdminLoginResponse(
        role=role,
        token=token,
        name=name,
        allowed_countries=allowed_countries,
    )


@router.get("/verify", response_model=AdminVerifyResponse)
def admin_verify_get(token: str = ""):
    """
    GET endpoint to verify JWT token validity.
    Used by frontend on page load to check if session is still active.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Token required")

    payload = verify_jwt_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Token expired or invalid")

    return AdminVerifyResponse(
        valid=True,
        role=payload.get("role"),
        name=payload.get("name"),
        allowed_countries=payload.get("allowed_countries"),
    )


@router.post("/check-password", response_model=PasswordCheckResponse)
def admin_check_password(
    req: PasswordCheckRequest,
    auth: AuthInfo = Depends(require_auth),
):
    """
    Check if a password is a valid manager-level password.
    Used for confirming dangerous operations (delete, status change, etc.)
    Requires authenticated session (JWT or API Key).
    """
    manager_passwords = _get_all_manager_passwords()
    is_valid = req.password in manager_passwords
    return PasswordCheckResponse(valid=is_valid, is_manager=is_valid)
