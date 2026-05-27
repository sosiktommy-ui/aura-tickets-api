from __future__ import annotations

import gzip
import json
import os
from collections import defaultdict, deque
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import psycopg2
from dotenv import load_dotenv
from psycopg2 import sql
from psycopg2.extras import RealDictCursor, execute_values

APP_DIR = Path(__file__).resolve().parent
DEFAULT_BACKUP_DIR = APP_DIR / "backups" / "postgres"
BACKUP_PREFIX = "impreza_pg_backup"
PRE_RESTORE_PREFIX = "impreza_pg_pre_restore"
DEFAULT_KEEP = 30
PRE_RESTORE_KEEP = 10
CHUNK_SIZE = 1000
SKIP_TABLES = {"spatial_ref_sys"}


def load_local_env() -> None:
    env_path = APP_DIR / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def get_database_url(explicit_url: str | None = None) -> str:
    if explicit_url:
        return explicit_url

    load_local_env()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set. Check aura-tickets-api/.env or pass --database-url.")
    return database_url


def connect_database(database_url: str | None = None, *, readonly: bool = False):
    connection = psycopg2.connect(get_database_url(database_url))
    if readonly:
        connection.set_session(readonly=True, autocommit=False)
    return connection


def redact_database_url(database_url: str) -> str:
    parts = urlsplit(database_url)
    hostname = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    username = parts.username or ""
    masked_user = f"{username}:***@" if username else ""
    netloc = f"{masked_user}{hostname}{port}" if hostname else parts.netloc
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def get_public_tables(connection) -> list[str]:
    query = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """
    with connection.cursor() as cursor:
        cursor.execute(query)
        return [row[0] for row in cursor.fetchall() if row[0] not in SKIP_TABLES]


def get_table_columns(connection, table_name: str) -> list[str]:
    query = """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
        ORDER BY ordinal_position
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (table_name,))
        return [row[0] for row in cursor.fetchall()]


