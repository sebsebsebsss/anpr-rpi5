#!/usr/bin/env python3
"""Create private configuration snapshots, optionally including SQLite history."""

import argparse
import fcntl
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_NAME = re.compile(r"^backup-\d{8}T\d{12}Z$")


def create_backup(db_path, destination, config_paths, keep=7):
    if keep < 1:
        raise ValueError("keep must be at least 1")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    with (destination / ".lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _create_backup_locked(Path(db_path) if db_path is not None else None, destination, config_paths, keep)


def _create_backup_locked(db_path, destination, config_paths, keep):
    stage = Path(tempfile.mkdtemp(prefix=".pending-", dir=destination))
    try:
        if db_path is not None:
            backup_db = stage / "events.db"
            # mode=ro avoids silently creating an empty database after a path typo.
            source = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
            target = sqlite3.connect(backup_db)
            try:
                source.backup(target, pages=256, sleep=0.05)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("SQLite backup failed its integrity check")
                # An existing but empty/wrong database is not a useful gate backup.
                target.execute("SELECT id FROM events LIMIT 1").fetchall()
            finally:
                target.close()
                source.close()
            backup_db.chmod(0o600)
        config_manifest = {}
        for value in config_paths:
            path = Path(value)
            if not path.exists():
                continue
            if path.name in config_manifest or path.name in {"events.db", "manifest.json"}:
                raise ValueError(f"Duplicate backup filename: {path.name}")
            target_path = stage / path.name
            shutil.copyfile(path, target_path)
            target_path.chmod(0o600)
            config_manifest[path.name] = str(path)
        now = datetime.now(timezone.utc)
        manifest = stage / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "created_at": now.isoformat(),
                    "database": str(db_path) if db_path else None,
                    "config_files": config_manifest,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        manifest.chmod(0o600)
        completed = destination / now.strftime("backup-%Y%m%dT%H%M%S%fZ")
        stage.rename(completed)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    # Publish the complete snapshot before removing any older successful copies.
    snapshots = sorted(
        path
        for path in destination.iterdir()
        if SNAPSHOT_NAME.fullmatch(path.name)
        and path.is_dir()
        and not path.is_symlink()
        and (path / "manifest.json").is_file()
    )
    for obsolete in snapshots[:-keep]:
        shutil.rmtree(obsolete)
    return completed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-database", action="store_true", help="Also back up vehicle history")
    parser.add_argument("--db-path")
    parser.add_argument("--env-path", default="/etc/gate_anpr.env")
    parser.add_argument("--destination", default="/var/backups/gate-anpr")
    parser.add_argument("--keep", type=int, default=7)
    parser.add_argument("--config", action="append", help="File to include; repeat for multiple files")
    args = parser.parse_args()
    persisted = {}
    env_path = Path(args.env_path)
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            persisted[key.strip()] = value
    db_path = (
        args.db_path
        or os.getenv("GATE_ANPR_EVENTS_DB")
        or persisted.get("GATE_ANPR_EVENTS_DB", "/opt/gate_anpr/events.db")
    )
    config_paths = (
        args.config
        if args.config is not None
        else [
            args.env_path,
            os.getenv("PLATE_ALLOWLIST_PATH") or persisted.get("PLATE_ALLOWLIST_PATH", "/opt/gate_anpr/allowlist.json"),
            "/opt/gate_anpr/ui_settings.json",
        ]
    )
    snapshot = create_backup(db_path if args.include_database else None, args.destination, config_paths, args.keep)
    print(f"Created gate backup: {snapshot}")


if __name__ == "__main__":
    main()
