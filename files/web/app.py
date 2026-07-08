#!/usr/bin/env python3
import json
import os
import re
import subprocess
from datetime import datetime, timedelta

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

app = Flask(__name__, static_folder=STATIC_DIR)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


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


@app.route("/api/events", methods=["GET"])
def get_events():
    limit = int(request.args.get("limit", "200"))
    offset = int(request.args.get("offset", "0"))
    kind = request.args.get("kind")
    window_key = request.args.get("window")
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
    return jsonify({"group_window_sec": MATCH_DEDUP_SECONDS})


@app.route("/api/stream-lag", methods=["GET"])
def stream_lag():
    stream_path = os.path.join(STATIC_DIR, "stream.jpg")
    now = datetime.utcnow().timestamp()
    if not os.path.exists(stream_path):
        return jsonify({"lag_ms": None, "frame_mtime": None})
    mtime = os.path.getmtime(stream_path)
    lag_ms = max(0, (now - mtime) * 1000)
    return jsonify({"lag_ms": int(lag_ms), "frame_mtime": mtime, "server_time": now})


def _ffmpeg_process_lines():
    try:
        result = subprocess.run(
            ["pgrep", "-fa", "ffmpeg"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2,
        )
    except Exception:
        return []
    if result.returncode not in (0, 1):
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


@app.route("/api/stream-health", methods=["GET"])
def stream_health():
    stream_path = os.path.join(STATIC_DIR, "stream.jpg")
    now = datetime.utcnow().timestamp()
    stale_seconds = int(os.getenv("GATE_WEB_STREAM_STALE_SECONDS", "10"))
    stream_mtime = os.path.getmtime(stream_path) if os.path.exists(stream_path) else None
    stream_age = now - stream_mtime if stream_mtime else None
    stream_stale = stream_age is None or stream_age > stale_seconds

    device10 = os.getenv("ALPRD_V4L2_DEVICE", "/dev/video10")
    device11 = os.getenv("ALPRD_V4L2_WEB_DEVICE", "/dev/video11")
    lines = _ffmpeg_process_lines()
    video10_writer = any(f"-f v4l2 {device10}" in line for line in lines)
    video11_writer = any(f"-f v4l2 {device11}" in line for line in lines)
    video11_reader = any(f"-i {device11}" in line for line in lines)
    video10_ok = os.path.exists(device10) and video10_writer
    video11_ok = os.path.exists(device11) and video11_writer and video11_reader

    return jsonify(
        {
            "now": now,
            "stream_mtime": stream_mtime,
            "stream_age_s": stream_age,
            "stream_stale": stream_stale,
            "stale_threshold_s": stale_seconds,
            "video10": {"device": device10, "ok": video10_ok, "writer": video10_writer},
            "video11": {
                "device": device11,
                "ok": video11_ok,
                "writer": video11_writer,
                "reader": video11_reader,
            },
            "ok": (not stream_stale) and video10_ok and video11_ok,
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
        "rtsp_v4l2": _systemctl_is_active("gate_anpr_rtsp_v4l2"),
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


@app.route("/images/<path:name>")
def get_image(name):
    return send_from_directory(PLATES_DIR, name)


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/admin")
def admin():
    return send_from_directory(STATIC_DIR, "admin.html")


@app.route("/stats")
def stats():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/<path:path>")
def static_proxy(path):
    return send_from_directory(STATIC_DIR, path)


if __name__ == "__main__":
    port = int(os.getenv("GATE_WEB_PORT", "80"))
    app.run(host="0.0.0.0", port=port)
