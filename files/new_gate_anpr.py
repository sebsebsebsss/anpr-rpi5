#!/usr/bin/env python3
## Version 0.4 – Python 3 refactor (behaviour unchanged)

import os
import sys
import traceback
import logging
import time
import json
import requests
import sqlite3
from time import gmtime, strftime

import greenstalk
import RPi.GPIO as GPIO

PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")
PUSHOVER_APP_TOKEN = os.getenv("PUSHOVER_APP_TOKEN")
ALLOWLIST_PATH = os.getenv("PLATE_ALLOWLIST_PATH", "").strip()
ALLOWLIST_JSON = os.getenv("PLATE_ALLOWLIST_JSON", "").strip()
FUZZY_ALLOWLIST = os.getenv("FUZZY_ALLOWLIST", "0").lower() in {"1", "true", "yes", "on"}
FUZZY_MAX_DISTANCE = int(os.getenv("FUZZY_MAX_DISTANCE", "1"))
FUZZY_MIN_CONFIDENCE = float(os.getenv("FUZZY_MIN_CONFIDENCE", "85"))

DEBUG = os.getenv("GATE_ANPR_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("gate_anpr")

server = "127.0.0.1"
port = 11300

last_seen_reg = ""
last_seen_time = 0
last_seen_allowed = {}
last_seen_unmatched = {}

MATCH_DEDUP_SECONDS = int(os.getenv("MATCH_DEDUP_SECONDS", "60"))
UNMATCHED_DEDUP_SECONDS = int(os.getenv("UNMATCHED_DEDUP_SECONDS", "30"))
EVENTS_DB_PATH = os.getenv("GATE_ANPR_EVENTS_DB", "/opt/gate_anpr/events.db")
EVENT_SOURCE = os.getenv("GATE_ANPR_EVENT_SOURCE", "alprd")


def _load_plate_allowlist():
    path = ALLOWLIST_PATH
    if not path and os.path.exists("/opt/gate_anpr/allowlist.json"):
        path = "/opt/gate_anpr/allowlist.json"
    if not path and os.path.exists("/etc/gate_anpr_allowlist.json"):
        path = "/etc/gate_anpr_allowlist.json"
    if path:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            mtime = os.path.getmtime(path)
        except FileNotFoundError:
            log.error("Allowlist file not found: %s", path)
            return [], None
        except json.JSONDecodeError as exc:
            log.error("Invalid allowlist JSON in %s: %s", path, exc)
            return [], None
    elif ALLOWLIST_JSON:
        try:
            data = json.loads(ALLOWLIST_JSON)
            mtime = None
        except json.JSONDecodeError as exc:
            log.error("Invalid PLATE_ALLOWLIST_JSON: %s", exc)
            return [], None
    else:
        return [], None
    allowlist = []
    for item in data:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            allowlist.append((str(item[0]).upper(), str(item[1])))
        elif isinstance(item, dict) and "owner" in item and "plates" in item:
            owner = str(item["owner"])
            plates = item.get("plates") or []
            if isinstance(plates, str):
                plates = [plates]
            for plate in plates:
                allowlist.append((str(plate).upper(), owner))
        elif isinstance(item, dict) and "plate" in item and "owner" in item:
            allowlist.append((str(item["plate"]).upper(), str(item["owner"])))
        else:
            log.warning("Skipping malformed allowlist entry: %r", item)
    return allowlist, mtime


list_of_plates, allowlist_mtime = _load_plate_allowlist()
if not list_of_plates:
    log.warning("No plates configured (PLATE_ALLOWLIST_PATH/PLATE_ALLOWLIST_JSON is empty)")

# NOTE: you had GPIO.BOARD with gatePin=23 in the original.
# That is internally inconsistent with the comment, but it "works" in your current setup.
# Keeping it unchanged to avoid breaking wiring assumptions.
gatePin = 23  # (legacy) used with GPIO.BOARD in original script
gatePin_bcm = 11  # BOARD 23 maps to BCM 11 on Raspberry Pi

PUSHOVER_ENABLED = bool(PUSHOVER_USER_KEY and PUSHOVER_APP_TOKEN)
if not PUSHOVER_ENABLED:
    log.warning("Pushover disabled (missing PUSHOVER_USER_KEY or PUSHOVER_APP_TOKEN)")


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


def _open_gate():
    log.debug("GPIO setup: mode=BOARD pin=%s", gatePin)
    try:
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(gatePin, GPIO.OUT)  # Gate pin set as output
        GPIO.output(gatePin, GPIO.HIGH)
        time.sleep(0.5)
        GPIO.output(gatePin, GPIO.LOW)
        GPIO.cleanup()
        return
    except RuntimeError as exc:
        # RPi.GPIO may not yet support Pi 5; fallback to lgpio (BCM numbering).
        log.warning("RPi.GPIO failed (%s). Falling back to lgpio BCM %s", exc, gatePin_bcm)

    try:
        import lgpio  # type: ignore
    except Exception as exc:
        log.error("lgpio not available; cannot toggle gate pin: %s", exc)
        raise

    handle = lgpio.gpiochip_open(0)
    try:
        lgpio.gpio_claim_output(handle, gatePin_bcm, 0)
        lgpio.gpio_write(handle, gatePin_bcm, 1)
        time.sleep(0.5)
        lgpio.gpio_write(handle, gatePin_bcm, 0)
    finally:
        lgpio.gpiochip_close(handle)


def _init_events_db():
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uuid TEXT,
                plate TEXT,
                owner TEXT,
                allowed INTEGER,
                confidence REAL,
                kind TEXT,
                image_name TEXT,
                captured_at TEXT,
                source TEXT,
                processing_time_ms INTEGER,
                observed_plate TEXT,
                observed_confidence REAL,
                fuzzy_distance INTEGER,
                fuzzy INTEGER,
                created_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_captured_at ON events(captured_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_plate ON events(plate)")
        conn.commit()
        _ensure_column(conn, "events", "source", "TEXT")
        _ensure_column(conn, "events", "processing_time_ms", "INTEGER")
        _ensure_column(conn, "events", "observed_plate", "TEXT")
        _ensure_column(conn, "events", "observed_confidence", "REAL")
        _ensure_column(conn, "events", "fuzzy_distance", "INTEGER")
        _ensure_column(conn, "events", "fuzzy", "INTEGER")
    finally:
        conn.close()


def _ensure_column(conn, table, column, column_type):
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
        conn.commit()


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
    created_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    conn = sqlite3.connect(EVENTS_DB_PATH, timeout=10)
    try:
        conn.execute(
            """
            INSERT INTO events (
                uuid, plate, owner, allowed, confidence, kind, image_name, captured_at,
                source, processing_time_ms, observed_plate, observed_confidence, fuzzy_distance, fuzzy,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid,
                plate,
                owner,
                int(allowed),
                confidence,
                kind,
                image_name,
                captured_at,
                EVENT_SOURCE,
                processing_time_ms,
                observed_plate,
                observed_confidence,
                fuzzy_distance,
                fuzzy,
                created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _maybe_reload_allowlist():
    global list_of_plates, allowlist_mtime
    if not ALLOWLIST_PATH:
        return
    try:
        mtime = os.path.getmtime(ALLOWLIST_PATH)
    except FileNotFoundError:
        return
    if allowlist_mtime is None or mtime != allowlist_mtime:
        list_of_plates, allowlist_mtime = _load_plate_allowlist()
        log.info("Reloaded plate allowlist (%s entries)", len(list_of_plates))


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
            body_str = _job_body_to_str(job.body)
            log.debug("Job %s body length=%s", getattr(job, "id", "?"), len(body_str))
            json_raw = json.loads(body_str)
            _maybe_reload_allowlist()

            # Touch early to reduce chance of TTR expiry during GPIO/pushover I/O
            client.touch(job)

            capture_epoch = json_raw["epoch_time"] / 1000
            min_time = capture_epoch + 10
            candidates = json_raw["results"][0]["candidates"]
            no_of_plates_seen = len(candidates)
            uuid = json_raw.get("uuid")
            image_name = f"{uuid}.jpg" if uuid else ""
            captured_at = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(capture_epoch)
            )
            processing_time_ms = json_raw.get("processing_time_ms")
            allowlist_map = {plate: owner for plate, owner in list_of_plates}

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
                matched_plate = None
                for cand in candidates:
                    number_plate = cand["plate"]
                    log.info("Candidate plate: %s", number_plate)

                    allowed = number_plate in allowlist_map
                    fuzzy_match = None
                    if not allowed:
                        conf = cand.get("confidence")
                        if conf is not None and conf >= FUZZY_MIN_CONFIDENCE:
                            fuzzy_match = _fuzzy_allowlist_match(number_plate, allowlist_map)
                            if fuzzy_match:
                                allowed = True

                    now = time.time()
                    match_plate = fuzzy_match[0] if fuzzy_match else number_plate
                    last_match = last_seen_allowed.get(match_plate, 0)
                    not_recently_seen = now > (last_match + MATCH_DEDUP_SECONDS)

                    if allowed and not_recently_seen:
                        last_seen_time = now
                        last_seen_reg = match_plate
                        last_seen_allowed[match_plate] = now
                        matched = True
                        matched_plate = match_plate
                        if fuzzy_match:
                            log.info(
                                "Fuzzy allowlist match: %s -> %s (dist=%s). Opening gate",
                                number_plate,
                                match_plate,
                                fuzzy_match[2],
                            )
                        else:
                            log.info("Plate %s recognised. Opening gate", number_plate)

                        _open_gate()

                        jpg_path = "/home/pi/plates/%s.jpg" % uuid if uuid else ""
                        log.debug("Sending pushover with image %s", jpg_path)
                        _record_event(
                            uuid=uuid,
                            plate=match_plate,
                            owner=allowlist_map.get(match_plate, ""),
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
                            try:
                                if os.path.exists(jpg_path):
                                    with open(jpg_path, "rb") as f:
                                        resp = requests.post(
                                            "https://api.pushover.net/1/messages.json",
                                            data={
                                                "token": PUSHOVER_APP_TOKEN,
                                                "user": PUSHOVER_USER_KEY,
                                                "message": "Pi5 - Opening Gate for %s" % number_plate,
                                            },
                                            files={
                                                "attachment": ("car-reg.jpg", f, "image/jpeg")
                                            },
                                            timeout=15,
                                        )
                                else:
                                    log.warning("Pushover image missing: %s", jpg_path)
                                    resp = requests.post(
                                        "https://api.pushover.net/1/messages.json",
                                        data={
                                            "token": PUSHOVER_APP_TOKEN,
                                            "user": PUSHOVER_USER_KEY,
                                            "message": "Pi5 - Opening Gate for %s" % number_plate,
                                        },
                                        timeout=15,
                                    )
                                if resp.status_code != 200:
                                    body = (resp.text or "").strip()
                                    if len(body) > 300:
                                        body = body[:300] + "…"
                                    log.warning(
                                        "Pushover failed: status=%s body=%s",
                                        resp.status_code,
                                        body,
                                    )
                                else:
                                    log.info("Pushover sent: status=200")
                            except Exception as exc:
                                log.warning("Pushover failed: %s", exc)
                        else:
                            log.debug("Pushover skipped (not configured)")
                        break
                    else:
                        if DEBUG:
                            log.debug(
                                "Plate %s allowed=%s recently_seen=%s",
                                number_plate,
                                allowed,
                                not not_recently_seen,
                            )
                            if allowed and not_recently_seen is False:
                                log.debug("Suppressing repeat match for %s", number_plate)
                if not matched:
                    top_plate = candidates[0]["plate"] if candidates else "UNKNOWN"
                    now = time.time()
                    last_unmatched = last_seen_unmatched.get(top_plate, 0)
                    if now > (last_unmatched + UNMATCHED_DEDUP_SECONDS):
                        last_seen_unmatched[top_plate] = now
                        _record_event(
                            uuid=uuid,
                            plate=top_plate,
                            owner=allowlist_map.get(top_plate, ""),
                            allowed=False,
                            confidence=candidates[0].get("confidence")
                            if candidates
                            else None,
                            kind="unmatched",
                            image_name=image_name,
                            captured_at=captured_at,
                            processing_time_ms=processing_time_ms,
                            observed_plate=top_plate,
                            observed_confidence=candidates[0].get("confidence")
                            if candidates
                            else None,
                            fuzzy_distance=None,
                            fuzzy=0,
                        )
                        log.info(
                            "Recorded event kind=unmatched plate=%s confidence=%s allowed=false",
                            top_plate,
                            candidates[0].get("confidence") if candidates else None,
                        )
            else:
                log.debug("Plate result too old; skipping (min_time=%s)", min_time)

            # Success: delete job so it doesn't come back
            client.delete(job)
            log.debug("Job %s processed and deleted", getattr(job, "id", "?"))

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
        _init_events_db()
        client = _new_client()

        log.info("Opening the gate for:")
        for plate, owner in list_of_plates:
            log.info(" - %s (%s)", plate, owner)
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
