"""Home snapshots stay bounded, truthful and read-only during normal failures."""

import json
import os
import sqlite3
import subprocess
from datetime import datetime
from types import SimpleNamespace

import app
import pytest
from gate_runtime import init_events_db, insert_event, insert_recent_decision

HEADERS = {"X-Gate-Api-Secret": "test-secret-for-ci"}
NOW = 1800000000


def stamp(seconds):
    return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def home(tmp_path, monkeypatch):
    db = tmp_path / "events.db"
    frame = tmp_path / "stream.jpg"
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(json.dumps([{"owner": "Example owner", "plates": ["AB12 CDE"]}]))
    init_events_db(str(db))
    frame.write_bytes(b"jpeg")
    os.utime(frame, (NOW - 1, NOW - 1))
    monkeypatch.setattr(app, "EVENTS_DB_PATH", str(db))
    monkeypatch.setattr(app, "STREAM_JPEG_PATH", str(frame))
    monkeypatch.setattr(app, "ALLOWLIST_PATH", str(allowlist))
    monkeypatch.setattr(app, "_display_cache", {"mtime": None, "map": {}})
    monkeypatch.setattr(app.time, "time", lambda: NOW)
    monkeypatch.setattr("gate_runtime.now_local_str", lambda: stamp(NOW))
    monkeypatch.setattr(app, "HOME_SERVICE_CACHE", {"checked_monotonic": None, "checked_at": None, "services": {}})
    checks = []

    def service():
        checks.append(True)
        return dict.fromkeys(["alprd", "gate_anpr", "stream_jpeg", "beanstalkd"], "active")

    def forbidden(*args, **kwargs):
        raise AssertionError("Read-only home routes must never activate the gate")

    monkeypatch.setattr(app, "_read_home_service_states", service)
    monkeypatch.setattr(app, "trigger_gate", forbidden)
    with app.app.test_client() as client:
        yield client, db, frame, checks


def test_snapshot_bounds_arrivals_and_preserves_decision_evidence(home):
    client, db, _, _ = home
    for index in range(20):
        insert_event(
            str(db),
            plate="A812 CDE",  # Older stored keys can contain presentation spaces.
            owner="Example owner",
            kind="recognised",
            captured_at=stamp(NOW - 10),
            image_name=f"sample-{index}.jpg",
            detail={"decision": {"reason": "allowlist_match", "relay_command": "pulse_sent"}},
        )
    for age, plate in [(301, "OLD123"), (50, "DEMO123"), (-60, "FUTURE123")]:
        insert_event(str(db), plate=plate, kind="unmatched", captured_at=stamp(NOW - age))
    for index in range(6):
        insert_recent_decision(
            str(db),
            plate="A812 CDE",
            captured_at=stamp(NOW - 600),
            detail={"decision": {"reason": "stale_capture", "relay_command": "not_requested", "sample": index}},
        )
    response = client.get("/api/home-status", headers=HEADERS)
    data = response.get_json()
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert [event["id"] for event in data["recognised"]] == [20, 19]
    assert data["recognised"][0]["plate"] == "AB12 CDE"
    assert data["recognised"][0]["detail"]["decision"]["relay_command"] == "pulse_sent"
    assert [event["plate"] for event in data["unfamiliar"]] == ["DEMO123"]
    assert data["unfamiliar"][0]["expires_at"] == NOW + 250
    assert len(data["recent_decisions"]) == 3
    assert data["recent_decisions"][0]["seen_at"] == NOW
    assert data["recent_decisions"][0]["captured_at"] == stamp(NOW - 600)
    assert data["decision_sampling"]["retained_limit"] == 200
    assert data["decisions_available"] is True
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 23
        assert conn.execute("SELECT COUNT(*) FROM recent_decisions").fetchone()[0] == 6


