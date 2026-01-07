from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date

from app.database import get_db
from app.models import Ticket, ScanHistory
from app.schemas import StatsResponse

router = APIRouter(prefix="/api/stats", tags=["stats"])

@router.get("/", response_model=StatsResponse)
def get_stats(event_date: str = None, club_id: int = None, db: Session = Depends(get_db)):
    """IMPREZA: Добавлен параметр club_id для фильтрации"""
    query = db.query(Ticket)
    
    if event_date:
        query = query.filter(Ticket.event_date.like(f"%{event_date}%"))
    
    # IMPREZA: Фильтр по club_id
    if club_id:
        query = query.filter(Ticket.club_id == club_id)
    
    total = query.count()
    entered = query.filter(Ticket.status == "used").count()
    pending = query.filter(Ticket.status == "valid").count()
    cancelled = query.filter(Ticket.status == "cancelled").count()
    
    today = date.today()
    today_scans_query = db.query(ScanHistory).filter(
        func.date(ScanHistory.scan_time) == today
    )
    
    # IMPREZA: Фильтр по club_id в scan_history
    if club_id:
        today_scans_query = today_scans_query.filter(ScanHistory.club_id == club_id)
    
    duplicate_attempts = today_scans_query.filter(ScanHistory.scan_result == "duplicate").count()
    invalid_attempts = today_scans_query.filter(ScanHistory.scan_result.in_(["invalid", "forged"])).count()
    
    return StatsResponse(
        total_tickets=total,
        entered=entered,
        pending=pending,
        cancelled=cancelled,
        duplicate_attempts=duplicate_attempts,
        invalid_attempts=invalid_attempts
    )
