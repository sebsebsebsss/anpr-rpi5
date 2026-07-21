#!/usr/bin/env python3
import fcntl
import hmac
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, send_from_directory

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
ROOT_DIR = os.path.dirname(APP_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from allowlist_util import normalise_plate  # noqa: E402
from gate_runtime import (  # noqa: E402
    configure_logging,
    env_int,
    init_events_db,
    insert_event,
    now_local_str,
    parse_local_timestamp,
    sqlite_healthcheck,
)
from gate_runtime import open_gate as trigger_gate  # noqa: E402

ALLOWLIST_PATH = os.getenv("PLATE_ALLOWLIST_PATH", "/opt/gate_anpr/allowlist.json")
LOG_PATH = "/var/log/gate-anpr/gate_anpr_web.log"
PLATES_DIR = "/home/pi/plates"
GATE_PIN_BOARD = env_int("GATE_PIN_BOARD", 23, "gate_anpr_web")
GATE_PIN_BCM = env_int("GATE_PIN_BCM", 11, "gate_anpr_web")
EVENTS_DB_PATH = os.getenv("GATE_ANPR_EVENTS_DB", "/opt/gate_anpr/events.db")
GATE_COOLDOWN_SECONDS = env_int("GATE_WEB_COOLDOWN", 30, "gate_anpr_web")
GATE_MANUAL_OPEN_MAX_AGE_SECONDS = env_int("GATE_WEB_MANUAL_OPEN_MAX_AGE_SECONDS", 15, "gate_anpr_web")
GATE_MANUAL_OPEN_FUTURE_SKEW_SECONDS = env_int("GATE_WEB_MANUAL_OPEN_FUTURE_SKEW_SECONDS", 10, "gate_anpr_web")
MATCH_DEDUP_SECONDS = env_int("MATCH_DEDUP_SECONDS", 60, "gate_anpr_web")
GATE_COOLDOWN_PATH = os.getenv("GATE_WEB_COOLDOWN_PATH", "/opt/gate_anpr/open_gate_last.txt")
UI_SETTINGS_PATH = "/opt/gate_anpr/ui_settings.json"
API_SHARED_SECRET = os.getenv("GATE_API_SHARED_SECRET", "").strip()
if not API_SHARED_SECRET:
    raise RuntimeError(
        "GATE_API_SHARED_SECRET must be set to a non-empty value. "
        "The deploy playbook auto-generates one into /etc/gate_anpr.env; "
        "re-run the deploy, or add it manually (openssl rand -hex 32)."
    )

# Optional extra origins accepted on top of the same-origin check (see _check_csrf).
_raw_origins = os.getenv("GATE_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = {o.strip().rstrip("/") for o in _raw_origins.split(",") if o.strip()}

MAINTENANCE_LOG_PATH = "/var/log/gate-anpr/gate-maintenance.log"
log = configure_logging("gate_anpr_web", log_path=LOG_PATH)

app = Flask(__name__, static_folder=STATIC_DIR)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


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


def _render_static_html(filename):
    path = os.path.join(STATIC_DIR, filename)
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    content = content.replace("__GATE_API_SHARED_SECRET__", escape(API_SHARED_SECRET, quote=True))
    return app.response_class(content, mimetype="text/html")


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
    from tz_centroids import TZ_CENTROIDS

    return TZ_CENTROIDS.get(timezone_name)


def _sunrise_sunset_utc(day, latitude, longitude):
    def _calc(is_sunrise):
        lng_hour = longitude / 15.0
        t = day.timetuple().tm_yday + ((6 - lng_hour) / 24 if is_sunrise else (18 - lng_hour) / 24)
        m = (0.9856 * t) - 3.289
        elon = m + (1.916 * math.sin(math.radians(m))) + (0.020 * math.sin(math.radians(2 * m))) + 282.634
        elon = (elon + 360) % 360
        ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(elon))))
        ra = (ra + 360) % 360
        l_quadrant = (math.floor(elon / 90)) * 90
        ra_quadrant = (math.floor(ra / 90)) * 90
        ra = (ra + (l_quadrant - ra_quadrant)) / 15
        sin_dec = 0.39782 * math.sin(math.radians(elon))
        cos_dec = math.cos(math.asin(sin_dec))
        cos_h = (math.cos(math.radians(90.833)) - (sin_dec * math.sin(math.radians(latitude)))) / (
            cos_dec * math.cos(math.radians(latitude))
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


def _resolve_lat_lon():
    """Three-tier lat/lon resolution: env vars → timezone centroid → None."""
    env_lat = os.getenv("GATE_UI_LAT", "").strip()
    env_lon = os.getenv("GATE_UI_LON", "").strip()
    if env_lat and env_lon:
        try:
            return float(env_lat), float(env_lon), "env"
        except ValueError:
            log.warning("Invalid GATE_UI_LAT/LON values: %r %r; falling back to tz centroid", env_lat, env_lon)

    tz_name = _get_timezone_name()
    centroid = _timezone_location(tz_name)
    if centroid is not None:
        return centroid[0], centroid[1], "tz_centroid"

    log.info("No lat/lon available (env unset, timezone %r not in centroid dict)", tz_name)
    return None, None, None


def _sun_times_payload():
    tz_name = _get_timezone_name()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz_name = "UTC"
        tz = ZoneInfo(tz_name)
    lat, lon, lat_source = _resolve_lat_lon()
    if lat is None or lon is None:
        return {
            "timezone": tz_name,
            "lat": None,
            "lon": None,
            "lat_source": None,
            "sunrise_ts": None,
            "sunset_ts": None,
            "sunrise_next_ts": None,
            "sunset_next_ts": None,
        }
    today = datetime.now(tz).date()
    tomorrow = today + timedelta(days=1)
    sunrise_today, sunset_today = _sunrise_sunset_utc(today, lat, lon)
    sunrise_tomorrow, sunset_tomorrow = _sunrise_sunset_utc(tomorrow, lat, lon)
    payload = {
        "timezone": tz_name,
        "lat": lat,
        "lon": lon,
        "lat_source": lat_source,
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
def add_cache_headers(response):
    if request.path.startswith("/images/"):
        # Plate JPEGs are content-addressed by timestamp — they never change.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.before_request
def require_api_secret():
    if request.method == "OPTIONS":
        return None
    if not request.path.startswith("/api/"):
        return None
    supplied = request.headers.get("X-Gate-Api-Secret", "")
    if hmac.compare_digest(supplied, API_SHARED_SECRET):
        return None
    return jsonify({"error": "forbidden"}), 401


def _check_csrf():
    """Same-origin check on Origin (falling back to Referer) for mutating endpoints.

    Browsers always attach Origin on cross-origin POST/PUT; JavaScript cannot
    spoof it. A cross-site form submission from evil.com carries
    Origin: https://evil.com and is rejected here.

    The primary rule is same-origin: the Origin host must match the Host header
    of this request — this works regardless of which hostname, avahi alias, or
    raw IP the client used to reach the UI. GATE_ALLOWED_ORIGINS adds explicit
    extra origins (e.g. a reverse proxy on another name) on top of that.
    """
    from urllib.parse import urlparse

    origin = request.headers.get("Origin", "").strip().rstrip("/")
    if not origin:
        ref = request.headers.get("Referer", "").strip()
        if ref:
            parsed = urlparse(ref)
            origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    if not origin:
        log.warning("CSRF check failed: no Origin or Referer header on %s %s", request.method, request.path)
        return jsonify({"error": "forbidden"}), 403

    origin_host = urlparse(origin).netloc.lower()
    request_host = request.host.lower()
    # Same-origin: host (incl. port) matches however the client addressed us.
    if origin_host and (origin_host == request_host or origin_host == request_host.split(":")[0]):
        return None
    if origin in ALLOWED_ORIGINS:
        return None
    log.warning(
        "CSRF check failed: origin %r does not match host %r and is not in allowlist on %s %s",
        origin,
        request_host,
        request.method,
        request.path,
    )
    return jsonify({"error": "forbidden"}), 403


def _int_arg(name, default, lo, hi):
    """Integer query parameter clamped to [lo, hi]; falls back to default on garbage."""
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, lo), hi)