@pytest.mark.parametrize("state", ["fresh", "stale", "missing", "empty"])
def test_empty_home_distinguishes_camera_states(home, state):
    client, _, frame, _ = home
    if state == "stale":
        os.utime(frame, (NOW - 30, NOW - 30))
    elif state == "missing":
        frame.unlink()
    elif state == "empty":
        frame.write_bytes(b"")
    response = client.get("/api/home-status", headers=HEADERS)
    data = response.get_json()
    assert response.status_code == 200
    assert data["stream"]["fresh"] is (state == "fresh")
    if state in {"missing", "empty"}:
        assert data["stream"]["age_seconds"] is None
    assert data["recognised"] == data["unfamiliar"] == data["recent_decisions"] == []


def test_service_checks_are_shared_across_screens_and_refresh(home, monkeypatch):
    client, _, _, checks = home
    clock = [100.0]
    monkeypatch.setattr(app.time, "monotonic", lambda: clock[0])
    for _ in range(4):
        assert client.get("/api/home-status", headers=HEADERS).status_code == 200
    assert len(checks) == 1
    clock[0] += 6
    monkeypatch.setattr(app, "_read_home_service_states", lambda: {"alprd": "inactive"})
    data = client.get("/api/home-status", headers=HEADERS).get_json()
    assert set(data["services"].values()) == {"inactive"}
    assert data["stream"]["fresh"] is True  # Preview freshness is a separate fact.


def test_concurrent_home_poll_does_not_wait_for_service_refresh(home):
    client, _, _, checks = home
    app.HOME_SERVICE_LOCK.acquire()
    try:
        response = client.get("/api/home-status", headers=HEADERS)
        assert response.status_code == 200
        assert response.get_json()["services_checked_at"] is None
        assert response.get_json()["services"] == {}
        assert checks == []
    finally:
        app.HOME_SERVICE_LOCK.release()


def test_service_states_use_one_bounded_query(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 1
        return SimpleNamespace(stdout="active\ninactive\nactive\nunknown\n", returncode=3)

    monkeypatch.setattr(app.subprocess, "run", run)
    assert app._read_home_service_states() == {
        "alprd": "active",
        "gate_anpr": "inactive",
        "stream_jpeg": "active",
        "beanstalkd": "unknown",
    }
    assert len(calls) == 1


def test_service_timeout_is_unknown_not_healthy(monkeypatch):
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(app.subprocess, "run", timeout)
    assert set(app._read_home_service_states().values()) == {"unknown"}


def test_old_schema_does_not_hide_camera_status(home):
    client, db, _, _ = home
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE recent_decisions")
    response = client.get("/api/home-status", headers=HEADERS)
    assert response.status_code == 200
    assert response.get_json()["decisions_available"] is False
    assert response.get_json()["stream"]["fresh"] is True
    assert client.get("/api/decisions", headers=HEADERS).status_code == 503


def test_missing_database_is_not_created_by_status_reads(home, monkeypatch, tmp_path):
    client, _, _, _ = home
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(app, "EVENTS_DB_PATH", str(missing))
    assert client.get("/api/home-status", headers=HEADERS).status_code == 503
    assert client.get("/api/decisions", headers=HEADERS).status_code == 503
    assert not missing.exists()


@pytest.mark.parametrize("route", ["/api/home-status", "/api/decisions"])
def test_status_requires_secret_and_rejects_writes(home, route):
    client, _, _, _ = home
    assert client.get(route).status_code == 401
    assert client.get(route, headers={"X-Gate-Api-Secret": "wrong"}).status_code == 401
    assert client.post(route, headers=HEADERS).status_code == 405


def test_decision_api_limit_is_bounded(home):
    client, db, _, _ = home
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO recent_decisions (plate, created_at, detail) VALUES (?, ?, ?)",
            [("DEMO123", stamp(NOW), "{}") for _ in range(150)],
        )
    assert len(client.get("/api/decisions?limit=100000", headers=HEADERS).get_json()["decisions"]) == 100
    assert len(client.get("/api/decisions?limit=1", headers=HEADERS).get_json()["decisions"]) == 1
