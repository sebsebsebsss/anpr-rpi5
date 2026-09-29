#!/usr/bin/env python3
"""Optional Chrome UI checks using local files and synthetic, intercepted APIs.

Run: .venv/bin/python tests/home_browser.py [--output-dir /tmp/gate-ui-preview]
Requires Playwright and Google Chrome. No request reaches a real gate/server.
Optional screenshots contain synthetic vehicles only; never production data.
"""

import argparse
import json
import mimetypes
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image
from playwright.sync_api import sync_playwright

STATIC = Path(__file__).resolve().parents[1] / "files" / "web" / "static"
IPAD_UA = (
    "Mozilla/5.0 (iPad; CPU OS 12_5_8 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/12.1.2 Mobile/15E148 Safari/604.1"
)


def require(condition, label):
    if not condition:
        raise AssertionError(label)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    image_buffer = BytesIO()
    Image.new("RGB", (640, 360), (67, 87, 79)).save(image_buffer, "JPEG")
    image = image_buffer.getvalue()
    fixture = {
        "frame_error": False,
        "source_stale": False,
        "api_error": False,
        "service_inactive": False,
        "decisions_available": True,
        "legacy_layout": False,
        "save": "validation",
        "plates": [{"owner": "Example household", "plates": ["TEST123"]}],
    }
    counters = {"blocked_gate_requests": 0, "unexpected_requests": 0, "page_errors": 0}

    def event(event_id, plate, kind):
        now = time.time()
        return {
            "id": event_id,
            "plate": plate,
            "owner": "Example household" if kind == "recognised" else "",
            "kind": kind,
            "seen_at": now - 30,
            "expires_at": now + 270,
            "captured_at": datetime.fromtimestamp(now - 30).strftime("%Y-%m-%d %H:%M:%S"),
            "image_url": "/images/synthetic.jpg",
            "thumbnail_url": "/previews/synthetic.jpg?size=160",
            "preview_url": "/previews/synthetic.jpg?size=640",
            "detail": {
                "decision": {
                    "version": 1,
                    "reason": "not_allowlisted" if kind == "unmatched" else "allowlist_match",
                    "match_type": "none" if kind == "unmatched" else "exact",
                    "relay_command": "not_requested" if kind == "unmatched" else "pulse_sent",
                }
            },
        }

    def intercept(route):
        request = route.request
        parsed = urlsplit(request.url)
        path = parsed.path
        if parsed.hostname != "gate.test":
            counters["unexpected_requests"] += 1
            route.abort()
            return
        if path == "/api/open-gate":
            counters["blocked_gate_requests"] += 1
            route.abort()
            return
        if request.method not in {"GET", "HEAD"} and not (path == "/api/plates" and request.method == "PUT"):
            counters["unexpected_requests"] += 1
            route.abort()
            return
        if path == "/static/stream.jpg":
            if fixture["frame_error"]:
                route.abort()
            else:
                route.fulfill(body=image, content_type="image/jpeg")
            return
        if path.startswith("/previews/"):
            route.fulfill(body=image, content_type="image/jpeg")
            return
        if path == "/api/home-status":
            if fixture["api_error"]:
                route.abort()
                return
            now = time.time()
            route.fulfill(
                json={
                    "server_time": now,
                    "services_checked_at": now,
                    "stream": {
                        "age_seconds": 60 if fixture["source_stale"] else 1,
                        "fresh": not fixture["source_stale"],
                        "stale_after_seconds": 15,
                    },
                    "services": {
                        "alprd": "inactive" if fixture["service_inactive"] else "active",
                        "gate_anpr": "active",
                        "stream_jpeg": "active",
                        "beanstalkd": "active",
                    },
                    "recognised": [event(2, "TEST123", "recognised"), event(1, "DEMO456", "recognised")],
                    "unfamiliar": [event(3, "NEW789", "unmatched")],
                    "recent_decisions": [event(3, "NEW789", "unmatched")],
                    "decisions_available": fixture["decisions_available"],
                }
            )
            return
        if path == "/api/plates":
            if request.method == "PUT":
                if fixture["save"] == "validation":
                    route.fulfill(status=400, json={"error": "Row 1: this plate is already assigned to another owner."})
                elif fixture["save"] == "offline":
                    route.abort()
                else:
                    fixture["plates"] = request.post_data_json
                    route.fulfill(json={"ok": True})
            else:
                route.fulfill(json=fixture["plates"])
            return
        apis = {
            "/api/config": {"stream_fps": 5, "group_window_sec": 60},
            "/api/ui-settings": {"theme_mode": "light"},
            "/api/stream": {"url": "/static/stream.jpg"},
            "/api/events": [event(2, "TEST123", "recognised")],
            "/api/gate-cooldown": {"remaining": 0},
            "/api/gate-last-open": {"last_open_ts": None},
            "/api/allowlist-status": [],
        }
        if path in apis:
            route.fulfill(json=apis[path])
            return
        filename = {"/": "index.html", "/admin": "admin.html", "/fullscreen": "fullscreen.html"}.get(path)
        local = STATIC / (filename or path.removeprefix("/static/").lstrip("/"))
        if local.is_file() and local.resolve().is_relative_to(STATIC.resolve()):
            content = local.read_bytes().replace(b"__GATE_API_SHARED_SECRET__", b"synthetic-test-secret")
            if local.name == "styles.css" and fixture["legacy_layout"]:
                # Exercise the legacy sizing fallback in Chrome without claiming
                # that a user agent string emulates the old WebKit engine.
                content = content.replace(b"@supports not (aspect-ratio: 1 / 1)", b"@supports (display: grid)")
            route.fulfill(body=content, content_type=mimetypes.guess_type(local.name)[0] or "application/octet-stream")
            return
        # Optional absent icons should not trigger any external network access.
        route.fulfill(status=404, body="")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1024, "height": 768}, user_agent=IPAD_UA, service_workers="block"
        )
        context.route("**/*", intercept)
        page = context.new_page()
        page.on("pageerror", lambda error: counters.__setitem__("page_errors", counters["page_errors"] + 1))
        page.set_default_timeout(15000)

        def ready():
            page.wait_for_function("document.getElementById('tablet-stream-status').textContent === 'View updating'")
            page.locator("#home-unfamiliar").wait_for(state="visible")
            require(
                page.locator("#tablet-timeline-list .tablet-timeline-row").count() == 2, "recognised arrivals missing"
            )

        def screenshot(name):
            require(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "horizontal overflow")
            if page.url.endswith("/fullscreen") or page.viewport_size["width"] >= 900:
                require(
                    page.evaluate(
                        "document.querySelector('.tablet-grid').getBoundingClientRect().bottom <= innerHeight"
                    ),
                    "tablet controls or arrivals below viewport",
                )
            if args.output_dir:
                args.output_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(args.output_dir / name), full_page=True)

        page.goto("http://gate.test/", wait_until="domcontentloaded")
        ready()
        screenshot("home-ipad.png")
        page.locator(".home-health-details summary").click()
        require(page.locator("#tablet-system-status").is_visible(), "status disclosure did not open on tap")
        page.locator(".home-health-details summary").click()
        require(page.locator("#tablet-system-status").is_hidden(), "status details should be collapsed initially")
        require(page.locator("#home-decision .decision-note").is_hidden(), "decision prose should be collapsed")
        page.locator("#home-decision summary").click()
        require(page.locator("#home-decision .decision-note").is_visible(), "decision explanation did not open")
        page.locator("#home-decision summary").click()
        fixture["frame_error"] = True
        page.wait_for_function("document.getElementById('tablet-stream-status').textContent === 'View interrupted'")
        fixture["frame_error"] = False
        ready()
        fixture["source_stale"] = True
        page.wait_for_function("document.getElementById('tablet-stream-status').textContent === 'Source stale'")
        fixture["source_stale"] = False
        fixture["service_inactive"] = True
        page.wait_for_function(
            "document.getElementById('tablet-system-status').textContent.includes('Recognition service inactive')"
        )
        fixture["api_error"] = True
        page.wait_for_function("document.getElementById('tablet-stream-status').textContent === 'Status unavailable'")
        # Simulate elapsed client time while disconnected without generating
        # hundreds of fake frames or making any real network requests.
        page.evaluate("state.homeStatusReceivedAt -= 301000; renderHomeArrivals()")
        require(page.locator("#home-unfamiliar").is_hidden(), "disconnected arrival did not expire")
        fixture.update(api_error=False, service_inactive=False, decisions_available=False)
        page.wait_for_function(
            "document.getElementById('home-decision').textContent === 'Decision history unavailable'"
        )
        fixture["decisions_available"] = True

        page.goto("http://gate.test/fullscreen", wait_until="domcontentloaded")
        ready()
        screenshot("home-fullscreen.png")
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto("http://gate.test/", wait_until="domcontentloaded")
        ready()
        screenshot("home-phone.png")
        for width, height, path, name in (
            (390, 664, "/", "phone-browser"),
            (320, 568, "/", "small-phone"),
            (1024, 748, "/", "ipad"),
            (800, 480, "/fullscreen", "kiosk-small"),
            (1024, 600, "/fullscreen", "kiosk-wide"),
            (1280, 800, "/fullscreen", "kiosk-large"),
        ):
            page.set_viewport_size({"width": width, "height": height})
            page.goto("http://gate.test" + path, wait_until="domcontentloaded")
            ready()
            for theme in ("light", "dark"):
                page.evaluate("theme => document.body.classList.toggle('theme-dark', theme === 'dark')", theme)
                screenshot("home-" + name + "-" + theme + ".png")
            require(
                page.locator("#open-gate-tablet").bounding_box()["y"]
                + page.locator("#open-gate-tablet").bounding_box()["height"]
                <= height,
                "gate action is below the initial viewport",
            )
            require(
                page.locator("#tablet-stream-frame img").evaluate(
                    "image => getComputedStyle(image).objectFit === 'contain'"
                ),
                "camera image must remain uncropped",
            )
        fixture["legacy_layout"] = True
        for width, height, path in ((1024, 748, "/"), (800, 480, "/fullscreen"), (390, 664, "/")):
            page.set_viewport_size({"width": width, "height": height})
            page.goto("http://gate.test" + path, wait_until="domcontentloaded")
            ready()
            require(
                page.locator("#tablet-stream-frame").bounding_box()["height"] >= 100,
                "legacy image sizing fallback collapsed the camera",
            )
            screenshot("home-legacy-layout-" + str(width) + ".png")
        fixture["legacy_layout"] = False
        page.set_viewport_size({"width": 390, "height": 844})

        page.goto("http://gate.test/admin", wait_until="domcontentloaded")
        page.locator(".owner-input").fill("Edited example owner")
        require(page.locator("#allowlist-status").inner_text() == "Unsaved changes", "unsaved feedback missing")
        page.locator("#save-plates").click()
        page.wait_for_function("document.getElementById('allowlist-status').textContent.startsWith('Row 1:')")
        require(page.locator(".owner-input").input_value() == "Edited example owner", "validation lost user edits")
        screenshot("admin-validation-phone.png")
        fixture["save"] = "offline"
        page.locator("#save-plates").click()
        page.wait_for_function(
            "document.getElementById('allowlist-status').textContent.includes('edits are still here')"
        )
        require(page.locator(".owner-input").input_value() == "Edited example owner", "network error lost user edits")
        fixture["save"] = "success"
        page.locator("#save-plates").click()
        page.wait_for_function("document.getElementById('allowlist-status').textContent === 'All changes saved'")
        browser.close()

    require(
        counters == {"blocked_gate_requests": 0, "unexpected_requests": 0, "page_errors": 0},
        "unexpected browser request/error",
    )
    print(json.dumps({"ok": True, "mocked_checks": 24, **counters}))


if __name__ == "__main__":
    main()
