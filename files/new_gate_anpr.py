#!/usr/bin/env python3
## Version 0.4 – Python 3 refactor (behaviour unchanged)

import os
import sys
import traceback
import logging
import time
import json
import requests
from time import gmtime, strftime

import greenstalk
import RPi.GPIO as GPIO

PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")
PUSHOVER_APP_TOKEN = os.getenv("PUSHOVER_APP_TOKEN")

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


def _load_plate_allowlist():
    path = os.getenv("PLATE_ALLOWLIST_PATH", "").strip()
    if not path and os.path.exists("/etc/gate_anpr_allowlist.json"):
        path = "/etc/gate_anpr_allowlist.json"
    raw = os.getenv("PLATE_ALLOWLIST_JSON", "").strip()
    if path:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            log.error("Allowlist file not found: %s", path)
            return []
        except json.JSONDecodeError as exc:
            log.error("Invalid allowlist JSON in %s: %s", path, exc)
            return []
    elif raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.error("Invalid PLATE_ALLOWLIST_JSON: %s", exc)
            return []
    else:
        return []
    allowlist = []
    for item in data:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            allowlist.append((str(item[0]).upper(), str(item[1])))
        elif isinstance(item, dict) and "plate" in item and "owner" in item:
            allowlist.append((str(item["plate"]).upper(), str(item["owner"])))
        else:
            log.warning("Skipping malformed allowlist entry: %r", item)
    return allowlist


list_of_plates = _load_plate_allowlist()
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

            # Touch early to reduce chance of TTR expiry during GPIO/pushover I/O
            client.touch(job)

            min_time = (json_raw["epoch_time"] / 1000) + 10
            candidates = json_raw["results"][0]["candidates"]
            no_of_plates_seen = len(candidates)

            log.info("Current Time: %s", strftime("%Y-%m-%d %H:%M:%S", gmtime()))
            log.info(
                "Time Plates Captured: %s",
                time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(json_raw["epoch_time"] / 1000),
                ),
            )
            log.info("Processing Time was: %s", json_raw.get("processing_time_ms"))
            log.info("%s plates seen", no_of_plates_seen)

            # Do the thing if plate is recent, valid and hasn't already been recently seen
            if min_time > time.time():
                for cand in candidates:
                    number_plate = cand["plate"]
                    log.info("Candidate plate: %s", number_plate)

                    allowed = any(number_plate == plate[0] for plate in list_of_plates)
                    not_recently_seen = time.time() > (last_seen_time + 55)

                    if allowed and not_recently_seen:
                        last_seen_time = time.time()
                        last_seen_reg = number_plate
                        log.info("Plate %s recognised. Opening gate", number_plate)

                        _open_gate()

                        jpg_path = "/home/pi/plates/%s.jpg" % json_raw["uuid"]
                        log.debug("Sending pushover with image %s", jpg_path)
                        if PUSHOVER_ENABLED:
                            try:
                                if os.path.exists(jpg_path):
                                    with open(jpg_path, "rb") as f:
                                        requests.post(
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
                                    requests.post(
                                        "https://api.pushover.net/1/messages.json",
                                        data={
                                            "token": PUSHOVER_APP_TOKEN,
                                            "user": PUSHOVER_USER_KEY,
                                            "message": "Pi5 - Opening Gate for %s" % number_plate,
                                        },
                                        timeout=15,
                                    )
                            except Exception as exc:
                                log.warning("Pushover failed: %s", exc)
                        else:
                            log.debug("Pushover skipped (not configured)")
                    elif DEBUG:
                        log.debug(
                            "Plate %s allowed=%s recently_seen=%s",
                            number_plate,
                            allowed,
                            not not_recently_seen,
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
