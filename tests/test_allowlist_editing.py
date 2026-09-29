"""Allowlist edits must preserve matching, legacy data, and the last valid file."""

import json

import app
import pytest
from allowlist_util import normalise_plate, validate_allowlist
from gate_runtime import init_events_db, insert_event

HEADERS = {"X-Gate-Api-Secret": "test-secret-for-ci", "Origin": "http://localhost"}
FORMATS = ("grouped", "flat", "pair")


def entry(kind, plate="SB12 XYZ", owner="Example owner"):
    if kind == "pair":
        return [plate, owner]
    if kind == "flat":
        return {"plate": plate, "owner": owner}
    return {"owner": owner, "plates": [plate]}


@pytest.fixture
def allowlist_client(tmp_path, monkeypatch):
    path = tmp_path / "allowlist.json"
    path.write_text('[{"owner": "Saved owner", "plates": ["QQ17 VVV"]}]\n', encoding="utf-8")
    database = str(tmp_path / "events.db")
    init_events_db(database)
    monkeypatch.setattr(app, "ALLOWLIST_PATH", str(path))
    monkeypatch.setattr(app, "EVENTS_DB_PATH", database)
    monkeypatch.setattr(app, "API_SHARED_SECRET", HEADERS["X-Gate-Api-Secret"])
    monkeypatch.setattr(app, "ALLOWED_ORIGINS", set())
    monkeypatch.setattr(app, "_display_cache", {"mtime": None, "map": {}})

    def reject_actuation(*args, **kwargs):
        raise AssertionError("allowlist reads and edits must never actuate GPIO")

    monkeypatch.setattr(app, "trigger_gate", reject_actuation)
    with app.app.test_client() as client:
        yield client, path


@pytest.mark.parametrize("plate", [" SB12 XYZ ", "SB12XYZ", "SB12\tXYZ", "SB12\u00a0XYZ"])
def test_spacing_and_existing_ocr_confusables_share_one_matching_key(plate):
    assert normalise_plate(plate) == "5812XY2"
    assert normalise_plate(normalise_plate(plate)) == "5812XY2"


@pytest.mark.parametrize("kind", FORMATS)
def test_validator_accepts_existing_formats_without_losing_display_spacing(kind):
    assert validate_allowlist([entry(kind, " sb12 xyz ", " Example owner ")]) == [
        {"owner": "Example owner", "plates": ["SB12 XYZ"]}
    ]


def test_same_registration_case_and_spacing_duplicates_are_collapsed():
    assert validate_allowlist([{"owner": "Example owner", "plates": ["sb12 xyz", "SB12XYZ", " SB12 XYZ "]}]) == [
        {"owner": "Example owner", "plates": ["SB12 XYZ"]}
    ]


@pytest.mark.parametrize("kind", FORMATS)
def test_accepted_api_edit_saves_grouped_format_and_registered_display(allowlist_client, kind):
    client, path = allowlist_client
    response = client.put("/api/plates", json=[entry(kind, " sb12 xyz ")], headers=HEADERS)
    assert response.status_code == 200
    expected = [{"owner": "Example owner", "plates": ["SB12 XYZ"]}]
    assert json.loads(path.read_text()) == expected
    assert client.get("/api/plates", headers=HEADERS).get_json() == expected
    assert app._display_plate("5812 XY2") == "SB12 XYZ"


@pytest.mark.parametrize("kind", FORMATS)
@pytest.mark.parametrize("second_owner", ["First owner", "Second owner"])
def test_distinct_confusable_registrations_reject_without_changing_file(allowlist_client, kind, second_owner):
    client, path = allowlist_client
    before, modified = path.read_bytes(), path.stat().st_mtime_ns
    payload = [entry("grouped", "AB12 CDE", "First owner"), entry(kind, "A812CDE", second_owner)]
    response = client.put("/api/plates", json=payload, headers=HEADERS)
    assert response.status_code == 400
    assert "conflict" in response.get_json()["error"].lower()
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == modified


