#!/usr/bin/env python3
## Version 0.4 – Python 3 refactor (behaviour unchanged)

import json
import os
import re
import signal
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from time import gmtime, strftime

import greenstalk
import requests
from allowlist_util import normalise_plate
from gate_runtime import configure_logging, env_int, init_events_db, insert_event, open_gate

_SAFE_UUID = re.compile(r"^[A-Za-z0-9\-]+$")

PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")
PUSHOVER_APP_TOKEN = os.getenv("PUSHOVER_APP_TOKEN")
ALLOWLIST_PATH = os.getenv("PLATE_ALLOWLIST_PATH", "").strip()
ALLOWLIST_JSON = os.getenv("PLATE_ALLOWLIST_JSON", "").strip()
FUZZY_ALLOWLIST = os.getenv("FUZZY_ALLOWLIST", "0").lower() in {"1", "true", "yes", "on"}
FUZZY_MAX_DISTANCE = env_int("FUZZY_MAX_DISTANCE", 1, "gate_anpr")
FUZZY_MIN_CONFIDENCE = float(os.getenv("FUZZY_MIN_CONFIDENCE", "85"))

log = configure_logging("gate_anpr", log_path="/var/log/gate-anpr/gate-anpr.log")

_pushover_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pushover")


def _handle_sigterm(*_):
    # Installing any handler removes SIGTERM's default terminate action, so we
    # must exit explicitly or `systemctl stop` hangs until the SIGKILL timeout.
    _pushover_pool.shutdown(wait=False)
    sys.exit(0)


signal.signal(signal.SIGTERM, _handle_sigterm)

server = "127.0.0.1"
port = 11300

last_seen_reg = ""
last_seen_time = 0
last_seen_allowed = {}
last_seen_unmatched = {}

MATCH_DEDUP_SECONDS = env_int("MATCH_DEDUP_SECONDS", 60, "gate_anpr")
UNMATCHED_DEDUP_SECONDS = env_int("UNMATCHED_DEDUP_SECONDS", 30, "gate_anpr")
VEHICLE_DEDUP_MAX_DISTANCE = env_int("VEHICLE_DEDUP_MAX_DISTANCE", 2, "gate_anpr")
EVENTS_DB_PATH = os.getenv("GATE_ANPR_EVENTS_DB", "/opt/gate_anpr/events.db")
EVENT_SOURCE = os.getenv("GATE_ANPR_EVENT_SOURCE", "alprd")


def _resolve_allowlist_path():
    path = ALLOWLIST_PATH
    if not path and os.path.exists("/opt/gate_anpr/allowlist.json"):
        path = "/opt/gate_anpr/allowlist.json"
    if not path and os.path.exists("/etc/gate_anpr_allowlist.json"):
        path = "/etc/gate_anpr_allowlist.json"
    return path


def _load_plate_allowlist():
    path = _resolve_allowlist_path()
    if path:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            mtime = os.path.getmtime(path)
        except FileNotFoundError:
            log.error("Allowlist file not found: %s", path)
            return [], None, path
        except json.JSONDecodeError as exc:
            log.error("Invalid allowlist JSON in %s: %s", path, exc)
            return [], None, path
    elif ALLOWLIST_JSON:
        try:
            data = json.loads(ALLOWLIST_JSON)
            mtime = None
        except json.JSONDecodeError as exc:
            log.error("Invalid PLATE_ALLOWLIST_JSON: %s", exc)
            return [], None, None
    else:
        return [], None, None
    allowlist = []
    for item in data:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            allowlist.append(_allowlist_entry(item[0], item[1]))
        elif isinstance(item, dict) and "owner" in item and "plates" in item:
            owner = str(item["owner"])
            plates = item.get("plates") or []
            if isinstance(plates, str):
                plates = [plates]
            for plate in plates:
                allowlist.append(_allowlist_entry(plate, owner))
        elif isinstance(item, dict) and "plate" in item and "owner" in item:
            allowlist.append(_allowlist_entry(item["plate"], item["owner"]))
        else:
            log.warning("Skipping malformed allowlist entry: %r", item)
    return allowlist, mtime, path


def _allowlist_entry(plate, owner):
    display_plate = str(plate).strip()
    return normalise_plate(display_plate), str(owner), display_plate


