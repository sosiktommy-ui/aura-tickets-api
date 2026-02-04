"""
Миграция: Добавление таблицы deleted_tickets для архива удалённых билетов

Запуск:
    python add_deleted_tickets_table.py
"""

import os
import sys

# Добавляем путь к приложению
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from app.database import engine, SessionLocal

def run_migration():
    """Создаём таблицу deleted_tickets"""
    
    db = SessionLocal()
    
    try:
        # Проверяем, существует ли таблица
        check_query = text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'deleted_tickets'
            );
        """)
        result = db.execute(check_query).scalar()
        
        if result:
            print("⚠️ Таблица deleted_tickets уже существует")
            return
        
        # Создаём таблицу deleted_tickets — копия tickets + поля для архива
        create_table_query = text("""
            CREATE TABLE deleted_tickets (
                id SERIAL PRIMARY KEY,
                
                -- Оригинальные поля из tickets
                original_id INTEGER NOT NULL,
                order_id VARCHAR(50) NOT NULL,
                transaction_id VARCHAR(100),
                
                customer_name VARCHAR(200) NOT NULL,
                customer_email VARCHAR(200),
                customer_phone VARCHAR(50),
                
                ticket_type VARCHAR(100) DEFAULT 'Standard',
                event_date VARCHAR(20),
                event_name VARCHAR(200),
                price FLOAT DEFAULT 0,
                subtotal FLOAT DEFAULT 0,
                discount FLOAT DEFAULT 0,
                payment_amount FLOAT DEFAULT 0,
                promocode VARCHAR(50),
                
                qr_token VARCHAR(100),
                qr_signature VARCHAR(100),
                
                country_code VARCHAR(10),
                city_name VARCHAR(100),
                club_id INTEGER,
                
                visible_to_managers BOOLEAN DEFAULT TRUE,
                quantity INTEGER DEFAULT 1,
                
                status VARCHAR(20) DEFAULT 'valid',
                scan_count INTEGER DEFAULT 0,
                first_scan_at TIMESTAMP,
                last_scan_at TIMESTAMP,
                scanned_by VARCHAR(100),
                
                telegram_message_id INTEGER,
                
                original_created_at TIMESTAMP,
                original_updated_at TIMESTAMP,
                
                -- Поля архива
                deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                deleted_by VARCHAR(100),
                delete_reason VARCHAR(500)
            );
            
            -- Индексы для быстрого поиска
            CREATE INDEX idx_deleted_tickets_order_id ON deleted_tickets(order_id);
            CREATE INDEX idx_deleted_tickets_customer_email ON deleted_tickets(customer_email);
            CREATE INDEX idx_deleted_tickets_event_name ON deleted_tickets(event_name);
            CREATE INDEX idx_deleted_tickets_deleted_at ON deleted_tickets(deleted_at);
            CREATE INDEX idx_deleted_tickets_city_name ON deleted_tickets(city_name);
        """)
        
        db.execute(create_table_query)
        db.commit()
        
        print("✅ Таблица deleted_tickets успешно создана!")
        print("   - Все удалённые билеты будут сохраняться в эту таблицу")
        print("   - Можно восстановить любой удалённый билет")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка создания таблицы: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print("🚀 Запуск миграции: создание таблицы deleted_tickets")
    print("=" * 50)
    run_migration()
    print("=" * 50)
    print("✅ Миграция завершена!")