def get_primary_key_columns(connection, table_name: str) -> list[str]:
    query = """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
         AND tc.table_name = kcu.table_name
        WHERE tc.table_schema = 'public'
          AND tc.table_name = %s
          AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (table_name,))
        return [row[0] for row in cursor.fetchall()]


def get_foreign_key_dependencies(connection) -> list[tuple[str, str]]:
    query = """
        SELECT parent.relname AS parent_table,
               child.relname AS child_table
        FROM pg_constraint constraint
        JOIN pg_class child
          ON constraint.conrelid = child.oid
        JOIN pg_namespace child_ns
          ON child.relnamespace = child_ns.oid
        JOIN pg_class parent
          ON constraint.confrelid = parent.oid
        JOIN pg_namespace parent_ns
          ON parent.relnamespace = parent_ns.oid
        WHERE constraint.contype = 'f'
          AND child_ns.nspname = 'public'
          AND parent_ns.nspname = 'public'
    """
    with connection.cursor() as cursor:
        cursor.execute(query)
        return [(row[0], row[1]) for row in cursor.fetchall()]


def order_tables_for_restore(tables: list[str], dependencies: list[tuple[str, str]]) -> list[str]:
    dependency_graph: dict[str, set[str]] = defaultdict(set)
    indegree = {table: 0 for table in tables}

    for parent, child in dependencies:
        if parent not in indegree or child not in indegree:
            continue
        if child in dependency_graph[parent]:
            continue
        dependency_graph[parent].add(child)
        indegree[child] += 1

    queue = deque(sorted(table for table, degree in indegree.items() if degree == 0))
    ordered: list[str] = []

    while queue:
        current = queue.popleft()
        ordered.append(current)
        for child in sorted(dependency_graph[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    if len(ordered) != len(tables):
        remaining = sorted(table for table in tables if table not in ordered)
        ordered.extend(remaining)

    return ordered


def serialize_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        return "\\x" + value.hex()
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize_value(item) for key, item in value.items()}
    return value


def fetch_table_rows(connection, table_name: str, columns: list[str], primary_key_columns: list[str]) -> list[dict[str, Any]]:
    column_list = sql.SQL(", ").join(sql.Identifier(column) for column in columns)
    query = sql.SQL("SELECT {columns} FROM {table}").format(
        columns=column_list,
        table=sql.Identifier("public", table_name),
    )

    if primary_key_columns:
        order_by = sql.SQL(", ").join(sql.Identifier(column) for column in primary_key_columns)
        query += sql.SQL(" ORDER BY ") + order_by

    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(query)
        rows = cursor.fetchall()

    return [
        {column: serialize_value(value) for column, value in row.items()}
        for row in rows
    ]


def build_backup_payload(connection, database_url: str) -> dict[str, Any]:
    tables = get_public_tables(connection)
    restore_order = order_tables_for_restore(tables, get_foreign_key_dependencies(connection))

    payload: dict[str, Any] = {
        "format_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": {
            "redacted_url": redact_database_url(database_url),
        },
        "restore_order": restore_order,
        "tables": {},
    }

    for table_name in restore_order:
        columns = get_table_columns(connection, table_name)
        primary_key_columns = get_primary_key_columns(connection, table_name)
        rows = fetch_table_rows(connection, table_name, columns, primary_key_columns)
        payload["tables"][table_name] = {
            "columns": columns,
            "primary_key": primary_key_columns,
            "row_count": len(rows),
            "rows": rows,
        }

    return payload


def ensure_backup_dir(backup_dir: str | Path) -> Path:
    backup_path = Path(backup_dir)
    backup_path.mkdir(parents=True, exist_ok=True)
    return backup_path


def list_backups(backup_dir: str | Path = DEFAULT_BACKUP_DIR, prefix: str = BACKUP_PREFIX) -> list[Path]:
    backup_path = ensure_backup_dir(backup_dir)
    return sorted(backup_path.glob(f"{prefix}_*.json.gz"))


def find_latest_backup(backup_dir: str | Path = DEFAULT_BACKUP_DIR, prefix: str = BACKUP_PREFIX) -> Path:
    backups = list_backups(backup_dir=backup_dir, prefix=prefix)
    if not backups:
        raise FileNotFoundError(f"No backups found in {ensure_backup_dir(backup_dir)}")
    return backups[-1]


def write_backup_payload(
    payload: dict[str, Any],
    backup_dir: str | Path = DEFAULT_BACKUP_DIR,
    prefix: str = BACKUP_PREFIX,
) -> Path:
    backup_path = ensure_backup_dir(backup_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_path = backup_path / f"{prefix}_{timestamp}.json.gz"
    temp_path = Path(f"{final_path}.tmp")

    with gzip.open(temp_path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    os.replace(temp_path, final_path)
    return final_path


def prune_old_backups(
    backup_dir: str | Path = DEFAULT_BACKUP_DIR,
    prefix: str = BACKUP_PREFIX,
    keep: int | None = DEFAULT_KEEP,
) -> list[Path]:
    if keep is None or keep <= 0:
        return []

    backups = list_backups(backup_dir=backup_dir, prefix=prefix)
    if len(backups) <= keep:
        return []

    to_remove = backups[:-keep]
    for file_path in to_remove:
        file_path.unlink(missing_ok=True)
    return to_remove


def create_backup(
    database_url: str | None = None,
    backup_dir: str | Path = DEFAULT_BACKUP_DIR,
    prefix: str = BACKUP_PREFIX,
    keep: int | None = DEFAULT_KEEP,
) -> dict[str, Any]:
    resolved_url = get_database_url(database_url)
    connection = connect_database(resolved_url, readonly=True)
    try:
        payload = build_backup_payload(connection, resolved_url)
    finally:
        connection.close()

    file_path = write_backup_payload(payload, backup_dir=backup_dir, prefix=prefix)
    removed_files = prune_old_backups(backup_dir=backup_dir, prefix=prefix, keep=keep)

    return {
        "path": file_path,
        "payload": payload,
        "removed": removed_files,
    }


def load_backup_payload(backup_file: str | Path) -> dict[str, Any]:
    with gzip.open(Path(backup_file), "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("format_version") != 1:
        raise ValueError(f"Unsupported backup format: {payload.get('format_version')}")
    if "tables" not in payload or "restore_order" not in payload:
        raise ValueError("Backup file is missing required keys")
    return payload


def truncate_tables(connection, tables: list[str]) -> None:
    if not tables:
        return

    query = sql.SQL("TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE").format(
        tables=sql.SQL(", ").join(sql.Identifier("public", table) for table in reversed(tables))
    )
    with connection.cursor() as cursor:
        cursor.execute(query)


def insert_table_rows(connection, table_name: str, columns: list[str], rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    values = [tuple(row.get(column) for column in columns) for row in rows]
    query = sql.SQL("INSERT INTO {table} ({columns}) VALUES %s").format(
        table=sql.Identifier("public", table_name),
        columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
    )

    with connection.cursor() as cursor:
        execute_values(cursor, query.as_string(connection), values, page_size=CHUNK_SIZE)

    return len(values)


def reset_sequences(connection, payload: dict[str, Any]) -> None:
    for table_name in payload["restore_order"]:
        table_payload = payload["tables"][table_name]
        rows = table_payload["rows"]

        with connection.cursor() as cursor:
            for column in table_payload["columns"]:
                cursor.execute(
                    "SELECT pg_get_serial_sequence(%s, %s)",
                    (f"public.{table_name}", column),
                )
                sequence_name = cursor.fetchone()[0]
                if not sequence_name:
                    continue

                values = [row.get(column) for row in rows if row.get(column) is not None]
                if values:
                    cursor.execute("SELECT setval(%s, %s, true)", (sequence_name, max(values)))
                else:
                    cursor.execute("SELECT setval(%s, 1, false)", (sequence_name,))


def restore_backup_payload(connection, payload: dict[str, Any]) -> dict[str, int]:
    table_names = payload["restore_order"]
    truncate_tables(connection, table_names)

    inserted_counts: dict[str, int] = {}
    for table_name in table_names:
        table_payload = payload["tables"][table_name]
        inserted_counts[table_name] = insert_table_rows(
            connection,
            table_name,
            table_payload["columns"],
            table_payload["rows"],
        )

    reset_sequences(connection, payload)
    return inserted_counts


def summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    table_counts = {
        table_name: payload["tables"][table_name]["row_count"]
        for table_name in payload["restore_order"]
    }
    return {
        "created_at_utc": payload.get("created_at_utc"),
        "redacted_url": payload.get("database", {}).get("redacted_url"),
        "table_counts": table_counts,
        "total_rows": sum(table_counts.values()),
    }