def _allowlist_owner_map(entries):
    return {entry[0]: entry[1] for entry in entries if len(entry) >= 2}


def _allowlist_display_map(entries):
    mapping = {}
    for entry in entries:
        if len(entry) < 2:
            continue
        display_plate = entry[2] if len(entry) >= 3 and entry[2] else entry[0]
        mapping.setdefault(entry[0], display_plate)
    return mapping


list_of_plates, allowlist_mtime, allowlist_watch_path = _load_plate_allowlist()
if not list_of_plates:
    log.warning("No plates configured (PLATE_ALLOWLIST_PATH/PLATE_ALLOWLIST_JSON is empty)")

# NOTE: you had GPIO.BOARD with gatePin=23 in the original.
# That is internally inconsistent with the comment, but it "works" in your current setup.
# Keeping the same defaults while allowing overrides via env.
gatePin = env_int("GATE_PIN_BOARD", 23, "gate_anpr")  # legacy BOARD numbering
gatePin_bcm = env_int("GATE_PIN_BCM", 11, "gate_anpr")  # BOARD 23 maps to BCM 11 on Raspberry Pi

PUSHOVER_ENABLED = bool(PUSHOVER_USER_KEY and PUSHOVER_APP_TOKEN)
if not PUSHOVER_ENABLED:
    log.warning("Pushover disabled (missing PUSHOVER_USER_KEY or PUSHOVER_APP_TOKEN)")


def _sanitise_uuid(uuid):
    """Return uuid only if it is a safe string; else None.

    The isinstance check matters — a non-str uuid would make re.match raise
    TypeError, which the generic job handler treats as transient and retries
    forever (poison job).
    """
    if uuid is None:
        return None
    if not isinstance(uuid, str) or not _SAFE_UUID.match(uuid):
        log.warning("Rejecting unsafe uuid %r; skipping plate image", uuid)
        return None
    return uuid


def _job_body_to_str(body):
    # greenstalk job.body may be str or bytes depending on config/version
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="strict")
    return body


def _fuzzy_distance(a, b, max_distance):
    # Early-exit Levenshtein with a max distance cutoff.
    if a == b:
        return 0
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    if max_distance <= 0:
        return max_distance + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        row_min = curr[0]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost))
            if curr[j] < row_min:
                row_min = curr[j]
        if row_min > max_distance:
            return max_distance + 1
        prev = curr
    return prev[-1]


def _fuzzy_allowlist_match(candidate, allowlist_map):
    if not FUZZY_ALLOWLIST or not allowlist_map:
        return None
    best_plate = None
    best_dist = FUZZY_MAX_DISTANCE + 1
    for plate, owner in allowlist_map.items():
        dist = _fuzzy_distance(candidate, plate, FUZZY_MAX_DISTANCE)
        if dist <= FUZZY_MAX_DISTANCE and dist < best_dist:
            best_dist = dist
            best_plate = plate
            best_owner = owner
    if best_plate is None:
        return None
    return best_plate, best_owner, best_dist


def _candidate_plate_keys(candidates):
    keys = []
    seen = set()
    for cand in candidates:
        plate = cand.get("plate") if isinstance(cand, dict) else None
        if not isinstance(plate, str) or not plate.strip():
            continue
        key = normalise_plate(plate)
        if key and key not in seen:
            keys.append(key)
            seen.add(key)
    return keys


def _mark_recent_plate_keys(cache, keys, now):
    for key in keys:
        if key:
            cache[key] = now


def _prune_recent_plate_keys(cache, now, window_seconds):
    expired = [key for key, ts in cache.items() if now > ts + window_seconds]
    for key in expired:
        cache.pop(key, None)


def _recent_vehicle_match(keys, cache, now, window_seconds, max_distance=0):
    _prune_recent_plate_keys(cache, now, window_seconds)
    keys = [key for key in keys if key]
    for key in keys:
        if key in cache:
            return key
    if max_distance <= 0:
        return None
    for key in keys:
        for seen_key in cache:
            if _fuzzy_distance(key, seen_key, max_distance) <= max_distance:
                return seen_key
    return None


