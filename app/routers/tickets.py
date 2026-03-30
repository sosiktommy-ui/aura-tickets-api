import logging

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from datetime import date, datetime
from typing import Optional

from app.database import get_db
from app.models import Ticket, ScanHistory
from app.schemas import TicketCreate, TicketResponse, TicketListResponse
from app.security import generate_token, generate_signature
from app.dependencies.auth import require_auth, require_role, AuthInfo

logger = logging.getLogger("impreza.security")

def convert_date_for_db_filter(date_str: str) -> str:
    """Конвертирует дату YYYY-MM-DD в формат для сравнения с event_date в базе (DD.MM)"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.day}.{dt.month}"  # Формат D.M или DD.MM без ведущих нулей

router = APIRouter(prefix="/api/tickets", tags=["tickets"])

@router.post("/", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def create_ticket(ticket: TicketCreate, db: Session = Depends(get_db), auth: AuthInfo = Depends(require_auth)):
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
        visible_to_managers=ticket.visible_to_managers,
        quantity=ticket.quantity  # Количество персон на билете
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
    limit: int = 10000,
    offset: int = 0,
    db: Session = Depends(get_db),
    auth: AuthInfo = Depends(require_auth),
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
    country_code: Optional[str] = None,
    event_name: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    ticket_id: Optional[int] = None,
    ticket_ids: Optional[str] = None,
    db: Session = Depends(get_db),
    auth: AuthInfo = Depends(require_role("manager")),
):
    """Скрыть билеты от менеджеров (visible_to_managers = false)
    
    ВАЖНО: Если указан ticket_id или ticket_ids, скрываются ТОЛЬКО эти билеты!
    """
    try:
        query = db.query(Ticket)
        
        # ПРИОРИТЕТ 1: Если указан конкретный ticket_id — скрываем ТОЛЬКО его
        if ticket_id:
            query = query.filter(Ticket.id == ticket_id)
            print(f"👁️ Скрытие конкретного билета ID={ticket_id}")
        
        # ПРИОРИТЕТ 2: Если указан список ticket_ids — скрываем ТОЛЬКО их
        elif ticket_ids:
            ids_list = [int(x.strip()) for x in ticket_ids.split(",") if x.strip().isdigit()]
            if not ids_list:
                raise HTTPException(status_code=400, detail="Неверный формат ticket_ids")
            query = query.filter(Ticket.id.in_(ids_list))
            print(f"👁️ Скрытие билетов ID={ids_list}")
        
        # ПРИОРИТЕТ 3: Массовое скрытие по фильтрам
        else:
            # Требуем хотя бы один фильтр для безопасности
            if not any([club_id, city_name, country_code, event_name, start_date]):
                raise HTTPException(
                    status_code=400, 
                    detail="Для массового скрытия требуется указать хотя бы один фильтр"
                )
            
            # Фильтр по стране
            if country_code:
                query = query.filter(Ticket.country_code == country_code)
            
            # Фильтр по городу (club_id или city_name)
            if club_id:
                query = query.filter(Ticket.club_id == club_id)
            elif city_name:
                query = query.filter(Ticket.city_name == city_name)
            
            # Фильтр по мероприятию
            if event_name:
                query = query.filter(Ticket.event_name == event_name)
            
            # Фильтр по датам
            if start_date and end_date:
                start_datetime = f"{start_date} 00:00:00"
                end_datetime = f"{end_date} 23:59:59"
                query = query.filter(Ticket.created_at >= start_datetime)
                query = query.filter(Ticket.created_at <= end_datetime)
        
        updated_count = query.update({"visible_to_managers": False}, synchronize_session='fetch')
        db.commit()
        
        print(f"✅ Скрыто {updated_count} билетов от менеджеров")
        return {"message": f"Скрыто {updated_count} билетов от менеджеров", "updated_count": updated_count}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка скрытия: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка скрытия: {str(e)}")


@router.get("/hidden-events")
def get_hidden_events(
    db: Session = Depends(get_db),
    auth: AuthInfo = Depends(require_role("manager")),
):
    """Получить список скрытых мероприятий (уникальные event_name со скрытыми билетами)"""
    try:
        # Получаем уникальные event_name, country_code, city_name для скрытых билетов
        hidden_tickets = db.query(Ticket).filter(Ticket.visible_to_managers == False).all()
        
        # Группируем по event_name с подсчётом и информацией о стране/городе
        events_data = {}
        for ticket in hidden_tickets:
            event_name = ticket.event_name or "Без названия"
            if event_name not in events_data:
                events_data[event_name] = {
                    "event_name": event_name,
                    "country_code": ticket.country_code,
                    "city_name": ticket.city_name,
                    "count": 0
                }
            events_data[event_name]["count"] += 1
        
        return {
            "hidden_events": list(events_data.values()),
            "total_hidden": len(hidden_tickets)
        }
        
    except Exception as e:
        print(f"❌ Ошибка получения скрытых событий: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка: {str(e)}")


@router.put("/show-to-managers")
def show_tickets_to_managers(
    club_id: Optional[int] = None,
    city_name: Optional[str] = None,
    event_name: Optional[str] = None,
    ticket_id: Optional[int] = None,
    ticket_ids: Optional[str] = None,
    order_id: Optional[str] = None,
    db: Session = Depends(get_db),
    auth: AuthInfo = Depends(require_role("manager")),
):
    """Восстановить скрытые билеты (visible_to_managers = true)
    
    ВАЖНО: Если указан ticket_id, ticket_ids или order_id, восстанавливаются ТОЛЬКО эти билеты!
    """
    try:
        query = db.query(Ticket).filter(Ticket.visible_to_managers == False)
        
        # ПРИОРИТЕТ 1: Если указан order_id (номер заказа) — восстанавливаем ТОЛЬКО его
        if order_id:
            query = query.filter(Ticket.order_id == order_id)
            print(f"👁️ Восстановление билета по order_id={order_id}")
        
        # ПРИОРИТЕТ 2: Если указан конкретный ticket_id — восстанавливаем ТОЛЬКО его
        elif ticket_id:
            query = query.filter(Ticket.id == ticket_id)
            print(f"👁️ Восстановление конкретного билета ID={ticket_id}")
        
        # ПРИОРИТЕТ 3: Если указан список ticket_ids — восстанавливаем ТОЛЬКО их
        elif ticket_ids:
            ids_list = [int(x.strip()) for x in ticket_ids.split(",") if x.strip().isdigit()]
            if not ids_list:
                raise HTTPException(status_code=400, detail="Неверный формат ticket_ids")
            query = query.filter(Ticket.id.in_(ids_list))
            print(f"👁️ Восстановление билетов ID={ids_list}")
        
        # ПРИОРИТЕТ 4: Массовое восстановление по фильтрам
        else:
            # Фильтр по городу (club_id или city_name)
            if club_id:
                query = query.filter(Ticket.club_id == club_id)
            elif city_name:
                query = query.filter(Ticket.city_name == city_name)
            
            # Фильтр по мероприятию
            if event_name:
                query = query.filter(Ticket.event_name == event_name)
        
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
    country_code: Optional[str] = None,
    event_name: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    ticket_id: Optional[int] = None,
    ticket_ids: Optional[str] = None,
    deleted_by: Optional[str] = None,
    db: Session = Depends(get_db),
    auth: AuthInfo = Depends(require_role("super")),
):
    """Удалить билеты — сначала архивируем в deleted_tickets, потом удаляем
    
    ВАЖНО: Если указан ticket_id или ticket_ids, удаляются ТОЛЬКО эти билеты!
    Остальные фильтры (даты, город) игнорируются для безопасности.
    """
    try:
        from app.models import DeletedTicket
        
        query = db.query(Ticket)
        
        # ПРИОРИТЕТ 1: Если указан конкретный ticket_id — удаляем ТОЛЬКО его
        if ticket_id:
            query = query.filter(Ticket.id == ticket_id)
            print(f"🗑️ Удаление конкретного билета ID={ticket_id}")
        
        # ПРИОРИТЕТ 2: Если указан список ticket_ids — удаляем ТОЛЬКО их
        elif ticket_ids:
            ids_list = [int(x.strip()) for x in ticket_ids.split(",") if x.strip().isdigit()]
            if not ids_list:
                raise HTTPException(status_code=400, detail="Неверный формат ticket_ids")
            query = query.filter(Ticket.id.in_(ids_list))
            print(f"🗑️ Удаление билетов ID={ids_list}")
        
        # ПРИОРИТЕТ 3: Массовое удаление по фильтрам (опасно!)
        else:
            # Требуем хотя бы один фильтр для безопасности
            if not any([club_id, city_name, country_code, event_name, start_date]):
                raise HTTPException(
                    status_code=400, 
                    detail="Для массового удаления требуется указать хотя бы один фильтр (club_id, city_name, country_code, event_name или start_date)"
                )
            
            # Фильтр по стране
            if country_code:
                query = query.filter(Ticket.country_code == country_code)
            
            # Фильтр по городу
            if club_id:
                query = query.filter(Ticket.club_id == club_id)
            elif city_name:
                query = query.filter(Ticket.city_name == city_name)
            
            # Фильтр по мероприятию
            if event_name:
                query = query.filter(Ticket.event_name == event_name)
            
            # Фильтр по датам
            if start_date and end_date:
                start_datetime = f"{start_date} 00:00:00"
                end_datetime = f"{end_date} 23:59:59"
                query = query.filter(Ticket.created_at >= start_datetime)
                query = query.filter(Ticket.created_at <= end_datetime)
        
        # Получаем билеты для удаления
        tickets_to_delete = query.all()
        deleted_count = len(tickets_to_delete)
        ids_to_delete = [t.id for t in tickets_to_delete]
        
        if tickets_to_delete:
            # ===== АРХИВИРОВАНИЕ: Копируем билеты в deleted_tickets =====
            archived_count = 0
            for ticket in tickets_to_delete:
                try:
                    archived = DeletedTicket(
                        original_id=ticket.id,
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
                        qr_token=ticket.qr_token,
                        qr_signature=ticket.qr_signature,
                        country_code=ticket.country_code,
                        city_name=ticket.city_name,
                        club_id=ticket.club_id,
                        visible_to_managers=ticket.visible_to_managers,
                        quantity=ticket.quantity,
                        status=ticket.status,
                        scan_count=ticket.scan_count,
                        first_scan_at=ticket.first_scan_at,
                        last_scan_at=ticket.last_scan_at,
                        scanned_by=ticket.scanned_by,
                        telegram_message_id=ticket.telegram_message_id,
                        original_created_at=ticket.created_at,
                        original_updated_at=ticket.updated_at,
                        deleted_by=deleted_by or "admin_panel"
                    )
                    db.add(archived)
                    archived_count += 1
                except Exception as e:
                    print(f"⚠️ Не удалось архивировать билет {ticket.id}: {e}")
            
            print(f"📦 Архивировано {archived_count} билетов в deleted_tickets")
            
            # ===== УДАЛЕНИЕ из основных таблиц =====
            # СНАЧАЛА удаляем связанные записи из scan_history (ForeignKey fix)
            db.query(ScanHistory).filter(ScanHistory.ticket_id.in_(ids_to_delete)).delete(synchronize_session='fetch')
            
            # ПОТОМ удаляем билеты
            db.query(Ticket).filter(Ticket.id.in_(ids_to_delete)).delete(synchronize_session='fetch')
        
        db.commit()
        
        print(f"✅ Удалено {deleted_count} билетов: {ids_to_delete}")
        return {
            "message": f"Удалено {deleted_count} билетов", 
            "deleted_count": deleted_count,
            "archived": True
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка удаления: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка удаления: {str(e)}")


@router.get("/{order_id}", response_model=TicketResponse)
def get_ticket(order_id: str, db: Session = Depends(get_db), auth: AuthInfo = Depends(require_auth)):
    ticket = db.query(Ticket).filter(Ticket.order_id == order_id).first()
    
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket {order_id} not found")
    
    return ticket


@router.put("/by-id/{ticket_id}")
def update_ticket_by_id(
    ticket_id: int,
    status: Optional[str] = None,
    first_scan_at: Optional[str] = None,
    scan_count: Optional[int] = None,
    visible_to_managers: Optional[bool] = None,
    db: Session = Depends(get_db),
    auth: AuthInfo = Depends(require_role("manager")),
):
    """Обновить билет по database ID (не order_id!)
    
    Можно обновлять:
    - status: "valid", "used", "cancelled"
    - first_scan_at: дата первого сканирования (или null для сброса)
    - scan_count: количество сканирований
    - visible_to_managers: видимость для менеджеров
    """
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket with id={ticket_id} not found")
    
    # Обновляем только переданные поля
    if status is not None:
        ticket.status = status
        print(f"✅ Билет {ticket_id}: статус → {status}")
    
    if first_scan_at is not None:
        # Если передан "null" или пустая строка — сбрасываем
        if first_scan_at == "" or first_scan_at.lower() == "null":
            ticket.first_scan_at = None
            print(f"✅ Билет {ticket_id}: first_scan_at → NULL")
        else:
            ticket.first_scan_at = first_scan_at
            print(f"✅ Билет {ticket_id}: first_scan_at → {first_scan_at}")
    
    if scan_count is not None:
        ticket.scan_count = scan_count
        print(f"✅ Билет {ticket_id}: scan_count → {scan_count}")
    
    if visible_to_managers is not None:
        ticket.visible_to_managers = visible_to_managers
        print(f"✅ Билет {ticket_id}: visible_to_managers → {visible_to_managers}")
    
    db.commit()
    db.refresh(ticket)
    
    return {"message": f"Билет {ticket_id} обновлён", "ticket": {
        "id": ticket.id,
        "order_id": ticket.order_id,
        "status": ticket.status,
        "first_scan_at": str(ticket.first_scan_at) if ticket.first_scan_at else None,
        "scan_count": ticket.scan_count,
        "visible_to_managers": ticket.visible_to_managers
    }}


@router.get("/token/{token}", response_model=TicketResponse)
def get_ticket_by_token(token: str, db: Session = Depends(get_db), auth: AuthInfo = Depends(require_auth)):
    ticket = db.query(Ticket).filter(Ticket.qr_token == token).first()
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    return ticket


@router.patch("/{order_id}/cancel")
def cancel_ticket(order_id: str, db: Session = Depends(get_db), auth: AuthInfo = Depends(require_role("manager"))):
    ticket = db.query(Ticket).filter(Ticket.order_id == order_id).first()
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    ticket.status = "cancelled"
    db.commit()
    
    return {"status": "cancelled", "order_id": order_id}


@router.patch("/{order_id}/scan")
def increment_scan_count(order_id: str, db: Session = Depends(get_db), auth: AuthInfo = Depends(require_auth)):
    """Увеличить счётчик сканирований билета (для HMAC fallback)
    
    Используется когда билет найден в базе, но HMAC подпись не совпала.
    Увеличивает scan_count на 1 и возвращает обновлённые данные.
    """
    ticket = db.query(Ticket).filter(Ticket.order_id == order_id).first()
    
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket {order_id} not found")
    
    # Увеличиваем счётчик
    ticket.scan_count = (ticket.scan_count or 0) + 1
    ticket.last_scan_at = datetime.now()
    
    # Если первое сканирование - запоминаем время
    if not ticket.first_scan_at:
        ticket.first_scan_at = datetime.now()
    
    # Определяем статус
    quantity = ticket.quantity or 1
    if ticket.scan_count >= quantity:
        ticket.status = "used"
        status_msg = f"Билет использован ({ticket.scan_count}/{quantity})"
        response_status = "used"
    else:
        status_msg = f"Вход {ticket.scan_count}/{quantity}"
        response_status = "valid"
    
    db.commit()
    
    return {
        "status": response_status,
        "message": status_msg,
        "data": {
            "order_id": ticket.order_id,
            "name": ticket.customer_name,
            "email": ticket.customer_email,
            "phone": ticket.customer_phone,
            "ticket_type": ticket.ticket_type,
            "event_date": ticket.event_date,
            "price": ticket.price,
            "quantity": ticket.quantity or 1,
            "scan_count": ticket.scan_count,
        }
    }


@router.patch("/{order_id}/status")
def change_ticket_status(order_id: str, data: dict, db: Session = Depends(get_db), auth: AuthInfo = Depends(require_role("manager"))):
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


@router.put("/{order_id}/hide")
def hide_ticket(order_id: str, db: Session = Depends(get_db), auth: AuthInfo = Depends(require_role("manager"))):
    """Скрыть билет от менеджера (visible_to_managers = false)"""
    ticket = db.query(Ticket).filter(Ticket.order_id == order_id).first()
    
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket {order_id} not found")
    
    ticket.visible_to_managers = False
    db.commit()
    
    return {
        "success": True,
        "order_id": order_id,
        "message": "Ticket hidden from managers"
    }


@router.patch("/{order_id}/reset-expiration")
def reset_ticket_expiration(order_id: str, db: Session = Depends(get_db), auth: AuthInfo = Depends(require_role("manager"))):
    """Сбросить истечение билета - обновить first_scan_at на текущее время"""
    ticket = db.query(Ticket).filter(Ticket.order_id == order_id).first()
    
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket {order_id} not found")
    
    # Обновляем first_scan_at на текущее время
    from datetime import datetime
    new_time = datetime.now()
    ticket.first_scan_at = new_time
    db.commit()
    
    return {
        "success": True,
        "order_id": order_id,
        "message": f"Expiration reset for {order_id}",
        "new_first_scan_at": new_time.isoformat()
    }


@router.delete("/")
def delete_tickets_by_club(
    club_id: int = None, 
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    auth: AuthInfo = Depends(require_role("super")),
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
    db: Session = Depends(get_db),
    auth: AuthInfo = Depends(require_role("super")),
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
def delete_all_tickets(db: Session = Depends(get_db), auth: AuthInfo = Depends(require_role("super"))):
    """Удаляет ВСЕ билеты из базы данных. Requires super admin."""
    logger.warning("DELETE ALL TICKETS requested by %s (role=%s)", auth.name, auth.role)
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
def fix_club_ids(db: Session = Depends(get_db), auth: AuthInfo = Depends(require_role("super"))):
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
def delete_tickets_by_event(
    event_name: str = Query(..., description="Название мероприятия"),
    deleted_by: str = Query(default="admin_panel", description="Кто удалил"),
    db: Session = Depends(get_db),
    auth: AuthInfo = Depends(require_role("super")),
):
    """Удалить все билеты по event_name — с архивированием в deleted_tickets"""
    
    try:
        from app.models import DeletedTicket
        
        if not event_name:
            raise HTTPException(status_code=400, detail="event_name is required")
        
        print(f"🗑️ Попытка удаления билетов для event: '{event_name}'")
        
        # Сначала находим все билеты для удаления
        tickets_to_delete = db.query(Ticket).filter(Ticket.event_name == event_name).all()
        count_before = len(tickets_to_delete)
        
        print(f"📊 Найдено билетов для удаления: {count_before}")
        
        if count_before == 0:
            print(f"⚠️ Билетов не найдено для event: '{event_name}'")
            return {"deleted_count": 0, "event_name": event_name, "message": "No tickets found", "archived": 0}
        
        # Получаем ID всех билетов для удаления
        ticket_ids = [ticket.id for ticket in tickets_to_delete]
        
        print(f"🔗 ID билетов для удаления: {ticket_ids}")
        
        # ===== АРХИВИРОВАНИЕ: Копируем билеты в deleted_tickets =====
        archived_count = 0
        for ticket in tickets_to_delete:
            try:
                archived = DeletedTicket(
                    original_id=ticket.id,
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
                    qr_token=ticket.qr_token,
                    qr_signature=ticket.qr_signature,
                    country_code=ticket.country_code,
                    city_name=ticket.city_name,
                    club_id=ticket.club_id,
                    visible_to_managers=ticket.visible_to_managers,
                    quantity=ticket.quantity,
                    status=ticket.status,
                    scan_count=ticket.scan_count,
                    first_scan_at=ticket.first_scan_at,
                    last_scan_at=ticket.last_scan_at,
                    scanned_by=ticket.scanned_by,
                    telegram_message_id=ticket.telegram_message_id,
                    original_created_at=ticket.created_at,
                    original_updated_at=ticket.updated_at,
                    deleted_by=deleted_by,
                    delete_reason=f"Удаление по EVENT TITLE: {event_name}"
                )
                db.add(archived)
                archived_count += 1
            except Exception as e:
                print(f"⚠️ Не удалось архивировать билет {ticket.id}: {e}")
        
        print(f"📦 Архивировано {archived_count} билетов в deleted_tickets")
        
        # ===== УДАЛЕНИЕ =====
        # Сначала удаляем все связанные записи из scan_history
        scan_history_deleted = db.query(ScanHistory).filter(ScanHistory.ticket_id.in_(ticket_ids)).delete(synchronize_session=False)
        
        print(f"🗑️ Удалено записей из scan_history: {scan_history_deleted}")
        
        # Теперь можно безопасно удалить билеты
        tickets_deleted = db.query(Ticket).filter(Ticket.event_name == event_name).delete(synchronize_session=False)
        
        # Подтверждаем транзакцию
        db.commit()
        
        print(f"✅ Удалено билетов: {tickets_deleted}")
        print(f"✅ Общий результат: archived={archived_count}, scan_history={scan_history_deleted}, tickets={tickets_deleted}")
        
        return {
            "deleted_count": tickets_deleted, 
            "event_name": event_name,
            "archived": archived_count,
            "scan_history_deleted": scan_history_deleted,
            "message": f"Deleted {tickets_deleted} tickets (archived {archived_count})"
        }
        
    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка при удалении билетов: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Delete by event error: {str(e)}")


@router.put("/rename-event")
def rename_event(
    old_name: str = Query(..., description="Текущее название мероприятия"),
    new_name: str = Query(..., description="Новое название мероприятия"),
    db: Session = Depends(get_db),
    auth: AuthInfo = Depends(require_role("super")),
):
    """Переименовать мероприятие — обновить event_name у всех билетов"""
    try:
        if not old_name or not new_name:
            raise HTTPException(status_code=400, detail="old_name and new_name are required")

        count = db.query(Ticket).filter(Ticket.event_name == old_name).count()
        if count == 0:
            return {"updated_count": 0, "message": f"No tickets found with event_name='{old_name}'"}

        db.query(Ticket).filter(Ticket.event_name == old_name).update(
            {Ticket.event_name: new_name}, synchronize_session=False
        )
        db.commit()

        print(f"✅ Переименовано мероприятие: '{old_name}' → '{new_name}' ({count} билетов)")
        return {
            "updated_count": count,
            "old_name": old_name,
            "new_name": new_name,
            "message": f"Renamed {count} tickets"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Rename event error: {str(e)}")
