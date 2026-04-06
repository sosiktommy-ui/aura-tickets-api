from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Dict, Any, Optional
import logging
import hmac
import hashlib
import re

from app.database import get_db
from app.models import Ticket, Club
from app.schemas import TicketCreate, TicketResponse
from app.security import generate_token, generate_signature
from app.config import settings

# Настройка логирования
logger = logging.getLogger("impreza.security")

router = APIRouter(prefix="/api/tilda", tags=["tilda"])

# Русские названия → английские (для нормализации city_name)
_CITY_RU_TO_EN = {
    "Краков": "Krakow", "Варшава": "Warsaw", "Вроцлав": "Wroclaw",
    "Гданьск": "Gdansk", "Люблин": "Lublin", "Катовице": "Katowice",
    "Берлин": "Berlin", "Мюнхен": "Munich", "Франкфурт": "Frankfurt",
    "Кёльн": "Cologne", "Штутгарт": "Stuttgart", "Дрезден": "Dresden",
    "Лейпциг": "Leipzig", "Дюссельдорф": "Dusseldorf",
    "Амстердам": "Amsterdam", "Роттердам": "Rotterdam",
    "Гаага": "Den Haag", "Эйндховен": "Eindhoven",
    "София": "Sofia", "Варна": "Varna", "Пловдив": "Plovdiv",
    "Прага": "Prague", "Брно": "Brno",
    "Барселона": "Barcelona", "Валенсия": "Valencia", "Мадрид": "Madrid",
    "Люксембург": "Luxembourg", "Вена": "Vienna", "Братислава": "Bratislava",
    "Вильнюс": "Vilnius", "Рига": "Riga", "Таллин": "Tallinn",
    "Париж": "Paris", "Лондон": "London", "Дубай": "Dubai",
    "Цюрих": "Zurich", "Сеул": "Seoul",
}


def normalize_city_name(city_name: str, db: Session = None) -> str:
    """Нормализация city_name: русский → английский, с поддержкой префиксов.
    'Роттердам MEET AND GREET' → 'Rotterdam'
    """
    if not city_name:
        return city_name
    
    city_lower = city_name.lower().strip()
    
    # Точное совпадение
    for ru, en in _CITY_RU_TO_EN.items():
        if ru.lower() == city_lower or en.lower() == city_lower:
            return en
    
    # Префиксное совпадение (город + пробел + доп. текст)
    for ru, en in sorted(_CITY_RU_TO_EN.items(), key=lambda x: len(x[0]), reverse=True):
        if city_lower.startswith(ru.lower() + ' ') or city_lower.startswith(ru.lower() + '-'):
            return en
        if city_lower.startswith(en.lower() + ' ') or city_lower.startswith(en.lower() + '-'):
            return en

    # Поиск в clubs таблице (если доступна БД)
    if db:
        try:
            club = db.query(Club).filter(
                Club.city_english.ilike(city_name)
            ).first()
            if club:
                return club.city_english
        except Exception:
            pass
    
    return city_name

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
    
    # Нормализуем city_name (русский → английский, убираем суффиксы типа "MEET AND GREET")
    normalized_city = normalize_city_name(webhook_data.city_name, db)
    if normalized_city != webhook_data.city_name:
        logger.info(f"City normalized: '{webhook_data.city_name}' → '{normalized_city}'")
    
    # Если club_id не указан, пробуем найти по нормализованному городу
    club_id = webhook_data.club_id
    if not club_id and normalized_city:
        try:
            club = db.query(Club).filter(
                Club.city_english.ilike(normalized_city)
            ).first()
            if club:
                club_id = club.club_id
                logger.info(f"Auto-resolved club_id={club_id} for city '{normalized_city}'")
        except Exception as e:
            logger.warning(f"Could not resolve club_id for city '{normalized_city}': {e}")
    
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
        city_name=normalized_city,
        country_code=webhook_data.country_code,
        club_id=club_id,
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
    Webhook endpoint для получения заказов от Tilda.
    Защищён проверкой секрета (X-Tilda-Secret header).
    """
    # ─── Проверка webhook secret ───
    webhook_secret = settings.TILDA_WEBHOOK_SECRET
    if webhook_secret:
        incoming_secret = request.headers.get("X-Tilda-Secret", "")
        if not hmac.compare_digest(incoming_secret, webhook_secret):
            logger.warning("Tilda webhook: invalid secret from %s", request.client.host if request.client else "unknown")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid webhook secret")
    
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
            detail="Webhook processing error"
        )