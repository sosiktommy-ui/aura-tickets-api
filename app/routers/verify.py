from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime

from app.database import get_db
from app.models import Ticket, ScanHistory
from app.schemas import VerifyRequest, VerifyResponse
from app.security import parse_qr_data, verify_signature_from_qr

router = APIRouter(prefix="/api", tags=["verify"])

# QUANTITY + EXPIRY: Время жизни билета после первого сканирования
TICKET_EXPIRY_HOURS = 10

@router.post("/verify", response_model=VerifyResponse)
def verify_ticket(request: VerifyRequest, db: Session = Depends(get_db)):
    
    # 1. Парсим QR
    qr_data = parse_qr_data(request.qr_data)
    
    if not qr_data:
        log_scan(db, None, None, "invalid", request.scanner_id, "Invalid QR format", club_id=None)
        return VerifyResponse(status="invalid", message="Invalid QR format")
    
    order_id = qr_data.get("order_id")
    token = qr_data.get("token")
    signature = qr_data.get("signature", "")
    
    # 2. Проверяем подпись (используем тот же метод что и бот)
    if not verify_signature_from_qr(qr_data, signature):
        log_scan(db, None, order_id, "forged", request.scanner_id, "Invalid signature", club_id=None)
        return VerifyResponse(
            status="invalid",
            message="Forged ticket - invalid signature",
            data=qr_data
        )
    
    # 3. Ищем билет в БД
    ticket = db.query(Ticket).filter(Ticket.qr_token == token).first()
    
    if not ticket:
        # Пробуем найти по order_id
        ticket = db.query(Ticket).filter(Ticket.order_id == order_id).first()
    
    if not ticket:
        log_scan(db, None, order_id, "invalid", request.scanner_id, "Not found in DB", club_id=None)
        return VerifyResponse(
            status="invalid",
            message="Ticket not found in database",
            data=qr_data
        )
    
    # Проверка: если билет скрыт от менеджеров — он "удалён" для сканера
    if ticket.visible_to_managers == False:
        log_scan(db, ticket.id, ticket.order_id, "invalid", request.scanner_id, "Hidden from managers", club_id=ticket.club_id)
        return VerifyResponse(
            status="invalid",
            message="Билет удалён",
            data={
                "order_id": ticket.order_id,
                "name": ticket.customer_name,
                "ticket_type": ticket.ticket_type,
                "email": ticket.customer_email,
                "phone": ticket.customer_phone,
                "price": ticket.price
            }
        )
    
    # 4. Проверяем статус
    if ticket.status == "cancelled":
        log_scan(db, ticket.id, ticket.order_id, "invalid", request.scanner_id, "Cancelled", club_id=ticket.club_id)
        return VerifyResponse(
            status="invalid",
            message="Ticket has been cancelled",
            data=ticket_to_dict(ticket)
        )
    
    if ticket.status == "used":
        ticket.scan_count += 1
        ticket.last_scan_at = datetime.now()
        db.commit()
        
        log_scan(db, ticket.id, ticket.order_id, "duplicate", request.scanner_id, notes=None, club_id=ticket.club_id)
        
        quantity = ticket.quantity or 1
        return VerifyResponse(
            status="used",
            message=f"Билет использован ({ticket.scan_count}/{quantity})",
            data=ticket_to_dict(ticket),
            used_at=ticket.first_scan_at.strftime("%H:%M:%S") if ticket.first_scan_at else None
        )
    
    # QUANTITY + EXPIRY: Проверка истечения срока билета
    if ticket.first_scan_at:
        hours_passed = (datetime.now() - ticket.first_scan_at).total_seconds() / 3600
        if hours_passed > TICKET_EXPIRY_HOURS:
            ticket.scan_count += 1
            ticket.last_scan_at = datetime.now()
            db.commit()
            
            log_scan(db, ticket.id, ticket.order_id, "expired", request.scanner_id, 
                    notes=f"Expired after {hours_passed:.1f}h", club_id=ticket.club_id)
            
            return VerifyResponse(
                status="expired",
                message=f"Билет просрочен (прошло {hours_passed:.1f} ч.)",
                data=ticket_to_dict(ticket),
                used_at=ticket.first_scan_at.strftime("%H:%M:%S") if ticket.first_scan_at else None
            )
    
    # QUANTITY: Логика для билетов на несколько человек
    quantity = ticket.quantity or 1
    scan_count = ticket.scan_count or 0
    
    if scan_count < quantity:
        # Ещё есть входы — разрешить
        ticket.scan_count = scan_count + 1
        ticket.last_scan_at = datetime.now()
        
        if ticket.scan_count == 1:
            ticket.first_scan_at = datetime.now()
        
        # Статус "used" только когда все входы использованы
        if ticket.scan_count >= quantity:
            ticket.status = "used"
        
        ticket.scanned_by = request.scanner_id
        db.commit()
        
        remaining = max(0, quantity - ticket.scan_count)
        
        if quantity > 1:
            message = f"Вход {ticket.scan_count} из {quantity}"
        else:
            message = "Access granted"
        
        log_scan(db, ticket.id, ticket.order_id, "valid", request.scanner_id, 
                notes=f"Entry {ticket.scan_count}/{quantity}", club_id=ticket.club_id)
        
        response_data = ticket_to_dict(ticket)
        response_data["quantity"] = quantity
        response_data["remaining_entries"] = remaining
        
        # Рассчитываем время до истечения
        if ticket.first_scan_at:
            hours_passed = (datetime.now() - ticket.first_scan_at).total_seconds() / 3600
            response_data["hours_until_expiry"] = max(0, TICKET_EXPIRY_HOURS - hours_passed)
        
        return VerifyResponse(
            status="valid",
            message=message,
            data=response_data
        )
    else:
        # Все входы использованы
        ticket.scan_count += 1
        ticket.last_scan_at = datetime.now()
        db.commit()
        
        log_scan(db, ticket.id, ticket.order_id, "duplicate", request.scanner_id, 
                notes=f"All entries used ({scan_count}/{quantity})", club_id=ticket.club_id)
        
        return VerifyResponse(
            status="used",
            message=f"Все входы использованы ({scan_count}/{quantity})",
            data=ticket_to_dict(ticket),
            used_at=ticket.first_scan_at.strftime("%H:%M:%S") if ticket.first_scan_at else None
        )


def ticket_to_dict(ticket: Ticket) -> dict:
    return {
        "order_id": ticket.order_id,
        "name": ticket.customer_name,
        "email": ticket.customer_email,
        "phone": ticket.customer_phone,
        "ticket_type": ticket.ticket_type,
        "event_date": ticket.event_date,
        "price": ticket.price,
        "scan_count": ticket.scan_count,
        "quantity": ticket.quantity or 1,
        "first_scan_at": ticket.first_scan_at.isoformat() if ticket.first_scan_at else None
    }


def log_scan(db: Session, ticket_id, order_id, result, scanner_id, notes=None, club_id=None):
    """IMPREZA: Добавлен параметр club_id для multitenancy"""
    scan = ScanHistory(
        ticket_id=ticket_id,
        order_id=order_id,
        scan_result=result,
        scanner_id=scanner_id,
        notes=notes,
        club_id=club_id
    )
    db.add(scan)
    db.commit()
