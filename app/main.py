from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.database import engine, Base
from app.routers import tickets, verify, stats, history
from app.config import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables created")
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
