import hmac
import hashlib
import secrets
from app.config import settings

def generate_token() -> str:
    return secrets.token_hex(16)

def generate_signature(order_id: str, token: str) -> str:
    sign_string = f"{order_id}:{token}:{settings.QR_SECRET_KEY}"
    return hmac.new(
        settings.QR_SECRET_KEY.encode(),
        sign_string.encode(),
        hashlib.sha256
    ).hexdigest()[:16]

def verify_signature_from_qr(qr_data: dict, signature: str) -> bool:
    """
    Проверяет подпись QR-кода так же как её создаёт бот:
    v1: AURA|1|order_id|type|date|name|email|phone|price|paid|token|city|country
    v2: AURA|2|order_id|type|date|name|email|phone|price|quantity|paid|token|city|country
    """
    version = qr_data.get("version", "1")
    
    if version == "2":
        # QUANTITY: Версия 2 с полем quantity
        data_parts = [
            "AURA",
            "2",
            qr_data.get("order_id", ""),
            qr_data.get("ticket_type", ""),
            qr_data.get("event_date", ""),
            qr_data.get("name", ""),
            qr_data.get("email", ""),
            qr_data.get("phone", ""),
            qr_data.get("price", ""),
            str(qr_data.get("quantity", "1")),  # QUANTITY: новое поле
            qr_data.get("paid", ""),
            qr_data.get("token", ""),
            qr_data.get("city", ""),
            qr_data.get("country", "")
        ]
    else:
        # Версия 1 (старый формат)
        data_parts = [
            "AURA",
            qr_data.get("version", "1"),
            qr_data.get("order_id", ""),
            qr_data.get("ticket_type", ""),
            qr_data.get("event_date", ""),
            qr_data.get("name", ""),
            qr_data.get("email", ""),
            qr_data.get("phone", ""),
            qr_data.get("price", ""),
            qr_data.get("paid", ""),
            qr_data.get("token", ""),
            qr_data.get("city", ""),
            qr_data.get("country", "")
        ]
    
    data_for_signing = "|".join(data_parts)
    
    # Генерируем подпись так же как бот
    expected = hmac.new(
        settings.QR_SECRET_KEY.encode('utf-8'),
        data_for_signing.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()[:16].upper()
    
    return hmac.compare_digest(expected, signature.upper())

def verify_signature(order_id: str, token: str, signature: str) -> bool:
    """Старый метод - оставляем для совместимости"""
    expected = generate_signature(order_id, token)
    return hmac.compare_digest(expected, signature)

def parse_qr_data(qr_string: str) -> dict | None:
    """
    Парсит QR строку
    v1: AURA|1|order_id|ticket_type|date|name|email|phone|price|paid|token|city|country|signature (14 полей)
    v2: AURA|2|order_id|ticket_type|date|name|email|phone|price|quantity|paid|token|city|country|signature (15 полей)
    """
    try:
        parts = qr_string.split("|")
        
        if len(parts) < 12 or parts[0] != "AURA":
            return None
        
        version = parts[1] if len(parts) > 1 else "1"
        
        # VERSION 2: С полем quantity (15 полей)
        if version == "2" and len(parts) >= 15:
            return {
                "version": "2",
                "order_id": parts[2],
                "ticket_type": parts[3],
                "event_date": parts[4],
                "name": parts[5],
                "email": parts[6],
                "phone": parts[7],
                "price": parts[8],
                "quantity": int(parts[9]),  # QUANTITY: новое поле
                "paid": parts[10],
                "token": parts[11],
                "city": parts[12],
                "country": parts[13],
                "signature": parts[14]
            }
        
        # VERSION 1: IMPREZA формат с city/country (14 полей)
        if len(parts) >= 14:
            return {
                "version": parts[1],
                "order_id": parts[2],
                "ticket_type": parts[3],
                "event_date": parts[4],
                "name": parts[5],
                "email": parts[6],
                "phone": parts[7],
                "price": parts[8],
                "quantity": 1,  # QUANTITY: default для v1
                "paid": parts[9],
                "token": parts[10],
                "city": parts[11],
                "country": parts[12],
                "signature": parts[13]
            }
        
        # OLD FORMAT: без city/country (12 полей)
        if len(parts) >= 12:
            return {
                "version": parts[1],
                "order_id": parts[2],
                "ticket_type": parts[3],
                "event_date": parts[4],
                "name": parts[5],
                "email": parts[6],
                "phone": parts[7],
                "price": parts[8],
                "quantity": 1,  # QUANTITY: default для старых
                "paid": parts[9],
                "token": parts[10],
                "city": "",
                "country": "",
                "signature": parts[11]
            }
        
        return None
    except Exception:
        return None
