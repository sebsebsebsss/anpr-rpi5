"""Tests for the /api/logs service-name whitelist."""


def _get_log_service_map():
    # Import lazily so the env var is set first.
    import app

    return app.LOG_SERVICE_MAP


def test_log_service_map_contains_expected_services():
    log_map = _get_log_service_map()
    for key in ("gate_anpr", "alprd", "web", "stream_jpeg", "beanstalkd"):
        assert key in log_map, f"Expected {key!r} in LOG_SERVICE_MAP"


def test_log_service_map_values_are_safe_unit_names():
    log_map = _get_log_service_map()
    for key, value in log_map.items():
        # Values must be safe systemd unit names — no shell metacharacters.
        assert all(c.isalnum() or c in "-_." for c in value), (
            f"LOG_SERVICE_MAP[{key!r}] = {value!r} contains unsafe characters"
        )
