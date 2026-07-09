"""Tests for request-IP attribution and query-parameter clamping (audit pass 2)."""

import pytest


@pytest.fixture(scope="module")
def client():
    import app as flask_app

    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        yield c


SECRET = {"X-Gate-Api-Secret": "test-secret-for-ci"}


def test_request_ip_ignores_x_forwarded_for():
    """X-Forwarded-For is client-controlled (nginx appends to it); the app
    must attribute requests to X-Real-IP or the socket address instead."""
    import app as flask_app

    with flask_app.app.test_request_context(headers={"X-Forwarded-For": "<img src=x onerror=alert(1)>, 10.0.0.9"}):
        ip = flask_app._request_ip()
    assert "<" not in ip
    assert ip != "<img src=x onerror=alert(1)>"


def test_request_ip_prefers_x_real_ip():
    import app as flask_app

    with flask_app.app.test_request_context(headers={"X-Real-IP": "192.168.1.50", "X-Forwarded-For": "6.6.6.6"}):
        assert flask_app._request_ip() == "192.168.1.50"


def test_request_ip_falls_back_to_remote_addr():
    import app as flask_app

    with flask_app.app.test_request_context(environ_base={"REMOTE_ADDR": "192.168.1.7"}):
        assert flask_app._request_ip() == "192.168.1.7"


def test_events_garbage_limit_does_not_500(client):
    resp = client.get("/api/events?limit=bogus&offset=x", headers=SECRET)
    assert resp.status_code == 200


def test_events_huge_limit_is_clamped(client):
    resp = client.get("/api/events?limit=999999999", headers=SECRET)
    assert resp.status_code == 200


def test_timeline_garbage_page_does_not_500(client):
    resp = client.get("/api/timeline?page=NaN&per_page=-5", headers=SECRET)
    assert resp.status_code == 200


def test_images_garbage_params_do_not_500(client):
    resp = client.get("/api/images?limit=;drop&offset=", headers=SECRET)
    assert resp.status_code == 200


def test_logs_garbage_lines_does_not_500(client):
    resp = client.get("/api/logs?service=app_log&lines=zzz", headers=SECRET)
    assert resp.status_code == 200


def test_worker_sigterm_handler_exits():
    """The SIGTERM handler must exit the process, not just shut the pool down,
    or `systemctl stop gate_anpr` hangs until systemd SIGKILLs it."""
    import signal

    import new_gate_anpr

    with pytest.raises(SystemExit):
        new_gate_anpr._handle_sigterm(signal.SIGTERM, None)


def test_sanitise_uuid_accepts_safe_string():
    import new_gate_anpr

    assert new_gate_anpr._sanitise_uuid("550e8400-e29b-41d4") == "550e8400-e29b-41d4"


def test_sanitise_uuid_rejects_traversal():
    import new_gate_anpr

    assert new_gate_anpr._sanitise_uuid("../../etc/passwd") is None


def test_sanitise_uuid_rejects_non_string():
    """A numeric uuid must not raise TypeError — that would turn the job into
    a poison message retried forever by the generic exception handler."""
    import new_gate_anpr

    assert new_gate_anpr._sanitise_uuid(12345) is None


def test_sanitise_uuid_passes_none_through():
    import new_gate_anpr

    assert new_gate_anpr._sanitise_uuid(None) is None
