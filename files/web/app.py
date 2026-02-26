#!/usr/bin/env python3
import json
import logging
import math
import os
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, send_from_directory
import time
import sqlite3
import fcntl

import RPi.GPIO as GPIO

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")

ALLOWLIST_PATH = os.getenv("PLATE_ALLOWLIST_PATH", "/opt/gate_anpr/allowlist.json")
LOG_PATH = "/var/log/gate-anpr/gate-anpr.log"
PLATES_DIR = "/home/pi/plates"
GATE_PIN_BOARD = 23
GATE_PIN_BCM = 11
EVENTS_DB_PATH = os.getenv("GATE_ANPR_EVENTS_DB", "/opt/gate_anpr/events.db")
GATE_COOLDOWN_SECONDS = int(os.getenv("GATE_WEB_COOLDOWN", "30"))
MATCH_DEDUP_SECONDS = int(os.getenv("MATCH_DEDUP_SECONDS", "60"))
GATE_COOLDOWN_PATH = os.getenv(
    "GATE_WEB_COOLDOWN_PATH", "/opt/gate_anpr/open_gate_last.txt"
)
UI_SETTINGS_PATH = "/opt/gate_anpr/ui_settings.json"

app = Flask(__name__, static_folder=STATIC_DIR)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


class _StreamLogFilter(logging.Filter):
    def filter(self, record):
        return "/static/stream.jpg" not in record.getMessage()


logging.getLogger("werkzeug").addFilter(_StreamLogFilter())


