from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from datetime import date, datetime
from typing import Optional

from app.database import get_db
from app.models import Ticket
from app.schemas import TicketCreate, TicketResponse, TicketListResponse
from app.security import generate_token, generate_signature

router = APIRouter(prefix="/api/tickets", tags=["tickets"])

@router.post("/", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def create_ticket(ticket: TicketCreate, db: Session = Depends(get_db)):
    existing = db.query(Ticket).filter(Ticket.order_id == ticket.order_id).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ticket with order_id {ticket.order_id} already exists"
        )
    
    # Используем переданные токен/подпись от бота, или генерируем новые
    token = ticket.qr_token if ticket.qr_token else generate_token()
    signature = ticket.qr_signature if ticket.qr_signature else generate_signature(ticket.order_id, token)
    
    db_ticket = Ticket(
        order_id=ticket.order_id,
        transaction_id=ticket.transaction_id,
        customer_name=ticket.customer_name,
        customer_email=ticket.customer_email,
        customer_phone=ticket.customer_phone,
        ticket_type=ticket.ticket_type,
        event_date=ticket.event_date,
        event_name=ticket.event_name,
        price=ticket.price,
        discount=ticket.discount,
        payment_amount=ticket.payment_amount,
        promocode=ticket.promocode,
        qr_token=token,
        qr_signature=signature,
        status="valid",
        city_name=ticket.city_name,  # IMPREZA
        country_code=ticket.country_code,  # IMPREZA
        club_id=ticket.club_id  # IMPREZA
    )
    
    db.add(db_ticket)
    db.commit()
    db.refresh(db_ticket)
    
    return db_ticket


@router.get("/", response_model=TicketListResponse)
def get_tickets(
    event_date: str = None,
    status_filter: str = None,
    club_id: int = None,  # IMPREZA: добавлен параметр
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    query = db.query(Ticket)
    
    if event_date:
        query = query.filter(Ticket.event_date.like(f"%{event_date}%"))
    
    if status_filter:
        query = query.filter(Ticket.status == status_filter)
    
    # IMPREZA: Фильтр по клубу
    if club_id:
        query = query.filter(Ticket.club_id == club_id)
    
    total = query.count()
    
    # IMPREZA: Подсчет entered тоже фильтруем по club_id
    entered_query = db.query(Ticket).filter(Ticket.status == "used")
    if club_id:
        entered_query = entered_query.filter(Ticket.club_id == club_id)
    entered = entered_query.count()
    
    # IMPREZA: Подсчет pending тоже фильтруем по club_id
    pending_query = db.query(Ticket).filter(Ticket.status == "valid")
    if club_id:
        pending_query = pending_query.filter(Ticket.club_id == club_id)
    pending = pending_query.count()
    
    tickets = query.order_by(Ticket.created_at.desc()).offset(offset).limit(limit).all()
    
    return TicketListResponse(
        tickets=tickets,
        total=total,
        bought=total,
        entered=entered,
        pending=pending
    )


@router.get("/{order_id}", response_model=TicketResponse)
def get_ticket(order_id: str, db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.order_id == order_id).first()
    
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket {order_id} not found")
    
    return ticket


@router.get("/token/{token}", response_model=TicketResponse)
def get_ticket_by_token(token: str, db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.qr_token == token).first()
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    return ticket


@router.patch("/{order_id}/cancel")
def cancel_ticket(order_id: str, db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.order_id == order_id).first()
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    ticket.status = "cancelled"
    db.commit()
    
    return {"status": "cancelled", "order_id": order_id}


@router.delete("/")
def delete_tickets_by_club(
    club_id: int = None, 
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    """Удаляет билеты для конкретного клуба или все билеты с поддержкой диапазона дат"""
    query = db.query(Ticket)
    
    if club_id:
        query = query.filter(Ticket.club_id == club_id)
    
    # Фильтрация по датам (event_date - строка в формате YYYY-MM-DD)
    if start_date:
        try:
            datetime.strptime(start_date, "%Y-%m-%d")  # Проверяем формат
            query = query.filter(Ticket.event_date >= start_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD")
    
    if end_date:
        try:
            datetime.strptime(end_date, "%Y-%m-%d")  # Проверяем формат
            query = query.filter(Ticket.event_date <= end_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end_date format. Use YYYY-MM-DD")
    
    count = query.count()
    query.delete()
    db.commit()
    
    return {"status": "deleted", "deleted": count, "club_id": club_id, "start_date": start_date, "end_date": end_date}


@router.delete("/club/{club_id}")
def delete_tickets_by_club_id(
    club_id: int,
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    """Удаляет билеты для конкретного клуба с поддержкой диапазона дат"""
    query = db.query(Ticket).filter(Ticket.club_id == club_id)
    
    # Фильтрация по датам (event_date - строка в формате YYYY-MM-DD)
    if start_date:
        try:
            datetime.strptime(start_date, "%Y-%m-%d")  # Проверяем формат
            query = query.filter(Ticket.event_date >= start_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD")
    
    if end_date:
        try:
            datetime.strptime(end_date, "%Y-%m-%d")  # Проверяем формат
            query = query.filter(Ticket.event_date <= end_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end_date format. Use YYYY-MM-DD")
    
    count = query.count()
    query.delete()
    db.commit()
    
    return {"status": "deleted", "deleted": count, "club_id": club_id, "start_date": start_date, "end_date": end_date}


@router.delete("/all")
def delete_all_tickets(db: Session = Depends(get_db)):
    """Удаляет ВСЕ билеты из базы данных"""
    count = db.query(Ticket).count()
    db.query(Ticket).delete()
    db.commit()
    
    return {"status": "deleted", "count": count, "message": f"Deleted {count} tickets"}
