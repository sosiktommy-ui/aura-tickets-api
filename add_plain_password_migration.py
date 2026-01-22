"""
Миграция: Добавление колонки plain_password в таблицу clubs
Запустить один раз для обновления БД
"""
import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "")

if not DATABASE_URL:
    print("❌ DATABASE_URL не установлен!")
    print("Установите переменную окружения DATABASE_URL")
    exit(1)

# Исправление для Railway PostgreSQL
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    try:
        # Проверяем, существует ли уже колонка
        check_query = text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'clubs' AND column_name = 'plain_password'
        """)
        result = conn.execute(check_query)
        
        if result.fetchone():
            print("✅ Колонка plain_password уже существует")
        else:
            # Добавляем колонку
            alter_query = text("""
                ALTER TABLE clubs 
                ADD COLUMN plain_password VARCHAR(255)
            """)
            conn.execute(alter_query)
            conn.commit()
            print("✅ Колонка plain_password добавлена в таблицу clubs")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
