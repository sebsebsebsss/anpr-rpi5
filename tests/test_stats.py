"""Stats use the same legacy recognition categories as history, without writes."""

import gc
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import app
import pytest
from gate_runtime import init_events_db

HEADERS = {"X-Gate-Api-Secret": "test-secret-for-ci"}
NOW = datetime(2026, 9, 29, 12, 0, 0)


class PiDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        assert tz is None
        return NOW


@pytest.fixture
def stats(tmp_path, monkeypatch):
    database = tmp_path / "events.db"
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text("[]")
    init_events_db(str(database))
    monkeypatch.setattr(app, "EVENTS_DB_PATH", str(database))
    monkeypatch.setattr(app, "ALLOWLIST_PATH", str(allowlist))
    monkeypatch.setattr(app, "_display_cache", {"mtime": None, "map": {}})
    monkeypatch.setattr(app, "datetime", PiDateTime)
    monkeypatch.setattr(app, "now_local_str", lambda: NOW.strftime("%Y-%m-%d %H:%M:%S"))

    def forbidden(*args, **kwargs):
        raise AssertionError("Stats requests must never activate the gate")

    monkeypatch.setattr(app, "trigger_gate", forbidden)

    def add(kind, plate, owner=None, processing=None, age=timedelta(hours=1), source="alprd"):
        # A connection's transaction context commits but does not close it.
        # Close before byte snapshots so later GC cannot checkpoint fixture WAL.
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                """INSERT INTO events
                (kind, plate, owner, processing_time_ms, captured_at, created_at, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    kind,
                    plate,
                    owner,
                    processing,
                    (NOW - age).strftime("%Y-%m-%d %H:%M:%S"),
                    NOW.strftime("%Y-%m-%d %H:%M:%S"),
                    source,
                ),
            )

    with app.app.test_client() as client:
        yield client, database, add


def add_mixed_history(add):
    add("recognised", "AB12CDE", "Example owner", 10)
    add("recognised", "AB12CDE", None, 30)
    add("candidate", "CD34EFG", "Example owner", 50)
    add("candidate", "CD34EFG", "Example owner", 70)
    add("unmatched", "CD34EFG", "Example owner", 90)
    add("unmatched", "XY11AAA", None, 100)
    add("candidate", "XY11AAA", "", 200)
    add("candidate", "UNKNOWN", None, 300)
    add("unmatched", None, None, 400)
    add("candidate", "", "", 500)
    add("manual_open", "UNKNOWN", source="web_ui")
    add("diagnostic", None, source="maintenance")


def test_stats_and_history_agree_on_legacy_recognition(stats):
    client, database, add = stats
    add_mixed_history(add)
    before = database.read_bytes()
    gc.collect()
    response = client.get("/api/stats?window=24h", headers=HEADERS)
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    summary = response.get_json()
    assert summary["counts"] == {"recognised": 5, "unmatched": 5, "manual_open": 1, "diagnostic": 1}
    assert summary["total"] == 12
    for kind in ("recognised", "unmatched"):
        history = client.get(f"/api/history?window=24h&kind={kind}", headers=HEADERS).get_json()
        assert len(history["items"]) == summary["counts"][kind]
    # Compatibility: these legacy metrics retain their raw event-kind definition.
    # Neither metric proves physical gate movement.
    assert summary["gate_opens"] == 3
    assert {item["source"]: item["count"] for item in summary["source_breakdown"]} == {
        "alprd": 2,
        "web_ui": 1,
    }
    gc.collect()
    assert database.read_bytes() == before


def test_rankings_and_processing_use_canonical_categories(stats):
    client, database, add = stats
    add_mixed_history(add)
    before = database.read_bytes()
    gc.collect()
    response = client.get("/api/stats/insights?window=24h", headers=HEADERS)
    assert response.status_code == 200
    insights = response.get_json()["insights"]
    assert insights["top_recognised"] == {"plate": "CD34EFG", "count": 3}
    assert insights["top_unmatched"] == {"plate": "UNKNOWN", "count": 3}
    assert insights["top_recognised_list"] == [
        {"plate": "CD34EFG", "count": 3},
        {"plate": "AB12CDE", "count": 2},
    ]
    assert insights["top_unmatched_list"] == [
        {"plate": "UNKNOWN", "count": 3},
        {"plate": "XY11AAA", "count": 2},
    ]
    assert insights["avg_processing_ms"] == {
        "overall": 175,
        "recognised": 50,
        "unmatched": 300,
        "candidate": 300,
    }
    assert insights["no_plate"] == 3
    gc.collect()
    assert database.read_bytes() == before


@pytest.mark.parametrize("window, days, bucket", [("24h", 1, "hour"), ("7d", 7, "day"), ("30d", 30, "day")])
def test_window_boundary_and_pi_civil_time_range(stats, window, days, bucket):
    client, _, add = stats
    boundary = timedelta(days=days)
    add("candidate", "AB12CDE", "Example owner", 40, age=boundary)
    add("unmatched", "CD34EFG", processing=100, age=boundary + timedelta(seconds=1))
    summary = client.get(f"/api/stats?window={window}", headers=HEADERS).get_json()
    assert summary["total"] == 1
    assert summary["counts"] == {"recognised": 1}
    assert summary["timeseries"] == {
        "start": (NOW - boundary).strftime("%Y-%m-%d %H:%M:%S"),
        "end": NOW.strftime("%Y-%m-%d %H:%M:%S"),
        "bucket": bucket,
        "series": [{"t": (NOW - boundary).strftime("%Y-%m-%d %H:00" if bucket == "hour" else "%Y-%m-%d"), "v": 1}],
    }
    insights = client.get(f"/api/stats/insights?window={window}", headers=HEADERS).get_json()["insights"]
    assert insights["top_recognised"] == {"plate": "AB12CDE", "count": 1}
    assert insights["top_unmatched"] == {"plate": None, "count": 0}
    assert insights["avg_processing_ms"]["overall"] == 40


@pytest.mark.parametrize("window", ["all", "forever"])
def test_unbounded_stats_keep_old_history(stats, window):
    client, _, add = stats
    add("candidate", "AB12CDE", age=timedelta(days=100))
    summary = client.get(f"/api/stats?window={window}", headers=HEADERS).get_json()
    assert summary["counts"] == {"unmatched": 1}
    assert summary["timeseries"]["start"] is None
    assert summary["timeseries"]["end"] == NOW.strftime("%Y-%m-%d %H:%M:%S")
    assert summary["timeseries"]["bucket"] == "day"


def test_no_plate_handles_worker_sentinel_and_filters_manual_rows(stats):
    client, _, add = stats
    for plate in (None, "", "UNKNOWN", "unknown", "   "):
        add("unmatched", plate)
    add("candidate", "UNKNOWN", age=timedelta(days=2))
    add("manual_open", "UNKNOWN", source="web_ui")
    add("recognised", "AB12CDE", "Example owner")
    insights = client.get("/api/stats/insights?window=24h", headers=HEADERS).get_json()["insights"]
    assert insights["no_plate"] == 5


@pytest.mark.parametrize(
    "kind, owner, category", [("candidate", "Example owner", "recognised"), ("unmatched", None, "unmatched")]
)
def test_rankings_merge_missing_plate_spellings_without_changing_other_display(stats, kind, owner, category):
    client, database, add = stats
    for plate in (None, "", "UNKNOWN", "unknown", " UnKnown  ", "   "):
        add(kind, plate, owner)
    add(kind, "AB12 CDE", owner)
    before = database.read_bytes()
    insights = client.get("/api/stats/insights?window=24h", headers=HEADERS).get_json()["insights"]
    assert insights[f"top_{category}"] == {"plate": "UNKNOWN", "count": 6}
    assert insights[f"top_{category}_list"] == [
        {"plate": "UNKNOWN", "count": 6},
        {"plate": "AB12 CDE", "count": 1},
    ]
    assert insights["no_plate"] == 6
    assert database.read_bytes() == before


def test_processing_ignores_missing_and_negative_timings(stats):
    client, _, add = stats
    add("candidate", "AB12CDE", "Example owner", 0)
    add("unmatched", "AB12CDE", "Example owner", -10)
    add("recognised", "AB12CDE", "Example owner", None)
    add("candidate", "CD34EFG", None, 25)
    add("unmatched", "CD34EFG", "", 75)
    insights = client.get("/api/stats/insights?window=24h", headers=HEADERS).get_json()["insights"]
    assert insights["avg_processing_ms"]["recognised"] == 0
    assert insights["avg_processing_ms"]["unmatched"] == 50


@pytest.mark.parametrize("route", ["/api/stats", "/api/stats/insights"])
def test_stats_routes_are_authenticated_read_only(stats, route):
    client, database, add = stats
    add_mixed_history(add)
    before = database.read_bytes()
    assert client.get(route).status_code == 401
    assert client.get(route, headers={"X-Gate-Api-Secret": "wrong"}).status_code == 401
    assert client.post(route, headers=HEADERS).status_code == 405
    assert client.get(route + "?window=invalid", headers=HEADERS).status_code == 400
    assert database.read_bytes() == before


def test_missing_database_is_never_created(stats, monkeypatch, tmp_path):
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(app, "EVENTS_DB_PATH", str(missing))
    with pytest.raises(sqlite3.OperationalError):
        app._stats_counts(None)
    assert not missing.exists()


def test_empty_stats_return_no_invented_rankings_or_timing(stats):
    client, _, _ = stats
    summary = client.get("/api/stats?window=24h", headers=HEADERS).get_json()
    insights = client.get("/api/stats/insights?window=24h", headers=HEADERS).get_json()["insights"]
    assert summary["total"] == summary["gate_opens"] == 0
    assert summary["counts"] == {}
    assert summary["timeseries"]["series"] == []
    assert insights["top_recognised"] == insights["top_unmatched"] == {"plate": None, "count": 0}
    assert insights["top_recognised_list"] == insights["top_unmatched_list"] == []
    assert all(value is None for value in insights["avg_processing_ms"].values())
    assert insights["no_plate"] == 0
