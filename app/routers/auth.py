"""
IMPREZA: Эндпоинт авторизации для сканеров
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from app.database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

class LoginRequest(BaseModel):
    login: str
    password_hash: str

@router.post("/login")
def login(credentials: LoginRequest):
    """Проверка логина/пароля клуба"""
    db = next(get_db())
    
    try:
        # Найти клуб
        query = text("""
            SELECT c.club_id, c.city_name, co.country_code
            FROM clubs c
            JOIN countries co ON c.country_id = co.country_id
            WHERE c.login = :login 
            AND c.password_hash = :password_hash 
            AND c.is_active = TRUE
        """)
        
        result = db.execute(query, {"login": credentials.login, "password_hash": credentials.password_hash})
        club = result.fetchone()
        
        if not club:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        return {
            "club_id": club[0],
            "city_name": club[1],
            "country_code": club[2]
        }
    
    finally:
        db.close()
