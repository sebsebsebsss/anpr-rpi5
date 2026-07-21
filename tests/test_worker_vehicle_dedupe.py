import json

import pytest


def test_candidate_plate_keys_normalise_and_dedupe():
    import new_gate_anpr

    candidates = [
        {"plate": "ab12cdo"},
        {"plate": "AB12CD0"},
        {"plate": "  "},
        {},
        {"plate": "XY99ZZZ"},
    ]

    assert new_gate_anpr._candidate_plate_keys(candidates) == ["A812CD0", "XY99222"]


def test_recent_vehicle_match_covers_ocr_variants_and_expiry():
    import new_gate_anpr

    cache = {}
    new_gate_anpr._mark_recent_plate_keys(cache, ["QQ7VVV"], 1000)

    assert new_gate_anpr._recent_vehicle_match(["QQ7VVV"], cache, 1010, 30) == "QQ7VVV"
    assert new_gate_anpr._recent_vehicle_match(["QQ0VVV"], cache, 1010, 30, max_distance=2) == "QQ7VVV"
    assert new_gate_anpr._recent_vehicle_match(["QQ0VVV"], cache, 1031, 30, max_distance=2) is None


class _FakeJob:
    def __init__(self, job_id, payload):
        self.id = job_id
        self.body = json.dumps(payload)


class _FakeClient:
    def __init__(self, jobs):
        self._jobs = list(jobs)
        self.deleted = []
        self.touched = []

    def watch(self, *_):
        pass

    def ignore(self, *_):
        pass

    def reserve(self, timeout=None):
        if self._jobs:
            return self._jobs.pop(0)
        raise KeyboardInterrupt

    def touch(self, job):
        self.touched.append(job.id)

    def delete(self, job):
        self.deleted.append(job.id)

    def release(self, *_args, **_kwargs):
        raise AssertionError("duplicate regression test should not release jobs")


def _payload(uuid, plate, confidence, *, epoch_ms=999000, extra_candidates=None):
    candidates = [{"plate": plate, "confidence": confidence}]
    if extra_candidates:
        candidates.extend(extra_candidates)
    return {
        "uuid": uuid,
        "epoch_time": epoch_ms,
        "processing_time_ms": 100,
        "results": [{"candidates": candidates}],
    }


def test_consumer_suppresses_same_vehicle_recognised_and_ocr_variant(monkeypatch):
    import new_gate_anpr

    records = []
    opened = []

    monkeypatch.setattr(new_gate_anpr.greenstalk, "TimedOutError", TimeoutError, raising=False)
    monkeypatch.setattr(new_gate_anpr, "_maybe_reload_allowlist", lambda: None)
    monkeypatch.setattr(new_gate_anpr, "open_gate", lambda *args, **kwargs: opened.append(args))
    monkeypatch.setattr(new_gate_anpr, "_record_event", lambda **kwargs: records.append(kwargs))
    monkeypatch.setattr(new_gate_anpr.time, "time", lambda: 1000)
    monkeypatch.setattr(new_gate_anpr, "list_of_plates", [("QQ17VVV", "Owner")])
    monkeypatch.setattr(new_gate_anpr, "last_seen_allowed", {})
    monkeypatch.setattr(new_gate_anpr, "last_seen_unmatched", {})
    monkeypatch.setattr(new_gate_anpr, "FUZZY_ALLOWLIST", True)
    monkeypatch.setattr(new_gate_anpr, "FUZZY_MAX_DISTANCE", 1)
    monkeypatch.setattr(new_gate_anpr, "FUZZY_MIN_CONFIDENCE", 70)
    monkeypatch.setattr(new_gate_anpr, "VEHICLE_DEDUP_MAX_DISTANCE", 2)

    client = _FakeClient(
        [
            _FakeJob(
                "job-1",
                _payload(
                    "frame-1",
                    "QQ7VVV",
                    80.43,
                    extra_candidates=[{"plate": "QQ17VVV", "confidence": 81.18}],
                ),
            ),
            _FakeJob("job-2", _payload("frame-2", "QQ17VVV", 81.18)),
            _FakeJob("job-3", _payload("frame-3", "QQ0VVV", 76.23)),
        ]
    )

    with pytest.raises(KeyboardInterrupt):
        new_gate_anpr.consumer_main(client)

    assert client.deleted == ["job-1", "job-2", "job-3"]
    assert len(opened) == 1
    assert len(records) == 1
    assert records[0]["kind"] == "recognised"
    assert records[0]["plate"] == "QQ17VVV"
    assert records[0]["observed_plate"] == "QQ7VVV"
