import json
import sqlite3
from collections import OrderedDict
from types import SimpleNamespace

import pytest
from gate_runtime import init_events_db, insert_event, insert_recent_decision, read_recent_decisions


def _detail(reason="not_allowlisted", command="not_requested"):
    return {"decision": {"version": 1, "reason": reason, "match_type": "none", "relay_command": command}}


def _events(database):
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute("SELECT * FROM events ORDER BY id")]
    for row in rows:
        row["detail"] = json.loads(row["detail"]) if row["detail"] else None
    return rows


def test_recent_decisions_upgrade_preserves_existing_history(tmp_path):
    database = tmp_path / "events.db"
    init_events_db(database)
    insert_event(database, plate="QQ17VVV", kind="recognised", detail={"legacy": True})
    with sqlite3.connect(database) as conn:
        conn.execute("DROP TABLE recent_decisions")
    init_events_db(database)
    init_events_db(database)

    assert read_recent_decisions(database) == []
    assert len(_events(database)) == 1
    assert _events(database)[0]["detail"] == {"legacy": True}


def test_recent_decisions_retention_order_and_read_contract(tmp_path):
    database = tmp_path / "events with ? in filename.db"
    init_events_db(database)
    insert_event(database, plate="existing", kind="unmatched")
    for number in range(205):
        insert_recent_decision(
            database,
            plate=f"sample-{number}",
            observed_plate=f"observed-{number}",
            owner="Owner",
            confidence=80.5,
            image_name="safe-id.jpg",
            captured_at="2026-09-29 12:34:56",
            detail=_detail(),
        )

    rows = read_recent_decisions(database)
    assert len(rows) == 30
    assert [row["plate"] for row in rows[:2]] == ["sample-204", "sample-203"]
    assert rows[0]["detail"] == _detail()
    assert rows[0]["observed_plate"] == "observed-204"
    assert rows[0]["confidence"] == 80.5
    assert set(rows[0]) == {
        "id",
        "plate",
        "observed_plate",
        "owner",
        "confidence",
        "image_name",
        "captured_at",
        "created_at",
        "detail",
    }
    assert len(read_recent_decisions(database, limit=1000)) == 100
    assert len(read_recent_decisions(database, limit=0)) == 1
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recent_decisions").fetchone()[0] == 200
        assert conn.execute("SELECT plate FROM recent_decisions ORDER BY id LIMIT 1").fetchone()[0] == "sample-5"
    assert len(_events(database)) == 1


def test_read_recent_decisions_does_not_create_missing_database(tmp_path):
    database = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        read_recent_decisions(database)
    assert not database.exists()


def test_read_recent_decisions_propagates_missing_table(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database):
        pass
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        read_recent_decisions(database)


def test_corrupt_sample_detail_does_not_hide_other_rows(tmp_path):
    database = tmp_path / "events.db"
    init_events_db(database)
    insert_recent_decision(database, plate="valid", detail=_detail())
    insert_recent_decision(database, plate="corrupt", detail=_detail())
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE recent_decisions SET detail = 'not json' WHERE plate = 'corrupt'")
    rows = read_recent_decisions(database)
    assert rows[0]["detail"] is None
    assert rows[1]["detail"] == _detail()


