from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from datetime import date, datetime
from typing import Optional

from app.database import get_db
from app.models import Ticket, ScanHistory
from app.schemas import TicketCreate, TicketResponse, TicketListResponse
from app.security import generate_token, generate_signature

def convert_date_for_db_filter(date_str: str) -> str:
    """Конвертирует дату YYYY-MM-DD в формат для сравнения с event_date в базе (DD.MM)"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.day}.{dt.month}"  # Формат D.M или DD.MM без ведущих нулей

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
        subtotal=ticket.subtotal,
        discount=ticket.discount,
        payment_amount=ticket.payment_amount,
        promocode=ticket.promocode,
        qr_token=token,
        qr_signature=signature,
        status="valid",
        city_name=ticket.city_name,
        country_code=ticket.country_code,
        club_id=ticket.club_id,
        visible_to_managers=ticket.visible_to_managers
    )
    
    db.add(db_ticket)
    db.commit()
    db.refresh(db_ticket)
    
    return db_ticket


@router.get("/", response_model=TicketListResponse)
def get_tickets(
    event_date: str = None,
    status_filter: str = None,
    club_id: int = None,
    show_all_for_admin: bool = False,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    query = db.query(Ticket)
    
    # БАГ FIX #2: Фильтрация по visible_to_managers
    if not show_all_for_admin:
        query = query.filter(Ticket.visible_to_managers == True)
    
    if event_date:
        query = query.filter(Ticket.event_date.like(f"%{event_date}%"))
    
    if status_filter:
        query = query.filter(Ticket.status == status_filter)
    
    if club_id:
        query = query.filter(Ticket.club_id == club_id)
    
    total = query.count()
    
    entered_query = db.query(Ticket).filter(Ticket.status == "used")
    if club_id:
        entered_query = entered_query.filter(Ticket.club_id == club_id)
    entered = entered_query.count()
    
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


@router.put("/hide-from-managers")
def hide_tickets_from_managers(
    club_id: Optional[int] = None,
    city_name: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Скрыть билеты от менеджеров (visible_to_managers = false)"""
    try:
        query = db.query(Ticket)
        
        # Фильтр по городу (club_id или city_name)
        if club_id:
            query = query.filter(Ticket.club_id == club_id)
        elif city_name:
            query = query.filter(Ticket.city_name == city_name)
        
        # Фильтр по датам (исправлено)
        if start_date and end_date:
            from datetime import datetime
            # Преобразуем строки дат в правильный формат
            start_datetime = f"{start_date} 00:00:00"
            end_datetime = f"{end_date} 23:59:59"
            query = query.filter(Ticket.created_at >= start_datetime)
            query = query.filter(Ticket.created_at <= end_datetime)
        
        updated_count = query.update({"visible_to_managers": False}, synchronize_session='fetch')
        db.commit()
        
        return {"message": f"Скрыто {updated_count} билетов от менеджеров", "updated_count": updated_count}
        
    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка скрытия: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка скрытия: {str(e)}")


