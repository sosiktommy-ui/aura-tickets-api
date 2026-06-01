from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date

from app.database import get_db
from app.models import Ticket, ScanHistory, Club
from app.schemas import StatsResponse
from app.dependencies.auth import require_auth, AuthInfo

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _scanner_allowed_club_ids(auth: AuthInfo, db: Session) -> list[int]:
    if auth.role != "scanner" or not auth.club_id:
        return []

    allowed = list(auth.club_ids) if auth.club_ids else [auth.club_id]
    if auth.club_id == 76:
        kdk_club = db.query(Club).filter(Club.club_id == 101).first()
        if kdk_club and kdk_club.club_id not in allowed:
            allowed.append(kdk_club.club_id)

    return allowed


@router.get("/", response_model=StatsResponse)
def get_stats(event_date: str = None, club_id: int = None, show_all_for_admin: bool = False, db: Session = Depends(get_db), auth: AuthInfo = Depends(require_auth)):
    """IMPREZA: Добавлен параметр club_id для фильтрации"""
    query = db.query(Ticket)
    
    # Сканеры видят только видимые билеты (не скрытые)
    if not show_all_for_admin:
        query = query.filter(Ticket.visible_to_managers == True)
    
    if event_date:
        query = query.filter(Ticket.event_date.like(f"%{event_date}%"))
    
    # IMPREZA: Фильтр по club_id — для сканеров используем их допустимые клубы
    filter_club_ids = None
    if auth.role == "scanner":
        filter_club_ids = _scanner_allowed_club_ids(auth, db)
        if filter_club_ids:
            query = query.filter(Ticket.club_id.in_(filter_club_ids))
    elif club_id:
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
    if filter_club_ids:
        today_scans_query = today_scans_query.filter(ScanHistory.club_id.in_(filter_club_ids))
    elif club_id:
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
