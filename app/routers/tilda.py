from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Dict, Any, Optional
import logging

from app.database import get_db
from app.models import Ticket
from app.schemas import TicketCreate, TicketResponse
from app.security import generate_token, generate_signature

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tilda", tags=["tilda"])

class TildaWebhookData:
    """Схема данных от Tilda"""
    def __init__(self, data: Dict[str, Any]):
        self.raw_data = data
        self.order_id = data.get('orderid', '')
        self.transaction_id = data.get('tranid', '')
        self.customer_name = data.get('name', '')
        self.customer_email = data.get('email', '')
        self.customer_phone = data.get('phone', '')
        self.payment_amount = float(data.get('amount', 0))
        self.payment_status = data.get('status', '')
        
        # Дополнительные поля из формы
        self.ticket_type = data.get('ticket_type', 'Standard')
        self.event_date = data.get('event_date', '')
        self.event_name = data.get('event_name', '')
        self.city_name = data.get('city', '')
        self.country_code = data.get('country', 'RU')
        self.club_id = int(data.get('club_id', 0)) if data.get('club_id') else None
        self.promocode = data.get('promocode', '')

def process_tilda_order(webhook_data: TildaWebhookData, db: Session) -> Ticket:
    """Обработка заказа от Tilda"""
    
    # Проверяем, существует ли уже билет с таким order_id
    existing_ticket = db.query(Ticket).filter(Ticket.order_id == webhook_data.order_id).first()
    if existing_ticket:
        logger.info(f"Ticket with order_id {webhook_data.order_id} already exists")
        return existing_ticket
    
    # Генерируем токен и подпись для QR-кода
    qr_token = generate_token()
    qr_signature = generate_signature(webhook_data.order_id, qr_token)
    
    # Создаем новый билет
    ticket_data = TicketCreate(
        order_id=webhook_data.order_id,
        transaction_id=webhook_data.transaction_id,
        customer_name=webhook_data.customer_name,
        customer_email=webhook_data.customer_email,
        customer_phone=webhook_data.customer_phone,
        ticket_type=webhook_data.ticket_type,
        event_date=webhook_data.event_date,
        event_name=webhook_data.event_name,
        price=webhook_data.payment_amount,
        discount=0,
        payment_amount=webhook_data.payment_amount,
        promocode=webhook_data.promocode,
        qr_token=qr_token,
        qr_signature=qr_signature,
        city_name=webhook_data.city_name,
        country_code=webhook_data.country_code,
        club_id=webhook_data.club_id,
        visible_to_managers=True
    )
    
    db_ticket = Ticket(
        order_id=ticket_data.order_id,
        transaction_id=ticket_data.transaction_id,
        customer_name=ticket_data.customer_name,
        customer_email=ticket_data.customer_email,
        customer_phone=ticket_data.customer_phone,
        ticket_type=ticket_data.ticket_type,
        event_date=ticket_data.event_date,
        event_name=ticket_data.event_name,
        price=ticket_data.price,
        discount=ticket_data.discount,
        payment_amount=ticket_data.payment_amount,
        promocode=ticket_data.promocode,
        qr_token=ticket_data.qr_token,
        qr_signature=ticket_data.qr_signature,
        status="valid",
        city_name=ticket_data.city_name,
        country_code=ticket_data.country_code,
        club_id=ticket_data.club_id,
        visible_to_managers=ticket_data.visible_to_managers
    )
    
    db.add(db_ticket)
    db.commit()
    db.refresh(db_ticket)
    
    logger.info(f"Created new ticket: {webhook_data.order_id}")
    return db_ticket

@router.post("/webhook", status_code=status.HTTP_200_OK)
async def tilda_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Webhook endpoint для получения заказов от Tilda
    
    Ожидаемый формат данных от Tilda:
    {
        "orderid": "12345",
        "tranid": "T-67890",
        "name": "Иван Иванов", 
        "email": "ivan@example.com",
        "phone": "+7900123456",
        "amount": "1500.00",
        "status": "confirmed",
        "ticket_type": "VIP",
        "event_date": "25.12",
        "event_name": "New Year Party",
        "city": "Moscow",
        "country": "RU",
        "club_id": "1",
        "promocode": "SAVE20"
    }
    """
    try:
        # Получаем данные от Tilda
        if request.headers.get("content-type") == "application/json":
            data = await request.json()
        else:
            # Tilda может отправлять form-data
            form_data = await request.form()
            data = dict(form_data)
        
        logger.info(f"Received Tilda webhook: {data}")
        
        # Парсим данные
        webhook_data = TildaWebhookData(data)
        
        # Проверяем обязательные поля
        if not webhook_data.order_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing required field: orderid"
            )
        
        if not webhook_data.customer_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing required field: name"
            )
        
        # Обрабатываем только успешные платежи
        if webhook_data.payment_status.lower() not in ['confirmed', 'paid', 'success']:
            logger.info(f"Skipping order {webhook_data.order_id} with status: {webhook_data.payment_status}")
            return {"status": "skipped", "reason": f"Payment status: {webhook_data.payment_status}"}
        
        # Создаем билет
        ticket = process_tilda_order(webhook_data, db)
        
        return {
            "status": "success", 
            "order_id": webhook_data.order_id,
            "ticket_id": ticket.id,
            "qr_token": ticket.qr_token
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing Tilda webhook: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/test", status_code=status.HTTP_200_OK)
def test_endpoint():
    """Тестовый endpoint для проверки работы Tilda роутера"""
    return {
        "status": "ok",
        "message": "Tilda webhook endpoint is working",
        "endpoint": "/api/tilda/webhook"
    }

@router.post("/test-webhook", status_code=status.HTTP_200_OK) 
async def test_webhook(request: Request, db: Session = Depends(get_db)):
    """Тестовый webhook с примером данных"""
    test_data = {
        "orderid": f"TEST-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "tranid": f"T-{datetime.now().strftime('%H%M%S')}",
        "name": "Тестовый Пользователь",
        "email": "test@example.com", 
        "phone": "+7900123456",
        "amount": "1000.00",
        "status": "confirmed",
        "ticket_type": "Standard",
        "event_date": "31.12",
        "event_name": "Test Event",
        "city": "Moscow",
        "country": "RU",
        "club_id": "1",
        "promocode": ""
    }
    
    webhook_data = TildaWebhookData(test_data)
    ticket = process_tilda_order(webhook_data, db)
    
    return {
        "status": "test_success",
        "ticket_id": ticket.id,
        "order_id": ticket.order_id,
        "qr_token": ticket.qr_token,
        "test_data": test_data
    }