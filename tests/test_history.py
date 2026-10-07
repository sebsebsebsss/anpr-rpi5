"""History stays bounded and stable while new captures arrive."""

import os
import time
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import app
import pytest
from gate_runtime import init_events_db, insert_event
from PIL import Image

SECRET = {"X-Gate-Api-Secret": "test-secret-for-ci"}


@pytest.fixture
def history_client(tmp_path, monkeypatch):
    database = str(tmp_path / "events.db")
    init_events_db(database)
    monkeypatch.setattr(app, "EVENTS_DB_PATH", database)
    monkeypatch.setattr(app, "PLATES_DIR", str(tmp_path / "plates"))
    (tmp_path / "plates").mkdir()
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text("[]")
    monkeypatch.setattr(app, "ALLOWLIST_PATH", str(allowlist))
    app.app.config["TESTING"] = True
    with app.app.test_client() as client:
        yield client


def add_event(*, age=0, kind="recognised", owner="", captured_at=None):
    insert_event(
        app.EVENTS_DB_PATH,
        plate="TEST123",
        owner=owner,
        kind=kind,
        captured_at=captured_at or (datetime.now() - timedelta(days=age)).strftime("%Y-%m-%d %H:%M:%S"),
        image_name="capture.jpg",
    )


def history(client, **query):
    response = client.get("/api/history", query_string=query, headers=SECRET)
    assert response.status_code == 200
    return response.get_json()


def test_cursor_pages_handle_ties_new_captures_and_delayed_events(history_client):
    captured = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for _ in range(7):
        add_event(captured_at=captured)
    first = history(history_client, limit=3)
    assert [event["id"] for event in first["items"]] == [7, 6, 5]
    # Both a new top event and a late-arriving older capture must stay outside
    # the browse snapshot, without repeating or skipping an original event.
    add_event(captured_at=captured)
    add_event(age=1)
    second = history(history_client, limit=3, cursor=first["next_cursor"])
    third = history(history_client, limit=3, cursor=second["next_cursor"])
    assert [event["id"] for event in second["items"]] == [4, 3, 2]
    assert [event["id"] for event in third["items"]] == [1]
    assert third["has_more"] is False
    assert third["next_cursor"] is None


def test_server_filters_match_display_categories_and_dates(history_client):
    add_event(kind="unmatched")
    add_event(kind="candidate")
    add_event(kind="candidate", owner="Registered owner")
    add_event(age=60)
    matched = history(history_client, kind="recognised")
    assert [event["id"] for event in matched["items"]] == [3]
    unmatched = history(history_client, kind="unmatched")
    assert {event["id"] for event in unmatched["items"]} == {1, 2}
    all_dates = history(history_client, kind="recognised", window="all")
    assert {event["id"] for event in all_dates["items"]} == {3, 4}


def test_targeted_jump_and_old_events_api_compatibility(history_client):
    for _ in range(70):
        add_event()
    targeted = history(history_client, start_id=10, limit=5)
    assert [event["id"] for event in targeted["items"]] == [10, 9, 8, 7, 6]
    response = history_client.get("/api/events?limit=1", headers=SECRET)
    assert isinstance(response.get_json(), list)
    assert response.get_json()[0]["image_url"] == "/images/capture.jpg"
    assert response.get_json()[0]["preview_url"] == "/previews/capture.jpg?size=640"


@pytest.mark.parametrize(
    "query",
    [
        {"cursor": "garbage"},
        {"cursor": "W10"},
        {"cursor": "x" * 513},
        {"window": "yesterday"},
        {"kind": "bogus"},
        {"start_id": "bad"},
        {"start_id": "99999999999999999999999"},
    ],
)
def test_bad_history_parameters_are_rejected(history_client, query):
    assert history_client.get("/api/history", query_string=query, headers=SECRET).status_code == 400


def test_empty_history_has_no_next_page(history_client):
    result = history(history_client)
    assert result == {"items": [], "has_more": False, "next_cursor": None}


def test_preview_is_small_cached_and_expires_with_source(history_client, monkeypatch):
    source = os.path.join(app.PLATES_DIR, "capture.jpg")
    Image.new("RGB", (2688, 1520), color="red").save(source, "JPEG")
    original_time = time.time() - 86400
    os.utime(source, (original_time, original_time))
    response = history_client.get("/previews/capture.jpg?size=160")
    assert response.status_code == 200
    assert response.content_type == "image/jpeg"
    with Image.open(BytesIO(response.data)) as preview:
        assert preview.width == 160
        assert preview.height < 160
    cached = list((Path(app.PLATES_DIR) / ".previews").glob("*.jpg"))
    assert len(cached) == 1
    assert cached[0].stat().st_mtime == pytest.approx(original_time)
    assert "immutable" in response.headers["Cache-Control"]

    # A cache hit must not decode the multi-megabyte original again.
    def fail_decode(*args, **kwargs):
        raise AssertionError("decoded original on cache hit")

    monkeypatch.setattr(app.Image, "open", fail_decode)
    assert history_client.get("/previews/capture.jpg?size=160").status_code == 200
    os.unlink(source)
    missing = history_client.get("/previews/capture.jpg?size=160")
    assert missing.status_code == 404
    assert "immutable" not in missing.headers["Cache-Control"]


def test_preview_rejects_unsafe_paths_bad_sizes_and_corrupt_images(history_client, tmp_path):
    outside = tmp_path / "private.jpg"
    Image.new("RGB", (20, 20)).save(outside)
    os.symlink(outside, os.path.join(app.PLATES_DIR, "linked.jpg"))
    assert history_client.get("/previews/linked.jpg").status_code == 404
    assert history_client.get("/previews/capture.jpg?size=99999").status_code == 400
    with open(os.path.join(app.PLATES_DIR, "broken.jpg"), "w") as handle:
        handle.write("not a JPEG")
    assert history_client.get("/previews/broken.jpg").status_code == 404


@pytest.mark.parametrize("failed_service", [None, "alprd", "gate_anpr", "beanstalkd", "gate_anpr_stream_jpeg"])
def test_health_requires_all_critical_services(history_client, monkeypatch, tmp_path, failed_service):
    stream = tmp_path / "stream.jpg"
    stream.write_bytes(b"frame")
    monkeypatch.setattr(app, "STREAM_JPEG_PATH", str(stream))
    monkeypatch.setattr(app, "_systemctl_is_active", lambda name: "failed" if name == failed_service else "active")
    monkeypatch.setattr(app, "_maintenance_health", lambda: {})
    response = history_client.get("/api/healthz", headers=SECRET)
    assert response.status_code == (503 if failed_service else 200)
    assert response.get_json()["ok"] is (failed_service is None)


def test_health_rejects_stale_or_missing_stream(history_client, monkeypatch, tmp_path):
    stream = tmp_path / "stream.jpg"
    stream.write_bytes(b"frame")
    os.utime(stream, (time.time() - 60, time.time() - 60))
    monkeypatch.setattr(app, "STREAM_JPEG_PATH", str(stream))
    monkeypatch.setattr(app, "_systemctl_is_active", lambda name: "active")
    monkeypatch.setattr(app, "_maintenance_health", lambda: {})
    stale = history_client.get("/api/healthz", headers=SECRET)
    assert stale.status_code == 503
    assert stale.get_json()["stream"]["fresh"] is False
    stream.unlink()
    assert history_client.get("/api/healthz", headers=SECRET).status_code == 503
