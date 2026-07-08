#!/usr/bin/env python3
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler


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


def prune_old_events(db_path, retention_days, logger):
    cutoff = (datetime.utcnow() - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
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


def open_gate(board_pin, bcm_pin, logger):
    logger.debug("GPIO setup: mode=BOARD pin=%s", board_pin)
    try:
        import RPi.GPIO as GPIO

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(board_pin, GPIO.OUT)
        try:
            GPIO.output(board_pin, RELAY_ON)
            time.sleep(0.5)
        finally:
            GPIO.output(board_pin, RELAY_OFF)
            GPIO.cleanup()
        return
    except RuntimeError as exc:
        logger.warning("RPi.GPIO failed (%s). Falling back to lgpio BCM %s", exc, bcm_pin)

    try:
        import lgpio  # type: ignore
    except Exception as exc:
        logger.error("lgpio not available; cannot toggle gate pin: %s", exc)
        raise

    handle = lgpio.gpiochip_open(0)
    try:
        lgpio.gpio_claim_output(handle, bcm_pin, RELAY_OFF)
        lgpio.gpio_write(handle, bcm_pin, RELAY_ON)
        time.sleep(0.5)
        lgpio.gpio_write(handle, bcm_pin, RELAY_OFF)
    finally:
        lgpio.gpiochip_close(handle)
