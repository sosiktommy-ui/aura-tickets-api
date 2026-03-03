import logging
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import os

from app.config import settings

# ─── Logging ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("impreza.security")

# ─── Rate Limiter ───
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])

app = FastAPI(
    title="AURA Tickets API",
    description="API для системы билетов AURA",
    version="2.0.0"
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ─── CORS: только разрешённые домены ───
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check РїРµСЂРІС‹Рј - Р±РµР· Р·Р°РІРёСЃРёРјРѕСЃС‚РµР№
@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "AURA Tickets API"}

@app.get("/")
def root():
    return {"service": "AURA Tickets API", "version": "2.0.0", "docs": "/docs"}

# Р РѕСѓС‚РµСЂС‹ РїРѕРґРєР»СЋС‡Р°РµРј РїРѕСЃР»Рµ
from app.routers import tickets, verify, stats, history, auth, clubs, tilda, deleted_tickets, admin_auth  # IMPREZA: РґРѕР±Р°РІР»РµРЅ deleted_tickets

app.include_router(tickets.router)
app.include_router(verify.router)
app.include_router(stats.router)
app.include_router(history.router)
app.include_router(auth.router)  # IMPREZA: РїРѕРґРєР»СЋС‡РµРЅ СЂРѕСѓС‚РµСЂ Р°РІС‚РѕСЂРёР·Р°С†РёРё
app.include_router(clubs.router)  # IMPREZA: РїРѕРґРєР»СЋС‡РµРЅ СЂРѕСѓС‚РµСЂ clubs
app.include_router(tilda.router)  # РџРѕРґРєР»СЋС‡РµРЅ СЂРѕСѓС‚РµСЂ РґР»СЏ Tilda webhooks
app.include_router(deleted_tickets.router)  # РђСЂС…РёРІ СѓРґР°Р»С‘РЅРЅС‹С… Р±РёР»РµС‚РѕРІ
app.include_router(admin_auth.router)  # IMPREZA: Web admin panel JWT auth

# РРЅРёС†РёР°Р»РёР·Р°С†РёСЏ Р‘Р” РїСЂРё РїРµСЂРІРѕРј Р·Р°РїСЂРѕСЃРµ
@app.on_event("startup")
async def startup():
    try:
        from app.database import engine, Base
        import sqlalchemy
        
        # РЎРѕР·РґР°С‘Рј С‚Р°Р±Р»РёС†С‹ РµСЃР»Рё РЅРµ СЃСѓС‰РµСЃС‚РІСѓСЋС‚
        Base.metadata.create_all(bind=engine)
        print("вњ… Database tables created/verified")
        
        # РђРІС‚РѕРјРёРіСЂР°С†РёСЏ: РґРѕР±Р°РІР»СЏРµРј РєРѕР»РѕРЅРєСѓ visible_to_managers РµСЃР»Рё РµС‘ РЅРµС‚
        with engine.connect() as conn:
            # РџСЂРѕРІРµСЂСЏРµРј РµСЃС‚СЊ Р»Рё РєРѕР»РѕРЅРєР° visible_to_managers
            result = conn.execute(sqlalchemy.text("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'tickets' AND column_name = 'visible_to_managers'
            """))
            if not result.fetchone():
                # РљРѕР»РѕРЅРєРё РЅРµС‚ - РґРѕР±Р°РІР»СЏРµРј
                conn.execute(sqlalchemy.text("""
                    ALTER TABLE tickets ADD COLUMN visible_to_managers BOOLEAN DEFAULT TRUE
                """))
                conn.commit()
                print("вњ… Added column: visible_to_managers")
            else:
                print("вњ… Column visible_to_managers already exists")
            
            # QUANTITY: Р”РѕР±Р°РІР»СЏРµРј РєРѕР»РѕРЅРєСѓ quantity РµСЃР»Рё РµС‘ РЅРµС‚
            result = conn.execute(sqlalchemy.text("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'tickets' AND column_name = 'quantity'
            """))
            if not result.fetchone():
                conn.execute(sqlalchemy.text("""
                    ALTER TABLE tickets ADD COLUMN quantity INTEGER DEFAULT 1
                """))
                conn.commit()
                print("вњ… Added column: quantity")
            else:
                print("вњ… Column quantity already exists")
            
            # DELETED_TICKETS: РЎРѕР·РґР°С‘Рј С‚Р°Р±Р»РёС†Сѓ Р°СЂС…РёРІР° РµСЃР»Рё РµС‘ РЅРµС‚
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
                print("вњ… Created table: deleted_tickets (archive)")
            else:
                print("вњ… Table deleted_tickets already exists")
                
    except Exception as e:
        print(f"вљ пёЏ DB init error: {e}")