@pytest.fixture
def worker(monkeypatch, tmp_path):
    import new_gate_anpr

    database = tmp_path / "events.db"
    init_events_db(database)
    state = SimpleNamespace(module=new_gate_anpr, database=database, now=1000.0, opened=[], result=True)
    monkeypatch.setattr(new_gate_anpr, "EVENTS_DB_PATH", str(database))
    monkeypatch.setattr(new_gate_anpr, "_maybe_reload_allowlist", lambda: None)
    monkeypatch.setattr(new_gate_anpr, "list_of_plates", [new_gate_anpr._allowlist_entry("QQ17VVV", "Owner")])
    monkeypatch.setattr(new_gate_anpr, "last_seen_allowed", {})
    monkeypatch.setattr(new_gate_anpr, "last_seen_unmatched", {})
    monkeypatch.setattr(new_gate_anpr, "_recent_decision_samples", OrderedDict())
    monkeypatch.setattr(new_gate_anpr, "_last_recent_decision_sample", None)
    monkeypatch.setattr(new_gate_anpr, "_last_decision_write_warning", None)
    monkeypatch.setattr(new_gate_anpr, "PUSHOVER_ENABLED", False)
    monkeypatch.setattr(new_gate_anpr, "FUZZY_ALLOWLIST", False)
    monkeypatch.setattr(new_gate_anpr, "FUZZY_MAX_DISTANCE", 1)
    monkeypatch.setattr(new_gate_anpr, "FUZZY_MIN_CONFIDENCE", 75)
    monkeypatch.setattr(new_gate_anpr, "MATCH_DEDUP_SECONDS", 60)
    monkeypatch.setattr(new_gate_anpr, "UNMATCHED_DEDUP_SECONDS", 30)
    monkeypatch.setattr(new_gate_anpr, "VEHICLE_DEDUP_MAX_DISTANCE", 2)
    monkeypatch.setattr(new_gate_anpr.time, "time", lambda: state.now)
    monkeypatch.setattr(new_gate_anpr.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(new_gate_anpr.greenstalk, "TimedOutError", TimeoutError, raising=False)

    def open_gate(*args):
        state.opened.append(args)
        return state.result

    monkeypatch.setattr(new_gate_anpr, "open_gate", open_gate)
    return state


def _payload(plate="QQ17VVV", confidence=95, *, captured=999, candidates=None):
    return {
        "uuid": "safe-image-id",
        "epoch_time": captured * 1000,
        "processing_time_ms": 80,
        "results": [
            {"candidates": candidates if candidates is not None else [{"plate": plate, "confidence": confidence}]}
        ],
    }


def _run(worker, steps):
    class Client:
        def __init__(self):
            self.pending = iter(steps)
            self.deleted = []
            self.released = []

        def reserve(self, timeout):
            try:
                worker.now, payload = next(self.pending)
            except StopIteration:
                raise KeyboardInterrupt from None
            return SimpleNamespace(id="job", body=json.dumps(payload))

        def touch(self, job):
            pass

        def delete(self, job):
            self.deleted.append(job.id)

        def release(self, job, delay):
            self.released.append((job.id, delay))

    client = Client()
    with pytest.raises(KeyboardInterrupt):
        worker.module.consumer_main(client)
    return client


@pytest.mark.parametrize(
    "registered,observed,confidence,fuzzy,expected",
    [
        ("QQ17VVV", "QQ17VVV", 1, False, "exact"),
        ("AB12CDE", "A812CDE", 90, False, "normalised"),
        ("QQ17VVV", "QQ17VVX", 90, True, "fuzzy"),
    ],
)
def test_history_and_sample_explain_selected_match_without_changing_thresholds(
    worker, monkeypatch, registered, observed, confidence, fuzzy, expected
):
    monkeypatch.setattr(worker.module, "list_of_plates", [worker.module._allowlist_entry(registered, "Owner")])
    monkeypatch.setattr(worker.module, "FUZZY_ALLOWLIST", fuzzy)
    client = _run(worker, [(1000, _payload(observed, confidence))])

    events = _events(worker.database)
    assert len(events) == len(worker.opened) == 1
    decision = events[0]["detail"]["decision"]
    assert decision["reason"] == "allowlist_match"
    assert decision["match_type"] == expected
    assert decision["relay_command"] == "pulse_sent"
    assert decision["candidate_rank"] == decision["candidate_count"] == 1
    assert decision["capture_age_ms"] == 1000
    assert read_recent_decisions(worker.database)[0]["detail"] == events[0]["detail"]
    assert client.deleted == ["job"]
    assert client.released == []


def test_explanations_preserve_fuzzy_before_later_exact_candidate_policy(worker, monkeypatch):
    monkeypatch.setattr(worker.module, "FUZZY_ALLOWLIST", True)
    payload = _payload(
        candidates=[
            {"plate": "QQ17VVX", "confidence": 85},
            {"plate": "QQ17VVV", "confidence": 99},
        ]
    )
    _run(worker, [(1000, payload)])
    event = _events(worker.database)[0]
    assert event["observed_plate"] == "QQ17VVX"
    assert event["detail"]["decision"]["match_type"] == "fuzzy"
    assert event["detail"]["decision"]["candidate_count"] == 2
    assert event["detail"]["decision"]["candidate_rank"] == 1
    assert len(worker.opened) == 1


def test_coalesced_request_is_not_reported_as_new_pulse_and_notification_is_unchanged(worker, monkeypatch):
    worker.result = False
    queued = []
    monkeypatch.setattr(worker.module, "PUSHOVER_ENABLED", True)
    monkeypatch.setattr(worker.module, "_pushover_pool", SimpleNamespace(submit=lambda *args: queued.append(args)))
    _run(worker, [(1000, _payload())])
    event = _events(worker.database)[0]
    assert event["kind"] == "recognised"
    assert event["detail"]["decision"]["relay_command"] == "coalesced"
    assert len(worker.opened) == len(queued) == 1


@pytest.mark.parametrize("empty", [False, True])
def test_unmatched_and_empty_candidates_have_explanations_and_samples(worker, empty):
    payload = _payload("XY99FFF", candidates=[] if empty else None)
    _run(worker, [(1000, payload)])
    events = _events(worker.database)
    assert len(events) == 1
    assert worker.opened == []
    decision = events[0]["detail"]["decision"]
    assert decision["reason"] == ("no_candidates" if empty else "not_allowlisted")
    assert decision["relay_command"] == "not_requested"
    assert read_recent_decisions(worker.database)[0]["detail"] == events[0]["detail"]


def test_repeated_allowed_reads_sample_suppression_without_extra_history_or_pulses(worker):
    client = _run(worker, [(when, _payload(captured=when - 1)) for when in [1000, 1001.2, 1002.4, 1032.4]])
    assert len(worker.opened) == len(_events(worker.database)) == 1
    assert len(client.deleted) == 4
    decisions = [row["detail"]["decision"] for row in read_recent_decisions(worker.database)]
    assert [item["reason"] for item in decisions] == ["recent_allowlisted", "recent_allowlisted", "allowlist_match"]
    assert decisions[0]["relay_command"] == "not_requested"
    assert decisions[0]["suppression_window_seconds"] == 60


@pytest.mark.parametrize("known,reason", [(True, "recent_vehicle"), (False, "recent_unmatched")])
def test_nearby_ocr_suppression_explains_existing_decision(worker, monkeypatch, known, reason):
    if not known:
        monkeypatch.setattr(worker.module, "list_of_plates", [])
    _run(worker, [(1000, _payload()), (1001.2, _payload("QQ17VVX", captured=1000))])
    assert len(_events(worker.database)) == 1
    assert len(worker.opened) == int(known)
    decision = read_recent_decisions(worker.database)[0]["detail"]["decision"]
    assert decision["reason"] == reason
    assert decision["relay_command"] == "not_requested"
    assert decision["matched_recent_plate"] == "QQ17VVV"


def test_stale_capture_is_visible_without_main_history_or_actuation(worker):
    client = _run(worker, [(1000, _payload(captured=989))])
    assert worker.opened == _events(worker.database) == []
    assert client.deleted == ["job"]
    decision = read_recent_decisions(worker.database)[0]["detail"]["decision"]
    assert decision["reason"] == "stale_capture"
    assert decision["match_type"] == "not_evaluated"
    assert decision["capture_age_ms"] == 11000


@pytest.mark.parametrize("activated", [False, True])
def test_relay_failure_explanation_preserves_retry_and_uncertain_suppression(worker, monkeypatch, activated):
    attempts = []

    def fail_once(*args):
        attempts.append(args)
        if len(attempts) == 1:
            raise worker.module.GateActuationError("mock GPIO failure", may_have_activated=activated)
        return True

    monkeypatch.setattr(worker.module, "open_gate", fail_once)
    client = _run(worker, [(1000, _payload()), (1005, _payload())])
    assert client.released == [("job", 5)]
    assert client.deleted == ["job"]
    assert len(attempts) == (1 if activated else 2)
    assert len(_events(worker.database)) == (0 if activated else 1)
    decisions = [row["detail"]["decision"] for row in read_recent_decisions(worker.database)]
    failure = next(item for item in decisions if item["reason"] == "relay_error")
    assert failure["relay_command"] == ("uncertain" if activated else "failed_before_activation")


def test_diagnostic_write_failure_does_not_retry_successful_job_or_hide_main_event(worker, monkeypatch):
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("diagnostic storage unavailable")

    monkeypatch.setattr(worker.module, "insert_recent_decision", unavailable)
    client = _run(worker, [(1000, _payload())])
    assert client.deleted == ["job"]
    assert client.released == []
    assert len(worker.opened) == len(_events(worker.database)) == 1
    assert _events(worker.database)[0]["detail"]["decision"]["relay_command"] == "pulse_sent"


@pytest.mark.parametrize("activated", [False, True])
def test_diagnostic_failure_cannot_replace_original_actuation_error(worker, monkeypatch, activated):
    def failed_pulse(*args):
        raise worker.module.GateActuationError("original pulse error", may_have_activated=activated)

    def failed_diagnostic(*args, **kwargs):
        # If this escaped, the consumer would treat it as a malformed job and
        # delete it instead of preserving the original relay retry behaviour.
        raise ValueError("diagnostic serialization failed")

    monkeypatch.setattr(worker.module, "open_gate", failed_pulse)
    monkeypatch.setattr(worker.module, "insert_recent_decision", failed_diagnostic)
    client = _run(worker, [(1000, _payload())])
    assert client.released == [("job", 5)]
    assert client.deleted == []
    assert bool(worker.module.last_seen_allowed) is activated
    assert _events(worker.database) == []


def test_sampling_bounds_writes_but_keeps_command_and_changed_error_outcome(worker, monkeypatch):
    stored = []
    monkeypatch.setattr(worker.module, "insert_recent_decision", lambda *args, **kwargs: stored.append(kwargs))
    record = worker.module._record_recent_decision
    assert record(plate="A", detail=_detail())
    worker.now += 0.1
    assert not record(plate="B", detail=_detail())
    assert record(plate="B", detail=_detail("allowlist_match", "pulse_sent"))
    assert record(plate="C", detail=_detail("relay_error", "failed_before_activation"))
    assert not record(plate="C", detail=_detail("relay_error", "failed_before_activation"))
    assert record(plate="C", detail=_detail("relay_error", "uncertain"))
    worker.now += 1
    assert not record(plate="A", detail=_detail())
    worker.now += 30
    assert record(plate="A", detail=_detail())
    assert len(stored) == 5
    for number in range(300):
        worker.now += 1
        record(plate=f"different-{number}", detail=_detail())
    assert len(worker.module._recent_decision_samples) == 256
