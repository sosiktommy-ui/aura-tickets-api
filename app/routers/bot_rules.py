"""Bot Rules CRUD — маппинг URL/products → город/страна.
Доступно только роли 'super'.
"""

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from app.database import get_db
from app.dependencies.auth import require_role

logger = logging.getLogger("impreza.security")

router = APIRouter(prefix="/api/bot-rules", tags=["bot-rules"])

VALID_TYPES = {"url_slug", "country_default", "product_keyword"}


# ── Pydantic schemas ──────────────────────────────────────────────

class BotRuleIn(BaseModel):
    rule_type: str
    pattern: str
    city_name: str
    country_code: str
    description: Optional[str] = None
    is_active: bool = True


class BotRuleOut(BaseModel):
    id: int
    rule_type: str
    pattern: str
    city_name: str
    country_code: str
    description: Optional[str]
    is_active: bool


# ── Helpers ───────────────────────────────────────────────────────

def _validate(body: BotRuleIn) -> None:
    if body.rule_type not in VALID_TYPES:
        raise HTTPException(400, f"rule_type must be one of: {sorted(VALID_TYPES)}")
    if not body.pattern.strip():
        raise HTTPException(400, "pattern cannot be empty")
    if not body.city_name.strip():
        raise HTTPException(400, "city_name cannot be empty")
    if len(body.country_code.strip()) != 2:
        raise HTTPException(400, "country_code must be exactly 2 characters (e.g. TR)")


def _row_to_out(row) -> BotRuleOut:
    return BotRuleOut(
        id=row[0],
        rule_type=row[1],
        pattern=row[2],
        city_name=row[3],
        country_code=row[4],
        description=row[5],
        is_active=bool(row[6]),
    )


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("", response_model=list[BotRuleOut])
def list_rules(
    _auth=Depends(require_role("super")),
    db=Depends(get_db),
):
    rows = db.execute(
        text("""
            SELECT id, rule_type, pattern, city_name, country_code, description, is_active
            FROM bot_rules
            ORDER BY rule_type, pattern
        """)
    ).fetchall()
    return [_row_to_out(r) for r in rows]


