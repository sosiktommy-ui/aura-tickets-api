from __future__ import annotations

import argparse
import sys
from pathlib import Path

from db_backup_utils import (
    DEFAULT_BACKUP_DIR,
    PRE_RESTORE_KEEP,
    PRE_RESTORE_PREFIX,
    connect_database,
    create_backup,
    find_latest_backup,
    load_backup_payload,
    restore_backup_payload,
    summarize_payload,
)

CONFIRM_WORD = "ВОССТАНОВИТЬ"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Restore Railway Postgres from a local .json.gz backup.")
    parser.add_argument("backup_file", nargs="?", help="Path to the .json.gz backup. Defaults to the latest backup in --backup-dir")
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR), help="Folder where backups are stored")
    parser.add_argument("--database-url", help="Override DATABASE_URL from .env")
    parser.add_argument("--latest", action="store_true", help="Restore the latest backup in --backup-dir")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation")
    parser.add_argument("--dry-run", action="store_true", help="Only show what would be restored")
    parser.add_argument(
        "--skip-pre-restore-backup",
        action="store_true",
        help="Do not create an automatic safety backup before overwriting the database",
    )
    return parser.parse_args()


def resolve_backup_path(args: argparse.Namespace) -> Path:
    if args.backup_file and args.latest:
        raise ValueError("Use either backup_file or --latest, not both")

    if args.backup_file:
        return Path(args.backup_file).expanduser().resolve()

    return find_latest_backup(args.backup_dir)


def main() -> int:
    args = parse_args()

    try:
        backup_path = resolve_backup_path(args)
        payload = load_backup_payload(backup_path)
    except Exception as exc:
        print(f"Restore setup failed: {exc}", file=sys.stderr)
        return 1

    summary = summarize_payload(payload)
    print(f"Selected backup: {backup_path}")
    print(f"Backup time (UTC): {summary['created_at_utc']}")
    print(f"Source DB: {summary['redacted_url']}")
    print(f"Total rows to restore: {summary['total_rows']}")
    print("Tables:")
    for table_name, row_count in summary["table_counts"].items():
        print(f"  {table_name}: {row_count}")

    if args.dry_run:
        print("Dry run only. Database was not modified.")
        return 0

    if not args.yes:
        typed = input(f'Type "{CONFIRM_WORD}" to overwrite current Postgres data: ').strip()
        if typed != CONFIRM_WORD:
            print("Aborted. No changes were made.")
            return 1

    pre_restore_backup_path = None
    if not args.skip_pre_restore_backup:
        try:
            pre_restore = create_backup(
                database_url=args.database_url,
                backup_dir=args.backup_dir,
                prefix=PRE_RESTORE_PREFIX,
                keep=PRE_RESTORE_KEEP,
            )
            pre_restore_backup_path = pre_restore["path"]
            print(f"Safety backup created: {pre_restore_backup_path}")
        except Exception as exc:
            print(f"Pre-restore backup failed: {exc}", file=sys.stderr)
            return 1

    connection = None
    try:
        connection = connect_database(args.database_url)
        with connection:
            inserted_counts = restore_backup_payload(connection, payload)
    except Exception as exc:
        print(f"Restore failed and was rolled back: {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()

    print("Restore committed.")
    if pre_restore_backup_path:
        print(f"Pre-restore safety copy: {pre_restore_backup_path}")
    print("Restored rows:")
    for table_name in payload["restore_order"]:
        print(f"  {table_name}: {inserted_counts.get(table_name, 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())