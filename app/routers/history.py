from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.models import Ticket, ScanHistory
from app.schemas import HistoryResponse, HistoryItem

router = APIRouter(prefix="/api/history", tags=["history"])

@router.get("/", response_model=HistoryResponse)
def get_history(event_date: str = None, limit: int = 100, db: Session = Depends(get_db)):
    query = db.query(Ticket)
    
    if event_date:
        query = query.filter(Ticket.event_date.like(f"%{event_date}%"))
    
    tickets = query.order_by(
        desc(Ticket.first_scan_at.isnot(None)),
        desc(Ticket.first_scan_at),
        desc(Ticket.created_at)
    ).limit(limit).all()
    
    items = []
    for t in tickets:
        if t.status == "used" and t.scan_count == 1:
            display_status = "entered"
        elif t.status == "used" and t.scan_count > 1:
            display_status = "duplicate"
        elif t.status == "cancelled":
            display_status = "cancelled"
        else:
            display_status = "pending"
        
        items.append(HistoryItem(
            id=t.id,
            order_id=t.order_id,
            customer_name=t.customer_name,
            ticket_type=t.ticket_type,
            event_date=t.event_date,
            status=display_status,
            scan_time=t.first_scan_at,
            price=t.price
        ))
    
    total = query.count()
    entered = query.filter(Ticket.status == "used").count()
    
    return HistoryResponse(
        items=items,
        stats={"bought": total, "entered": entered, "pending": total - entered}
    )

@router.delete("/")
def delete_history(club_id: int, db: Session = Depends(get_db)):
    """Удаляет историю сканирования для конкретного клуба (города) - IMPREZA Multitenancy"""
    if not club_id:
        raise HTTPException(status_code=400, detail="club_id обязателен")
    
    try:
        # Удаляем все записи из scan_history для этого club_id
        deleted_scans = db.query(ScanHistory).filter(
            ScanHistory.club_id == club_id
        ).delete(synchronize_session=False)
        
        # Сбрасываем статусы билетов этого клуба обратно в valid
        updated_tickets = db.query(Ticket).filter(
            Ticket.club_id == club_id,
            Ticket.status.in_(["used", "cancelled"])
        ).update({
            "status": "valid",
            "scan_count": 0,
            "first_scan_at": None,
            "last_scan_at": None
        }, synchronize_session=False)
        
        db.commit()
        
        return {
            "success": True,
            "deleted_scans": deleted_scans,
            "reset_tickets": updated_tickets,
            "club_id": club_id,
            "message": f"История для club_id={club_id} успешно удалена"
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка удаления истории: {str(e)}")
