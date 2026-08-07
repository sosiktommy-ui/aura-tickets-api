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
    # ─── Пул ящиков для round-robin рассылок ───
    # Рассылка чередует отправку по всем ящикам (нагрузка размазывается,
    # меньше риск спам-блокировки Gmail). Переопределяется env SMTP_POOL в
    # формате "user1:apppass1,user2:apppass2,...". Если пусто — берётся
    # дефолтный пул рабочих ящиков imprezaevents.org (app-пароли без пробелов).
    SMTP_POOL: str = os.getenv("SMTP_POOL", "")
    MAIL_FROM_NAME: str = os.getenv("MAIL_FROM_NAME", "IMPREZA Events")
    MAIL_REPLY_TO: str = os.getenv("MAIL_REPLY_TO", "info@imprezaevents.org")
    # ─── Темп рассылки (round-robin по пулу ящиков) ───
    # Норма писем на КАЖДЫЙ ящик за сутки (цель 300–400, потолок Gmail Workspace ~2000).
    MAIL_DAILY_PER_ACCOUNT: int = int(os.getenv("MAIL_DAILY_PER_ACCOUNT", "350"))
    # На сколько часов растягиваем дневную норму каждого ящика (пауза считается авто).
    MAIL_SEND_WINDOW_HOURS: float = float(os.getenv("MAIL_SEND_WINDOW_HOURS", "24"))
    # ±доля случайности к паузе, чтобы ритм не выглядел роботом.
    MAIL_JITTER: float = float(os.getenv("MAIL_JITTER", "0.2"))
    # Нижняя граница глобальной паузы между письмами (сек), страховка.
    MAIL_MIN_DELAY: float = float(os.getenv("MAIL_MIN_DELAY", "8"))
    # Legacy (совместимость): если задан фикс-диапазон, используется как раньше.
    MAIL_DELAY_MIN: float = float(os.getenv("MAIL_DELAY_MIN", "0"))
    MAIL_DELAY_MAX: float = float(os.getenv("MAIL_DELAY_MAX", "0"))
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

    def get_smtp_accounts(self) -> list[dict]:
        """Пул ящиков для round-robin рассылок.

        Приоритет — env SMTP_POOL ("user:apppass,user:apppass"). Если пусто —
        дефолтный список рабочих ящиков imprezaevents.org. Гарантированно
        непустой: как минимум возвращает SMTP_USER/SMTP_PASS.
        """
        raw = (self.SMTP_POOL or "").strip()
        if raw:
            out = []
            seen = set()
            for part in raw.split(","):
                part = part.strip()
                if not part or ":" not in part:
                    continue
                user, pw = part.split(":", 1)
                user = user.strip()
                pw = pw.replace(" ", "").strip()  # app-пароли Gmail без пробелов
                if user and pw and user.lower() not in seen:
                    seen.add(user.lower())
                    out.append({"user": user, "password": pw})
            if out:
                return out
        default_pool = [
            {"user": "team@imprezaevents.org",     "password": "chbhrvjrcyhpykvm"},
            {"user": "support@imprezaevents.org",  "password": "rzgebdenevlidetu"},
            {"user": "info@imprezaevents.org",     "password": "ttieiczpavqklqcg"},
            {"user": "news@imprezaevents.org",     "password": "cszldvnowfsbqjrq"},
            {"user": "concerts@imprezaevents.org", "password": "hjdlisoipntzkbmd"},
            {"user": "show@imprezaevents.org",     "password": "ratbpsytjoxjqcdn"},
            {"user": "hello@imprezaevents.org",    "password": "lpneozvahbzoxdmy"},
        ]
        return default_pool or [{"user": self.SMTP_USER, "password": self.SMTP_PASS}]

    class Config:
        env_file = ".env"
        extra = "allow"

@lru_cache()
def get_settings():
    return Settings()

settings = get_settings()
