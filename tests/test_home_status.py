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
    monkeypatch.setattr(app, "_read_cpu_temperature_c", lambda: 48.6)
    monkeypatch.setattr(app.shutil, "disk_usage", lambda path: (1000, 875, 125))
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


def test_snapshot_bounds_arrivals_and_preserves_capture_evidence(home):
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
    assert [event["id"] for event in data["arrivals"]] == [20, 19]
    assert data["arrivals"][0]["plate"] == "AB12 CDE"
    assert data["arrivals"][0]["detail"]["decision"]["relay_command"] == "pulse_sent"
    assert data["arrivals"][0]["seen_at"] == NOW - 10
    assert (
        not {"recognised", "unfamiliar", "recent_decisions", "decisions_available", "decision_sampling"} & data.keys()
    )
    decisions = client.get("/api/decisions?limit=3", headers=HEADERS).get_json()
    assert len(decisions["decisions"]) == 3
    assert decisions["decisions"][0]["seen_at"] == NOW
    assert decisions["decisions"][0]["captured_at"] == stamp(NOW - 600)
    assert decisions["sampling"]["retained_limit"] == 200
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 23
        assert conn.execute("SELECT COUNT(*) FROM recent_decisions").fetchone()[0] == 6


@pytest.mark.parametrize(
    ("captures", "expected"),
    [
        (
            [("OLDER_MATCH", "recognised", 30), ("UNFAMILIAR", "unmatched", 20), ("NEW_MATCH", "recognised", 10)],
            ["NEW_MATCH", "OLDER_MATCH"],
        ),
        (
            [
                ("OLDER_MATCH", "recognised", 40),
                ("OLD_UNFAMILIAR", "unmatched", 30),
                ("NEW_MATCH", "recognised", 20),
                ("NEW_UNFAMILIAR", "unmatched", 10),
            ],
            ["NEW_UNFAMILIAR", "NEW_MATCH"],
        ),
        (
            [
                ("OLDEST_UNFAMILIAR", "unmatched", 900),
                ("OLDER_UNFAMILIAR", "unmatched", 600),
                ("NEW_UNFAMILIAR", "unmatched", 10),
            ],
            ["NEW_UNFAMILIAR", "OLDER_UNFAMILIAR"],
        ),
        (
            [("OLDEST_MATCH", "recognised", 30), ("OLDER_MATCH", "recognised", 20), ("NEW_MATCH", "recognised", 10)],
            ["NEW_MATCH", "OLDER_MATCH"],
        ),
        (
            [("OLDER_MATCH", "recognised", 1200), ("UNFAMILIAR", "unmatched", 600)],
            ["UNFAMILIAR", "OLDER_MATCH"],
        ),
        (
            [
                ("NEW_MATCH", "recognised", 20),
                ("UNFAMILIAR", "unmatched", 10),
                ("FUTURE_MATCH", "recognised", -60),
                ("FUTURE_UNFAMILIAR", "unmatched", -30),
            ],
            ["UNFAMILIAR", "NEW_MATCH"],
        ),
        (
            [("OLDER_MATCH", "recognised", 10), ("UNFAMILIAR", "unmatched", 10), ("NEW_MATCH", "recognised", 10)],
            ["NEW_MATCH", "OLDER_MATCH"],
        ),
        (
            [("OLD_UNFAMILIAR", "unmatched", 10), ("MATCH", "recognised", 10), ("NEW_UNFAMILIAR", "unmatched", 10)],
            ["NEW_UNFAMILIAR", "MATCH"],
        ),
    ],
    ids=[
        "match-latest",
        "unfamiliar-latest",
        "no-matches",
        "no-unfamiliar",
        "old-unfamiliar",
        "future-captures",
        "match-wins-tie",
        "unfamiliar-wins-tie",
    ],
)
def test_latest_seen_selection(home, captures, expected):
    client, db, _, _ = home
    for plate, kind, age in captures:
        insert_event(str(db), plate=plate, kind=kind, captured_at=stamp(NOW - age))
    response = client.get("/api/home-status", headers=HEADERS)
    assert response.status_code == 200
    arrivals = response.get_json()["arrivals"]
    assert [event["plate"] for event in arrivals] == expected
    assert len(arrivals) <= 2
    assert [(event["captured_at"], event["id"]) for event in arrivals] == sorted(
        [(event["captured_at"], event["id"]) for event in arrivals], reverse=True
    )
    for event in arrivals:
        assert event["seen_at"] == datetime.strptime(event["captured_at"], "%Y-%m-%d %H:%M:%S").timestamp()
        assert "expires_at" not in event


