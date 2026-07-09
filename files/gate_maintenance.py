#!/usr/bin/env python3
import argparse

from gate_runtime import configure_logging, prune_old_events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--retention-days", type=int, default=180)
    parser.add_argument(
        "--dry-run", action="store_true", help="Print how many rows would be deleted without deleting them"
    )
    args = parser.parse_args()

    logger = configure_logging(
        "gate_maintenance",
        log_path="/var/log/gate-anpr/gate-maintenance.log",
    )
    if args.dry_run:
        import sqlite3
        from datetime import datetime, timedelta

        # Local time to match captured_at (see prune_old_events).
        cutoff = (datetime.now() - timedelta(days=args.retention_days)).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(args.db_path, timeout=10)
        try:
            row = conn.execute("SELECT COUNT(*) FROM events WHERE captured_at < ?", (cutoff,)).fetchone()
        finally:
            conn.close()
        count = row[0] if row else 0
        logger.info("[dry-run] Would prune %s events older than %s days", count, args.retention_days)
    else:
        prune_old_events(args.db_path, args.retention_days, logger)


if __name__ == "__main__":
    main()
