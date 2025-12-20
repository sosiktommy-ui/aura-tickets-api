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

def verify_signature(order_id: str, token: str, signature: str) -> bool:
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
