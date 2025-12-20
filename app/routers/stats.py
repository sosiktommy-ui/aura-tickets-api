from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date

from app.database import get_db
from app.models import Ticket, ScanHistory
from app.schemas import StatsResponse

router = APIRouter(prefix="/api/stats", tags=["stats"])

@router.get("/", response_model=StatsResponse)
def get_stats(event_date: str = None, db: Session = Depends(get_db)):
    query = db.query(Ticket)
    
    if event_date:
        query = query.filter(Ticket.event_date.like(f"%{event_date}%"))
    
    total = query.count()
    entered = query.filter(Ticket.status == "used").count()
    pending = query.filter(Ticket.status == "valid").count()
    cancelled = query.filter(Ticket.status == "cancelled").count()
    
    today = date.today()
    today_scans = db.query(ScanHistory).filter(
        func.date(ScanHistory.scan_time) == today
    )
    
    duplicate_attempts = today_scans.filter(ScanHistory.scan_result == "duplicate").count()
    invalid_attempts = today_scans.filter(ScanHistory.scan_result.in_(["invalid", "forged"])).count()
    
    return StatsResponse(
        total_tickets=total,
        entered=entered,
        pending=pending,
        cancelled=cancelled,
        duplicate_attempts=duplicate_attempts,
        invalid_attempts=invalid_attempts
    )
