"""HTTP and TLS requests must match the complete browser origin."""

import app
import pytest
from waitress.proxy_headers import proxy_headers_middleware


@pytest.mark.parametrize(
    ("base_url", "origin"),
    [
        ("http://gate.example.test", "http://gate.example.test"),
        ("https://gate.example.test", "https://gate.example.test"),
        ("http://gate.example.test", "http://gate.example.test:80"),
        ("https://gate.example.test", "https://gate.example.test:443"),
        ("https://gate.example.test:443", "https://gate.example.test"),
        ("https://gate.example.test:8443", "https://GATE.EXAMPLE.TEST:8443"),
        ("http://192.0.2.10", "http://192.0.2.10"),
        ("http://[::1]:8080", "http://[::1]:8080"),
    ],
)
def test_matching_origins_accept_http_and_tls(monkeypatch, base_url, origin):
    monkeypatch.setattr(app, "ALLOWED_ORIGINS", set())
    with app.app.test_request_context("/api/example", method="POST", base_url=base_url, headers={"Origin": origin}):
        assert app._check_csrf() is None


@pytest.mark.parametrize(
    ("base_url", "origin"),
    [
        ("https://gate.example.test", "http://gate.example.test"),
        ("http://gate.example.test", "https://gate.example.test"),
        ("https://gate.example.test:8443", "https://gate.example.test"),
        ("https://gate.example.test", "https://gate.example.test:8443"),
        ("http://gate.example.test:8080", "http://gate.example.test"),
        ("https://gate.example.test", "https://other.example.test"),
        ("https://gate.example.test", "https://gate.example.test:99999"),
        ("https://gate.example.test", "https://user@gate.example.test"),
        ("https://gate.example.test", "https://gate.example.test/path"),
        ("https://gate.example.test", "null"),
        ("https://gate.example.test", "https://[invalid"),
    ],
)
def test_mismatched_or_malformed_origins_fail(monkeypatch, base_url, origin):
    monkeypatch.setattr(app, "ALLOWED_ORIGINS", set())
    with app.app.test_request_context("/api/example", method="POST", base_url=base_url, headers={"Origin": origin}):
        assert app._check_csrf()[1] == 403


def test_explicit_origin_exception_is_preserved(monkeypatch):
    monkeypatch.setattr(app, "ALLOWED_ORIGINS", {"http://other.example.test:8080"})
    with app.app.test_request_context(
        "/api/example",
        method="POST",
        base_url="https://gate.example.test",
        headers={"Origin": "http://other.example.test:8080"},
    ):
        assert app._check_csrf() is None


@pytest.mark.parametrize(
    ("referer", "allowed"),
    [
        ("https://gate.example.test/history?window=all", True),
        ("http://gate.example.test/history", False),
        ("https://gate.example.test:8443/history", False),
        ("https://[invalid", False),
    ],
)
def test_referer_fallback_uses_the_same_origin_rule(monkeypatch, referer, allowed):
    monkeypatch.setattr(app, "ALLOWED_ORIGINS", set())
    with app.app.test_request_context(
        "/api/example",
        method="POST",
        base_url="https://gate.example.test",
        headers={"Referer": referer},
    ):
        result = app._check_csrf()
        assert (result is None) is allowed


@pytest.mark.parametrize(("peer", "allowed"), [("127.0.0.1", True), ("192.0.2.50", False)])
def test_waitress_accepts_forwarded_tls_only_from_the_trusted_proxy(monkeypatch, peer, allowed):
    """Exercise Waitress's actual translation without touching a mutation route."""
    monkeypatch.setattr(app, "ALLOWED_ORIGINS", set())
    seen = []

    def check_origin(environ, start_response):
        with app.app.request_context(environ):
            seen.append(app._check_csrf() is None)
        start_response("200 OK", [])
        return [b""]

    wrapped = proxy_headers_middleware(
        check_origin,
        trusted_proxy="127.0.0.1",
        trusted_proxy_headers={"x-forwarded-proto"},
        clear_untrusted=True,
    )
    with app.app.test_request_context(
        "/api/example",
        method="POST",
        base_url="http://gate.example.test",
        headers={"Origin": "https://gate.example.test", "X-Forwarded-Proto": "https"},
        environ_base={"REMOTE_ADDR": peer},
    ):
        wrapped(dict(app.request.environ), lambda status, headers: None)
    assert seen == [allowed]
