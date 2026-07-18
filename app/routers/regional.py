"""Regional managers CRUD — распределение стран между региональными менеджерами.
Доступно только роли 'super'. Общая конфигурация для всех админов.

Таблицы:
  regional_managers(id, name, color, position, created_at)
  regional_assignments(country_code PK, manager_id FK -> regional_managers ON DELETE CASCADE)

Инвариант: одна страна закреплена максимум за одним менеджером (PK по country_code).
Организационная справка — на бота/QR/билеты не влияет.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.database import get_db
from app.dependencies.auth import require_role

logger = logging.getLogger("impreza.security")

router = APIRouter(prefix="/api/regional", tags=["regional"])


# ── Значения по умолчанию (seed) ──────────────────────────────────
# name, color, [country_code,...]
DEFAULT_CONFIG: list[dict] = [
    {"name": "Ксения",  "color": "#E24B4A", "countries": ["ES", "CA", "KR", "RO", "TR"]},
    {"name": "Мариана", "color": "#2FA968", "countries": ["DE", "NL", "LU", "PT", "BG", "FR", "CH", "IT"]},
    {"name": "Светлана", "color": "#8B7BE8", "countries": []},
]


# ── Pydantic schemas ──────────────────────────────────────────────

class ManagerIn(BaseModel):
    name: str
    color: str = "#8B7BE8"
    position: Optional[int] = None


class ManagerPatch(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    position: Optional[int] = None


class ManagerOut(BaseModel):
    id: int
    name: str
    color: str
    position: int
    countries: list[str] = Field(default_factory=list)


class RegionalOut(BaseModel):
    managers: list[ManagerOut]


class AssignIn(BaseModel):
    country_code: str
    manager_id: Optional[int] = None   # null → открепить


class BulkManagerIn(BaseModel):
    name: str
    color: str = "#8B7BE8"
    countries: list[str] = Field(default_factory=list)


class BulkIn(BaseModel):
    managers: list[BulkManagerIn]


# ── Helpers ───────────────────────────────────────────────────────

def _ensure_tables(db) -> None:
    """Создаёт таблицы при первом обращении и засевает конфигурацию по умолчанию."""
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS regional_managers (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#8B7BE8',
            position INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS regional_assignments (
            country_code VARCHAR(2) PRIMARY KEY,
            manager_id INTEGER REFERENCES regional_managers(id) ON DELETE CASCADE
        )
    """))
    db.commit()

    exists = db.execute(text("SELECT EXISTS (SELECT 1 FROM regional_managers)")).scalar()
    if not exists:
        _seed(db, DEFAULT_CONFIG)


def _seed(db, config: list[dict]) -> None:
    """Полностью перезаписывает конфигурацию переданной (wipe + insert)."""
    db.execute(text("DELETE FROM regional_assignments"))
    db.execute(text("DELETE FROM regional_managers"))
    for pos, m in enumerate(config):
        mid = db.execute(
            text("""
                INSERT INTO regional_managers (name, color, position)
                VALUES (:n, :c, :p) RETURNING id
            """),
            {"n": m["name"], "c": m["color"], "p": pos},
        ).scalar()
        for cc in m.get("countries", []):
            cc2 = (cc or "").strip().upper()
            if len(cc2) != 2:
                continue
            db.execute(
                text("""
                    INSERT INTO regional_assignments (country_code, manager_id)
                    VALUES (:cc, :mid)
                    ON CONFLICT (country_code) DO UPDATE SET manager_id = EXCLUDED.manager_id
                """),
                {"cc": cc2, "mid": mid},
            )
    db.commit()
    logger.info("regional config seeded: %d managers", len(config))


def _load(db) -> list[ManagerOut]:
    rows = db.execute(text("""
        SELECT id, name, color, position FROM regional_managers ORDER BY position, id
    """)).fetchall()
    assigns = db.execute(text("""
        SELECT country_code, manager_id FROM regional_assignments WHERE manager_id IS NOT NULL
    """)).fetchall()
    by_manager: dict[int, list[str]] = {}
    for cc, mid in assigns:
        by_manager.setdefault(mid, []).append(cc)
    for lst in by_manager.values():
        lst.sort()
    return [
        ManagerOut(id=r[0], name=r[1], color=r[2], position=r[3], countries=by_manager.get(r[0], []))
        for r in rows
    ]


def _validate_color(color: str) -> str:
    c = (color or "").strip()
    if not (c.startswith("#") and len(c) in (4, 7)):
        raise HTTPException(400, "color must be a hex string like #E24B4A")
    return c


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("", response_model=RegionalOut)
def get_config(_auth=Depends(require_role("super")), db=Depends(get_db)):
    _ensure_tables(db)
    return RegionalOut(managers=_load(db))


