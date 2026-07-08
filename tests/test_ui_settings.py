"""Tests for lat/lon resolution in /api/ui-settings."""
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("GATE_UI_LAT", "GATE_UI_LON"):
        monkeypatch.delenv(key, raising=False)
    yield


def _resolve(monkeypatch, lat=None, lon=None, tz="Europe/London"):
    if lat is not None:
        monkeypatch.setenv("GATE_UI_LAT", str(lat))
    if lon is not None:
        monkeypatch.setenv("GATE_UI_LON", str(lon))

    with patch("app._get_timezone_name", return_value=tz):
        import importlib

        import app
        importlib.reload(app)
        return app._resolve_lat_lon()


def test_env_vars_take_precedence(monkeypatch):
    result_lat, result_lon, source = _resolve(monkeypatch, lat=52.0, lon=1.0)
    assert result_lat == pytest.approx(52.0)
    assert result_lon == pytest.approx(1.0)
    assert source == "env"


def test_timezone_centroid_fallback(monkeypatch):
    import importlib

    import app
    importlib.reload(app)
    with patch("app._get_timezone_name", return_value="Europe/London"):
        result_lat, result_lon, source = app._resolve_lat_lon()
    assert result_lat == pytest.approx(54.0)
    assert result_lon == pytest.approx(-2.0)
    assert source == "tz_centroid"


def test_unknown_timezone_returns_null(monkeypatch):
    import importlib

    import app
    importlib.reload(app)
    with patch("app._get_timezone_name", return_value="Mars/Olympus_Mons"):
        result_lat, result_lon, source = app._resolve_lat_lon()
    assert result_lat is None
    assert result_lon is None
    assert source is None
