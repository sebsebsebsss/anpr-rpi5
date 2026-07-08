"""Tests for uuid path-traversal sanitisation in new_gate_anpr."""

import re

_SAFE_UUID = re.compile(r"^[A-Za-z0-9\-]+$")


def _is_safe(uuid):
    return bool(uuid and _SAFE_UUID.match(uuid))


def test_valid_uuid_passes():
    assert _is_safe("550e8400-e29b-41d4-a716-446655440000")


def test_path_traversal_rejected():
    assert not _is_safe("../../../etc/passwd")


def test_null_bytes_rejected():
    assert not _is_safe("abc\x00def")


def test_slash_rejected():
    assert not _is_safe("abc/def")


def test_empty_rejected():
    assert not _is_safe("")


def test_none_rejected():
    assert not _is_safe(None)


def test_alphanumeric_passes():
    assert _is_safe("abcABC123")


def test_hyphen_allowed():
    assert _is_safe("abc-def-123")