def _read_ui_settings():
    if not os.path.exists(UI_SETTINGS_PATH):
        return {}
    try:
        with open(UI_SETTINGS_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def _write_ui_settings(data):
    tmp_path = UI_SETTINGS_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(tmp_path, UI_SETTINGS_PATH)


def _get_timezone_name():
    try:
        result = subprocess.run(
            ["timedatectl", "show", "-p", "Timezone", "--value"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    try:
        with open("/etc/timezone", "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except Exception:
        return "UTC"


def _timezone_location(timezone_name):
    locations = {
        "Europe/London": (51.5074, -0.1278),
        "Europe/Dublin": (53.3498, -6.2603),
        "Europe/Paris": (48.8566, 2.3522),
        "Europe/Berlin": (52.5200, 13.4050),
        "America/New_York": (40.7128, -74.0060),
        "America/Chicago": (41.8781, -87.6298),
        "America/Denver": (39.7392, -104.9903),
        "America/Los_Angeles": (34.0522, -118.2437),
        "Asia/Singapore": (1.3521, 103.8198),
        "Asia/Tokyo": (35.6762, 139.6503),
        "Australia/Sydney": (-33.8688, 151.2093),
    }
    return locations.get(timezone_name, locations["Europe/London"])


def _sunrise_sunset_utc(day, latitude, longitude):
    def _calc(is_sunrise):
        lng_hour = longitude / 15.0
        t = day.timetuple().tm_yday + ((6 - lng_hour) / 24 if is_sunrise else (18 - lng_hour) / 24)
        m = (0.9856 * t) - 3.289
        l = m + (1.916 * math.sin(math.radians(m))) + (0.020 * math.sin(math.radians(2 * m))) + 282.634
        l = (l + 360) % 360
        ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(l))))
        ra = (ra + 360) % 360
        l_quadrant = (math.floor(l / 90)) * 90
        ra_quadrant = (math.floor(ra / 90)) * 90
        ra = (ra + (l_quadrant - ra_quadrant)) / 15
        sin_dec = 0.39782 * math.sin(math.radians(l))
        cos_dec = math.cos(math.asin(sin_dec))
        cos_h = (
            (math.cos(math.radians(90.833)) - (sin_dec * math.sin(math.radians(latitude))))
            / (cos_dec * math.cos(math.radians(latitude)))
        )
        if cos_h > 1 or cos_h < -1:
            return None
        h = (360 - math.degrees(math.acos(cos_h))) if is_sunrise else math.degrees(math.acos(cos_h))
        h /= 15
        t_local = h + ra - (0.06571 * t) - 6.622
        ut = (t_local - lng_hour) % 24
        hours = int(ut)
        minutes = int((ut - hours) * 60)
        seconds = int((((ut - hours) * 60) - minutes) * 60)
        return datetime(day.year, day.month, day.day, hours, minutes, seconds, tzinfo=timezone.utc)

    return _calc(True), _calc(False)


def _sun_times_payload():
    tz_name = _get_timezone_name()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz_name = "UTC"
        tz = ZoneInfo(tz_name)
    lat, lon = _timezone_location(tz_name)
    today = datetime.now(tz).date()
    tomorrow = today + timedelta(days=1)
    sunrise_today, sunset_today = _sunrise_sunset_utc(today, lat, lon)
    sunrise_tomorrow, sunset_tomorrow = _sunrise_sunset_utc(tomorrow, lat, lon)
    payload = {
        "timezone": tz_name,
        "lat": lat,
        "lon": lon,
        "sunrise_ts": int(sunrise_today.timestamp()) if sunrise_today else None,
        "sunset_ts": int(sunset_today.timestamp()) if sunset_today else None,
        "sunrise_next_ts": int(sunrise_tomorrow.timestamp()) if sunrise_tomorrow else None,
        "sunset_next_ts": int(sunset_tomorrow.timestamp()) if sunset_tomorrow else None,
    }
    return payload

LOG_SERVICE_MAP = {
    "alprd": "alprd",
    "gate_anpr": "gate_anpr",
    "stream_jpeg": "gate_anpr_stream_jpeg",
    "web": "gate_anpr_web",
    "stream_watchdog": "gate_anpr_stream_watchdog",
    "stream_watchdog_timer": "gate_anpr_stream_watchdog.timer",
    "beanstalkd": "beanstalkd",
}

LOG_RANGE_MAP = {
    "15m": "15 minutes ago",
    "1h": "1 hour ago",
    "6h": "6 hours ago",
    "24h": "24 hours ago",
    "7d": "7 days ago",
    "30d": "30 days ago",
}


@app.after_request
def add_no_cache_headers(response):
    if request.path.startswith("/images/"):
        return response
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


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


def _query_events(kind=None, offset=0, limit=60, since=None):
    conn = sqlite3.connect(EVENTS_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        clauses = []
        params = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if since:
            clauses.append("captured_at >= ?")
            params.append(since.strftime("%Y-%m-%d %H:%M:%S"))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""
            SELECT * FROM events
            {where}
            ORDER BY captured_at DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
        results = []
        for row in rows:
            image_name = row["image_name"] or ""
            results.append(
                {
                    "id": row["id"],
                    "uuid": row["uuid"],
                    "plate": row["plate"],
                    "owner": row["owner"],
                    "allowed": bool(row["allowed"]),
                    "confidence": row["confidence"],
                    "kind": row["kind"],
                    "captured_at": row["captured_at"],
                    "source": row["source"],
                    "processing_time_ms": row["processing_time_ms"],
                    "observed_plate": row["observed_plate"],
                    "observed_confidence": row["observed_confidence"],
                    "fuzzy_distance": row["fuzzy_distance"],
                    "fuzzy": bool(row["fuzzy"]) if row["fuzzy"] is not None else False,
                    "image_name": image_name,
                    "image_url": f"/images/{image_name}" if image_name else "",
                }
            )
        return results
    finally:
        conn.close()


_init_events_db()


def _read_allowlist():
    if not os.path.exists(ALLOWLIST_PATH):
        return []
    with open(ALLOWLIST_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_allowlist(data):
    tmp_path = ALLOWLIST_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(tmp_path, ALLOWLIST_PATH)


def _tail_lines(path, max_lines=500):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        return handle.readlines()[-max_lines:]


def _journalctl_lines(service, since, max_lines=500):
    try:
        result = subprocess.run(
            [
                "journalctl",
                "-u",
                service,
                "--since",
                since,
                "-n",
                str(max_lines),
                "--no-pager",
                "--output",
                "short-iso",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return [], "journalctl failed"

    if result.returncode != 0:
        return [], result.stderr.strip() or "journalctl failed"

    return result.stdout.splitlines(), ""


def _journalctl_system_lines(since, max_lines=500):
    try:
        result = subprocess.run(
            [
                "journalctl",
                "--since",
                since,
                "-p",
                "warning..emerg",
                "-n",
                str(max_lines),
                "--no-pager",
                "--output",
                "short-iso",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return [], "journalctl failed"

    if result.returncode != 0:
        return [], result.stderr.strip() or "journalctl failed"

    return result.stdout.splitlines(), ""


def _parse_events(lines, allowlist_set):
    events = []
    candidate_re = re.compile(r"^(?P<ts>\\S+ \\S+) INFO Candidate plate: (?P<plate>\\S+)")
    recognised_re = re.compile(
        r"^(?P<ts>\\S+ \\S+) INFO Plate (?P<plate>\\S+) recognised"
    )
    for line in lines:
        line = line.strip()
        m = recognised_re.match(line)
        if m:
            plate = m.group("plate")
            events.append(
                {
                    "timestamp": m.group("ts"),
                    "plate": plate,
                    "type": "recognised",
                    "allowed": plate in allowlist_set,
                }
            )
            continue
        m = candidate_re.match(line)
        if m:
            plate = m.group("plate")
            events.append(
                {
                    "timestamp": m.group("ts"),
                    "plate": plate,
                    "type": "candidate",
                    "allowed": plate in allowlist_set,
                }
            )
    return events


def _open_gate():
    try:
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(GATE_PIN_BOARD, GPIO.OUT)
        GPIO.output(GATE_PIN_BOARD, GPIO.HIGH)
        time.sleep(0.5)
        GPIO.output(GATE_PIN_BOARD, GPIO.LOW)
        GPIO.cleanup()
        return
    except RuntimeError:
        pass

    try:
        import lgpio  # type: ignore
    except Exception:
        raise

    handle = lgpio.gpiochip_open(0)
    try:
        lgpio.gpio_claim_output(handle, GATE_PIN_BCM, 0)
        lgpio.gpio_write(handle, GATE_PIN_BCM, 1)
        time.sleep(0.5)
        lgpio.gpio_write(handle, GATE_PIN_BCM, 0)
    finally:
        lgpio.gpiochip_close(handle)


def _read_last_open_time():
    try:
        with open(GATE_COOLDOWN_PATH, "r", encoding="utf-8") as handle:
            return float(handle.read().strip())
    except (FileNotFoundError, ValueError):
        return 0.0


def _write_last_open_time(ts):
    with open(GATE_COOLDOWN_PATH, "w", encoding="utf-8") as handle:
        handle.write(f"{ts:.3f}\n")


def _gate_cooldown_remaining():
    last_ts = _read_last_open_time()
    if last_ts <= 0:
        return 0
    elapsed = time.time() - last_ts
    remaining = int(max(0, GATE_COOLDOWN_SECONDS - elapsed))
    return remaining


def _gate_cooldown_remaining_locked():
    with open(GATE_COOLDOWN_PATH, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        try:
            last_ts = float(handle.read().strip() or "0")
        except ValueError:
            last_ts = 0
        fcntl.flock(handle, fcntl.LOCK_UN)
    if last_ts <= 0:
        return 0
    elapsed = time.time() - last_ts
    remaining = int(max(0, GATE_COOLDOWN_SECONDS - elapsed))
    return remaining


def _gate_check_and_mark():
    with open(GATE_COOLDOWN_PATH, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        try:
            last_ts = float(handle.read().strip() or "0")
        except ValueError:
            last_ts = 0
        now = time.time()
        remaining = max(0, int(GATE_COOLDOWN_SECONDS - (now - last_ts)))
        if remaining == 0:
            handle.seek(0)
            handle.truncate()
            handle.write(f"{now:.3f}\n")
            handle.flush()
        fcntl.flock(handle, fcntl.LOCK_UN)
    return remaining


@app.route("/api/plates", methods=["GET"])
def get_plates():
    raw = _read_allowlist()
    grouped = []
    if not raw:
        return jsonify([])
    if isinstance(raw, list) and raw and isinstance(raw[0], (list, tuple)):
        owners = {}
        for plate, owner in raw:
            owners.setdefault(owner, []).append(plate)
        for owner, plates in owners.items():
            grouped.append({"owner": owner, "plates": sorted(set(plates))})
    elif isinstance(raw, list) and raw and isinstance(raw[0], dict) and "plates" in raw[0]:
        grouped = raw
    else:
        grouped = raw
    return jsonify(grouped)


@app.route("/api/plates", methods=["PUT"])
def put_plates():
    data = request.get_json(force=True)
    if not isinstance(data, list):
        return jsonify({"error": "expected list"}), 400
    cleaned = []
    for item in data:
        if isinstance(item, dict) and "owner" in item and "plates" in item:
            owner = str(item["owner"]).strip()
            plates = item.get("plates") or []
            if isinstance(plates, str):
                plates = [plates]
            norm = [str(p).upper().strip() for p in plates if str(p).strip()]
            if not owner:
                return jsonify({"error": "owner required"}), 400
            if not norm:
                return jsonify({"error": f"no plates for owner {owner}"}), 400
            cleaned.append({"owner": owner, "plates": sorted(set(norm))})
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            plate = str(item[0]).upper().strip()
            owner = str(item[1]).strip()
            cleaned.append({"owner": owner, "plates": [plate]})
        elif isinstance(item, dict) and "plate" in item and "owner" in item:
            plate = str(item["plate"]).upper().strip()
            owner = str(item["owner"]).strip()
            cleaned.append({"owner": owner, "plates": [plate]})
        else:
            return jsonify({"error": f"invalid entry: {item!r}"}), 400
    _write_allowlist(cleaned)
    return jsonify({"ok": True, "count": len(cleaned)})


@app.route("/api/allowlist-status", methods=["GET"])
def get_allowlist_status():
    allowlist = _read_allowlist()
    grouped = []
    if isinstance(allowlist, list) and allowlist and isinstance(allowlist[0], (list, tuple)):
        owners = {}
        for plate, owner in allowlist:
            owners.setdefault(owner, []).append(plate)
        for owner, plates in owners.items():
            grouped.append({"owner": owner, "plates": sorted(set(plates))})
    elif isinstance(allowlist, list) and allowlist and isinstance(allowlist[0], dict):
        grouped = allowlist
    else:
        grouped = []
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT plate, MAX(captured_at) as last_seen, MAX(confidence) as confidence
            FROM events
            WHERE kind = 'recognised'
            GROUP BY plate
            """
        ).fetchall()
        last_seen_map = {row[0]: row[1] for row in rows}
        confidence_map = {row[0]: row[2] for row in rows}
    finally:
        conn.close()
    response = []
    for entry in grouped:
        owner = entry.get("owner", "")
        plates = entry.get("plates") or []
        if isinstance(plates, str):
            plates = [plates]
        plate_meta = []
        for plate in plates:
            plate_meta.append(
                {
                    "plate": plate,
                    "last_seen": last_seen_map.get(plate),
                    "confidence": confidence_map.get(plate),
                }
            )
        response.append({"owner": owner, "plates": plate_meta})
    return jsonify(response)


@app.route("/api/open-gate", methods=["POST"])
def open_gate():
    remaining = _gate_check_and_mark()
    if remaining > 0:
        return jsonify({"ok": False, "retry_in": remaining}), 429
    _open_gate()
    return jsonify({"ok": True})


@app.route("/api/gate-cooldown", methods=["GET"])
def get_gate_cooldown():
    return jsonify({"remaining": _gate_cooldown_remaining_locked()})


@app.route("/api/gate-last-open", methods=["GET"])
def get_gate_last_open():
    last_ts = _read_last_open_time()
    if last_ts <= 0:
        return jsonify({"last_open_ts": None, "last_open_iso": None})
    last_iso = datetime.fromtimestamp(last_ts).isoformat()
    return jsonify({"last_open_ts": last_ts, "last_open_iso": last_iso})


@app.route("/api/events", methods=["GET"])
def get_events():
    limit = int(request.args.get("limit", "200"))
    offset = int(request.args.get("offset", "0"))
    kind = request.args.get("kind")
    window_key = request.args.get("window")
    if not window_key:
        window_key = "30d"
    window = _window_bounds(window_key) if window_key else None
    events = _query_events(kind=kind, offset=offset, limit=limit, since=window)
    return jsonify(events)


def _window_bounds(window):
    now = datetime.utcnow()
    if window == "24h":
        return now - timedelta(hours=24)
    if window == "7d":
        return now - timedelta(days=7)
    if window == "30d":
        return now - timedelta(days=30)
    if window == "all":
        return None
    return None


def _stats_counts(window):
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        if window:
            return conn.execute(
                """
                SELECT kind, COUNT(*) FROM events
                WHERE captured_at >= ?
                GROUP BY kind
                """,
                (window.strftime("%Y-%m-%d %H:%M:%S"),),
            ).fetchall()
        return conn.execute(
            """
            SELECT kind, COUNT(*) FROM events
            GROUP BY kind
            """
        ).fetchall()
    finally:
        conn.close()


def _stats_timeseries(window):
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        if window and window >= datetime.utcnow() - timedelta(days=2):
            rows = conn.execute(
                """
                SELECT strftime('%Y-%m-%d %H:00', captured_at) as bucket, COUNT(*)
                FROM events
                WHERE captured_at >= ?
                GROUP BY bucket
                ORDER BY bucket ASC
                """,
                (window.strftime("%Y-%m-%d %H:%M:%S"),),
            ).fetchall()
            return {"bucket": "hour", "series": [{"t": r[0], "v": r[1]} for r in rows]}
        if window:
            rows = conn.execute(
                """
                SELECT substr(captured_at, 1, 10) as bucket, COUNT(*)
                FROM events
                WHERE captured_at >= ?
                GROUP BY bucket
                ORDER BY bucket ASC
                """,
                (window.strftime("%Y-%m-%d %H:%M:%S"),),
            ).fetchall()
            return {"bucket": "day", "series": [{"t": r[0], "v": r[1]} for r in rows]}
        rows = conn.execute(
            """
            SELECT substr(captured_at, 1, 10) as bucket, COUNT(*)
            FROM events
            GROUP BY bucket
            ORDER BY bucket ASC
            """
        ).fetchall()
        return {"bucket": "day", "series": [{"t": r[0], "v": r[1]} for r in rows]}
    finally:
        conn.close()


def _stats_top_plate(window, kind):
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        where = ["1=1"]
        params = []
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if window:
            where.append("captured_at >= ?")
            params.append(window.strftime("%Y-%m-%d %H:%M:%S"))
        query = f"""
            SELECT COALESCE(NULLIF(plate, ''), 'UNKNOWN') as plate, COUNT(*) as count
            FROM events
            WHERE {' AND '.join(where)}
            GROUP BY plate
            ORDER BY count DESC
            LIMIT 1
        """
        row = conn.execute(query, params).fetchone()
        if not row:
            return {"plate": None, "count": 0}
        return {"plate": row[0], "count": row[1]}
    finally:
        conn.close()


def _stats_top_plate_multi(window, kinds):
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        where = ["kind IN ({})".format(",".join(["?"] * len(kinds)))]
        params = list(kinds)
        if window:
            where.append("captured_at >= ?")
            params.append(window.strftime("%Y-%m-%d %H:%M:%S"))
        query = f"""
            SELECT COALESCE(NULLIF(plate, ''), 'UNKNOWN') as plate, COUNT(*) as count
            FROM events
            WHERE {' AND '.join(where)}
            GROUP BY plate
            ORDER BY count DESC
            LIMIT 1
        """
        row = conn.execute(query, params).fetchone()
        if not row:
            return {"plate": None, "count": 0}
        return {"plate": row[0], "count": row[1]}
    finally:
        conn.close()


def _stats_top_list(window, kinds, limit=12):
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        where = ["kind IN ({})".format(",".join(["?"] * len(kinds)))]
        params = list(kinds)
        if window:
            where.append("captured_at >= ?")
            params.append(window.strftime("%Y-%m-%d %H:%M:%S"))
        query = f"""
            SELECT COALESCE(NULLIF(plate, ''), 'UNKNOWN') as plate, COUNT(*) as count
            FROM events
            WHERE {' AND '.join(where)}
            GROUP BY plate
            ORDER BY count DESC
            LIMIT ?
        """
        rows = conn.execute(query, (*params, limit)).fetchall()
        return [{"plate": row[0], "count": row[1]} for row in rows]
    finally:
        conn.close()
def _stats_busiest_bucket(window):
    now = datetime.utcnow()
    bucket = "day"
    if window and window >= now - timedelta(days=2):
        bucket = "hour"
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        if bucket == "hour":
            rows = conn.execute(
                """
                SELECT strftime('%Y-%m-%d %H:00', captured_at) as bucket, COUNT(*)
                FROM events
                WHERE captured_at >= ?
                GROUP BY bucket
                ORDER BY COUNT(*) DESC
                LIMIT 1
                """,
                (window.strftime("%Y-%m-%d %H:%M:%S"),),
            ).fetchone()
        elif window:
            rows = conn.execute(
                """
                SELECT substr(captured_at, 1, 10) as bucket, COUNT(*)
                FROM events
                WHERE captured_at >= ?
                GROUP BY bucket
                ORDER BY COUNT(*) DESC
                LIMIT 1
                """,
                (window.strftime("%Y-%m-%d %H:%M:%S"),),
            ).fetchone()
        else:
            rows = conn.execute(
                """
                SELECT substr(captured_at, 1, 10) as bucket, COUNT(*)
                FROM events
                GROUP BY bucket
                ORDER BY COUNT(*) DESC
                LIMIT 1
                """
            ).fetchone()
        if not rows:
            return {"bucket": None, "count": 0, "bucket_type": bucket}
        return {"bucket": rows[0], "count": rows[1], "bucket_type": bucket}
    finally:
        conn.close()


def _stats_processing_ms(window, kind=None):
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        where = ["processing_time_ms IS NOT NULL", "processing_time_ms >= 0"]
        params = []
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if window:
            where.append("captured_at >= ?")
            params.append(window.strftime("%Y-%m-%d %H:%M:%S"))
        query = f"""
            SELECT AVG(processing_time_ms)
            FROM events
            WHERE {' AND '.join(where)}
        """
        row = conn.execute(query, params).fetchone()
        return row[0] if row and row[0] is not None else None
    finally:
        conn.close()


def _stats_no_plate(window):
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        where = ["(plate IS NULL OR plate = '')"]
        params = []
        if window:
            where.append("captured_at >= ?")
            params.append(window.strftime("%Y-%m-%d %H:%M:%S"))
        query = f"""
            SELECT COUNT(*)
            FROM events
            WHERE {' AND '.join(where)}
        """
        row = conn.execute(query, params).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


@app.route("/api/stats", methods=["GET"])
def get_stats():
    window_key = request.args.get("window", "24h")
    if window_key not in {"24h", "7d", "30d", "all"}:
        return jsonify({"error": "invalid window"}), 400
    window = _window_bounds(window_key)
    counts = _stats_counts(window)
    summary = {kind: count for kind, count in counts}
    total = sum(summary.values())
    timeseries = _stats_timeseries(window)
    return jsonify(
        {
            "window": window_key,
            "total": total,
            "counts": summary,
            "timeseries": timeseries,
        }
    )


@app.route("/api/stats/insights", methods=["GET"])
def get_stats_insights():
    window_key = request.args.get("window", "24h")
    if window_key not in {"24h", "7d", "30d", "all"}:
        return jsonify({"error": "invalid window"}), 400
    window = _window_bounds(window_key)
    insights = {
        "top_recognised": _stats_top_plate(window, "recognised"),
        "top_unmatched": _stats_top_plate_multi(window, ["unmatched", "candidate"]),
        "top_recognised_list": _stats_top_list(window, ["recognised"]),
        "top_unmatched_list": _stats_top_list(window, ["unmatched", "candidate"]),
        "busiest_bucket": _stats_busiest_bucket(window),
        "avg_processing_ms": {
            "overall": _stats_processing_ms(window),
            "recognised": _stats_processing_ms(window, "recognised"),
            "candidate": _stats_processing_ms(window, "candidate"),
            "unmatched": _stats_processing_ms(window, "unmatched"),
        },
        "no_plate": _stats_no_plate(window),
    }
    return jsonify({"window": window_key, "insights": insights})


@app.route("/api/images", methods=["GET"])
def list_images():
    limit = int(request.args.get("limit", "60"))
    offset = int(request.args.get("offset", "0"))
    if not os.path.exists(PLATES_DIR):
        return jsonify([])
    entries = []
    for name in os.listdir(PLATES_DIR):
        if not name.lower().endswith(".jpg"):
            continue
        path = os.path.join(PLATES_DIR, name)
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            continue
        entries.append(
            {
                "name": name,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }
        )
    entries.sort(key=lambda x: x["mtime"], reverse=True)
    return jsonify(entries[offset : offset + limit])


@app.route("/api/stream", methods=["GET"])
def stream_info():
    stream_url = os.getenv("GATE_WEB_STREAM_URL", "").strip()
    return jsonify({"url": stream_url})


@app.route("/api/config", methods=["GET"])
def app_config():
    stream_fps = os.getenv("GATE_WEB_STREAM_FPS")
    try:
        stream_fps_val = float(stream_fps) if stream_fps is not None else None
    except (TypeError, ValueError):
        stream_fps_val = None
    return jsonify(
        {
            "group_window_sec": MATCH_DEDUP_SECONDS,
            "stream_fps": stream_fps_val,
        }
    )


@app.route("/api/ui-settings", methods=["GET", "PUT"])
def ui_settings():
    if request.method == "PUT":
        payload = request.get_json(silent=True) or {}
        mode = payload.get("theme_mode")
        if mode not in ("light", "dark", "auto"):
            return jsonify({"error": "Invalid theme_mode"}), 400
        settings = _read_ui_settings()
        settings["theme_mode"] = mode
        _write_ui_settings(settings)
        return jsonify({"ok": True, "theme_mode": mode})

    settings = _read_ui_settings()
    theme_mode = settings.get("theme_mode", "light")
    sun_payload = _sun_times_payload()
    return jsonify({"theme_mode": theme_mode, **sun_payload})


@app.route("/api/stream-lag", methods=["GET"])
def stream_lag():
    stream_path = os.path.join(STATIC_DIR, "stream.jpg")
    now = datetime.utcnow().timestamp()
    if not os.path.exists(stream_path):
        return jsonify({"lag_ms": None, "frame_mtime": None})
    mtime = os.path.getmtime(stream_path)
    lag_ms = max(0, (now - mtime) * 1000)
    return jsonify({"lag_ms": int(lag_ms), "frame_mtime": mtime, "server_time": now})


@app.route("/api/stream-health", methods=["GET"])
def stream_health():
    stream_path = os.path.join(STATIC_DIR, "stream.jpg")
    now = datetime.utcnow().timestamp()
    stale_seconds = int(os.getenv("GATE_WEB_STREAM_STALE_SECONDS", "10"))
    stream_mtime = os.path.getmtime(stream_path) if os.path.exists(stream_path) else None
    stream_age = now - stream_mtime if stream_mtime else None
    stream_stale = stream_age is None or stream_age > stale_seconds

    return jsonify(
        {
            "now": now,
            "stream_mtime": stream_mtime,
            "stream_age_s": stream_age,
            "stream_stale": stream_stale,
            "stale_threshold_s": stale_seconds,
            "ok": not stream_stale,
        }
    )


def _systemctl_is_active(service):
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2,
        )
    except Exception:
        return "unknown"
    if result.returncode == 0:
        return result.stdout.strip() or "active"
    if result.returncode == 3:
        return result.stdout.strip() or "inactive"
    return result.stdout.strip() or "unknown"


def _latest_event_timestamp():
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        row = conn.execute(
            "SELECT captured_at FROM events ORDER BY captured_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    try:
        return datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


@app.route("/api/service-health", methods=["GET"])
def service_health():
    services = {
        "alprd": _systemctl_is_active("alprd"),
        "gate_anpr": _systemctl_is_active("gate_anpr"),
        "stream_jpeg": _systemctl_is_active("gate_anpr_stream_jpeg"),
    }
    last_event = _latest_event_timestamp()
    now = datetime.now()
    last_event_age = (now - last_event).total_seconds() if last_event else None
    return jsonify(
        {
            "services": services,
            "last_event_time": last_event.isoformat() if last_event else None,
            "last_event_age_s": last_event_age,
            "now": now.isoformat(),
        }
    )


@app.route("/api/logs", methods=["GET"])
def logs():
    service_key = request.args.get("service", "gate_anpr")
    range_key = request.args.get("range", "1h")
    max_lines = int(request.args.get("lines", "300"))
    max_lines = min(max(max_lines, 50), 1000)

    since = LOG_RANGE_MAP.get(range_key, LOG_RANGE_MAP["1h"])

    if service_key == "app_log":
        lines = [line.rstrip("\n") for line in _tail_lines(LOG_PATH, max_lines)]
        return jsonify(
            {
                "service": service_key,
                "range": range_key,
                "lines": lines,
                "error": "",
            }
        )

    if service_key == "system":
        lines, error = _journalctl_system_lines(since, max_lines)
        return jsonify(
            {
                "service": service_key,
                "range": range_key,
                "lines": lines,
                "error": error,
            }
        )

    service_name = LOG_SERVICE_MAP.get(service_key)
    if not service_name:
        return jsonify(
            {
                "service": service_key,
                "range": range_key,
                "lines": [],
                "error": "Unknown service",
            }
        )

    lines, error = _journalctl_lines(service_name, since, max_lines)
    return jsonify(
        {
            "service": service_key,
            "range": range_key,
            "lines": lines,
            "error": error,
        }
    )


@app.route("/images/<path:name>")
def get_image(name):
    return send_from_directory(PLATES_DIR, name)


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/admin")
def admin():
    return send_from_directory(STATIC_DIR, "admin.html")

@app.route("/fullscreen")
def fullscreen():
    return send_from_directory(STATIC_DIR, "fullscreen.html")


@app.route("/stats")
def stats():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/<path:path>")
def static_proxy(path):
    return send_from_directory(STATIC_DIR, path)


if __name__ == "__main__":
    port = int(os.getenv("GATE_WEB_PORT", "80"))
    app.run(host="0.0.0.0", port=port)