def _record_event(
    *,
    uuid,
    plate,
    owner,
    allowed,
    confidence,
    kind,
    image_name,
    captured_at,
    processing_time_ms,
    observed_plate=None,
    observed_confidence=None,
    fuzzy_distance=None,
    fuzzy=None,
):
    insert_event(
        EVENTS_DB_PATH,
        uuid=uuid,
        plate=plate,
        owner=owner,
        allowed=allowed,
        confidence=confidence,
        kind=kind,
        image_name=image_name,
        captured_at=captured_at,
        source=EVENT_SOURCE,
        processing_time_ms=processing_time_ms,
        observed_plate=observed_plate,
        observed_confidence=observed_confidence,
        fuzzy_distance=fuzzy_distance,
        fuzzy=fuzzy,
    )


def _pushover_message(owner, display_plate):
    owner_text = str(owner or "").strip()
    plate_text = str(display_plate or "").strip()
    if owner_text and plate_text:
        return "%s - %s" % (owner_text, plate_text)
    return owner_text or plate_text


def _send_pushover(owner, display_plate, jpg_path):
    """Fire-and-forget Pushover notification; runs off the consumer hot path."""
    message = _pushover_message(owner, display_plate)
    try:
        if os.path.exists(jpg_path):
            with open(jpg_path, "rb") as f:
                resp = requests.post(
                    "https://api.pushover.net/1/messages.json",
                    data={
                        "token": PUSHOVER_APP_TOKEN,
                        "user": PUSHOVER_USER_KEY,
                        "message": message,
                    },
                    files={"attachment": ("car-reg.jpg", f, "image/jpeg")},
                    timeout=15,
                )
        else:
            log.warning("Pushover image missing: %s", jpg_path)
            resp = requests.post(
                "https://api.pushover.net/1/messages.json",
                data={
                    "token": PUSHOVER_APP_TOKEN,
                    "user": PUSHOVER_USER_KEY,
                    "message": message,
                },
                timeout=15,
            )
        if resp.status_code != 200:
            body = (resp.text or "").strip()
            if len(body) > 300:
                body = body[:300] + "…"
            log.warning("Pushover failed: status=%s body=%s", resp.status_code, body)
        else:
            log.info("Pushover sent: status=200")
    except Exception as exc:
        log.warning("Pushover failed: %s", exc)


def _maybe_reload_allowlist():
    global list_of_plates, allowlist_mtime, allowlist_watch_path
    path = _resolve_allowlist_path()
    if not path:
        return
    try:
        mtime = os.path.getmtime(path)
    except FileNotFoundError:
        return
    if allowlist_watch_path != path or allowlist_mtime is None or mtime != allowlist_mtime:
        list_of_plates, allowlist_mtime, allowlist_watch_path = _load_plate_allowlist()
        log.info("Reloaded plate allowlist (%s entries)", len(list_of_plates))


def _extract_job_data(job):
    body_str = _job_body_to_str(job.body)
    log.debug("Job %s body length=%s", getattr(job, "id", "?"), len(body_str))
    payload = json.loads(body_str)
    if not isinstance(payload, dict):
        raise ValueError("job payload must be a JSON object")
    epoch_time = payload.get("epoch_time")
    if not isinstance(epoch_time, (int, float)):
        raise ValueError("job payload missing numeric epoch_time")
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("job payload missing results")
    first_result = results[0]
    if not isinstance(first_result, dict):
        raise ValueError("job payload results[0] must be an object")
    candidates = first_result.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("job payload results[0].candidates must be a list")
    for idx, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError(f"candidate {idx} must be an object")
        plate = candidate.get("plate")
        if not isinstance(plate, str) or not plate.strip():
            raise ValueError(f"candidate {idx} missing plate")
    return payload, candidates


def _new_client():
    client = greenstalk.Client((server, port))
    client.watch("alprd")
    try:
        client.ignore("default")
    except Exception:
        pass
    return client


