from __future__ import annotations

import argparse
import sys

from db_backup_utils import BACKUP_PREFIX, DEFAULT_BACKUP_DIR, DEFAULT_KEEP, create_backup, summarize_payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a local compressed backup of Railway Postgres.")
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR), help="Where .json.gz backups should be stored")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="How many latest backups to keep")
    parser.add_argument("--prefix", default=BACKUP_PREFIX, help="Filename prefix for the backup")
    parser.add_argument("--database-url", help="Override DATABASE_URL from .env")
    parser.add_argument("--quiet", action="store_true", help="Only print the final backup path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        result = create_backup(
            database_url=args.database_url,
            backup_dir=args.backup_dir,
            prefix=args.prefix,
            keep=args.keep,
        )
    except Exception as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1

    if args.quiet:
        print(result["path"])
        return 0

    summary = summarize_payload(result["payload"])
    print(f"Backup created: {result['path']}")
    print(f"Source: {summary['redacted_url']}")
    print(f"Created at (UTC): {summary['created_at_utc']}")
    print(f"Total rows: {summary['total_rows']}")
    print("Tables:")
    for table_name, row_count in summary["table_counts"].items():
        print(f"  {table_name}: {row_count}")

    if result["removed"]:
        print("Pruned old backups:")
        for removed_path in result["removed"]:
            print(f"  {removed_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())