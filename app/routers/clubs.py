"""
IMPREZA: Эндпоинт для получения списка всех клубов/городов
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from app.database import get_db

router = APIRouter(prefix="/clubs", tags=["clubs"])


@router.get("/migrate-plain-password")
def migrate_plain_password():
    """Добавить колонку plain_password в таблицу clubs (одноразовая миграция)"""
    db = next(get_db())
    
    try:
        # Проверяем, существует ли уже колонка
        check_query = text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'clubs' AND column_name = 'plain_password'
        """)
        result = db.execute(check_query)
        
        if result.fetchone():
            return {"status": "exists", "message": "Колонка plain_password уже существует"}
        
        # Добавляем колонку
        alter_query = text("""
            ALTER TABLE clubs 
            ADD COLUMN plain_password VARCHAR(255)
        """)
        db.execute(alter_query)
        db.commit()
        
        return {"status": "success", "message": "Колонка plain_password добавлена"}
        
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    
    finally:
        db.close()


@router.get("/migrate-subtotal")
def migrate_subtotal():
    """Добавить колонку subtotal в таблицу tickets (одноразовая миграция)"""
    db = next(get_db())
    
    try:
        # Проверяем, существует ли уже колонка
        check_query = text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'tickets' AND column_name = 'subtotal'
        """)
        result = db.execute(check_query)
        
        if result.fetchone():
            return {"status": "exists", "message": "Колонка subtotal уже существует"}
        
        # Добавляем колонку
        alter_query = text("""
            ALTER TABLE tickets 
            ADD COLUMN subtotal FLOAT DEFAULT 0
        """)
        db.execute(alter_query)
        db.commit()
        
        return {"status": "success", "message": "Колонка subtotal добавлена"}
        
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    
    finally:
        db.close()


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
                c.is_active,
                c.plain_password
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
                "is_active": row[6],
                "plain_password": row[7]
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
        plain_password = data.get("plain_password")  # Получаем plain-text пароль
        if not password_hash:
            raise HTTPException(status_code=400, detail="password_hash required")
        
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
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        db.close()


@router.get("/add-fakeid")
def add_fakeid_club():
    """
    Добавить клуб Fakeid для бренда Fake ID (одноразовая миграция)
    Login: pl_fakeid
    Password: fakeid_2025!Imp
    """
    import hashlib
    
    db = next(get_db())
    
    try:
        # Проверяем, существует ли уже клуб
        check_query = text("""
            SELECT club_id FROM clubs WHERE login = 'pl_fakeid'
        """)
        result = db.execute(check_query)
        
        if result.fetchone():
            return {"status": "exists", "message": "Клуб Fakeid уже существует"}
        
        # Получаем country_id для Польши
        country_query = text("""
            SELECT country_id FROM countries WHERE country_code = 'PL'
        """)
        country_result = db.execute(country_query)
        country_row = country_result.fetchone()
        
        if not country_row:
            return {"status": "error", "message": "Страна PL не найдена"}
        
        country_id = country_row[0]
        
        # Генерируем пароль
        login = "pl_fakeid"
        password = "fakeid_2025!Imp"
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        
        # Вставляем клуб
        insert_query = text("""
            INSERT INTO clubs (country_id, city_name, city_english, login, password_hash, plain_password, is_active)
            VALUES (:country_id, 'Fakeid', 'Fakeid', :login, :password_hash, :plain_password, TRUE)
        """)
        db.execute(insert_query, {
            "country_id": country_id,
            "login": login,
            "password_hash": password_hash,
            "plain_password": password
        })
        db.commit()
        
        return {
            "status": "success", 
            "message": "Клуб Fakeid добавлен",
            "login": login,
            "password": password
        }
        
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    
    finally:
        db.close()
