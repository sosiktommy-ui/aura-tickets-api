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
from app.routers import tickets, verify, stats, history, auth, clubs  # IMPREZA: добавлен auth и clubs

app.include_router(tickets.router)
app.include_router(verify.router)
app.include_router(stats.router)
app.include_router(history.router)
app.include_router(auth.router)  # IMPREZA: подключен роутер авторизации
app.include_router(clubs.router)  # IMPREZA: подключен роутер clubs

# Инициализация БД при первом запросе
@app.on_event("startup")
async def startup():
    try:
        from app.database import engine, Base
        Base.metadata.create_all(bind=engine)
        print("✅ Database tables created")
    except Exception as e:
        print(f"⚠️ DB init error: {e}")