def test_same_row_confusable_registrations_are_not_treated_as_duplicates(allowlist_client):
    client, path = allowlist_client
    before = path.read_bytes()
    response = client.put(
        "/api/plates",
        json=[{"owner": "Example owner", "plates": ["AB12 CDE", "A812CDE"]}],
        headers=HEADERS,
    )
    assert response.status_code == 400
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", FORMATS)
@pytest.mark.parametrize("bad_plate", [None, 123, False, {}, [], " \t ", "AB/12", "\u0410B12CDE", "A" * 33])
def test_invalid_plate_types_and_values_never_replace_saved_allowlist(allowlist_client, kind, bad_plate):
    client, path = allowlist_client
    before, modified = path.read_bytes(), path.stat().st_mtime_ns
    # A valid earlier row must not be written before the invalid row is found.
    payload = [entry("grouped", "QQ17 VVV", "Valid owner"), entry(kind, bad_plate)]
    response = client.put("/api/plates", json=payload, headers=HEADERS)
    assert response.status_code == 400
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == modified


@pytest.mark.parametrize("kind", FORMATS)
@pytest.mark.parametrize("bad_owner", [None, 123, False, {}, [], " \t "])
def test_invalid_owners_are_rejected_consistently_for_every_format(allowlist_client, kind, bad_owner):
    client, path = allowlist_client
    before = path.read_bytes()
    response = client.put("/api/plates", json=[entry(kind, owner=bad_owner)], headers=HEADERS)
    assert response.status_code == 400
    assert path.read_bytes() == before


@pytest.mark.parametrize("plates", [None, 123, False, {}, [], {"SB12XYZ": True}])
def test_invalid_grouped_plate_collections_do_not_become_iterable_plate_names(allowlist_client, plates):
    client, path = allowlist_client
    before = path.read_bytes()
    response = client.put("/api/plates", json=[{"owner": "Example owner", "plates": plates}], headers=HEADERS)
    assert response.status_code == 400
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", (*FORMATS, "grouped_string"))
def test_legacy_reads_join_old_spaced_history_without_rewriting_the_file(allowlist_client, kind):
    client, path = allowlist_client
    saved = entry(kind)
    if kind == "grouped_string":
        saved["plates"] = "SB12 XYZ"
    path.write_text(json.dumps([saved], indent=4) + "\n", encoding="utf-8")
    before, modified = path.read_bytes(), path.stat().st_mtime_ns
    insert_event(
        app.EVENTS_DB_PATH,
        plate="5812 XY2",
        kind="recognised",
        captured_at="2026-01-01 03:04:05",
        confidence=81,
    )
    insert_event(
        app.EVENTS_DB_PATH,
        plate="5812XY2",
        kind="recognised",
        captured_at="2026-01-02 03:04:05",
        confidence=91,
    )
    plates = client.get("/api/plates", headers=HEADERS)
    status = client.get("/api/allowlist-status", headers=HEADERS)
    assert plates.status_code == status.status_code == 200
    assert plates.get_json() == [{"owner": "Example owner", "plates": ["SB12 XYZ"]}]
    assert status.get_json() == [
        {
            "owner": "Example owner",
            "plates": [{"plate": "SB12 XYZ", "last_seen": "2026-01-02 03:04:05", "confidence": 91}],
        }
    ]
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == modified


def test_existing_conflicts_can_still_be_read_for_correction(allowlist_client):
    client, path = allowlist_client
    path.write_text(json.dumps([entry("pair", "AB12 CDE", "One"), entry("pair", "A812CDE", "Two")]))
    before = path.read_bytes()
    response = client.get("/api/plates", headers=HEADERS)
    assert response.status_code == 200
    assert response.get_json() == [
        {"owner": "One", "plates": ["AB12 CDE"]},
        {"owner": "Two", "plates": ["A812CDE"]},
    ]
    assert path.read_bytes() == before


def test_explicit_empty_replacement_clears_allowlist(allowlist_client):
    client, path = allowlist_client
    response = client.put("/api/plates", json=[], headers=HEADERS)
    assert response.status_code == 200
    assert json.loads(path.read_text()) == []
