"""
Миграция: таблицы региональных менеджеров (regional_managers + regional_assignments)
и seed конфигурации по умолчанию.

Запуск:
    python add_regional_tables.py

Примечание: то же самое делает автомиграция в app/main.py при старте API —
этот скрипт нужен для ручного применения / локальной разработки.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.database import SessionLocal
from app.routers.regional import DEFAULT_CONFIG


def run_migration():
    db = SessionLocal()
    try:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS regional_managers (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                color TEXT NOT NULL DEFAULT '#8B7BE8',
                position INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS regional_assignments (
                country_code VARCHAR(2) PRIMARY KEY,
                manager_id INTEGER REFERENCES regional_managers(id) ON DELETE CASCADE
            )
        """))
        db.commit()

        has_mgr = db.execute(text("SELECT EXISTS (SELECT 1 FROM regional_managers)")).scalar()
        if has_mgr:
            print("⚠️ regional_managers уже содержит данные — seed пропущен")
            return

        for pos, m in enumerate(DEFAULT_CONFIG):
            mid = db.execute(text(
                "INSERT INTO regional_managers (name, color, position) VALUES (:n,:c,:p) RETURNING id"
            ), {"n": m["name"], "c": m["color"], "p": pos}).scalar()
            for cc in m.get("countries", []):
                db.execute(text("""
                    INSERT INTO regional_assignments (country_code, manager_id)
                    VALUES (:cc, :mid)
                    ON CONFLICT (country_code) DO UPDATE SET manager_id = EXCLUDED.manager_id
                """), {"cc": cc.strip().upper(), "mid": mid})
        db.commit()
        print(f"✅ Таблицы созданы, засеяно менеджеров: {len(DEFAULT_CONFIG)}")

    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка миграции: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print("🚀 Миграция: regional_managers / regional_assignments")
    print("=" * 50)
    run_migration()
    print("=" * 50)
    print("✅ Готово")