@router.post("/managers", response_model=ManagerOut, status_code=status.HTTP_201_CREATED)
def create_manager(body: ManagerIn, _auth=Depends(require_role("super")), db=Depends(get_db)):
    _ensure_tables(db)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "name cannot be empty")
    color = _validate_color(body.color)
    if body.position is not None:
        pos = body.position
    else:
        pos = (db.execute(text("SELECT COALESCE(MAX(position), -1) + 1 FROM regional_managers")).scalar()) or 0
    mid = db.execute(
        text("INSERT INTO regional_managers (name, color, position) VALUES (:n,:c,:p) RETURNING id"),
        {"n": name, "c": color, "p": pos},
    ).scalar()
    db.commit()
    logger.info("regional manager created: id=%s name=%s", mid, name)
    return ManagerOut(id=mid, name=name, color=color, position=pos, countries=[])


@router.patch("/managers/{manager_id}", response_model=ManagerOut)
def update_manager(manager_id: int, body: ManagerPatch, _auth=Depends(require_role("super")), db=Depends(get_db)):
    _ensure_tables(db)
    fields, params = [], {"id": manager_id}
    if body.name is not None:
        n = body.name.strip()
        if not n:
            raise HTTPException(400, "name cannot be empty")
        fields.append("name = :n"); params["n"] = n
    if body.color is not None:
        fields.append("color = :c"); params["c"] = _validate_color(body.color)
    if body.position is not None:
        fields.append("position = :p"); params["p"] = body.position
    if not fields:
        raise HTTPException(400, "nothing to update")
    row = db.execute(
        text(f"UPDATE regional_managers SET {', '.join(fields)} WHERE id = :id RETURNING id"),
        params,
    ).fetchone()
    if not row:
        raise HTTPException(404, "manager not found")
    db.commit()
    managers = _load(db)
    out = next((m for m in managers if m.id == manager_id), None)
    if not out:
        raise HTTPException(404, "manager not found")
    logger.info("regional manager updated: id=%s", manager_id)
    return out


@router.delete("/managers/{manager_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_manager(manager_id: int, _auth=Depends(require_role("super")), db=Depends(get_db)):
    _ensure_tables(db)
    # ON DELETE CASCADE снимет закрепления (страны станут «не закреплены»)
    result = db.execute(text("DELETE FROM regional_managers WHERE id = :id"), {"id": manager_id})
    if result.rowcount == 0:
        raise HTTPException(404, "manager not found")
    db.commit()
    logger.info("regional manager deleted: id=%s", manager_id)


@router.put("/assign", response_model=RegionalOut)
def assign_country(body: AssignIn, _auth=Depends(require_role("super")), db=Depends(get_db)):
    _ensure_tables(db)
    cc = (body.country_code or "").strip().upper()
    if len(cc) != 2:
        raise HTTPException(400, "country_code must be exactly 2 characters")
    if body.manager_id is None:
        db.execute(text("DELETE FROM regional_assignments WHERE country_code = :cc"), {"cc": cc})
    else:
        exists = db.execute(
            text("SELECT EXISTS (SELECT 1 FROM regional_managers WHERE id = :id)"),
            {"id": body.manager_id},
        ).scalar()
        if not exists:
            raise HTTPException(404, "manager not found")
        db.execute(
            text("""
                INSERT INTO regional_assignments (country_code, manager_id)
                VALUES (:cc, :mid)
                ON CONFLICT (country_code) DO UPDATE SET manager_id = EXCLUDED.manager_id
            """),
            {"cc": cc, "mid": body.manager_id},
        )
    db.commit()
    return RegionalOut(managers=_load(db))


@router.put("/bulk", response_model=RegionalOut)
def bulk_replace(body: BulkIn, _auth=Depends(require_role("super")), db=Depends(get_db)):
    """Заменить всю конфигурацию (для «Сбросить к умолчанию» / импорта)."""
    _ensure_tables(db)
    for m in body.managers:
        _validate_color(m.color)
        if not m.name.strip():
            raise HTTPException(400, "manager name cannot be empty")
    cfg = [{"name": m.name.strip(), "color": m.color.strip(), "countries": m.countries} for m in body.managers]
    _seed(db, cfg)
    return RegionalOut(managers=_load(db))


@router.post("/reset", response_model=RegionalOut)
def reset_config(_auth=Depends(require_role("super")), db=Depends(get_db)):
    """Вернуть конфигурацию по умолчанию (seed)."""
    _ensure_tables(db)
    _seed(db, DEFAULT_CONFIG)
    return RegionalOut(managers=_load(db))
