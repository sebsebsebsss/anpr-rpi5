import json
import os
import sqlite3
import sys

import pytest
from gate_backup import create_backup, main


def _database(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, plate TEXT)")
    conn.execute("INSERT INTO events VALUES (1, 'TEST123')")
    conn.commit()
    return conn


def test_config_only_backup_does_not_need_database(tmp_path):
    config = tmp_path / "allowlist.json"
    config.write_text('[{"owner":"Test","plates":["TEST123"]}]')
    snapshot = create_backup(None, tmp_path / "backups", [config])
    assert not (snapshot / "events.db").exists()
    assert json.loads((snapshot / "manifest.json").read_text())["database"] is None
    assert (snapshot / "allowlist.json").read_text() == config.read_text()


def test_cli_uses_persisted_allowlist_path_without_database(tmp_path, monkeypatch):
    custom = tmp_path / "custom_allowlist.json"
    custom.write_text("[]")
    env_path = tmp_path / "gate.env"
    env_path.write_text(f'PLATE_ALLOWLIST_PATH="{custom}"\nGATE_ANPR_EVENTS_DB=/does/not/exist.db\n')
    destination = tmp_path / "backups"
    monkeypatch.delenv("PLATE_ALLOWLIST_PATH", raising=False)
    monkeypatch.setattr(sys, "argv", ["gate_backup.py", "--env-path", str(env_path), "--destination", str(destination)])
    main()
    snapshot = next(destination.glob("backup-*"))
    assert (snapshot / "custom_allowlist.json").read_text() == "[]"
    assert (snapshot / "gate.env").read_text() == env_path.read_text()
    assert not (snapshot / "events.db").exists()


def test_backup_captures_committed_wal_and_private_config(tmp_path):
    source = tmp_path / "live.db"
    live = _database(source)
    config = tmp_path / "gate.env"
    config.write_text("SECRET=private\n")
    try:
        # Hold the live connection open, keeping the committed row in its WAL.
        assert source.with_name("live.db-wal").exists()
        snapshot = create_backup(source, tmp_path / "backups", [config, tmp_path / "optional.json"])
        with sqlite3.connect(snapshot / "events.db") as conn:
            assert conn.execute("SELECT plate FROM events").fetchall() == [("TEST123",)]
        assert (snapshot / "gate.env").read_text() == "SECRET=private\n"
        assert json.loads((snapshot / "manifest.json").read_text())["config_files"] == {"gate.env": str(config)}
        assert os.stat(snapshot).st_mode & 0o777 == 0o700
        assert os.stat(snapshot.parent).st_mode & 0o777 == 0o700
        assert all(os.stat(path).st_mode & 0o777 == 0o600 for path in snapshot.iterdir())
    finally:
        live.close()


def test_rotation_preserves_unrelated_files_and_failed_backup_keeps_previous(tmp_path):
    source = tmp_path / "live.db"
    _database(source).close()
    destination = tmp_path / "backups"
    first = create_backup(source, destination, [], keep=2)
    unrelated = destination / "family-documents"
    unrelated.mkdir()
    second = create_backup(source, destination, [], keep=2)
    third = create_backup(source, destination, [], keep=2)
    assert not first.exists()
    assert second.exists() and third.exists() and unrelated.exists()
    source.write_bytes(b"invalid database")
    with pytest.raises(sqlite3.DatabaseError):
        create_backup(source, destination, [], keep=1)
    assert second.exists() and third.exists()
    assert not list(destination.glob(".pending-*"))


def test_missing_or_empty_database_does_not_create_successful_snapshot(tmp_path):
    source = tmp_path / "missing.db"
    destination = tmp_path / "backups"
    with pytest.raises(sqlite3.OperationalError):
        create_backup(source, destination, [])
    assert not source.exists()
    assert not list(destination.glob("backup-*"))
    source.touch()
    with pytest.raises(sqlite3.OperationalError):
        create_backup(source, destination, [])
    assert not list(destination.glob("backup-*"))
