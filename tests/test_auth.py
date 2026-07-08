"""Tests for the Origin/Referer CSRF check and shared-secret auth."""
import pytest


@pytest.fixture(scope="module")
def client():
    import app as flask_app
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        yield c


def test_missing_secret_returns_401(client):
    resp = client.post("/api/open-gate")
    assert resp.status_code == 401


def test_wrong_secret_returns_401(client):
    resp = client.post(
        "/api/open-gate",
        headers={"X-Gate-Api-Secret": "wrong"},
    )
    assert resp.status_code == 401


def test_correct_secret_no_origin_returns_403(client):
    resp = client.post(
        "/api/open-gate",
        headers={"X-Gate-Api-Secret": "test-secret-for-ci"},
    )
    assert resp.status_code == 403


def test_correct_secret_wrong_origin_returns_403(client):
    resp = client.post(
        "/api/open-gate",
        headers={
            "X-Gate-Api-Secret": "test-secret-for-ci",
            "Origin": "https://evil.com",
        },
    )
    assert resp.status_code == 403


def test_correct_secret_allowed_origin_accepted(client, monkeypatch):
    """With correct secret and an allowed origin the handler is reached.
    We mock trigger_gate so no real GPIO call happens."""
    import app as flask_app

    def _noop(*a, **kw):
        pass

    monkeypatch.setattr(flask_app, "trigger_gate", _noop)
    monkeypatch.setattr(flask_app, "insert_event", _noop)

    resp = client.post(
        "/api/open-gate",
        headers={
            "X-Gate-Api-Secret": "test-secret-for-ci",
            "Origin": "http://gatepi5",
        },
    )
    # 200 (gate opened) or 429 (cooldown) — either means CSRF check passed.
    assert resp.status_code in (200, 429)


def test_referer_fallback_accepted(client, monkeypatch):
    """Referer header should be accepted when Origin is absent."""
    import app as flask_app

    monkeypatch.setattr(flask_app, "trigger_gate", lambda *a, **kw: None)
    monkeypatch.setattr(flask_app, "insert_event", lambda *a, **kw: None)

    resp = client.post(
        "/api/open-gate",
        headers={
            "X-Gate-Api-Secret": "test-secret-for-ci",
            "Referer": "http://gatepi5/",
        },
    )
    assert resp.status_code in (200, 429)
