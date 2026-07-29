from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional
import os
import json

class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/railway")
    # ─── Security: все секреты из env-переменных ───
    API_SECRET_KEY: str = os.getenv("JWT_SECRET", "CHANGE_ME_IN_PRODUCTION")
    QR_SECRET_KEY: str = os.getenv("QR_SECRET_KEY", "CHANGE_ME_IN_PRODUCTION")
    INTERNAL_API_KEY: str = os.getenv("INTERNAL_API_KEY", "")
    TILDA_WEBHOOK_SECRET: str = os.getenv("TILDA_WEBHOOK_SECRET", "")
    # ─── Минимальный iat для JWT (для инвалидации старых токенов) ───
    JWT_MIN_IAT: str = os.getenv("JWT_MIN_IAT", "0")
    ALLOWED_ORIGINS: str = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://localhost:3000"
    )
    # ─── Пароли ролей из env (JSON) ───
    ADMIN_PASSWORDS: str = os.getenv("ADMIN_PASSWORDS", "{}")

    # ─── SMTP для рассылок (Рассылки) ───
    # По умолчанию — рабочий Gmail-аккаунт IMPREZA; переопределяется через env.
    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "team@imprezaevents.org")
    SMTP_PASS: str = os.getenv("SMTP_PASS", "chbhrvjrcyhpykvm")
    MAIL_FROM_NAME: str = os.getenv("MAIL_FROM_NAME", "IMPREZA Events")
    MAIL_REPLY_TO: str = os.getenv("MAIL_REPLY_TO", "info@imprezaevents.org")
    # Задержка между письмами (сек) — защита от лимитов Gmail.
    MAIL_DELAY_MIN: float = float(os.getenv("MAIL_DELAY_MIN", "8"))
    MAIL_DELAY_MAX: float = float(os.getenv("MAIL_DELAY_MAX", "18"))
    # Публичный адрес этого API — для ссылок отписки в письмах (фоновый воркер без request).
    PUBLIC_BASE_URL: str = os.getenv(
        "PUBLIC_BASE_URL", "https://aura-tickets-api-production.up.railway.app"
    )

    APP_NAME: str = "AURA Tickets API"
    DEBUG: bool = False

    def get_allowed_origins(self) -> list[str]:
        """Парсит ALLOWED_ORIGINS в список доменов"""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    def get_admin_passwords(self) -> dict:
        """Парсит ADMIN_PASSWORDS из JSON-строки"""
        try:
            return json.loads(self.ADMIN_PASSWORDS)
        except (json.JSONDecodeError, TypeError):
            return {}

    class Config:
        env_file = ".env"
        extra = "allow"

@lru_cache()
def get_settings():
    return Settings()

settings = get_settings()
