import time

import pytest
from gate_runtime import GateActuationError


@pytest.fixture
def api(monkeypatch, tmp_path):
    import app

    monkeypatch.setattr(app, "GATE_COOLDOWN_PATH", str(tmp_path / "cooldown"))
    events = []
    monkeypatch.setattr(app, "insert_event", lambda *args, **kwargs: events.append(kwargs))
    return app, app.app.test_client(), events


def request_open(client):
    return client.post(
        "/api/open-gate",
        headers={
            "X-Gate-Api-Secret": "test-secret-for-ci",
            "Origin": "http://localhost",
            "X-Gate-Requested-At": str(int(time.time() * 1000)),
        },
    )


@pytest.mark.parametrize("activated", [False, True])
def test_gpio_failure_returns_controlled_error_and_correct_retry_policy(api, monkeypatch, activated):
    app, client, events = api
    calls = []

    def trigger(*args):
        calls.append(True)
        if len(calls) == 1:
            raise GateActuationError("private hardware details", may_have_activated=activated)
        return True

    monkeypatch.setattr(app, "trigger_gate", trigger)
    response = request_open(client)
    assert response.status_code == 503
    assert response.json["may_have_activated"] is activated
    assert "private hardware details" not in response.get_data(as_text=True)
    assert events == []
    retry = request_open(client)
    assert retry.status_code == (429 if activated else 200)
    assert len(calls) == (1 if activated else 2)


def test_coalesced_automatic_open_does_not_record_manual_pulse(api, monkeypatch):
    app, client, events = api
    monkeypatch.setattr(app, "trigger_gate", lambda *args: False)
    assert request_open(client).status_code == 429
    assert events == []
