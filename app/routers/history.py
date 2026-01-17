from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime
from pydantic import BaseModel

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


class HideDateRange(BaseModel):
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD

@router.post("/hide-for-managers")
def hide_for_all_managers(
    date_range: HideDateRange,
    db: Session = Depends(get_db)
):
    """Скрывает записи от ВСЕХ менеджеров в выбранном диапазоне дат"""
    try:
        start_dt = datetime.strptime(date_range.start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(date_range.end_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    # Обновляем записи в диапазоне дат
    query = db.query(ScanHistory).filter(
        ScanHistory.scan_time >= start_dt,
        ScanHistory.scan_time <= datetime.combine(end_dt, datetime.max.time()),
        ScanHistory.hidden_for_manager == False
    )
    
    count = query.count()
    query.update({"hidden_for_manager": True})
    db.commit()
    
    return {
        "status": "hidden",
        "hidden": count,
        "start_date": date_range.start_date,
        "end_date": date_range.end_date,
        "scope": "all_managers"
    }

@router.post("/hide-for-manager/{club_id}")
def hide_for_city_manager(
    club_id: int,
    date_range: HideDateRange,
    db: Session = Depends(get_db)
):
    """Скрывает записи от менеджера конкретного города в выбранном диапазоне дат"""
    try:
        start_dt = datetime.strptime(date_range.start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(date_range.end_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    # Обновляем записи для конкретного города в диапазоне дат
    query = db.query(ScanHistory).filter(
        ScanHistory.club_id == club_id,
        ScanHistory.scan_time >= start_dt,
        ScanHistory.scan_time <= datetime.combine(end_dt, datetime.max.time()),
        ScanHistory.hidden_for_manager == False
    )
    
    count = query.count()
    query.update({"hidden_for_manager": True})
    db.commit()
    
    return {
        "status": "hidden",
        "hidden": count,
        "club_id": club_id,
        "start_date": date_range.start_date,
        "end_date": date_range.end_date,
        "scope": "city_manager"
    }


# === ВОССТАНОВЛЕНИЕ СКРЫТЫХ ЗАПИСЕЙ ===

@router.post("/restore-hidden")
def restore_all_hidden(
    db: Session = Depends(get_db)
):
    """Восстанавливает ВСЕ скрытые записи (делает видимыми для менеджеров)"""
    query = db.query(ScanHistory).filter(
        ScanHistory.hidden_for_manager == True
    )
    
    count = query.count()
    if count == 0:
        return {
            "status": "no_changes",
            "restored": 0,
            "message": "Нет скрытых записей для восстановления"
        }
    
    query.update({"hidden_for_manager": False})
    db.commit()
    
    return {
        "status": "restored",
        "restored": count,
        "scope": "all"
    }


@router.post("/restore-hidden/{club_id}")
def restore_hidden_by_city(
    club_id: int,
    db: Session = Depends(get_db)
):
    """Восстанавливает скрытые записи для конкретного города"""
    query = db.query(ScanHistory).filter(
        ScanHistory.club_id == club_id,
        ScanHistory.hidden_for_manager == True
    )
    
    count = query.count()
    if count == 0:
        return {
            "status": "no_changes",
            "restored": 0,
            "club_id": club_id,
            "message": f"Нет скрытых записей для club_id={club_id}"
        }
    
    query.update({"hidden_for_manager": False})
    db.commit()
    
    return {
        "status": "restored",
        "restored": count,
        "club_id": club_id,
        "scope": "city"
    }


class RestoreDateRange(BaseModel):
    start_date: str = None  # YYYY-MM-DD (опционально)
    end_date: str = None    # YYYY-MM-DD (опционально)
    club_id: int = None     # опционально


@router.post("/restore-hidden-filtered")
def restore_hidden_filtered(
    filters: RestoreDateRange,
    db: Session = Depends(get_db)
):
    """Восстанавливает скрытые записи с фильтрами по дате и городу"""
    query = db.query(ScanHistory).filter(
        ScanHistory.hidden_for_manager == True
    )
    
    # Применяем фильтры
    if filters.club_id:
        query = query.filter(ScanHistory.club_id == filters.club_id)
    
    if filters.start_date:
        try:
            start_dt = datetime.strptime(filters.start_date, "%Y-%m-%d").date()
            query = query.filter(ScanHistory.scan_time >= start_dt)
        except ValueError:
            pass
    
    if filters.end_date:
        try:
            end_dt = datetime.strptime(filters.end_date, "%Y-%m-%d").date()
            query = query.filter(ScanHistory.scan_time <= datetime.combine(end_dt, datetime.max.time()))
        except ValueError:
            pass
    
    count = query.count()
    if count == 0:
        return {
            "status": "no_changes",
            "restored": 0,
            "message": "Нет скрытых записей с указанными фильтрами"
        }
    
    query.update({"hidden_for_manager": False})
    db.commit()
    
    return {
        "status": "restored",
        "restored": count,
        "filters": {
            "club_id": filters.club_id,
            "start_date": filters.start_date,
            "end_date": filters.end_date
        }
    }