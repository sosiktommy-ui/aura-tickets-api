"""
IMPREZA: Эндпоинт для получения списка всех клубов/городов
"""
import logging

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import text
from app.database import get_db
from app.dependencies.auth import require_auth, require_role, AuthInfo

logger = logging.getLogger("impreza.security")

router = APIRouter(prefix="/clubs", tags=["clubs"])


@router.get("/")
def get_all_clubs(auth: AuthInfo = Depends(require_auth)):
    """Получить список всех клубов для админ панели.
    plain_password возвращается только для super admin."""
    db = next(get_db())
    is_super = auth.role == "super"
    
    try:
        if is_super:
            query = text("""
                SELECT 
                    c.club_id as id,
                    c.city_name,
                    c.city_english,
                    co.country_code,
                    c.login,
                    c.is_active,
                    c.plain_password
                FROM clubs c
                JOIN countries co ON c.country_id = co.country_id
                ORDER BY co.country_code, c.city_name
            """)
        else:
            query = text("""
                SELECT 
                    c.club_id as id,
                    c.city_name,
                    c.city_english,
                    co.country_code,
                    c.login,
                    c.is_active
                FROM clubs c
                JOIN countries co ON c.country_id = co.country_id
                ORDER BY co.country_code, c.city_name
            """)
        
        result = db.execute(query)
        clubs = []
        
        for row in result:
            club = {
                "id": row[0],
                "city_name": row[1],
                "city_english": row[2],
                "country_code": row[3],
                "login": row[4],
                "is_active": row[5],
            }
            if is_super and len(row) > 6:
                club["plain_password"] = row[6]
            clubs.append(club)
        
        return {"clubs": clubs}
    
    finally:
        db.close()


@router.get("/{club_id}")
def get_club_by_id(club_id: int, auth: AuthInfo = Depends(require_auth)):
    """Получить информацию о конкретном клубе по ID. Пароли НЕ возвращаются."""
    db = next(get_db())
    
    try:
        query = text("""
            SELECT 
                c.club_id as id,
                c.city_name,
                c.city_english,
                co.country_code,
                c.login,
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
            "is_active": row[5],
        }
    
    finally:
        db.close()


@router.put("/{club_id}/password")
def update_club_password(club_id: int, data: dict, auth: AuthInfo = Depends(require_role("super"))):
    """Обновить пароль клуба (только для super admin).
    Принимает:
      - {new_password: "..."} — хеш считается на сервере (веб-панель)
      - {password_hash: "...", plain_password: "..."} — старый формат (десктоп)
    """
    import hashlib
    logger.info("Club %s password updated by %s", club_id, auth.name)
    db = next(get_db())
    
    try:
        new_password = data.get("new_password")
        if new_password:
            # Веб-панель: приходит простой пароль, хешируем на сервере
            password_hash = hashlib.sha256(new_password.encode()).hexdigest()
            plain_password = new_password
        else:
            # Десктоп: приходит готовый хеш
            password_hash = data.get("password_hash")
            plain_password = data.get("plain_password")

        if not password_hash:
            raise HTTPException(status_code=400, detail="new_password or password_hash required")
        
        query = text("""
            UPDATE clubs 
            SET password_hash = :password_hash,
                plain_password = :plain_password
            WHERE club_id = :club_id
        """)
        
        db.execute(query, {
            "password_hash": password_hash, 
            "plain_password": plain_password,
            "club_id": club_id
        })
        db.commit()
        
        return {"success": True, "message": "Password updated"}
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")
    
    finally:
        db.close()
