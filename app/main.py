from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import time

from app.routers import tickets, verify, stats, history
from app.config import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Отложенная инициализация БД
    max_retries = 5
    for i in range(max_retries):
        try:
            from app.database import engine, Base
            Base.metadata.create_all(bind=engine)
            print("✅ Database tables created")
            break
        except Exception as e:
            print(f"⚠️ DB connection attempt {i+1}/{max_retries} failed: {e}")
            if i < max_retries - 1:
                time.sleep(2)
            else:
                print("❌ Could not connect to database, continuing anyway...")
    yield
    print("👋 Shutting down...")

app = FastAPI(
    title=settings.APP_NAME,
    description="API для системы билетов AURA",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tickets.router)
app.include_router(verify.router)
app.include_router(stats.router)
app.include_router(history.router)

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": settings.APP_NAME}

@app.get("/")
def root():
    return {
        "service": settings.APP_NAME,
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": {
            "create_ticket": "POST /api/tickets/",
            "get_tickets": "GET /api/tickets/",
            "verify_qr": "POST /api/verify",
            "get_stats": "GET /api/stats/",
            "get_history": "GET /api/history/"
        }
    }
