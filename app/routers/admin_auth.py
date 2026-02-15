"""
IMPREZA Admin Panel - Server-side authentication
JWT-based login for the web admin panel.
Replicates check_role() logic from admin_panel.py exactly.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt

from app.config import settings

router = APIRouter(prefix="/api/admin", tags=["admin-auth"])

# Password -> Role mapping (exact copy from admin_panel.py lines 145-180)

SUPER_PASSWORDS = ["LotusCore88Admin", "ImprezaMaster2025"]
SUPER_OBSERVER_PASSWORD = "SuperView2025Archive"
MANAGER_PASSWORD = "Coffee!8Night"
OBSERVER_PASSWORDS = ["ObserverView2025", "WatchOnly2025"]

COUNTRY_MANAGER_ACCOUNTS = {
    "KatyaBG2025!": {
        "name": "Катя",
        "countries": ["BG", "NL", "DE"],
    },
    "FedorPL2025!": {
        "name": "Фёдор",
        "countries": ["PL"],
    },
}

ALL_MANAGER_PASSWORDS = SUPER_PASSWORDS + [MANAGER_PASSWORD] + list(COUNTRY_MANAGER_ACCOUNTS.keys())

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24


def check_role(password: str):
    """Определить роль по паролю - точная копия из admin_panel.py"""
    if password in SUPER_PASSWORDS:
        return "super"
    elif password == SUPER_OBSERVER_PASSWORD:
        return "super_observer"
    elif password == MANAGER_PASSWORD:
        return "manager"
    elif password in OBSERVER_PASSWORDS:
        return "observer"
    elif password in COUNTRY_MANAGER_ACCOUNTS:
        return "country_manager"
    return None


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
        raise HTTPException(status_code=401, detail="Неверный пароль")

    payload = {"role": role}
    name = None
    allowed_countries = None

    if role == "country_manager":
        account = COUNTRY_MANAGER_ACCOUNTS[req.password]
        name = account["name"]
        allowed_countries = account["countries"]
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
def admin_check_password(req: PasswordCheckRequest):
    """
    Check if a password is a valid manager-level password.
    Used for confirming dangerous operations (delete, status change, etc.)
    """
    is_valid = req.password in ALL_MANAGER_PASSWORDS
    return PasswordCheckResponse(valid=is_valid, is_manager=is_valid)