def test_home_is_independent_of_recent_decision_reads(home, monkeypatch):
    client, _, _, _ = home

    def forbidden(*args, **kwargs):
        raise AssertionError("Home must use sightings rather than diagnostic decision samples")

    monkeypatch.setattr(app, "_recent_decision_payloads", forbidden)
    assert client.get("/api/home-status", headers=HEADERS).status_code == 200


def test_home_metrics_are_lightweight_and_preserve_arrival_measurements(home, monkeypatch):
    client, db, _, _ = home
    insert_event(
        str(db),
        plate="TEST123",
        kind="unmatched",
        captured_at=stamp(NOW - 10),
        processing_time_ms=0,
        confidence=92.4,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Home metrics must not invoke the heavier service-health diagnostics")

    monkeypatch.setattr(app, "_systemctl_is_active", forbidden)
    monkeypatch.setattr(app, "_failed_systemd_units", forbidden)
    monkeypatch.setattr(app, "_maintenance_health", forbidden)
    data = client.get("/api/home-status", headers=HEADERS).get_json()
    assert data["metrics"] == {"temperature_c": 48.6, "disk_free_pct": 12.5}
    assert data["arrivals"][0]["processing_time_ms"] == 0
    assert data["arrivals"][0]["confidence"] == 92.4
    assert data["stream"]["fresh"] is True


@pytest.mark.parametrize("temperature", [None, float("nan"), float("inf")])
def test_unavailable_temperature_does_not_hide_disk_or_camera(home, monkeypatch, temperature):
    client, _, _, _ = home
    monkeypatch.setattr(app, "_read_cpu_temperature_c", lambda: temperature)
    response = client.get("/api/home-status", headers=HEADERS)
    assert response.status_code == 200
    data = response.get_json()
    assert data["metrics"] == {"temperature_c": None, "disk_free_pct": 12.5}
    assert data["stream"]["fresh"] is True


@pytest.mark.parametrize("failure", ["temperature", "disk", "both"])
def test_metric_read_failures_are_independent(home, monkeypatch, failure):
    client, _, _, _ = home

    def unavailable(*args, **kwargs):
        raise OSError("Synthetic metric unavailable")

    if failure in {"temperature", "both"}:
        monkeypatch.setattr(app, "_read_cpu_temperature_c", unavailable)
    if failure in {"disk", "both"}:
        monkeypatch.setattr(app.shutil, "disk_usage", unavailable)
    response = client.get("/api/home-status", headers=HEADERS)
    assert response.status_code == 200
    data = response.get_json()
    assert data["metrics"]["temperature_c"] == (48.6 if failure == "disk" else None)
    assert data["metrics"]["disk_free_pct"] == (12.5 if failure == "temperature" else None)
    assert data["arrivals"] == []
    assert data["stream"]["fresh"] is True
    assert set(data["services"].values()) == {"active"}


@pytest.mark.parametrize("usage", [(0, 0, 0), (100, 101, -1), (100, -1, 101), (float("inf"), 0, float("inf"))])
def test_invalid_disk_measurements_remain_unavailable(home, monkeypatch, usage):
    client, _, _, _ = home
    monkeypatch.setattr(app.shutil, "disk_usage", lambda path: usage)
    response = client.get("/api/home-status", headers=HEADERS)
    assert response.status_code == 200
    assert response.get_json()["metrics"] == {"temperature_c": 48.6, "disk_free_pct": None}


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
    assert data["arrivals"] == []


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
    assert response.get_json()["arrivals"] == []
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
