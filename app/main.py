from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI(
    title="AURA Tickets API",
    description="API для системы билетов AURA",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check первым - без зависимостей
@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "AURA Tickets API"}

@app.get("/")
def root():
    return {"service": "AURA Tickets API", "version": "1.0.0", "docs": "/docs"}

# Диагностика БД
@app.get("/debug/db")
def debug_db():
    """Проверка подключения к базе данных"""
    try:
        from app.database import engine
        from app.config import settings
        import sqlalchemy
        
        # Пробуем подключиться
        with engine.connect() as conn:
            result = conn.execute(sqlalchemy.text("SELECT 1"))
            result.fetchone()
        
        return {
            "status": "connected",
            "database_url": settings.DATABASE_URL[:50] + "..." if len(settings.DATABASE_URL) > 50 else settings.DATABASE_URL,
            "message": "Database connection successful"
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "error_type": type(e).__name__
        }

# Роутеры подключаем после
from app.routers import tickets, verify, stats, history, auth, clubs, tilda, deleted_tickets  # IMPREZA: добавлен deleted_tickets

app.include_router(tickets.router)
app.include_router(verify.router)
app.include_router(stats.router)
app.include_router(history.router)
app.include_router(auth.router)  # IMPREZA: подключен роутер авторизации
app.include_router(clubs.router)  # IMPREZA: подключен роутер clubs
app.include_router(tilda.router)  # Подключен роутер для Tilda webhooks
app.include_router(deleted_tickets.router)  # Архив удалённых билетов

# Инициализация БД при первом запросе
@app.on_event("startup")
async def startup():
    try:
        from app.database import engine, Base
        import sqlalchemy
        
        # Создаём таблицы если не существуют
        Base.metadata.create_all(bind=engine)
        print("✅ Database tables created/verified")
        
        # Автомиграция: добавляем колонку visible_to_managers если её нет
        with engine.connect() as conn:
            # Проверяем есть ли колонка visible_to_managers
            result = conn.execute(sqlalchemy.text("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'tickets' AND column_name = 'visible_to_managers'
            """))
            if not result.fetchone():
                # Колонки нет - добавляем
                conn.execute(sqlalchemy.text("""
                    ALTER TABLE tickets ADD COLUMN visible_to_managers BOOLEAN DEFAULT TRUE
                """))
                conn.commit()
                print("✅ Added column: visible_to_managers")
            else:
                print("✅ Column visible_to_managers already exists")
            
            # QUANTITY: Добавляем колонку quantity если её нет
            result = conn.execute(sqlalchemy.text("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'tickets' AND column_name = 'quantity'
            """))
            if not result.fetchone():
                conn.execute(sqlalchemy.text("""
                    ALTER TABLE tickets ADD COLUMN quantity INTEGER DEFAULT 1
                """))
                conn.commit()
                print("✅ Added column: quantity")
            else:
                print("✅ Column quantity already exists")
            
            # DELETED_TICKETS: Создаём таблицу архива если её нет
            result = conn.execute(sqlalchemy.text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'deleted_tickets'
                )
            """))
            if not result.scalar():
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE deleted_tickets (
                        id SERIAL PRIMARY KEY,
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
                        deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        deleted_by VARCHAR(100),
                        delete_reason VARCHAR(500)
                    )
                """))
                conn.execute(sqlalchemy.text("CREATE INDEX idx_deleted_tickets_order_id ON deleted_tickets(order_id)"))
                conn.execute(sqlalchemy.text("CREATE INDEX idx_deleted_tickets_deleted_at ON deleted_tickets(deleted_at)"))
                conn.commit()
                print("✅ Created table: deleted_tickets (archive)")
            else:
                print("✅ Table deleted_tickets already exists")
                
    except Exception as e:
        print(f"⚠️ DB init error: {e}")