def _parse_detail(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return value


def _query_events(kinds=None, offset=0, limit=60, since=None):
    conn = sqlite3.connect(EVENTS_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        clauses = []
        params = []
        if kinds:
            clauses.append("kind IN ({})".format(",".join(["?"] * len(kinds))))
            params.extend(kinds)
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
                    "plate": _display_plate(row["plate"]),
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
                    "request_ip": row["request_ip"],
                    "detail": _parse_detail(row["detail"]),
                    "image_name": image_name,
                    "image_url": f"/images/{image_name}" if image_name else "",
                }
            )
        return results
    finally:
        conn.close()


init_events_db(EVENTS_DB_PATH)


def _read_allowlist():
    if not os.path.exists(ALLOWLIST_PATH):
        return []
    with open(ALLOWLIST_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


# Cache the normalised-key -> registered-plate map, rebuilt when the allowlist
# file changes. Recognition matches on the normalised (OCR-confusable-folded)
# key, so a plate registered as "S3BPN" is stored/grouped as "538PN". For
# display we always resolve back to the plate exactly as entered in admin;
# the recorded value is never trusted, since what the camera saw may be wrong.
_display_cache = {"mtime": None, "map": {}}


def _plate_display_map():
    try:
        mtime = os.path.getmtime(ALLOWLIST_PATH)
    except OSError:
        mtime = None
    if _display_cache["mtime"] != mtime:
        mapping = {}
        for entry in _read_allowlist() or []:
            if isinstance(entry, dict) and "plates" in entry:
                plates = entry.get("plates") or []
                if isinstance(plates, str):
                    plates = [plates]
            elif isinstance(entry, dict) and "plate" in entry:
                plates = [entry.get("plate")]
            elif isinstance(entry, (list, tuple)) and entry:
                plates = [entry[0]]
            else:
                plates = []
            for plate in plates:
                display = str(plate).strip()
                if display:
                    mapping.setdefault(normalise_plate(display), display)
        _display_cache["mtime"] = mtime
        _display_cache["map"] = mapping
    return _display_cache["map"]


def _display_plate(stored):
    """Registered plate for a stored/observed value, or the value unchanged."""
    text = str(stored or "")
    if not text:
        return stored
    return _plate_display_map().get(normalise_plate(text), stored)


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


def _gate_run_with_cooldown(action):
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
            action()
            handle.seek(0)
            handle.truncate()
            handle.write(f"{now:.3f}\n")
            handle.flush()
        fcntl.flock(handle, fcntl.LOCK_UN)
    return remaining


def _request_ip():
    # nginx sets X-Real-IP to the direct client address. X-Forwarded-For is
    # deliberately not consulted: nginx appends to whatever list the client
    # sent, so its first entry is client-controlled and ends up stored in the
    # events DB and rendered in the UI.
    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip
    return request.remote_addr or ""


def _manual_open_request_age_seconds():
    raw = request.headers.get("X-Gate-Requested-At", "").strip()
    if not raw:
        payload = request.get_json(silent=True) or {}
        raw = str(payload.get("requested_at_ms") or payload.get("requested_at") or "").strip()
    if not raw:
        return None, "missing"
    try:
        requested_at = float(raw)
    except (TypeError, ValueError):
        return None, "invalid"
    # Browser clients send Date.now() in milliseconds. Accept seconds too so
    # scripts can still call the endpoint deliberately.
    if requested_at > 10_000_000_000:
        requested_at /= 1000.0
    return time.time() - requested_at, None


def _check_manual_open_freshness(request_ip):
    age_seconds, error = _manual_open_request_age_seconds()
    if error:
        log.warning(
            "Manual gate open rejected from %s: %s request timestamp",
            request_ip or "unknown",
            error,
        )
        return jsonify({"error": "stale_open_request", "reason": f"{error} request timestamp"}), 400
    if age_seconds < -GATE_MANUAL_OPEN_FUTURE_SKEW_SECONDS:
        log.warning(
            "Manual gate open rejected from %s: request timestamp %.1fs in the future",
            request_ip or "unknown",
            abs(age_seconds),
        )
        return jsonify({"error": "stale_open_request", "reason": "request timestamp is in the future"}), 400
    if age_seconds > GATE_MANUAL_OPEN_MAX_AGE_SECONDS:
        log.warning(
            "Manual gate open rejected from %s: request age %.1fs exceeds %ss",
            request_ip or "unknown",
            age_seconds,
            GATE_MANUAL_OPEN_MAX_AGE_SECONDS,
        )
        return jsonify(
            {
                "error": "stale_open_request",
                "reason": "request expired",
                "age_seconds": round(age_seconds, 1),
                "max_age_seconds": GATE_MANUAL_OPEN_MAX_AGE_SECONDS,
            }
        ), 409
    return None


def _latest_gate_open_event():
    conn = sqlite3.connect(EVENTS_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id, captured_at, kind, source, request_ip, detail
            FROM events
            WHERE kind IN ('recognised', 'manual_open')
            ORDER BY captured_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    detail = _parse_detail(row["detail"])
    captured_at = row["captured_at"]
    ts = parse_local_timestamp(captured_at)
    return {
        "id": row["id"],
        "captured_at": captured_at,
        "last_open_ts": ts.timestamp() if ts else None,
        "kind": row["kind"],
        "source": row["source"],
        "request_ip": row["request_ip"],
        "detail": detail,
    }


def _read_cpu_temperature_c():
    paths = [
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/devices/virtual/thermal/thermal_zone0/temp",
    ]
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = handle.read().strip()
            value = float(raw)
            if value > 1000:
                value /= 1000.0
            return round(value, 1)
        except Exception:
            continue
    return None


def _failed_systemd_units():
    try:
        result = subprocess.run(
            ["systemctl", "--failed", "--no-legend", "--plain"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return []
    if result.returncode not in (0, 1):
        return []
    units = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        units.append(line.split()[0])
    return units


def _maintenance_health():
    latest_success = None
    latest_error = None
    lines = _tail_lines(MAINTENANCE_LOG_PATH, 200)
    for raw in reversed(lines):
        line = raw.strip()
        if not line:
            continue
        match = re.match(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Pruned ", line)
        if match and latest_success is None:
            latest_success = match.group("ts")
        if latest_error is None and re.search(r"\b(ERROR|Traceback|failed)\b", line, re.IGNORECASE):
            latest_error = line
        if latest_success and latest_error:
            break
    success_dt = parse_local_timestamp(latest_success) if latest_success else None
    age_hours = None
    stale = True
    if success_dt:
        age_hours = round((datetime.now() - success_dt).total_seconds() / 3600, 1)
        stale = age_hours > 36
    return {
        "log_path": MAINTENANCE_LOG_PATH,
        "last_success": latest_success,
        "age_hours": age_hours,
        "stale": stale if latest_success else True,
        "last_error": latest_error,
    }


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
    err = _check_csrf()
    if err:
        return err
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
        # Events store the normalised match key, config stores the registered
        # plate; key both by the normalised form so the join lands. Several
        # stored variants can fold to one key, so keep the strongest signal.
        last_seen_map = {}
        confidence_map = {}
        for stored_plate, last_seen, confidence in rows:
            key = normalise_plate(str(stored_plate or ""))
            if last_seen is not None and last_seen > last_seen_map.get(key, ""):
                last_seen_map[key] = last_seen
            if confidence is not None and confidence > confidence_map.get(key, -1):
                confidence_map[key] = confidence
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
            key = normalise_plate(str(plate))
            plate_meta.append(
                {
                    "plate": plate,
                    "last_seen": last_seen_map.get(key),
                    "confidence": confidence_map.get(key),
                }
            )
        response.append({"owner": owner, "plates": plate_meta})
    return jsonify(response)


@app.route("/api/open-gate", methods=["POST"])
def open_gate():
    err = _check_csrf()
    if err:
        return err
    request_ip = _request_ip()
    err = _check_manual_open_freshness(request_ip)
    if err:
        return err
    remaining = _gate_run_with_cooldown(lambda: trigger_gate(GATE_PIN_BOARD, GATE_PIN_BCM, log))
    if remaining > 0:
        return jsonify({"ok": False, "retry_in": remaining}), 429
    insert_event(
        EVENTS_DB_PATH,
        plate="",
        owner="",
        allowed=True,
        confidence=None,
        kind="manual_open",
        image_name="",
        captured_at=now_local_str(),
        source="web_ui",
        request_ip=request_ip,
        detail={
            "action": "open_gate",
            "label": "Manual open",
            "user_agent": request.user_agent.string or "",
        },
    )
    log.info("Manual gate open triggered from %s", request_ip or "unknown")
    return jsonify({"ok": True})


@app.route("/api/gate-cooldown", methods=["GET"])
def get_gate_cooldown():
    return jsonify({"remaining": _gate_cooldown_remaining_locked()})


@app.route("/api/gate-last-open", methods=["GET"])
def get_gate_last_open():
    latest = _latest_gate_open_event()
    if latest:
        return jsonify(latest)
    last_ts = _read_last_open_time()
    if last_ts <= 0:
        return jsonify({"last_open_ts": None, "last_open_iso": None})
    last_iso = datetime.fromtimestamp(last_ts).isoformat()
    return jsonify(
        {
            "last_open_ts": last_ts,
            "last_open_iso": last_iso,
            "kind": "unknown",
            "source": "cooldown_file",
            "request_ip": None,
            "detail": None,
        }
    )


@app.route("/api/events", methods=["GET"])
def get_events():
    limit = _int_arg("limit", 200, 1, 1000)
    offset = _int_arg("offset", 0, 0, 1_000_000)
    kind_arg = request.args.get("kind", "").strip()
    kinds = [part.strip() for part in kind_arg.split(",") if part.strip()] or None
    window_key = request.args.get("window")
    if not window_key:
        window_key = "30d"
    window = _window_bounds(window_key) if window_key else None
    events = _query_events(kinds=kinds, offset=offset, limit=limit, since=window)
    return jsonify(events)


@app.route("/api/timeline", methods=["GET"])
def get_timeline():
    per_page = _int_arg("per_page", 25, 5, 100)
    page = _int_arg("page", 1, 1, 1_000_000)
    window_key = request.args.get("window", "30d")
    if window_key not in {"7d", "30d", "all", "forever"}:
        return jsonify({"error": "invalid window"}), 400
    window = _window_bounds(window_key)
    kinds = ["recognised", "manual_open"]
    offset = (page - 1) * per_page
    items = _query_events(kinds=kinds, offset=offset, limit=per_page, since=window)

    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        clauses = ["kind IN ({})".format(",".join(["?"] * len(kinds)))]
        params = list(kinds)
        if window:
            clauses.append("captured_at >= ?")
            params.append(window.strftime("%Y-%m-%d %H:%M:%S"))
        total = conn.execute(
            f"SELECT COUNT(*) FROM events WHERE {' AND '.join(clauses)}",
            params,
        ).fetchone()[0]
    finally:
        conn.close()

    total_pages = max(1, math.ceil(total / per_page)) if total else 1
    return jsonify(
        {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "items": items,
        }
    )


def _window_bounds(window):
    now = datetime.now()
    if window == "24h":
        return now - timedelta(hours=24)
    if window == "7d":
        return now - timedelta(days=7)
    if window == "30d":
        return now - timedelta(days=30)
    if window in {"all", "forever"}:
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
        if window and window >= datetime.now() - timedelta(days=2):
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
            WHERE {" AND ".join(where)}
            GROUP BY plate
            ORDER BY count DESC
            LIMIT 1
        """
        row = conn.execute(query, params).fetchone()
        if not row:
            return {"plate": None, "count": 0}
        return {"plate": _display_plate(row[0]), "count": row[1]}
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
            WHERE {" AND ".join(where)}
            GROUP BY plate
            ORDER BY count DESC
            LIMIT 1
        """
        row = conn.execute(query, params).fetchone()
        if not row:
            return {"plate": None, "count": 0}
        return {"plate": _display_plate(row[0]), "count": row[1]}
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
            WHERE {" AND ".join(where)}
            GROUP BY plate
            ORDER BY count DESC
            LIMIT ?
        """
        rows = conn.execute(query, (*params, limit)).fetchall()
        return [{"plate": _display_plate(row[0]), "count": row[1]} for row in rows]
    finally:
        conn.close()


def _stats_busiest_bucket(window):
    now = datetime.now()
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
            WHERE {" AND ".join(where)}
        """
        row = conn.execute(query, params).fetchone()
        return row[0] if row and row[0] is not None else None
    finally:
        conn.close()


def _stats_no_plate(window):
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        where = ["(plate IS NULL OR plate = '')", "kind IN ('recognised', 'unmatched', 'candidate')"]
        params = []
        if window:
            where.append("captured_at >= ?")
            params.append(window.strftime("%Y-%m-%d %H:%M:%S"))
        query = f"""
            SELECT COUNT(*)
            FROM events
            WHERE {" AND ".join(where)}
        """
        row = conn.execute(query, params).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def _stats_recent_gate_opens(window):
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        where = ["kind IN ('recognised', 'manual_open')"]
        params = []
        if window:
            where.append("captured_at >= ?")
            params.append(window.strftime("%Y-%m-%d %H:%M:%S"))
        row = conn.execute(
            f"SELECT COUNT(*) FROM events WHERE {' AND '.join(where)}",
            params,
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def _stats_source_breakdown(window):
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        where = ["kind IN ('recognised', 'manual_open')"]
        params = []
        if window:
            where.append("captured_at >= ?")
            params.append(window.strftime("%Y-%m-%d %H:%M:%S"))
        rows = conn.execute(
            f"""
            SELECT COALESCE(NULLIF(source, ''), 'unknown') as source, COUNT(*)
            FROM events
            WHERE {" AND ".join(where)}
            GROUP BY source
            ORDER BY COUNT(*) DESC
            """,
            params,
        ).fetchall()
        return [{"source": row[0], "count": row[1]} for row in rows]
    finally:
        conn.close()


def _stats_manual_open_top_ips(window, limit=5):
    conn = sqlite3.connect(EVENTS_DB_PATH)
    try:
        where = ["kind = 'manual_open'", "request_ip IS NOT NULL", "request_ip != ''"]
        params = []
        if window:
            where.append("captured_at >= ?")
            params.append(window.strftime("%Y-%m-%d %H:%M:%S"))
        rows = conn.execute(
            f"""
            SELECT request_ip, COUNT(*) as count
            FROM events
            WHERE {" AND ".join(where)}
            GROUP BY request_ip
            ORDER BY count DESC, request_ip ASC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [{"request_ip": row[0], "count": row[1]} for row in rows]
    finally:
        conn.close()


@app.route("/api/stats", methods=["GET"])
def get_stats():
    window_key = request.args.get("window", "24h")
    if window_key not in {"24h", "7d", "30d", "all", "forever"}:
        return jsonify({"error": "invalid window"}), 400
    window = _window_bounds(window_key)
    counts = _stats_counts(window)
    summary = {kind: count for kind, count in counts}
    total = sum(summary.values())
    timeseries = _stats_timeseries(window)
    gate_opens = _stats_recent_gate_opens(window)
    return jsonify(
        {
            "window": window_key,
            "total": total,
            "counts": summary,
            "timeseries": timeseries,
            "gate_opens": gate_opens,
            "source_breakdown": _stats_source_breakdown(window),
        }
    )


@app.route("/api/stats/insights", methods=["GET"])
def get_stats_insights():
    window_key = request.args.get("window", "24h")
    if window_key not in {"24h", "7d", "30d", "all", "forever"}:
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
        "top_manual_ips": _stats_manual_open_top_ips(window),
        "latest_gate_open": _latest_gate_open_event(),
        "source_breakdown": _stats_source_breakdown(window),
    }
    return jsonify({"window": window_key, "insights": insights})


@app.route("/api/images", methods=["GET"])
def list_images():
    limit = _int_arg("limit", 60, 1, 1000)
    offset = _int_arg("offset", 0, 0, 1_000_000)
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
        err = _check_csrf()
        if err:
            return err
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
    now = datetime.now(timezone.utc).timestamp()
    if not os.path.exists(stream_path):
        return jsonify({"lag_ms": None, "frame_mtime": None})
    mtime = os.path.getmtime(stream_path)
    lag_ms = max(0, (now - mtime) * 1000)
    return jsonify({"lag_ms": int(lag_ms), "frame_mtime": mtime, "server_time": now})


@app.route("/api/stream-health", methods=["GET"])
def stream_health():
    stream_path = os.path.join(STATIC_DIR, "stream.jpg")
    now = datetime.now(timezone.utc).timestamp()
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
        row = conn.execute("SELECT captured_at FROM events ORDER BY captured_at DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    return parse_local_timestamp(row[0]) if row and row[0] else None


@app.route("/api/healthz", methods=["GET"])
def healthz():
    db_ok, db_error = sqlite_healthcheck(EVENTS_DB_PATH)
    allowlist_ok = os.path.exists(ALLOWLIST_PATH) and os.access(ALLOWLIST_PATH, os.R_OK)
    stream_path = os.path.join(STATIC_DIR, "stream.jpg")
    stream_exists = os.path.exists(stream_path)
    services = {
        "alprd": _systemctl_is_active("alprd"),
        "gate_anpr": _systemctl_is_active("gate_anpr"),
        "gate_anpr_web": _systemctl_is_active("gate_anpr_web"),
    }
    ok = db_ok and allowlist_ok and services["gate_anpr_web"] == "active"
    maint = _maintenance_health()
    return jsonify(
        {
            "ok": ok,
            "db": {"ok": db_ok, "error": db_error},
            "allowlist": {"ok": allowlist_ok},
            "stream": {"exists": stream_exists},
            "services": services,
            "maintenance": {
                "last_success": maint.get("last_success"),
                "age_hours": maint.get("age_hours"),
                "stale": maint.get("stale"),
                "last_error": maint.get("last_error"),
            },
        }
    ), (200 if ok else 503)


@app.route("/api/service-health", methods=["GET"])
def service_health():
    services = {
        "alprd": _systemctl_is_active("alprd"),
        "gate_anpr": _systemctl_is_active("gate_anpr"),
        "gate_anpr_web": _systemctl_is_active("gate_anpr_web"),
        "stream_jpeg": _systemctl_is_active("gate_anpr_stream_jpeg"),
    }
    last_event = _latest_event_timestamp()
    latest_open = _latest_gate_open_event()
    now = datetime.now()
    last_event_age = (now - last_event).total_seconds() if last_event else None
    disk_total, disk_used, disk_free = shutil.disk_usage("/")
    failed_units = _failed_systemd_units()
    return jsonify(
        {
            "services": services,
            "last_event_time": last_event.isoformat() if last_event else None,
            "last_event_age_s": last_event_age,
            "now": now.isoformat(),
            "last_gate_open": latest_open,
            "disk": {
                "total_bytes": disk_total,
                "used_bytes": disk_used,
                "free_bytes": disk_free,
                "free_pct": round((disk_free / disk_total) * 100, 1) if disk_total else None,
            },
            "temperature_c": _read_cpu_temperature_c(),
            "failed_units": failed_units,
            "maintenance": _maintenance_health(),
        }
    )


@app.route("/api/logs", methods=["GET"])
def logs():
    service_key = request.args.get("service", "gate_anpr")
    range_key = request.args.get("range", "1h")
    max_lines = _int_arg("lines", 300, 50, 1000)

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
    return _render_static_html("index.html")


@app.route("/admin")
def admin():
    return _render_static_html("admin.html")


@app.route("/fullscreen")
def fullscreen():
    return _render_static_html("fullscreen.html")


@app.route("/stats")
def stats():
    return _render_static_html("index.html")


@app.route("/<path:path>")
def static_proxy(path):
    if path in {"index.html", "admin.html", "fullscreen.html", "stats.html"}:
        return _render_static_html(path)
    return send_from_directory(STATIC_DIR, path)


if __name__ == "__main__":
    # Local dev only. Production uses waitress via systemd.
    port = int(os.getenv("GATE_WEB_PORT", "8080"))
    app.run(host="127.0.0.1", port=port, debug=False)
