"""Plates must be displayed as entered in admin, resolved at render time from
the current allowlist — never as the recorded/observed value.

Regression for: a plate registered as "S3BPN" showed as "538PN" everywhere,
because recognition matches on an OCR-confusable-folded key (S->5, B->8) and
the UI trusted that stored key instead of resolving back to the admin entry.
"""

import json

import app
from gate_runtime import insert_event


def _set_allowlist(tmp_path, entries):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    app.ALLOWLIST_PATH = str(path)
    app._display_cache["mtime"] = None  # force rebuild regardless of mtime granularity


def test_folded_key_resolves_to_registered_plate(tmp_path):
    _set_allowlist(tmp_path, [{"owner": "Seb", "plates": ["S3BPN"]}])
    # Whatever variant got stored, display is always the admin entry.
    assert app._display_plate("538PN") == "S3BPN"
    assert app._display_plate("S3BPN") == "S3BPN"


def test_unknown_plate_passes_through(tmp_path):
    _set_allowlist(tmp_path, [{"owner": "Seb", "plates": ["S3BPN"]}])
    assert app._display_plate("XY99ZZZ") == "XY99ZZZ"  # not on the allowlist
    assert app._display_plate("") == ""


def test_display_follows_admin_edits(tmp_path):
    _set_allowlist(tmp_path, [{"owner": "Seb", "plates": ["S3BPN"]}])
    assert app._display_plate("538PN") == "S3BPN"
    # Owner re-enters the plate with a different (still-folding) form; display
    # tracks the current admin value, not the historical record.
    _set_allowlist(tmp_path, [{"owner": "Seb", "plates": ["S3BPN "]}])
    assert app._display_plate("538PN") == "S3BPN"


def test_query_events_renders_registered_plate(tmp_path):
    _set_allowlist(tmp_path, [{"owner": "Seb", "plates": ["S3BPN"]}])
    # An event recorded with the folded key (as recognition stores it)...
    insert_event(
        app.EVENTS_DB_PATH,
        plate="538PN",
        owner="Seb",
        allowed=True,
        kind="recognised",
        observed_plate="538PN",
    )
    events = app._query_events(kinds=["recognised"], limit=1)
    assert events, "expected the inserted event back"
    # ...is rendered as the registered plate, with the raw read still available.
    assert events[0]["plate"] == "S3BPN"
    assert events[0]["observed_plate"] == "538PN"
