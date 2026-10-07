#!/usr/bin/env python3
import fcntl
import json
import logging
import math
import os
import sqlite3
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

RECENT_DECISIONS_LIMIT = 200
RECENT_DECISION_SAMPLE_SECONDS = 30
RECENT_DECISION_GLOBAL_SAMPLE_SECONDS = 1


def env_int(name, default, logger_name="gate_runtime"):
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        logging.getLogger(logger_name).warning("Invalid %s=%r; using %s", name, raw, default)
        return default


def configure_logging(service_name, *, log_path, debug_env="GATE_ANPR_DEBUG"):
    logger = logging.getLogger(service_name)
    if getattr(logger, "_gate_configured", False):
        return logger

    level = logging.DEBUG if os.getenv(debug_env, "0").lower() in {"1", "true", "yes", "on"} else logging.INFO
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    logger.handlers.clear()
    logger.setLevel(level)
    logger.propagate = False

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    try:
        directory = os.path.dirname(log_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=env_int("GATE_LOG_MAX_BYTES", 10 * 1024 * 1024, service_name),
            backupCount=env_int("GATE_LOG_BACKUPS", 5, service_name),
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as exc:
        logger.warning("File logging unavailable for %s: %s", log_path, exc)

    logger._gate_configured = True
    return logger


def ensure_column(conn, table, column, column_type):
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
        conn.commit()


def init_events_db(db_path):
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
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
        ensure_column(conn, "events", "source", "TEXT")
        ensure_column(conn, "events", "processing_time_ms", "INTEGER")
        ensure_column(conn, "events", "observed_plate", "TEXT")
        ensure_column(conn, "events", "observed_confidence", "REAL")
        ensure_column(conn, "events", "fuzzy_distance", "INTEGER")
        ensure_column(conn, "events", "fuzzy", "INTEGER")
        ensure_column(conn, "events", "request_ip", "TEXT")
        ensure_column(conn, "events", "detail", "TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recent_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate TEXT,
                observed_plate TEXT,
                owner TEXT,
                confidence REAL,
                image_name TEXT,
                captured_at TEXT,
                created_at TEXT NOT NULL,
                detail TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def now_local_str():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def parse_local_timestamp(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def insert_event(
    db_path,
    *,
    uuid=None,
    plate="",
    owner="",
    allowed=None,
    confidence=None,
    kind="",
    image_name="",
    captured_at=None,
    source="",
    processing_time_ms=None,
    observed_plate=None,
    observed_confidence=None,
    fuzzy_distance=None,
    fuzzy=None,
    request_ip=None,
    detail=None,
):
    created_at = now_local_str()
    captured_value = captured_at or created_at
    detail_text = detail
    if isinstance(detail, (dict, list)):
        detail_text = json.dumps(detail, separators=(",", ":"), sort_keys=True)

    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute(
            """
            INSERT INTO events (
                uuid, plate, owner, allowed, confidence, kind, image_name, captured_at,
                source, processing_time_ms, observed_plate, observed_confidence, fuzzy_distance, fuzzy,
                request_ip, detail, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid,
                plate,
                owner,
                int(allowed) if allowed is not None else None,
                confidence,
                kind,
                image_name,
                captured_value,
                source,
                processing_time_ms,
                observed_plate,
                observed_confidence,
                fuzzy_distance,
                fuzzy,
                request_ip,
                detail_text,
                created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def insert_recent_decision(
    db_path,
    *,
    plate="",
    observed_plate="",
    owner="",
    confidence=None,
    image_name="",
    captured_at=None,
    detail,
):
    """Store one diagnostic sample without allowing this table to grow indefinitely.

    The caller handles failures separately from gate actions and ordinary event
    persistence. A busy diagnostic write must not hold up the recognition loop.
    """
    conn = sqlite3.connect(db_path, timeout=0.05)
    try:
        conn.execute(
            """
            INSERT INTO recent_decisions (
                plate, observed_plate, owner, confidence, image_name,
                captured_at, created_at, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plate,
                observed_plate,
                owner,
                confidence,
                image_name,
                captured_at,
                now_local_str(),
                json.dumps(detail, separators=(",", ":"), sort_keys=True),
            ),
        )
        conn.execute(
            """
            DELETE FROM recent_decisions
            WHERE id NOT IN (SELECT id FROM recent_decisions ORDER BY id DESC LIMIT ?)
            """,
            (RECENT_DECISIONS_LIMIT,),
        )
        conn.commit()
    finally:
        conn.close()


def read_recent_decisions(db_path, limit=30):
    """Read bounded diagnostic samples; missing/unreadable storage raises normally."""
    limit = max(1, min(100, int(limit)))
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, plate, observed_plate, owner, confidence, image_name,
                   captured_at, created_at, detail
            FROM recent_decisions ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        decisions = []
        for row in rows:
            item = dict(row)
            try:
                detail = json.loads(item["detail"])
                item["detail"] = detail if isinstance(detail, dict) else None
            except (TypeError, ValueError):
                item["detail"] = None
            decisions.append(item)
        return decisions
    finally:
        conn.close()


def prune_old_events(db_path, retention_days, logger):
    # captured_at is stored as local time (now_local_str), so the cutoff must
    # be local too or retention is off by the UTC offset.
    cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "DELETE FROM events WHERE captured_at < ?",
            (cutoff,),
        )
        deleted = cursor.rowcount if cursor.rowcount is not None else 0
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.execute("PRAGMA optimize;")
        logger.info(
            "Pruned %s events older than %s days from %s",
            deleted,
            retention_days,
            db_path,
        )
        return deleted
    finally:
        conn.close()


def sqlite_healthcheck(db_path):
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.execute("SELECT 1").fetchone()
        return True, ""
    except Exception as exc:
        return False, str(exc)
    finally:
        conn.close()


# Relay polarity constants — HIGH activates the relay (closes the gate contact).
# Change these two lines if your relay board is wired active-LOW.
RELAY_ON = 1  # GPIO.HIGH
RELAY_OFF = 0  # GPIO.LOW


class GateActuationError(RuntimeError):
    """A failed request, including whether a relay pulse may already have begun."""

    def __init__(self, message, *, may_have_activated):
        super().__init__(message)
        self.may_have_activated = may_have_activated


_logged_gpio_config = None


def _pulse_lgpio(bcm_pin, chip, before_activate):
    import lgpio

    handle = lgpio.gpiochip_open(chip)
    try:
        lgpio.gpio_claim_output(handle, bcm_pin, RELAY_OFF)
        try:
            before_activate()
            lgpio.gpio_write(handle, bcm_pin, RELAY_ON)
            time.sleep(0.5)
        finally:
            # Closing a gpiochip does not guarantee a LOW output on Raspberry Pi.
            # Also runs when the worker's SIGTERM handler raises SystemExit.
            lgpio.gpio_write(handle, bcm_pin, RELAY_OFF)
    finally:
        lgpio.gpiochip_close(handle)


def _pulse_rpi_gpio(board_pin, before_activate):
    import RPi.GPIO as GPIO

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(board_pin, GPIO.OUT, initial=RELAY_OFF)
    try:
        before_activate()
        GPIO.output(board_pin, RELAY_ON)
        time.sleep(0.5)
    finally:
        try:
            GPIO.output(board_pin, RELAY_OFF)
        finally:
            GPIO.cleanup(board_pin)


def open_gate(board_pin, bcm_pin, logger):
    """Pulse the relay, or return False if another caller just pulsed it.

    Both the web server and ANPR worker take this same process lock. The short
    shared interval only coalesces overlapping requests; their longer manual
    cooldown and per-plate deduplication policies remain with the callers.
    A backend is selected explicitly, never retried after a possible activation.
    """
    global _logged_gpio_config
    activation_started = False
    try:
        backend = os.getenv("GATE_GPIO_BACKEND", "lgpio").strip().lower()
        if backend not in {"lgpio", "rpi_gpio"}:
            raise ValueError("GATE_GPIO_BACKEND must be lgpio or rpi_gpio")
        chip = int(os.getenv("GATE_GPIO_CHIP", "0"))
        if chip < 0:
            raise ValueError("GATE_GPIO_CHIP must be non-negative")
        min_interval = float(os.getenv("GATE_GPIO_MIN_INTERVAL_SECONDS", "1.0"))
        if not math.isfinite(min_interval) or min_interval < 0.5:
            raise ValueError("GATE_GPIO_MIN_INTERVAL_SECONDS must be at least 0.5")
        lock_path = os.getenv("GATE_GPIO_LOCK_PATH", "/opt/gate_anpr/gate_gpio.lock")

        with open(lock_path, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            try:
                last_state = json.loads(handle.read() or "{}")
                last_attempt = float(last_state.get("attempted_at", 0))
                completed = last_state.get("completed", False)
            except (ValueError, TypeError, AttributeError):
                last_attempt, completed = 0.0, False
            now = time.time()
            if last_attempt > 0 and 0 <= now - last_attempt < min_interval:
                if not completed:
                    raise GateActuationError("A recent relay pulse has an uncertain outcome", may_have_activated=True)
                logger.info("Gate request coalesced with a recent relay pulse")
                return False

            attempted_at = None

            def save_state(completed):
                handle.seek(0)
                handle.truncate()
                json.dump({"attempted_at": attempted_at, "completed": completed}, handle)
                handle.flush()

            def before_activate():
                nonlocal activation_started, attempted_at
                # Persist the attempt before HIGH so another process does not
                # immediately repeat a pulse whose outcome is uncertain.
                attempted_at = time.time()
                save_state(completed=False)
                activation_started = True

            config = (backend, chip, board_pin, bcm_pin)
            if config != _logged_gpio_config:
                logger.info("Gate GPIO backend=%s chip=%s BOARD=%s BCM=%s", *config)
                _logged_gpio_config = config
            if backend == "lgpio":
                _pulse_lgpio(bcm_pin, chip, before_activate)
            else:
                _pulse_rpi_gpio(board_pin, before_activate)
            save_state(completed=True)
            return True
    except GateActuationError:
        raise
    except Exception as exc:
        logger.error("Gate GPIO failed (activation attempted=%s): %s", activation_started, exc)
        raise GateActuationError(str(exc), may_have_activated=activation_started) from exc
