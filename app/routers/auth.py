"""
IMPREZA: Эндпоинт авторизации для сканеров.
Возвращает JWT токен при успешной авторизации.
"""
import logging
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.config import settings
from app.database import get_db

logger = logging.getLogger("impreza.security")

router = APIRouter(prefix="/auth", tags=["auth"])

JWT_ALGORITHM = "HS256"
SCANNER_TOKEN_HOURS = 24

class LoginRequest(BaseModel):
    login: str
    password_hash: str

@router.post("/login")
def login(credentials: LoginRequest):
    """Проверка логина/пароля клуба → возвращает JWT токен"""
    db = next(get_db())
    
    try:
        # Найти клуб
        query = text("""
            SELECT c.club_id, c.city_name, co.country_code, c.city_english
            FROM clubs c
            JOIN countries co ON c.country_id = co.country_id
            WHERE c.login = :login 
            AND c.password_hash = :password_hash 
            AND c.is_active = TRUE
        """)
        
        result = db.execute(query, {"login": credentials.login, "password_hash": credentials.password_hash})
        club = result.fetchone()
        
        if not club:
            logger.warning("Failed scanner login attempt: login=%s", credentials.login)
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        # Генерируем JWT токен для сканера
        payload = {
            "club_id": club[0],
            "role": "scanner",
            "name": club[1],
            "city_english": club[3],
            "allowed_countries": [club[2]],
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=SCANNER_TOKEN_HOURS),
        }
        token = jwt.encode(payload, settings.API_SECRET_KEY, algorithm=JWT_ALGORITHM)
        
        logger.info("Scanner login success: club_id=%s, city=%s", club[0], club[1])
        
        return {
            "club_id": club[0],
            "city_name": club[1],
            "country_code": club[2],
            "city_english": club[3],
            "token": token,
        }
    
    finally:
        db.close()
