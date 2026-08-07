"""Рассылки (email campaigns) — создание, запуск и отслеживание массовых писем.

Доступно роли 'super'. Фронт (вкладка «Рассылки») собирает готовый HTML из
шаблона + вписанного текста и отправляет его сюда вместе со списком адресов.
Бэкенд хранит кампанию в БД и рассылает письма в фоновом потоке через SMTP,
обновляя прогресс — так что вкладка может показывать «отправлено N из M» в реалтайме.

Таблица:
  mailing_campaigns(
    id, name, subject, from_name, reply_to, html,
    recipients (JSONB), sent_emails (JSONB), failed (JSONB),
    status(draft|sending|done|error), total, sent, failed_count, error,
    created_by, created_at, started_at, finished_at
  )

Возобновляемость: при повторном запуске письма уже отправленным адресам не дублируются
(todo = recipients − sent_emails − failed).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import random
import re
import smtplib
import ssl
import threading
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from html import unescape
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text

from app.config import settings
from app.database import SessionLocal, get_db
from app.dependencies.auth import require_role

logger = logging.getLogger("impreza.security")

router = APIRouter(prefix="/api/mailings", tags=["mailings"])

# Кампании, которые прямо сейчас рассылаются — чтобы не запустить одну дважды.
_RUNNING: set[int] = set()
_RUNNING_LOCK = threading.Lock()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_RECIPIENTS = 60000

# Плейсхолдер в HTML письма, который на отправке заменяется на персональную ссылку отписки.
UNSUB_PLACEHOLDER = "{{unsubscribe_url}}"


# ── Отписка: подписанные токены (email нельзя подделать/подставить чужой) ──

def _unsub_token(email: str) -> str:
    """token = base64url(email) + '.' + hmac_sha256(email)[:20]."""
    e = (email or "").strip().lower()
    b = base64.urlsafe_b64encode(e.encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(settings.QR_SECRET_KEY.encode("utf-8"), e.encode("utf-8"), hashlib.sha256).hexdigest()[:20]
    return f"{b}.{sig}"


def _unsub_verify(token: str) -> Optional[str]:
    """Проверяет подпись токена и возвращает email (lowercase) либо None."""
    try:
        b, sig = (token or "").split(".", 1)
        pad = "=" * (-len(b) % 4)
        email = base64.urlsafe_b64decode((b + pad).encode("ascii")).decode("utf-8").strip().lower()
        expected = hmac.new(settings.QR_SECRET_KEY.encode("utf-8"), email.encode("utf-8"), hashlib.sha256).hexdigest()[:20]
        if hmac.compare_digest(sig, expected) and _EMAIL_RE.match(email):
            return email
    except Exception:
        return None
    return None


def _unsub_url(email: str, campaign_id: Optional[int] = None) -> str:
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    url = f"{base}/api/mailings/unsubscribe?t={_unsub_token(email)}"
    if campaign_id:
        url += f"&c={campaign_id}"  # атрибуция отписки к кампании (подпись не требуется)
    return url


# ── Трекинг открытий и кликов ─────────────────────────────────────
# Единая схема для админ-рассылок И для внешнего скрипта TUSOVKA: оба
# подписывают токен тем же QR_SECRET_KEY и стучатся в эти же эндпоинты,
# поэтому вся статистика собирается в одну таблицу mailing_events и один дашборд.
#
#   payload открытия: "o|<campaign_id>|<email>"
#   payload клика:     "c|<campaign_id>|<email>|<url>"   (url внутри подписи → нет open-redirect)
#   token = base64url(payload) + '.' + hmac_sha256(payload)[:20]
#
# ВАЖНО: любое изменение схемы токена нужно синхронно править в
# маил/send_tusovka_campaign.py (функция _track_token), иначе токены скрипта
# перестанут проходить проверку на бэкенде.

# 1×1 прозрачный GIF — тело пикселя открытия.
_PIXEL_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


def _track_token(payload: str) -> str:
    b = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(settings.QR_SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:20]
    return f"{b}.{sig}"


def _track_verify(token: str) -> Optional[str]:
    """Проверяет подпись и возвращает исходный payload либо None."""
    try:
        b, sig = (token or "").split(".", 1)
        pad = "=" * (-len(b) % 4)
        payload = base64.urlsafe_b64decode((b + pad).encode("ascii")).decode("utf-8")
        expected = hmac.new(settings.QR_SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:20]
        if hmac.compare_digest(sig, expected):
            return payload
    except Exception:
        return None
    return None


def _open_pixel_url(campaign_id: int, email: str) -> str:
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    tok = _track_token(f"o|{campaign_id}|{(email or '').strip().lower()}")
    return f"{base}/api/mailings/open?t={tok}"


def _click_url(campaign_id: int, email: str, target: str) -> str:
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    real = unescape(target)  # href в шаблоне хранит &amp; — редиректим на настоящий URL
    tok = _track_token(f"c|{campaign_id}|{(email or '').strip().lower()}|{real}")
    return f"{base}/api/mailings/click?t={tok}"


def _should_track_link(url: str) -> bool:
    u = (url or "").strip().lower()
    if not u.startswith(("http://", "https://")):
        return False  # mailto:/tel:/#/cid:/data: — не трогаем
    if u.startswith(settings.PUBLIC_BASE_URL.rstrip("/").lower()):
        return False  # наши собственные ссылки (отписка/трекинг) не оборачиваем
    return True


def _inject_tracking(html: str, campaign_id: int, email: str) -> str:
    """Оборачивает внешние ссылки в клик-редирект и вставляет пиксель открытия.
    Делается персонально на каждого получателя (email в подписи токена)."""
    if not campaign_id or not email:
        return html

    def _wrap(m: "re.Match") -> str:
        quote, url = m.group(1), m.group(2)
        if not _should_track_link(url):
            return m.group(0)
        return f"href={quote}{_click_url(campaign_id, email, url)}{quote}"

    html = re.sub(r'href=(["\'])(.*?)\1', _wrap, html, flags=re.IGNORECASE | re.DOTALL)

    pixel = (f'<img src="{_open_pixel_url(campaign_id, email)}" width="1" height="1" '
             f'alt="" style="display:none;width:1px;height:1px;opacity:0;overflow:hidden;border:0;">')
    if re.search(r"</body>", html, re.IGNORECASE):
        html = re.sub(r"</body>", pixel + "</body>", html, count=1, flags=re.IGNORECASE)
    else:
        html += pixel
    return html


def _record_event(campaign_id: int, email: str, kind: str, url: Optional[str], request: Optional[Request]) -> None:
    db = SessionLocal()
    try:
        _ensure_table(db)
        ua = (request.headers.get("user-agent") if request else "") or ""
        ip = (request.client.host if request and request.client else "") or ""
        db.execute(text("""
            INSERT INTO mailing_events (campaign_id, email, kind, url, user_agent, ip)
            VALUES (:c, :e, :k, :u, :ua, :ip)
        """), {"c": campaign_id, "e": (email or "").strip().lower(), "k": kind,
               "u": (url or "")[:500], "ua": ua[:300], "ip": ip[:64]})
        db.commit()
    except Exception:
        logger.exception("mailings: failed to record %s event (campaign %s)", kind, campaign_id)
    finally:
        db.close()


# ── Pydantic schemas ──────────────────────────────────────────────

class CampaignIn(BaseModel):
    name: str
    subject: str
    html: str
    recipients: list[str] = Field(default_factory=list)
    from_name: Optional[str] = None
    reply_to: Optional[str] = None


class CampaignPatch(BaseModel):
    name: Optional[str] = None
    subject: Optional[str] = None
    html: Optional[str] = None
    recipients: Optional[list[str]] = None
    from_name: Optional[str] = None
    reply_to: Optional[str] = None


class TestIn(BaseModel):
    subject: str
    html: str
    to: str
    from_name: Optional[str] = None
    reply_to: Optional[str] = None


class CampaignOut(BaseModel):
    id: int
    name: str
    subject: str
    from_name: str
    reply_to: str
    status: str
    total: int
    sent: int
    failed_count: int
    recipients_count: int
    error: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    # Трекинг (v1): открытия/клики/отписки. unique — по уникальным адресам.
    opens_unique: int = 0
    opens_total: int = 0
    clicks_unique: int = 0
    clicks_total: int = 0
    unsubs: int = 0


class CampaignDetail(CampaignOut):
    html: str
    recipients: list[str] = Field(default_factory=list)
    sent_emails: list[str] = Field(default_factory=list)
    failed: list[dict] = Field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────

def _ensure_table(db) -> None:
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS mailing_campaigns (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            subject TEXT NOT NULL,
            from_name TEXT NOT NULL DEFAULT 'IMPREZA Events',
            reply_to TEXT NOT NULL DEFAULT '',
            html TEXT NOT NULL DEFAULT '',
            recipients JSONB NOT NULL DEFAULT '[]',
            sent_emails JSONB NOT NULL DEFAULT '[]',
            failed JSONB NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'draft',
            total INTEGER NOT NULL DEFAULT 0,
            sent INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            created_by TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS mailing_unsubscribes (
            email TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            campaign_id INTEGER,
            source TEXT
        )
    """))
    # События трекинга: открытия и клики. Одна строка на каждое событие
    # (уникальные метрики считаются через COUNT(DISTINCT email) в запросе).
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS mailing_events (
            id BIGSERIAL PRIMARY KEY,
            campaign_id INTEGER,
            email TEXT,
            kind TEXT,              -- 'open' | 'click'
            url TEXT,               -- целевая ссылка (для кликов)
            user_agent TEXT,
            ip TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_mailing_events_cid_kind ON mailing_events(campaign_id, kind)"))
    db.commit()


def _load_suppressed(db) -> set[str]:
    """Множество отписавшихся адресов (lowercase)."""
    try:
        rows = db.execute(text("SELECT email FROM mailing_unsubscribes")).fetchall()
        return {(r.email or "").lower() for r in rows}
    except Exception:
        return set()


def _clean_recipients(raw: list[str]) -> list[str]:
    """Нормализует, валидирует и дедуплицирует список адресов (регистр игнорируется)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in raw or []:
        # допускаем строки с несколькими адресами через запятую/перенос/точку с запятой
        for piece in re.split(r"[,\n;\s]+", str(item)):
            e = piece.strip().strip("<>").strip()
            if not e or "@" not in e:
                continue
            if not _EMAIL_RE.match(e):
                continue
            key = e.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
    return out


def _iso(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


_EMPTY_STATS = {"opens_unique": 0, "opens_total": 0, "clicks_unique": 0, "clicks_total": 0, "unsubs": 0}


def _stats_map(db, ids: list[int]) -> dict[int, dict]:
    """Считает открытия/клики/отписки по кампаниям одним махом (id → метрики)."""
    out = {i: dict(_EMPTY_STATS) for i in ids}
    if not ids:
        return out
    try:
        ev = text("""
            SELECT campaign_id, kind, COUNT(*) AS total, COUNT(DISTINCT email) AS uniq
            FROM mailing_events WHERE campaign_id IN :ids GROUP BY campaign_id, kind
        """).bindparams(bindparam("ids", expanding=True))
        for r in db.execute(ev, {"ids": ids}).fetchall():
            s = out.get(r.campaign_id)
            if not s:
                continue
            if r.kind == "open":
                s["opens_total"], s["opens_unique"] = r.total, r.uniq
            elif r.kind == "click":
                s["clicks_total"], s["clicks_unique"] = r.total, r.uniq
        un = text("""
            SELECT campaign_id, COUNT(*) AS n FROM mailing_unsubscribes
            WHERE campaign_id IN :ids GROUP BY campaign_id
        """).bindparams(bindparam("ids", expanding=True))
        for r in db.execute(un, {"ids": ids}).fetchall():
            if r.campaign_id in out:
                out[r.campaign_id]["unsubs"] = r.n
    except Exception:
        logger.exception("mailings: stats query failed")
    return out


def _row_to_out(r, stats: Optional[dict] = None) -> CampaignOut:
    recipients = r.recipients or []
    s = stats or _EMPTY_STATS
    return CampaignOut(
        id=r.id,
        name=r.name,
        subject=r.subject,
        from_name=r.from_name,
        reply_to=r.reply_to or "",
        status=r.status,
        total=r.total,
        sent=r.sent,
        failed_count=r.failed_count,
        recipients_count=len(recipients),
        error=r.error,
        created_by=r.created_by,
        created_at=_iso(r.created_at),
        started_at=_iso(r.started_at),
        finished_at=_iso(r.finished_at),
        opens_unique=s["opens_unique"],
        opens_total=s["opens_total"],
        clicks_unique=s["clicks_unique"],
        clicks_total=s["clicks_total"],
        unsubs=s["unsubs"],
    )


def _plaintext_from_html(html: str) -> str:
    """Грубый текстовый фолбэк из HTML для multipart/alternative."""
    txt = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    txt = re.sub(r"(?is)<br\s*/?>", "\n", txt)
    txt = re.sub(r"(?is)</(p|div|tr|table|h[1-6])>", "\n", txt)
    txt = re.sub(r"(?is)<[^>]+>", "", txt)
    txt = unescape(txt)
    lines = [ln.strip() for ln in txt.splitlines()]
    out, blank = [], 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(ln)
    return "\n".join(out).strip() or "Открой письмо в HTML-режиме."


def _build_message(
    to_email: str, subject: str, html: str, from_name: str, reply_to: str,
    unsubscribe_url: Optional[str] = None, from_email: Optional[str] = None,
    campaign_id: Optional[int] = None,
) -> MIMEMultipart:
    # Персональная ссылка отписки: подставляем плейсхолдер + шлём стандартные заголовки
    # List-Unsubscribe (кнопка «Отписаться» в Gmail/Outlook, one-click по RFC 8058).
    if unsubscribe_url:
        html = html.replace(UNSUB_PLACEHOLDER, unsubscribe_url)

    # Трекинг открытий/кликов (персонально). Для тестовых писем (campaign_id=None) не встраиваем.
    if campaign_id:
        html = _inject_tracking(html, campaign_id, to_email)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, from_email or settings.SMTP_USER))
    msg["To"] = to_email
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="imprezaevents.org")
    msg["X-Priority"] = "3"
    if unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.attach(MIMEText(_plaintext_from_html(html), "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def _smtp_connect(account: Optional[dict] = None) -> smtplib.SMTP:
    """Открывает SMTP-сессию. account={'user','password'}; по умолчанию — SMTP_USER/PASS."""
    user = (account or {}).get("user") or settings.SMTP_USER
    password = (account or {}).get("password") or settings.SMTP_PASS
    s = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30)
    s.ehlo()
    s.starttls(context=ssl.create_default_context())
    s.login(user, password)
    return s


# ── Фоновая рассылка ──────────────────────────────────────────────

def _send_worker(campaign_id: int) -> None:
    """Отправляет письма кампании. Запускается в отдельном потоке."""
    db = SessionLocal()
    try:
        r = db.execute(text("""
            SELECT id, subject, html, from_name, reply_to, recipients, sent_emails, failed
            FROM mailing_campaigns WHERE id = :id
        """), {"id": campaign_id}).fetchone()
        if not r:
            return

        recipients = list(r.recipients or [])
        sent_emails = list(r.sent_emails or [])
        failed = list(r.failed or [])
        done = {e.lower() for e in sent_emails} | {f.get("email", "").lower() for f in failed}

        # Отписавшихся не трогаем и в знаменатель прогресса не берём.
        suppressed = _load_suppressed(db)
        sendable = [e for e in recipients if e.lower() not in suppressed]
        skipped = len(recipients) - len(sendable)
        todo = [e for e in sendable if e.lower() not in done]

        db.execute(text("""
            UPDATE mailing_campaigns
            SET status='sending', total=:total, started_at=COALESCE(started_at, NOW()), error=NULL
            WHERE id=:id
        """), {"id": campaign_id, "total": len(sendable)})
        db.commit()
        if skipped:
            logger.info("mailings: campaign %s — %d recipient(s) skipped (unsubscribed)", campaign_id, skipped)

        if not todo:
            db.execute(text("UPDATE mailing_campaigns SET status='done', finished_at=NOW() WHERE id=:id"),
                       {"id": campaign_id})
            db.commit()
            return

        # Пул ящиков для round-robin: письма чередуются по всем аккаунтам, чтобы
        # размазать нагрузку и снизить риск спам-блокировки. Аккаунты, не прошедшие
        # авторизацию, исключаются из ротации; если ни один не прошёл — кампания в ошибку.
        pool = settings.get_smtp_accounts()
        connections: dict[str, smtplib.SMTP] = {}
        active: list[dict] = []
        for acc in pool:
            try:
                connections[acc["user"]] = _smtp_connect(acc)
                active.append(acc)
                logger.info("mailings: campaign %s — SMTP auth OK -> %s", campaign_id, acc["user"])
            except Exception as e:
                logger.warning("mailings: campaign %s — SMTP auth FAILED -> %s (%s), исключён из ротации",
                               campaign_id, acc["user"], e)
        if not active:
            logger.error("mailings: SMTP connect failed for campaign %s: ни один ящик не авторизовался", campaign_id)
            db.execute(text("UPDATE mailing_campaigns SET status='error', error=:err, finished_at=NOW() WHERE id=:id"),
                       {"id": campaign_id, "err": "SMTP: ни один ящик пула не авторизовался"[:500]})
            db.commit()
            return

        subject, html = r.subject, r.html
        from_name = r.from_name or settings.MAIL_FROM_NAME
        reply_to = r.reply_to or settings.MAIL_REPLY_TO

        def _flush():
            db.execute(text("""
                UPDATE mailing_campaigns
                SET sent_emails=CAST(:se AS JSONB), failed=CAST(:fa AS JSONB),
                    sent=:sent, failed_count=:fc
                WHERE id=:id
            """), {
                "id": campaign_id,
                "se": _json(sent_emails), "fa": _json(failed),
                "sent": len(sent_emails), "fc": len(failed),
            })
            db.commit()

        n = len(todo)
        n_acc = len(active)

        # Темп: дневную норму ОДНОГО ящика (MAIL_DAILY_PER_ACCOUNT) растягиваем на окно
        # MAIL_SEND_WINDOW_HOURS. Ящики чередуются (round-robin), поэтому глобальная
        # пауза между любыми двумя письмами = окно / (норма * кол-во ящиков). В итоге
        # КАЖДЫЙ ящик отправляет ~MAIL_DAILY_PER_ACCOUNT писем за сутки, равномерно.
        # Legacy: если явно задан MAIL_DELAY_MAX>0 — используется старый фикс-диапазон.
        if settings.MAIL_DELAY_MAX > 0:
            base_delay = None
        else:
            per_acc = max(1, settings.MAIL_DAILY_PER_ACCOUNT)
            window_s = max(1.0, settings.MAIL_SEND_WINDOW_HOURS * 3600.0)
            base_delay = max(settings.MAIL_MIN_DELAY, window_s / (per_acc * n_acc))
        logger.info(
            "mailings: campaign %s — %d ящик(ов) в ротации, пауза ~%s сек/письмо "
            "(каждый ящик ~%d писем/%.0fч)",
            campaign_id, n_acc,
            f"{base_delay:.0f}" if base_delay is not None else f"{settings.MAIL_DELAY_MIN:.0f}-{settings.MAIL_DELAY_MAX:.0f}",
            settings.MAIL_DAILY_PER_ACCOUNT, settings.MAIL_SEND_WINDOW_HOURS,
        )

        for i, to_email in enumerate(todo, 1):
            acc = active[(i - 1) % n_acc]          # round-robin по ящикам пула
            user = acc["user"]
            msg = _build_message(to_email, subject, html, from_name, reply_to,
                                 _unsub_url(to_email, campaign_id), from_email=user,
                                 campaign_id=campaign_id)
            try:
                connections[user].sendmail(user, [to_email], msg.as_string())
                sent_emails.append(to_email)
            except smtplib.SMTPServerDisconnected:
                try:
                    connections[user] = _smtp_connect(acc)
                    connections[user].sendmail(user, [to_email], msg.as_string())
                    sent_emails.append(to_email)
                except Exception as e2:
                    failed.append({"email": to_email, "error": str(e2)[:200]})
            except Exception as e:
                failed.append({"email": to_email, "error": str(e)[:200]})

            if i % 10 == 0 or i == n:
                _flush()
            if i < n:
                if base_delay is None:  # legacy фикс-диапазон
                    time.sleep(random.uniform(settings.MAIL_DELAY_MIN, settings.MAIL_DELAY_MAX))
                else:
                    jit = settings.MAIL_JITTER
                    time.sleep(random.uniform(base_delay * (1.0 - jit), base_delay * (1.0 + jit)))

        for s in connections.values():
            try:
                s.quit()
            except Exception:
                pass

        _flush()
        db.execute(text("UPDATE mailing_campaigns SET status='done', finished_at=NOW() WHERE id=:id"),
                   {"id": campaign_id})
        db.commit()
        logger.info("mailings: campaign %s done (sent=%d failed=%d)", campaign_id, len(sent_emails), len(failed))
    except Exception as e:
        logger.exception("mailings: worker crashed for campaign %s", campaign_id)
        try:
            db.execute(text("UPDATE mailing_campaigns SET status='error', error=:err, finished_at=NOW() WHERE id=:id"),
                       {"id": campaign_id, "err": str(e)[:500]})
            db.commit()
        except Exception:
            pass
    finally:
        db.close()
        with _RUNNING_LOCK:
            _RUNNING.discard(campaign_id)


def _json(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("", response_model=list[CampaignOut])
def list_campaigns(_auth=Depends(require_role("super")), db=Depends(get_db)):
    _ensure_table(db)
    rows = db.execute(text("""
        SELECT id, name, subject, from_name, reply_to, status, total, sent, failed_count,
               recipients, error, created_by, created_at, started_at, finished_at
        FROM mailing_campaigns ORDER BY id DESC LIMIT 200
    """)).fetchall()
    stats = _stats_map(db, [r.id for r in rows])
    return [_row_to_out(r, stats.get(r.id)) for r in rows]


# ── Отписка (публично, без авторизации) ───────────────────────────
# ВАЖНО: объявлено ДО GET /{campaign_id}, иначе "unsubscribe" попадёт под int-путь.

_UNSUB_PAGE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Отписка · IMPREZA</title>
<style>
  html,body{{margin:0;height:100%;background:#0b0b0d;color:#e9e7ef;
    font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;}}
  .wrap{{min-height:100%;display:flex;align-items:center;justify-content:center;padding:24px;}}
  .card{{max-width:440px;width:100%;background:#17151c;border:1px solid #2a2731;
    border-radius:16px;padding:36px 32px;text-align:center;}}
  .brand{{font-size:14px;font-weight:800;letter-spacing:5px;text-transform:uppercase;color:#fff;margin-bottom:22px;}}
  .brand span{{color:#ffd800;}}
  h1{{font-size:22px;margin:0 0 12px;color:#fff;}}
  p{{font-size:15px;line-height:1.55;color:#a39fae;margin:0 0 6px;}}
  .mail{{color:#ffd800;font-weight:600;word-break:break-all;}}
  .ico{{font-size:42px;margin-bottom:10px;}}
</style></head>
<body><div class="wrap"><div class="card">
  <div class="brand">IMPREZA <span>EVENTS</span></div>
  <div class="ico">{icon}</div>
  <h1>{title}</h1>
  <p>{body}</p>
  {mail}
</div></div></body></html>"""


def _unsub_html(icon: str, title: str, body: str, email: Optional[str] = None) -> str:
    mail = f'<p class="mail">{email}</p>' if email else ""
    return _UNSUB_PAGE.format(icon=icon, title=title, body=body, mail=mail)


def _do_unsubscribe(token: str, request: Request, campaign_id: Optional[int] = None) -> Optional[str]:
    """Записывает отписку по подписанному токену. Возвращает email или None (плохой токен)."""
    email = _unsub_verify(token)
    if not email:
        return None
    db = SessionLocal()
    try:
        _ensure_table(db)
        src = request.client.host if request and request.client else None
        db.execute(text("""
            INSERT INTO mailing_unsubscribes (email, source, campaign_id)
            VALUES (:e, :src, :cid)
            ON CONFLICT (email) DO NOTHING
        """), {"e": email, "src": (src or "")[:64], "cid": campaign_id})
        db.commit()
        logger.info("mailings: unsubscribe recorded for %s", email)
    except Exception:
        logger.exception("mailings: failed to record unsubscribe for %s", email)
    finally:
        db.close()
    return email


@router.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_page(t: str = "", c: Optional[int] = None, request: Request = None):
    """Страница отписки (клик по ссылке из письма). c — id кампании (атрибуция)."""
    email = _do_unsubscribe(t, request, c)
    if not email:
        return HTMLResponse(
            _unsub_html("⚠️", "Ссылка недействительна",
                        "Похоже, ссылка повреждена или устарела. Напишите нам на info@imprezaevents.org — отпишем вручную."),
            status_code=400,
        )
    return HTMLResponse(_unsub_html(
        "✓", "Вы отписаны",
        "Больше писем от IMPREZA на этот адрес не придёт. Передумали? Просто вернитесь на наши тусовки 🖤",
        email,
    ))


@router.post("/unsubscribe")
def unsubscribe_oneclick(t: str = "", c: Optional[int] = None, request: Request = None):
    """One-click отписка (List-Unsubscribe-Post, RFC 8058) — вызывает почтовый клиент."""
    _do_unsubscribe(t, request, c)
    return Response(status_code=200)


# ── Трекинг: пиксель открытия и клик-редирект (публично, без авторизации) ──
# ВАЖНО: объявлено ДО GET /{campaign_id}, иначе "open"/"click" попадут под int-путь.

@router.get("/open")
def track_open(t: str = "", request: Request = None):
    """Пиксель открытия письма. Всегда отдаёт 1×1 GIF (даже при плохом токене)."""
    payload = _track_verify(t)
    if payload and payload.startswith("o|"):
        try:
            _, cid, email = payload.split("|", 2)
            _record_event(int(cid), email, "open", None, request)
        except Exception:
            logger.debug("mailings: bad open payload")
    return Response(
        content=_PIXEL_GIF,
        media_type="image/gif",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"},
    )


@router.get("/click")
def track_click(t: str = "", request: Request = None):
    """Клик по ссылке: логирует и делает 302-редирект на исходный URL (внутри подписи)."""
    payload = _track_verify(t)
    target = None
    if payload and payload.startswith("c|"):
        try:
            _, cid, email, url = payload.split("|", 3)
            target = url
            _record_event(int(cid), email, "click", url, request)
        except Exception:
            logger.debug("mailings: bad click payload")
    if not target or not target.lower().startswith(("http://", "https://")):
        target = "https://impreza.events"  # фолбэк при повреждённом токене
    return RedirectResponse(url=target, status_code=302)


@router.get("/{campaign_id}", response_model=CampaignDetail)
def get_campaign(campaign_id: int, _auth=Depends(require_role("super")), db=Depends(get_db)):
    _ensure_table(db)
    r = db.execute(text("SELECT * FROM mailing_campaigns WHERE id=:id"), {"id": campaign_id}).fetchone()
    if not r:
        raise HTTPException(404, "campaign not found")
    base = _row_to_out(r, _stats_map(db, [campaign_id]).get(campaign_id))
    return CampaignDetail(
        **base.model_dump(),
        html=r.html or "",
        recipients=list(r.recipients or []),
        sent_emails=list(r.sent_emails or []),
        failed=list(r.failed or []),
    )


@router.post("", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
def create_campaign(body: CampaignIn, auth=Depends(require_role("super")), db=Depends(get_db)):
    _ensure_table(db)
    name = (body.name or "").strip()
    subject = (body.subject or "").strip()
    if not name:
        raise HTTPException(400, "name cannot be empty")
    if not subject:
        raise HTTPException(400, "subject cannot be empty")
    if not (body.html or "").strip():
        raise HTTPException(400, "html cannot be empty")
    recipients = _clean_recipients(body.recipients)
    if len(recipients) > MAX_RECIPIENTS:
        raise HTTPException(400, f"too many recipients (>{MAX_RECIPIENTS})")
    rid = db.execute(text("""
        INSERT INTO mailing_campaigns (name, subject, from_name, reply_to, html, recipients, total, created_by)
        VALUES (:name, :subject, :fn, :rt, :html, CAST(:rec AS JSONB), :total, :by)
        RETURNING id
    """), {
        "name": name, "subject": subject,
        "fn": (body.from_name or settings.MAIL_FROM_NAME).strip(),
        "rt": (body.reply_to or settings.MAIL_REPLY_TO).strip(),
        "html": body.html, "rec": _json(recipients), "total": len(recipients),
        "by": getattr(auth, "name", None),
    }).scalar()
    db.commit()
    r = db.execute(text("""
        SELECT id, name, subject, from_name, reply_to, status, total, sent, failed_count,
               recipients, error, created_by, created_at, started_at, finished_at
        FROM mailing_campaigns WHERE id=:id
    """), {"id": rid}).fetchone()
    logger.info("mailings: campaign created id=%s name=%s recipients=%d", rid, name, len(recipients))
    return _row_to_out(r)


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update_campaign(campaign_id: int, body: CampaignPatch, _auth=Depends(require_role("super")), db=Depends(get_db)):
    _ensure_table(db)
    r = db.execute(text("SELECT status FROM mailing_campaigns WHERE id=:id"), {"id": campaign_id}).fetchone()
    if not r:
        raise HTTPException(404, "campaign not found")
    if r.status == "sending":
        raise HTTPException(409, "campaign is sending — cannot edit")

    fields, params = [], {"id": campaign_id}
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(400, "name cannot be empty")
        fields.append("name=:name"); params["name"] = body.name.strip()
    if body.subject is not None:
        if not body.subject.strip():
            raise HTTPException(400, "subject cannot be empty")
        fields.append("subject=:subject"); params["subject"] = body.subject.strip()
    if body.html is not None:
        fields.append("html=:html"); params["html"] = body.html
    if body.from_name is not None:
        fields.append("from_name=:fn"); params["fn"] = body.from_name.strip() or settings.MAIL_FROM_NAME
    if body.reply_to is not None:
        fields.append("reply_to=:rt"); params["rt"] = body.reply_to.strip()
    if body.recipients is not None:
        rec = _clean_recipients(body.recipients)
        if len(rec) > MAX_RECIPIENTS:
            raise HTTPException(400, f"too many recipients (>{MAX_RECIPIENTS})")
        fields.append("recipients=CAST(:rec AS JSONB)"); params["rec"] = _json(rec)
        fields.append("total=:total"); params["total"] = len(rec)
    if not fields:
        raise HTTPException(400, "nothing to update")
    db.execute(text(f"UPDATE mailing_campaigns SET {', '.join(fields)} WHERE id=:id"), params)
    db.commit()
    r = db.execute(text("""
        SELECT id, name, subject, from_name, reply_to, status, total, sent, failed_count,
               recipients, error, created_by, created_at, started_at, finished_at
        FROM mailing_campaigns WHERE id=:id
    """), {"id": campaign_id}).fetchone()
    return _row_to_out(r)


@router.post("/{campaign_id}/send", response_model=CampaignOut)
def send_campaign(campaign_id: int, _auth=Depends(require_role("super")), db=Depends(get_db)):
    """Запускает (или возобновляет) рассылку в фоне. Уже отправленным письма не дублируются."""
    _ensure_table(db)
    r = db.execute(text("""
        SELECT id, name, subject, from_name, reply_to, status, total, sent, failed_count,
               recipients, error, created_by, created_at, started_at, finished_at
        FROM mailing_campaigns WHERE id=:id
    """), {"id": campaign_id}).fetchone()
    if not r:
        raise HTTPException(404, "campaign not found")
    if not (r.recipients or []):
        raise HTTPException(400, "no recipients")

    with _RUNNING_LOCK:
        if campaign_id in _RUNNING or r.status == "sending":
            raise HTTPException(409, "campaign is already sending")
        _RUNNING.add(campaign_id)

    db.execute(text("UPDATE mailing_campaigns SET status='sending', error=NULL WHERE id=:id"),
               {"id": campaign_id})
    db.commit()

    threading.Thread(target=_send_worker, args=(campaign_id,), daemon=True).start()
    logger.info("mailings: campaign %s send started", campaign_id)
    r = db.execute(text("""
        SELECT id, name, subject, from_name, reply_to, status, total, sent, failed_count,
               recipients, error, created_by, created_at, started_at, finished_at
        FROM mailing_campaigns WHERE id=:id
    """), {"id": campaign_id}).fetchone()
    return _row_to_out(r)


@router.post("/test")
def send_test(body: TestIn, _auth=Depends(require_role("super"))):
    """Отправляет одно тестовое письмо на указанный адрес (синхронно)."""
    to = (body.to or "").strip()
    if not _EMAIL_RE.match(to):
        raise HTTPException(400, "invalid test email")
    if not (body.html or "").strip():
        raise HTTPException(400, "html cannot be empty")
    acc = settings.get_smtp_accounts()[0]
    try:
        server = _smtp_connect(acc)
        msg = _build_message(
            to, (body.subject or "IMPREZA — тест").strip(), body.html,
            (body.from_name or settings.MAIL_FROM_NAME).strip(),
            (body.reply_to or settings.MAIL_REPLY_TO).strip(),
            _unsub_url(to), from_email=acc["user"],
        )
        server.sendmail(acc["user"], [to], msg.as_string())
        try:
            server.quit()
        except Exception:
            pass
    except Exception as e:
        logger.error("mailings: test send failed: %s", e)
        raise HTTPException(502, f"SMTP error: {e}")
    return {"ok": True, "to": to}


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_campaign(campaign_id: int, _auth=Depends(require_role("super")), db=Depends(get_db)):
    _ensure_table(db)
    with _RUNNING_LOCK:
        if campaign_id in _RUNNING:
            raise HTTPException(409, "campaign is sending — cannot delete")
    res = db.execute(text("DELETE FROM mailing_campaigns WHERE id=:id"), {"id": campaign_id})
    if res.rowcount == 0:
        raise HTTPException(404, "campaign not found")
    db.commit()
    logger.info("mailings: campaign %s deleted", campaign_id)
