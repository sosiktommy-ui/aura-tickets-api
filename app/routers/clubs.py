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


@router.get("/countries")
def get_countries(auth: AuthInfo = Depends(require_auth)):
    """Получить список всех стран."""
    db = next(get_db())
    try:
        rows = db.execute(text(
            "SELECT country_id, country_code, country_name FROM countries ORDER BY country_name"
        )).fetchall()
        return {"countries": [{"country_id": r[0], "country_code": r[1], "country_name": r[2]} for r in rows]}
    finally:
        db.close()


@router.post("/", status_code=201)
def create_club(data: dict, auth: AuthInfo = Depends(require_role("super"))):
    """Создать новый клуб/город (только super admin)."""
    import hashlib

    country_id = data.get("country_id")
    city_name = (data.get("city_name") or "").strip()
    city_english = (data.get("city_english") or "").strip()
    login = (data.get("login") or "").strip()
    plain_password = (data.get("plain_password") or "").strip()
    is_active = data.get("is_active", True)

    if not all([country_id, city_name, city_english, login, plain_password]):
        raise HTTPException(400, "country_id, city_name, city_english, login and plain_password are required")

    password_hash = hashlib.sha256(plain_password.encode()).hexdigest()

    db = next(get_db())
    try:
        # Check login uniqueness
        existing = db.execute(text("SELECT club_id FROM clubs WHERE login = :login"), {"login": login}).fetchone()
        if existing:
            raise HTTPException(400, f"Login '{login}' already exists")

        row = db.execute(text("""
            INSERT INTO clubs (country_id, city_name, city_english, login, password_hash, plain_password, is_active)
            VALUES (:cid, :city, :city_en, :login, :ph, :pp, :active)
            RETURNING club_id, city_name, city_english, login, is_active
        """), {
            "cid": country_id,
            "city": city_name,
            "city_en": city_english,
            "login": login,
            "ph": password_hash,
            "pp": plain_password,
            "active": is_active,
        }).fetchone()
        db.commit()
        logger.info("Club created: id=%s login=%s by %s", row[0], row[3], auth.name)

        # Fetch country_code for response
        cc_row = db.execute(text("SELECT country_code FROM countries WHERE country_id = :id"), {"id": country_id}).fetchone()
        country_code = cc_row[0] if cc_row else ""

        return {
            "id": row[0],
            "city_name": row[1],
            "city_english": row[2],
            "country_code": country_code,
            "login": row[3],
            "is_active": row[4],
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Failed to create club: {e}")
    finally:
        db.close()


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
