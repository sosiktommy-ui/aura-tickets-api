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
    Бот подписывает: AURA|version|order_id|type|date|name|email|phone|price|paid|token
    """
    # Восстанавливаем строку для подписи (без самой подписи)
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
        qr_data.get("token", "")
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
    Формат: AURA|version|order_id|ticket_type|date|name|email|phone|price|paid|token|signature
    """
    try:
        parts = qr_string.split("|")
        
        if len(parts) < 12 or parts[0] != "AURA":
            return None
        
        return {
            "version": parts[1],
            "order_id": parts[2],
            "ticket_type": parts[3],
            "event_date": parts[4],
            "name": parts[5],
            "email": parts[6],
            "phone": parts[7],
            "price": parts[8],
            "paid": parts[9],
            "token": parts[10],
            "signature": parts[11] if len(parts) > 11 else ""
        }
    except Exception:
        return None