@router.put("/show-to-managers")
def show_tickets_to_managers(
    club_id: Optional[int] = None,
    city_name: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Восстановить скрытые билеты (visible_to_managers = true)"""
    try:
        query = db.query(Ticket).filter(Ticket.visible_to_managers == False)
        
        # Фильтр по городу (club_id или city_name)
        if club_id:
            query = query.filter(Ticket.club_id == club_id)
        elif city_name:
            query = query.filter(Ticket.city_name == city_name)
        
        updated_count = query.update({"visible_to_managers": True}, synchronize_session='fetch')
        db.commit()
        
        print(f"✅ Восстановлено {updated_count} билетов для менеджеров")
        return {"message": f"Восстановлено {updated_count} билетов", "updated_count": updated_count}
        
    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка восстановления: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка восстановления: {str(e)}")


@router.delete("/delete-range")
def delete_tickets_range(
    club_id: Optional[int] = None,
    city_name: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Полностью удалить билеты из БД (сначала scan_history, потом tickets)"""
    try:
        query = db.query(Ticket)
        
        # Фильтр по городу
        if club_id:
            query = query.filter(Ticket.club_id == club_id)
        elif city_name:
            query = query.filter(Ticket.city_name == city_name)
        
        # Фильтр по датам (исправлено)
        if start_date and end_date:
            from datetime import datetime
            # Преобразуем строки дат в правильный формат
            start_datetime = f"{start_date} 00:00:00"
            end_datetime = f"{end_date} 23:59:59"
            query = query.filter(Ticket.created_at >= start_datetime)
            query = query.filter(Ticket.created_at <= end_datetime)
        
        # Подсчитываем перед удалением
        deleted_count = query.count()
        
        # Получаем ID билетов для удаления
        ticket_ids = [t.id for t in query.all()]
        
        if ticket_ids:
            # СНАЧАЛА удаляем связанные записи из scan_history (ForeignKey fix)
            db.query(ScanHistory).filter(ScanHistory.ticket_id.in_(ticket_ids)).delete(synchronize_session='fetch')
            
            # ПОТОМ удаляем билеты
            db.query(Ticket).filter(Ticket.id.in_(ticket_ids)).delete(synchronize_session='fetch')
        
        db.commit()
        
        return {"message": f"Удалено {deleted_count} билетов", "deleted_count": deleted_count}
        
    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка удаления: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка удаления: {str(e)}")


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


@router.patch("/{order_id}/status")
def change_ticket_status(order_id: str, data: dict, db: Session = Depends(get_db)):
    """Изменить статус билета вручную (для менеджера/админа)
    
    Принимает: {"status": "valid" | "used" | "cancelled", "scan_count": int (опционально)}
    """
    ticket = db.query(Ticket).filter(Ticket.order_id == order_id).first()
    
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket {order_id} not found")
    
    new_status = data.get("status")
    if new_status not in ["valid", "used", "cancelled"]:
        raise HTTPException(status_code=400, detail="Invalid status. Must be: valid, used, or cancelled")
    
    old_status = ticket.status
    ticket.status = new_status
    
    # Если переводим в used - установить scan_count = 1
    if new_status == "used" and ticket.scan_count == 0:
        ticket.scan_count = 1
    
    # Если переводим в valid - сбросить scan_count
    if new_status == "valid":
        ticket.scan_count = 0
    
    # Если передан scan_count - использовать его
    if "scan_count" in data:
        ticket.scan_count = data["scan_count"]
    
    db.commit()
    
    return {
        "success": True,
        "order_id": order_id,
        "old_status": old_status,
        "new_status": new_status,
        "scan_count": ticket.scan_count
    }


@router.delete("/")
def delete_tickets_by_club(
    club_id: int = None, 
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    """Удаляет билеты для конкретного клуба или все билеты с поддержкой диапазона дат"""
    try:
        query = db.query(Ticket)
        
        if club_id:
            query = query.filter(Ticket.club_id == club_id)
        
        # Фильтрация по датам
        if start_date or end_date:
            month_filters = []
            
            if start_date:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                start_month = start_dt.month
            else:
                start_month = 1
                
            if end_date:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                end_month = end_dt.month
            else:
                end_month = 12
            
            if start_month <= end_month:
                for month in range(start_month, end_month + 1):
                    month_filter = f".{month:02d}"
                    month_filters.append(Ticket.event_date.like(f"%{month_filter}"))
            else:
                for month in range(start_month, 13):
                    month_filter = f".{month:02d}"
                    month_filters.append(Ticket.event_date.like(f"%{month_filter}"))
                for month in range(1, end_month + 1):
                    month_filter = f".{month:02d}"
                    month_filters.append(Ticket.event_date.like(f"%{month_filter}"))
            
            if month_filters:
                query = query.filter(or_(*month_filters))
        
        # Получаем ID билетов для удаления
        ticket_ids = [t.id for t in query.all()]
        count = len(ticket_ids)
        
        if ticket_ids:
            # Сначала удаляем связанные записи из scan_history (FK constraint)
            db.query(ScanHistory).filter(ScanHistory.ticket_id.in_(ticket_ids)).delete(synchronize_session=False)
            # Потом удаляем билеты
            db.query(Ticket).filter(Ticket.id.in_(ticket_ids)).delete(synchronize_session=False)
        
        db.commit()
        
        return {"status": "deleted", "deleted": count, "club_id": club_id, "start_date": start_date, "end_date": end_date}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Delete error: {str(e)}")


@router.delete("/club/{club_id}")
def delete_tickets_by_club_id(
    club_id: int,
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    """Удаляет билеты для конкретного клуба с поддержкой диапазона дат"""
    try:
        query = db.query(Ticket).filter(Ticket.club_id == club_id)
        
        if start_date or end_date:
            month_filters = []
            
            if start_date:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                start_month = start_dt.month
            else:
                start_month = 1
                
            if end_date:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                end_month = end_dt.month
            else:
                end_month = 12
            
            if start_month <= end_month:
                for month in range(start_month, end_month + 1):
                    month_filter = f".{month:02d}"
                    month_filters.append(Ticket.event_date.like(f"%{month_filter}"))
            else:
                for month in range(start_month, 13):
                    month_filter = f".{month:02d}"
                    month_filters.append(Ticket.event_date.like(f"%{month_filter}"))
                for month in range(1, end_month + 1):
                    month_filter = f".{month:02d}"
                    month_filters.append(Ticket.event_date.like(f"%{month_filter}"))
            
            if month_filters:
                query = query.filter(or_(*month_filters))
        
        # Получаем ID билетов для удаления
        ticket_ids = [t.id for t in query.all()]
        count = len(ticket_ids)
        
        if ticket_ids:
            # Сначала удаляем связанные записи из scan_history (FK constraint)
            db.query(ScanHistory).filter(ScanHistory.ticket_id.in_(ticket_ids)).delete(synchronize_session=False)
            # Потом удаляем билеты
            db.query(Ticket).filter(Ticket.id.in_(ticket_ids)).delete(synchronize_session=False)
        
        db.commit()
        
        return {"status": "deleted", "deleted": count, "club_id": club_id, "start_date": start_date, "end_date": end_date}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Delete error: {str(e)}")


@router.delete("/all")
def delete_all_tickets(db: Session = Depends(get_db)):
    """Удаляет ВСЕ билеты из базы данных"""
    try:
        count = db.query(Ticket).count()
        
        # Сначала удаляем всю историю сканирований (FK constraint)
        db.query(ScanHistory).delete(synchronize_session=False)
        # Потом удаляем все билеты
        db.query(Ticket).delete(synchronize_session=False)
        
        db.commit()
        
        return {"status": "deleted", "count": count, "message": f"Deleted {count} tickets"}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Delete error: {str(e)}")


@router.put("/fix-club-ids")
def fix_club_ids(db: Session = Depends(get_db)):
    """Исправляет club_id для всех билетов на основе city_name.
    Маппинг city_name (английское название) → club_id из таблицы clubs.
    """
    from sqlalchemy import text
    
    try:
        # Загружаем все клубы через raw SQL
        result = db.execute(text("SELECT club_id, city_english FROM clubs WHERE is_active = true"))
        
        # Создаём маппинг city_english -> club_id
        city_to_club_id = {}
        for row in result:
            if row[1]:  # city_english
                city_to_club_id[row[1].lower()] = row[0]  # club_id
        
        print(f"📋 Загружено {len(city_to_club_id)} клубов для маппинга")
        print(f"📋 Маппинг: {city_to_club_id}")
        
        # Находим билеты с club_id = NULL
        tickets_to_fix = db.query(Ticket).filter(Ticket.club_id == None).all()
        
        updated_count = 0
        not_found_cities = set()
        
        for ticket in tickets_to_fix:
            city_name = ticket.city_name
            if city_name:
                club_id = city_to_club_id.get(city_name.lower())
                if club_id:
                    ticket.club_id = club_id
                    updated_count += 1
                    print(f"✅ Билет {ticket.id}: {city_name} -> club_id={club_id}")
                else:
                    not_found_cities.add(city_name)
        
        db.commit()
        
        return {
            "status": "success",
            "updated_count": updated_count,
            "total_with_null": len(tickets_to_fix),
            "not_found_cities": list(not_found_cities),
            "clubs_mapping": city_to_club_id
        }
        
    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Fix club_ids error: {str(e)}")


@router.delete("/by-event")
def delete_tickets_by_event(event_name: str = Query(..., description="Название мероприятия"), db: Session = Depends(get_db)):
    """Удалить все билеты по event_name"""
    
    try:
        if not event_name:
            raise HTTPException(status_code=400, detail="event_name is required")
        
        print(f"🗑️ Попытка удаления билетов для event: '{event_name}'")
        
        # Сначала находим все билеты для удаления
        tickets_to_delete = db.query(Ticket).filter(Ticket.event_name == event_name).all()
        count_before = len(tickets_to_delete)
        
        print(f"📊 Найдено билетов для удаления: {count_before}")
        
        if count_before == 0:
            print(f"⚠️ Билетов не найдено для event: '{event_name}'")
            return {"deleted_count": 0, "event_name": event_name, "message": "No tickets found"}
        
        # Получаем ID всех билетов для удаления
        ticket_ids = [ticket.id for ticket in tickets_to_delete]
        
        print(f"🔗 ID билетов для удаления: {ticket_ids}")
        
        # Сначала удаляем все связанные записи из scan_history
        scan_history_deleted = db.query(ScanHistory).filter(ScanHistory.ticket_id.in_(ticket_ids)).delete(synchronize_session=False)
        
        print(f"🗑️ Удалено записей из scan_history: {scan_history_deleted}")
        
        # Теперь можно безопасно удалить билеты
        tickets_deleted = db.query(Ticket).filter(Ticket.event_name == event_name).delete(synchronize_session=False)
        
        # Подтверждаем транзакцию
        db.commit()
        
        print(f"✅ Удалено билетов: {tickets_deleted}")
        print(f"✅ Общий результат: scan_history={scan_history_deleted}, tickets={tickets_deleted}")
        
        return {
            "deleted_count": tickets_deleted, 
            "event_name": event_name,
            "scan_history_deleted": scan_history_deleted,
            "message": f"Deleted {tickets_deleted} tickets and {scan_history_deleted} scan history records"
        }
        
    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка при удалении билетов: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Delete by event error: {str(e)}")
