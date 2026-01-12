"""
Миграция: Добавление поля hidden_for_manager в таблицу scan_history
"""

import psycopg2
import os

def run_migration():
    # Railway PostgreSQL подключение
    DATABASE_URL = os.getenv("DATABASE_URL") or "postgresql://postgres:aura_admin_2024@viaduct.proxy.rlwy.net:46789/railway"
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        # Проверяем есть ли уже колонка
        cursor.execute("""
            SELECT COUNT(*) 
            FROM information_schema.columns 
            WHERE table_name = 'scan_history' 
            AND column_name = 'hidden_for_manager'
        """)
        
        if cursor.fetchone()[0] == 0:
            # Добавляем колонку
            cursor.execute("""
                ALTER TABLE scan_history 
                ADD COLUMN hidden_for_manager BOOLEAN DEFAULT FALSE;
            """)
            
            # Создаем индекс для производительности
            cursor.execute("""
                CREATE INDEX idx_scan_history_hidden_for_manager 
                ON scan_history (hidden_for_manager);
            """)
            
            conn.commit()
            print("✅ Миграция выполнена: добавлено поле hidden_for_manager")
        else:
            print("ℹ️  Поле hidden_for_manager уже существует")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"❌ Ошибка миграции: {e}")

if __name__ == "__main__":
    run_migration()