def consumer_main(client):
    global last_seen_time, last_seen_reg

    log.info("Enabling the pins")

    while True:
        log.debug("Waiting for job on beanstalkd")
        try:
            job = client.reserve(timeout=10)
        except greenstalk.TimedOutError:
            log.debug("No job available; waiting")
            continue
        except Exception as exc:
            log.warning("Beanstalk reserve failed: %s", exc)
            time.sleep(2)
            client = _new_client()
            continue

        try:
            # Parse job JSON
            json_raw, candidates = _extract_job_data(job)
            _maybe_reload_allowlist()

            # Touch early to reduce chance of TTR expiry during GPIO/pushover I/O
            client.touch(job)

            capture_epoch = json_raw["epoch_time"] / 1000
            min_time = capture_epoch + 10
            no_of_plates_seen = len(candidates)
            # Sanitise before uuid is used anywhere: image_name is stored in the
            # events DB and becomes an /images/ URL in the UI on every code path,
            # not just the matched one.
            uuid = _sanitise_uuid(json_raw.get("uuid"))
            image_name = f"{uuid}.jpg" if uuid else ""
            captured_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(capture_epoch))
            processing_time_ms = json_raw.get("processing_time_ms")
            allowlist_map = _allowlist_owner_map(list_of_plates)
            allowlist_display_map = _allowlist_display_map(list_of_plates)
            candidate_keys = _candidate_plate_keys(candidates)

            log.info("Current Time: %s", strftime("%Y-%m-%d %H:%M:%S", gmtime()))
            log.info(
                "Time Plates Captured: %s",
                captured_at,
            )
            log.info("Processing Time was: %s", json_raw.get("processing_time_ms"))
            log.info("%s plates seen", no_of_plates_seen)

            # Do the thing if plate is recent, valid and hasn't already been recently seen
            if min_time > time.time():
                matched = False
                suppressed_duplicate = False
                for cand in candidates:
                    number_plate = cand["plate"]
                    norm_plate = normalise_plate(number_plate)
                    log.info("Candidate plate: %s", number_plate)

                    allowed = norm_plate in allowlist_map
                    fuzzy_match = None
                    if not allowed:
                        conf = cand.get("confidence")
                        if conf is not None and conf >= FUZZY_MIN_CONFIDENCE:
                            fuzzy_match = _fuzzy_allowlist_match(norm_plate, allowlist_map)
                            if fuzzy_match:
                                allowed = True

                    now = time.time()
                    match_plate = fuzzy_match[0] if fuzzy_match else norm_plate
                    last_match = last_seen_allowed.get(match_plate, 0)
                    not_recently_seen = now > (last_match + MATCH_DEDUP_SECONDS)

                    if allowed and not_recently_seen:
                        last_seen_time = now
                        last_seen_reg = match_plate
                        owner = allowlist_map.get(match_plate, "")
                        display_plate = allowlist_display_map.get(match_plate, match_plate)
                        _mark_recent_plate_keys(last_seen_allowed, [match_plate, norm_plate] + candidate_keys, now)
                        matched = True
                        if fuzzy_match:
                            log.info(
                                "Fuzzy allowlist match: %s -> %s (dist=%s). Opening gate",
                                number_plate,
                                display_plate,
                                fuzzy_match[2],
                            )
                        else:
                            log.info("Plate %s recognised. Opening gate", display_plate)

                        open_gate(gatePin, gatePin_bcm, log)

                        jpg_path = "/home/pi/plates/%s.jpg" % uuid if uuid else ""
                        log.debug("Sending pushover with image %s", jpg_path)
                        _record_event(
                            uuid=uuid,
                            plate=match_plate,
                            owner=owner,
                            allowed=True,
                            confidence=cand.get("confidence"),
                            kind="recognised",
                            image_name=image_name,
                            captured_at=captured_at,
                            processing_time_ms=processing_time_ms,
                            observed_plate=number_plate,
                            observed_confidence=cand.get("confidence"),
                            fuzzy_distance=fuzzy_match[2] if fuzzy_match else 0,
                            fuzzy=1 if fuzzy_match else 0,
                        )
                        log.info(
                            "Recorded event kind=recognised plate=%s confidence=%s allowed=true",
                            match_plate,
                            cand.get("confidence"),
                        )
                        if PUSHOVER_ENABLED:
                            _pushover_pool.submit(_send_pushover, owner, display_plate, jpg_path)
                            log.debug("Pushover queued for %s", _pushover_message(owner, display_plate))
                        else:
                            log.debug("Pushover skipped (not configured)")
                        break
                    else:
                        log.debug(
                            "Plate %s allowed=%s recently_seen=%s",
                            number_plate,
                            allowed,
                            not not_recently_seen,
                        )
                        if allowed and not not_recently_seen:
                            suppressed_duplicate = True
                            _mark_recent_plate_keys(
                                last_seen_allowed,
                                [match_plate, norm_plate] + candidate_keys,
                                now,
                            )
                            log.info(
                                "Suppressing duplicate recognised vehicle for %s (matched %s)",
                                number_plate,
                                match_plate,
                            )
                            break
                if not matched and not suppressed_duplicate:
                    top_plate = candidates[0]["plate"] if candidates else "UNKNOWN"
                    now = time.time()
                    top_key = normalise_plate(top_plate)
                    recent_allowed = _recent_vehicle_match(
                        [top_key] + candidate_keys,
                        last_seen_allowed,
                        now,
                        MATCH_DEDUP_SECONDS,
                        VEHICLE_DEDUP_MAX_DISTANCE,
                    )
                    recent_unmatched = _recent_vehicle_match(
                        [top_key] + candidate_keys,
                        last_seen_unmatched,
                        now,
                        UNMATCHED_DEDUP_SECONDS,
                        VEHICLE_DEDUP_MAX_DISTANCE,
                    )
                    if recent_allowed:
                        _mark_recent_plate_keys(last_seen_allowed, [top_key] + candidate_keys, now)
                        log.info(
                            "Suppressing duplicate vehicle read %s near recent recognised key %s",
                            top_plate,
                            recent_allowed,
                        )
                    elif recent_unmatched:
                        _mark_recent_plate_keys(last_seen_unmatched, [top_key] + candidate_keys, now)
                        log.info(
                            "Suppressing duplicate unmatched vehicle read %s near recent key %s",
                            top_plate,
                            recent_unmatched,
                        )
                    else:
                        _mark_recent_plate_keys(last_seen_unmatched, [top_key] + candidate_keys, now)
                        owner = allowlist_map.get(top_key, "")
                        is_known = bool(owner)
                        event_kind = "recognised" if is_known else "unmatched"
                        event_plate = top_key if is_known else top_plate
                        _record_event(
                            uuid=uuid,
                            plate=event_plate,
                            owner=owner,
                            allowed=is_known,
                            confidence=candidates[0].get("confidence") if candidates else None,
                            kind=event_kind,
                            image_name=image_name,
                            captured_at=captured_at,
                            processing_time_ms=processing_time_ms,
                            observed_plate=top_plate,
                            observed_confidence=candidates[0].get("confidence") if candidates else None,
                            fuzzy_distance=None,
                            fuzzy=0,
                        )
                        log.info(
                            "Recorded event kind=%s plate=%s confidence=%s allowed=%s",
                            event_kind,
                            top_plate,
                            candidates[0].get("confidence") if candidates else None,
                            is_known,
                        )
            else:
                log.debug("Plate result too old; skipping (min_time=%s)", min_time)

            # Success: delete job so it doesn't come back
            client.delete(job)
            log.debug("Job %s processed and deleted", getattr(job, "id", "?"))

        except ValueError as exc:
            log.error("Dropping malformed job %s: %s", getattr(job, "id", "?"), exc)
            try:
                client.delete(job)
            except Exception:
                log.exception("Failed to delete malformed job %s", getattr(job, "id", "?"))
        except Exception:
            log.exception("Exception in consumer loop")
            traceback.print_exc()
            # Transient failure: put it back for retry after a short delay
            try:
                client.release(job, delay=5)
            except Exception:
                pass
            continue


def main():
    try:
        log.info("Setting up connection")
        init_events_db(EVENTS_DB_PATH)
        client = _new_client()

        log.info("Opening the gate for:")
        for entry in list_of_plates:
            plate, owner = entry[0], entry[1]
            display_plate = entry[2] if len(entry) >= 3 and entry[2] else plate
            log.info(" - %s (%s)", display_plate, owner)
        log.info("---")
        log.info("Eating the beans")
        consumer_main(client)

    except KeyboardInterrupt:
        log.info("Killed by user")
        sys.exit(0)
    except Exception:
        log.exception("Exception Occurred")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
