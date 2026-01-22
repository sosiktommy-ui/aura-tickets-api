"""
IMPREZA: Эндпоинт для получения списка всех клубов/городов
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from app.database import get_db

router = APIRouter(prefix="/clubs", tags=["clubs"])

@router.get("/")
def get_all_clubs():
    """Получить список всех клубов для админ панели"""
    db = next(get_db())
    
    try:
        query = text("""
            SELECT 
                c.club_id as id,
                c.city_name,
                c.city_english,
                co.country_code,
                c.login,
                c.password_hash,
                c.is_active
            FROM clubs c
            JOIN countries co ON c.country_id = co.country_id
            ORDER BY co.country_code, c.city_name
        """)
        
        result = db.execute(query)
        clubs = []
        
        for row in result:
            clubs.append({
                "id": row[0],
                "city_name": row[1],
                "city_english": row[2],
                "country_code": row[3],
                "login": row[4],
                "password_hash": row[5],
                "is_active": row[6]
            })
        
        return {"clubs": clubs}
    
    finally:
        db.close()


@router.get("/{club_id}")
def get_club_by_id(club_id: int):
    """Получить информацию о конкретном клубе по ID"""
    db = next(get_db())
    
    try:
        query = text("""
            SELECT 
                c.club_id as id,
                c.city_name,
                c.city_english,
                co.country_code,
                c.login,
                c.password_hash,
                c.is_active
            FROM clubs c
            JOIN countries co ON c.country_id = co.country_id
            WHERE c.club_id = :club_id
        """)
        
        result = db.execute(query, {"club_id": club_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Club not found")
        
        return {
            "id": row[0],
            "city_name": row[1], 
            "city_english": row[2],
            "country_code": row[3],
            "login": row[4],
            "password": row[5],  # Возвращаем как "password" для совместимости
            "is_active": row[6]
        }
    
    finally:
        db.close()


@router.put("/{club_id}/password")
def update_club_password(club_id: int, data: dict):
    """Обновить пароль клуба (только для админа)"""
    db = next(get_db())
    
    try:
        password_hash = data.get("password_hash")
        if not password_hash:
            raise HTTPException(status_code=400, detail="password_hash required")
        
        query = text("""
            UPDATE clubs 
            SET password_hash = :password_hash 
            WHERE club_id = :club_id
        """)
        
        db.execute(query, {"password_hash": password_hash, "club_id": club_id})
        db.commit()
        
        return {"success": True, "message": "Password updated"}
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        db.close()
