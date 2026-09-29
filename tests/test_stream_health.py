"""Stream diagnostics must describe the same RAM-backed frame as nginx."""

import os

import app
import pytest

SECRET = {"X-Gate-Api-Secret": "test-secret-for-ci"}


@pytest.fixture
def stream_client(tmp_path, monkeypatch):
    frame = tmp_path / "ram" / "stream.jpg"
    frame.parent.mkdir()
    static = tmp_path / "static"
    static.mkdir()
    monkeypatch.setattr(app, "STREAM_JPEG_PATH", str(frame))
    monkeypatch.setattr(app, "STATIC_DIR", str(static))
    monkeypatch.setattr(app, "STREAM_STALE_SECONDS", 15)
    monkeypatch.setattr(app.time, "time", lambda: 1000)
    with app.app.test_client() as client:
        yield client, frame, static / "stream.jpg"


def frame_at(path, timestamp, content=b"frame"):
    path.write_bytes(content)
    os.utime(path, (timestamp, timestamp))


def diagnostics(client):
    lag = client.get("/api/stream-lag", headers=SECRET)
    health = client.get("/api/stream-health", headers=SECRET)
    assert lag.status_code == health.status_code == 200
    return lag.get_json(), health.get_json()


@pytest.mark.parametrize("old_static_frame", [False, True])
def test_fresh_ram_frame_ignores_absent_or_stale_static_frame(stream_client, old_static_frame):
    client, frame, old_frame = stream_client
    frame_at(frame, 998)
    if old_static_frame:
        frame_at(old_frame, 100)
    lag, health = diagnostics(client)
    assert lag == {"lag_ms": 2000, "frame_mtime": 998, "server_time": 1000}
    assert health["stream_age_s"] == 2
    assert health["ok"] is True
    assert health["stream_stale"] is False


def test_stale_ram_frame_ignores_fresh_static_frame(stream_client, monkeypatch):
    client, frame, old_frame = stream_client
    frame_at(frame, 980)
    frame_at(old_frame, 1000)
    lag, health = diagnostics(client)
    assert lag["lag_ms"] == 20000
    assert health["stream_stale"] is True
    assert health["ok"] is False
    monkeypatch.setattr(app, "STREAM_STALE_SECONDS", 30)
    _, health = diagnostics(client)
    assert health["stale_threshold_s"] == 30
    assert health["stream_stale"] is False


@pytest.mark.parametrize("empty", [False, True])
def test_missing_or_empty_ram_frame_is_unknown_even_with_fresh_static_frame(stream_client, empty):
    client, frame, old_frame = stream_client
    frame_at(old_frame, 1000)
    if empty:
        frame_at(frame, 1000, content=b"")
    lag, health = diagnostics(client)
    assert lag["lag_ms"] is None
    assert lag["frame_mtime"] is None
    assert health["stream_age_s"] is None
    assert health["stream_stale"] is True
    assert health["ok"] is False


def test_future_frame_time_is_clamped_after_clock_adjustment(stream_client):
    client, frame, _ = stream_client
    frame_at(frame, 1010)
    lag, health = diagnostics(client)
    assert lag["lag_ms"] == 0
    assert health["stream_age_s"] == 0
    assert health["stream_stale"] is False