@router.post("", response_model=BotRuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(
    body: BotRuleIn,
    _auth=Depends(require_role("super")),
    db=Depends(get_db),
):
    _validate(body)
    row = db.execute(
        text("""
            INSERT INTO bot_rules (rule_type, pattern, city_name, country_code, description, is_active)
            VALUES (:rt, :pat, :city, :cc, :desc, :active)
            RETURNING id, rule_type, pattern, city_name, country_code, description, is_active
        """),
        {
            "rt": body.rule_type,
            "pat": body.pattern.strip(),
            "city": body.city_name.strip(),
            "cc": body.country_code.strip().upper(),
            "desc": body.description,
            "active": body.is_active,
        },
    ).fetchone()
    db.commit()
    logger.info("bot_rule created: id=%s type=%s pattern=%s", row[0], row[1], row[2])
    return _row_to_out(row)


@router.patch("/{rule_id}", response_model=BotRuleOut)
def update_rule(
    rule_id: int,
    body: BotRuleIn,
    _auth=Depends(require_role("super")),
    db=Depends(get_db),
):
    _validate(body)
    row = db.execute(
        text("""
            UPDATE bot_rules
            SET rule_type = :rt,
                pattern = :pat,
                city_name = :city,
                country_code = :cc,
                description = :desc,
                is_active = :active,
                updated_at = NOW()
            WHERE id = :id
            RETURNING id, rule_type, pattern, city_name, country_code, description, is_active
        """),
        {
            "rt": body.rule_type,
            "pat": body.pattern.strip(),
            "city": body.city_name.strip(),
            "cc": body.country_code.strip().upper(),
            "desc": body.description,
            "active": body.is_active,
            "id": rule_id,
        },
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.commit()
    logger.info("bot_rule updated: id=%s", rule_id)
    return _row_to_out(row)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(
    rule_id: int,
    _auth=Depends(require_role("super")),
    db=Depends(get_db),
):
    result = db.execute(text("DELETE FROM bot_rules WHERE id = :id"), {"id": rule_id})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.commit()
    logger.info("bot_rule deleted: id=%s", rule_id)


# ── /bulk — создать сразу несколько правил одним запросом ─────────

class BotRuleBulkIn(BaseModel):
    rules: list[BotRuleIn]


@router.post("/bulk", response_model=list[BotRuleOut], status_code=status.HTTP_201_CREATED)
def bulk_create(
    body: BotRuleBulkIn,
    _auth=Depends(require_role("super")),
    db=Depends(get_db),
):
    """Создать несколько правил атомарно (всё-или-ничего)."""
    if not body.rules:
        raise HTTPException(400, "rules list cannot be empty")
    for r in body.rules:
        _validate(r)

    created = []
    try:
        for r in body.rules:
            row = db.execute(
                text("""
                    INSERT INTO bot_rules (rule_type, pattern, city_name, country_code, description, is_active)
                    VALUES (:rt, :pat, :city, :cc, :desc, :active)
                    RETURNING id, rule_type, pattern, city_name, country_code, description, is_active
                """),
                {
                    "rt": r.rule_type,
                    "pat": r.pattern.strip(),
                    "city": r.city_name.strip(),
                    "cc": r.country_code.strip().upper(),
                    "desc": r.description,
                    "active": r.is_active,
                },
            ).fetchone()
            created.append(_row_to_out(row))
        db.commit()
        logger.info("bot_rules bulk created: %d rules", len(created))
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"bulk create failed: {e}")
    return created


# ── /test — проверить какое правило сработает ─────────────────────

class TestIn(BaseModel):
    products: Optional[str] = ""
    referer: Optional[str] = ""


class TestOut(BaseModel):
    matched: bool
    source: Optional[str] = None
    rule_id: Optional[int] = None
    pattern: Optional[str] = None
    city_name: Optional[str] = None
    country_code: Optional[str] = None
    message: str


@router.post("/test", response_model=TestOut)
def test_rules(
    body: TestIn,
    _auth=Depends(require_role("super")),
    db=Depends(get_db),
):
    """Эмулирует логику qrbot: product_keyword → url_slug → country_default."""
    products = (body.products or "").strip()
    referer = (body.referer or "").strip()

    # 1) product_keyword (regex, ORDER BY LENGTH DESC) — приоритет
    if products:
        rows = db.execute(
            text("""
                SELECT id, pattern, city_name, country_code
                FROM bot_rules
                WHERE rule_type = 'product_keyword' AND is_active = TRUE
                ORDER BY LENGTH(pattern) DESC, pattern
            """)
        ).fetchall()
        for rid, pat, city, cc in rows:
            try:
                if re.search(pat, products, re.IGNORECASE):
                    return TestOut(
                        matched=True, source="product_keyword",
                        rule_id=rid, pattern=pat, city_name=city, country_code=cc,
                        message=f"✅ Совпало по products: '{pat}' → {city} ({cc})",
                    )
            except re.error:
                continue

    # 2) url_slug
    if referer:
        m = re.search(r"impreza\.events/(?:concerts|after)/([a-z0-9\-]+)", referer, re.IGNORECASE)
        if m:
            slug = m.group(1).lower()
            rows = db.execute(
                text("""
                    SELECT id, pattern, city_name, country_code
                    FROM bot_rules WHERE rule_type = 'url_slug' AND is_active = TRUE
                """)
            ).fetchall()
            for rid, pat, city, cc in rows:
                if pat.lower() == slug or pat.lower() in slug:
                    return TestOut(
                        matched=True, source="url_slug",
                        rule_id=rid, pattern=pat, city_name=city, country_code=cc,
                        message=f"✅ Совпало по URL slug '{slug}' → {city} ({cc})",
                    )

        # 3) country_default
        m2 = re.search(r"impreza\.events/([a-z]{2})/?", referer, re.IGNORECASE)
        if m2:
            cc_url = m2.group(1).upper()
            rows = db.execute(
                text("""
                    SELECT id, pattern, city_name, country_code
                    FROM bot_rules WHERE rule_type = 'country_default' AND is_active = TRUE
                """)
            ).fetchall()
            for rid, pat, city, cc in rows:
                if pat.upper() == cc_url:
                    return TestOut(
                        matched=True, source="country_default",
                        rule_id=rid, pattern=pat, city_name=city, country_code=cc,
                        message=f"✅ Совпало по country_default '{cc_url}' → {city} ({cc})",
                    )

    return TestOut(
        matched=False,
        message="❌ Ни одно правило не сработало — бот использует fallback парсинг из products/URL",
    )
