"""
API для работы с удалёнными билетами (архив)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional, List
from datetime import datetime

from app.database import get_db
from app.models import Ticket, DeletedTicket, ScanHistory
from app.dependencies.auth import require_auth, require_role, AuthInfo

router = APIRouter(prefix="/api/deleted-tickets", tags=["deleted-tickets"])


@router.get("")
def get_deleted_tickets(
    city_name: Optional[str] = None,
    event_name: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=500, le=2000),
    offset: int = 0,
    db: Session = Depends(get_db),
    auth: AuthInfo = Depends(require_role("super_observer")),
):
    """Получить список удалённых билетов"""
    try:
        query = db.query(DeletedTicket)
        
        # Фильтры
        if city_name:
            query = query.filter(DeletedTicket.city_name == city_name)
        
        if event_name:
            query = query.filter(DeletedTicket.event_name == event_name)
        
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (DeletedTicket.customer_name.ilike(search_pattern)) |
                (DeletedTicket.customer_email.ilike(search_pattern)) |
                (DeletedTicket.order_id.ilike(search_pattern))
            )
        
        # Сортировка по дате удаления (новые первые)
        total = query.count()
        tickets = query.order_by(desc(DeletedTicket.deleted_at)).offset(offset).limit(limit).all()
        
        result = []
        for t in tickets:
            result.append({
                "id": t.id,
                "original_id": t.original_id,
                "order_id": t.order_id,
                "customer_name": t.customer_name,
                "customer_email": t.customer_email,
                "customer_phone": t.customer_phone,
                "event_name": t.event_name,
                "event_date": t.event_date,
                "price": t.price,
                "quantity": t.quantity,
                "status": t.status,
                "city_name": t.city_name,
                "country_code": t.country_code,
                "promocode": t.promocode,
                "qr_token": t.qr_token,
                "deleted_at": str(t.deleted_at) if t.deleted_at else None,
                "deleted_by": t.deleted_by,
                "delete_reason": t.delete_reason,
                "original_created_at": str(t.original_created_at) if t.original_created_at else None,
            })
        
        return {
            "tickets": result,
            "total": total,
            "limit": limit,
            "offset": offset
        }
        
    except Exception as e:
        print(f"❌ Ошибка получения удалённых билетов: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{deleted_ticket_id}/restore")
def restore_ticket(
    deleted_ticket_id: int,
    db: Session = Depends(get_db),
    auth: AuthInfo = Depends(require_role("super")),
):
    """Восстановить билет из архива в основную таблицу"""
    try:
        # Находим удалённый билет
        deleted = db.query(DeletedTicket).filter(DeletedTicket.id == deleted_ticket_id).first()
        
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Удалённый билет #{deleted_ticket_id} не найден")
        
        # Проверяем, нет ли уже билета с таким order_id
        existing = db.query(Ticket).filter(Ticket.order_id == deleted.order_id).first()
        if existing:
            raise HTTPException(
                status_code=400, 
                detail=f"Билет с order_id={deleted.order_id} уже существует (id={existing.id})"
            )
        
        # Создаём новый билет в основной таблице
        restored_ticket = Ticket(
            order_id=deleted.order_id,
            transaction_id=deleted.transaction_id,
            customer_name=deleted.customer_name,
            customer_email=deleted.customer_email,
            customer_phone=deleted.customer_phone,
            ticket_type=deleted.ticket_type,
            event_date=deleted.event_date,
            event_name=deleted.event_name,
            price=deleted.price,
            subtotal=deleted.subtotal,
            discount=deleted.discount,
            payment_amount=deleted.payment_amount,
            promocode=deleted.promocode,
            qr_token=deleted.qr_token,
            qr_signature=deleted.qr_signature,
            country_code=deleted.country_code,
            city_name=deleted.city_name,
            club_id=deleted.club_id,
            visible_to_managers=deleted.visible_to_managers,
            quantity=deleted.quantity,
            status=deleted.status,
            scan_count=deleted.scan_count,
            first_scan_at=deleted.first_scan_at,
            last_scan_at=deleted.last_scan_at,
            scanned_by=deleted.scanned_by,
            telegram_message_id=deleted.telegram_message_id,
        )
        
        db.add(restored_ticket)
        
        # Удаляем из архива
        db.delete(deleted)
        
        db.commit()
        db.refresh(restored_ticket)
        
        print(f"✅ Билет #{deleted_ticket_id} восстановлен как #{restored_ticket.id}")
        
        return {
            "message": f"Билет восстановлен",
            "restored_ticket_id": restored_ticket.id,
            "order_id": restored_ticket.order_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка восстановления билета: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{deleted_ticket_id}/permanent")
def permanently_delete(
    deleted_ticket_id: int,
    db: Session = Depends(get_db),
    auth: AuthInfo = Depends(require_role("super")),
):
    """Полностью удалить билет из архива (необратимо!)"""
    try:
        deleted = db.query(DeletedTicket).filter(DeletedTicket.id == deleted_ticket_id).first()
        
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Билет #{deleted_ticket_id} не найден в архиве")
        
        order_id = deleted.order_id
        db.delete(deleted)
        db.commit()
        
        print(f"🗑️ Билет #{deleted_ticket_id} ({order_id}) полностью удалён из архива")
        
        return {"message": f"Билет {order_id} полностью удалён", "deleted_id": deleted_ticket_id}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка удаления из архива: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
def get_deleted_stats(db: Session = Depends(get_db), auth: AuthInfo = Depends(require_role("super_observer"))):
    """Статистика удалённых билетов"""
    try:
        total = db.query(DeletedTicket).count()
        
        # По городам
        from sqlalchemy import func
        by_city = db.query(
            DeletedTicket.city_name, 
            func.count(DeletedTicket.id)
        ).group_by(DeletedTicket.city_name).all()
        
        # По мероприятиям
        by_event = db.query(
            DeletedTicket.event_name, 
            func.count(DeletedTicket.id)
        ).group_by(DeletedTicket.event_name).order_by(func.count(DeletedTicket.id).desc()).limit(10).all()
        
        return {
            "total_deleted": total,
            "by_city": {city: count for city, count in by_city if city},
            "by_event": {event: count for event, count in by_event if event}
        }
        
    except Exception as e:
        print(f"❌ Ошибка статистики: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/all-with-deleted")
def get_all_tickets_with_deleted(
    city_name: Optional[str] = None,
    event_name: Optional[str] = None,
    search: Optional[str] = None,
    filter_mode: str = Query(default="all", description="all | deleted | active"),
    limit: int = Query(default=10000, le=50000),
    offset: int = 0,
    db: Session = Depends(get_db),
    auth: AuthInfo = Depends(require_role("super_observer")),
):
    """Получить ВСЕ билеты (активные + удалённые) для Super Observer
    
    filter_mode:
    - all: все билеты (активные + удалённые)
    - deleted: только удалённые
    - active: только активные
    """
    try:
        result = []
        
        # ===== АКТИВНЫЕ БИЛЕТЫ =====
        if filter_mode in ("all", "active"):
            query = db.query(Ticket)
            
            if city_name:
                query = query.filter(Ticket.city_name == city_name)
            if event_name:
                query = query.filter(Ticket.event_name == event_name)
            if search:
                search_pattern = f"%{search}%"
                query = query.filter(
                    (Ticket.customer_name.ilike(search_pattern)) |
                    (Ticket.customer_email.ilike(search_pattern)) |
                    (Ticket.order_id.ilike(search_pattern))
                )
            
            active_tickets = query.all()
            for t in active_tickets:
                result.append({
                    "id": t.id,
                    "order_id": t.order_id,
                    "customer_name": t.customer_name,
                    "customer_email": t.customer_email,
                    "customer_phone": t.customer_phone,
                    "ticket_type": t.ticket_type,
                    "event_date": t.event_date,
                    "event_name": t.event_name,
                    "price": t.price,
                    "subtotal": t.subtotal,
                    "promocode": t.promocode,
                    "status": t.status,
                    "scan_count": t.scan_count,
                    "first_scan_at": str(t.first_scan_at) if t.first_scan_at else None,
                    "qr_token": t.qr_token,
                    "qr_signature": t.qr_signature,
                    "created_at": str(t.created_at) if t.created_at else None,
                    "city_name": t.city_name,
                    "country_code": t.country_code,
                    "club_id": t.club_id,
                    "visible_to_managers": t.visible_to_managers,
                    "quantity": t.quantity,
                    # ВАЖНО: Пометка что билет НЕ удалён
                    "_is_deleted": False,
                    "_deleted_at": None,
                    "_deleted_by": None
                })
        
        # ===== УДАЛЁННЫЕ БИЛЕТЫ =====
        if filter_mode in ("all", "deleted"):
            query = db.query(DeletedTicket)
            
            if city_name:
                query = query.filter(DeletedTicket.city_name == city_name)
            if event_name:
                query = query.filter(DeletedTicket.event_name == event_name)
            if search:
                search_pattern = f"%{search}%"
                query = query.filter(
                    (DeletedTicket.customer_name.ilike(search_pattern)) |
                    (DeletedTicket.customer_email.ilike(search_pattern)) |
                    (DeletedTicket.order_id.ilike(search_pattern))
                )
            
            deleted_tickets = query.all()
            for t in deleted_tickets:
                result.append({
                    "id": t.id,
                    "order_id": t.order_id,
                    "customer_name": t.customer_name,
                    "customer_email": t.customer_email,
                    "customer_phone": t.customer_phone,
                    "ticket_type": t.ticket_type,
                    "event_date": t.event_date,
                    "event_name": t.event_name,
                    "price": t.price,
                    "subtotal": t.subtotal,
                    "promocode": t.promocode,
                    "status": t.status,
                    "scan_count": t.scan_count,
                    "first_scan_at": str(t.first_scan_at) if t.first_scan_at else None,
                    "qr_token": t.qr_token,
                    "qr_signature": t.qr_signature,
                    "created_at": str(t.original_created_at) if t.original_created_at else None,
                    "city_name": t.city_name,
                    "country_code": t.country_code,
                    "club_id": t.club_id,
                    "visible_to_managers": t.visible_to_managers,
                    "quantity": t.quantity,
                    # ВАЖНО: Пометка что билет УДАЛЁН
                    "_is_deleted": True,
                    "_deleted_at": str(t.deleted_at) if t.deleted_at else None,
                    "_deleted_by": t.deleted_by,
                    "_original_id": t.original_id
                })
        
        # Сортировка по дате создания (новые первые)
        result.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        
        total = len(result)
        active_count = sum(1 for r in result if not r.get("_is_deleted"))
        deleted_count = sum(1 for r in result if r.get("_is_deleted"))
        
        # Применяем offset/limit
        result = result[offset:offset+limit]
        
        return {
            "tickets": result,
            "total": total,
            "active_count": active_count,
            "deleted_count": deleted_count,
            "filter_mode": filter_mode,
            "limit": limit,
            "offset": offset
        }
        
    except Exception as e:
        print(f"❌ Ошибка получения всех билетов: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
