from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.models import Ticket
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
