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
# Primary screens are appliances, not scrolling dashboards. These minima are
# larger than the deployed dcec8ae button bounds measured with the same fixture.
WALL_BUTTON_MINIMA = {
    (1024, 748, "/"): (216, 499),
    (1024, 704, "/"): (216, 455),
    (1024, 680, "/"): (216, 431),
    (800, 480, "/fullscreen"): (195, 342),
    (1024, 600, "/fullscreen"): (256, 462),
    (1280, 800, "/fullscreen"): (326, 662),
}


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
    profiles = {}
    for name, size in (("stream.jpg", (960, 540)), ("stream-tablet.jpg", (800, 450)), ("stream-kiosk.jpg", (640, 360))):
        buffer = BytesIO()
        Image.new("RGB", size, (67, 87, 79)).save(buffer, "JPEG")
        profiles["/static/" + name] = buffer.getvalue()
    fixture = {
        "frame_error": False,
        "source_stale": False,
        "api_error": False,
        "service_inactive": False,
        "decisions_available": True,
        "unfamiliar": True,
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
        if path in profiles:
            if fixture["frame_error"]:
                route.abort()
            else:
                route.fulfill(body=profiles[path], content_type="image/jpeg")
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
                    "unfamiliar": [event(3, "NEW789", "unmatched")] if fixture["unfamiliar"] else [],
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
            "/api/config": {"stream_fps": 10, "group_window_sec": 60},
            "/api/ui-settings": {"theme_mode": "light"},
            "/api/stream": {
                "url": "/static/stream.jpg",
                "profiles": {"tablet": "/static/stream-tablet.jpg", "kiosk": "/static/stream-kiosk.jpg"},
            },
            "/api/events": [event(2, "TEST123", "recognised")],
            "/api/gate-cooldown": {"remaining": 0},
            "/api/gate-last-open": {"last_open_ts": None},
            "/api/allowlist-status": [],
            "/api/stats": {
                "window": "24h",
                "counts": {"recognised": 10, "unmatched": 2, "manual_open": 1},
                "timeseries": {
                    "bucket": "hour",
                    "start": "2026-09-28T10:00:00",
                    "end": "2026-09-29T10:00:00",
                    "buckets": [],
                },
            },
            "/api/service-health": {},
            "/api/stats/insights": {"insights": {}},
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
                # Old Safari's vh can remain at the screen height after its URL
                # banner appears. Reproduce that disagreement with innerHeight.
                content = content.replace(b"100vh", b"768px")
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
        wall_measurements = []

        def ready():
            page.wait_for_function(r"""() => {
                const badge = document.getElementById('tablet-stream-status');
                return badge.classList.contains('status-ok') && /^View \d+\.\d FPS$/.test(badge.textContent);
            }""")
            require(
                page.locator("#tablet-source-status").inner_text() == "Pi frame fresh",
                "the source-freshness pill must remain alongside the view FPS",
            )
            expected_width = 640 if urlsplit(page.url).path == "/fullscreen" else 800
            page.wait_for_function(
                "width => document.querySelector('#tablet-stream-frame img').naturalWidth === width",
                arg=expected_width,
            )
            page.locator("#home-unfamiliar").wait_for(state="visible" if fixture["unfamiliar"] else "hidden")
            require(
                page.locator("#tablet-timeline-list .tablet-timeline-row").count() == 2, "recognised arrivals missing"
            )

        def wall_geometry(fps_number=None):
            return page.evaluate("""fpsNumber => {
              const badge = document.getElementById('tablet-stream-status');
              const number = badge.querySelector('.view-fps-value');
              const originalNumber = number.textContent;
              if (fpsNumber) number.textContent = fpsNumber;
              const bounds = box => ({
                x: box.x, y: box.y, width: box.width, height: box.height, bottom: box.bottom
              });
              const rect = selector => {
                const element = document.querySelector(selector);
                return bounds(element.getBoundingClientRect());
              };
              const textRect = text => {
                const node = Array.from(number.parentNode.childNodes).find(child =>
                  child.nodeType === Node.TEXT_NODE && child.textContent.includes(text));
                const range = document.createRange();
                range.selectNodeContents(node);
                return bounds(range.getBoundingClientRect());
              };
              const panels = Array.from(document.querySelectorAll(
                '#tab-home, .tablet-grid, .tablet-grid > .card, .tablet-timeline-list'
              ));
              const contents = Array.from(document.querySelectorAll(
                '.tablet-grid > .card > *, .tablet-timeline-row, .home-health'
              )).filter(element => element.getClientRects().length);
              const pills = Array.from(document.querySelector('.home-health').children)
                .map(element => element.getBoundingClientRect());
              const geometry = {
                documentHeight: document.documentElement.scrollHeight,
                viewportHeight: innerHeight,
                scrollY,
                button: rect('#open-gate-tablet'),
                camera: rect('#tablet-stream-frame'),
                fpsBadge: rect('#tablet-stream-status'),
                viewText: textRect('View'),
                fpsText: textRect('FPS'),
                healthPillsInOneRow: pills.length === 3 && pills.every(box =>
                  Math.abs((box.top + box.bottom) / 2 - (pills[0].top + pills[0].bottom) / 2) <= 1),
                panelsFit: panels.every(element => {
                  const box = element.getBoundingClientRect();
                  const style = getComputedStyle(element);
                  return box.top >= 0 && box.bottom <= innerHeight + 1 &&
                    element.scrollHeight <= element.clientHeight + 1 &&
                    element.scrollWidth <= element.clientWidth + 1 &&
                    !['hidden', 'clip', 'scroll', 'auto'].includes(style.overflowY);
                }),
                contentsFit: contents.every(element => {
                  const box = element.getBoundingClientRect();
                  const card = element.closest('.card').getBoundingClientRect();
                  return box.top >= card.top && box.bottom <= card.bottom + 1 &&
                    box.left >= card.left - 1 && box.right <= card.right + 1;
                }),
              };
              if (fpsNumber) number.textContent = originalNumber;
              return geometry;
            }""", fps_number)

        def check_wall_layout():
            size = page.viewport_size
            path = urlsplit(page.url).path
            if path != "/fullscreen" and size["width"] < 900:
                return None
            geometry = wall_geometry()
            require(geometry["documentHeight"] <= geometry["viewportHeight"], "wall Home document must not scroll")
            require(geometry["scrollY"] == 0, "wall Home must remain at the top of its viewport")
            require(geometry["panelsFit"], "wall panels must fit without scrolling or hiding overflow")
            require(geometry["contentsFit"], "wall content extends outside its card")
            require(geometry["healthPillsInOneRow"], "wall status pills must remain on one row")
            # Cross the digit-count boundary without changing the stream or
            # waiting for luck; only the number itself should move or change.
            lower_rate = wall_geometry("9.8")
            higher_rate = wall_geometry("10.1")
            for rate in (lower_rate, higher_rate):
                require(rate["healthPillsInOneRow"], "FPS changes must not wrap the wall status pills")
                require(rate["contentsFit"], "the FPS pill must fit inside the live-view card")
                require(
                    rate["camera"] == geometry["camera"] and rate["button"] == geometry["button"],
                    "the FPS digit count must not move the camera or shrink the gate target",
                )
            require(
                all(lower_rate[key] == higher_rate[key] for key in ("fpsBadge", "viewText", "fpsText")),
                "changing 9.8 to 10.1 FPS must not move View, FPS, or the status pill",
            )
            decision = page.locator("#home-decision .decision-summary")
            if decision.count():
                require(
                    decision.evaluate("row => row.getBoundingClientRect().height <= 36"),
                    "the latest-check summary must stay a compact row on wall screens",
                )
            minimum = WALL_BUTTON_MINIMA.get((size["width"], size["height"], path))
            if minimum:
                require(geometry["button"]["width"] >= minimum[0], "wall gate target became narrower")
                require(geometry["button"]["height"] >= minimum[1], "wall gate target became shorter")
            return geometry

        def screenshot(name):
            require(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "horizontal overflow")
            if urlsplit(page.url).path in {"/", "/fullscreen"}:
                check_wall_layout()
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
            (1024, 704, "/", "ipad-safari-banner"),
            (1024, 680, "/", "ipad-safari-tall-banner"),
            (800, 480, "/fullscreen", "kiosk-small"),
            (1024, 600, "/fullscreen", "kiosk-wide"),
            (1280, 800, "/fullscreen", "kiosk-large"),
        ):
            page.set_viewport_size({"width": width, "height": height})
            page.goto("http://gate.test" + path, wait_until="domcontentloaded")
            ready()
            page.evaluate("document.fonts.ready")
            for theme in ("light", "dark"):
                page.evaluate("theme => document.body.classList.toggle('theme-dark', theme === 'dark')", theme)
                screenshot("home-" + name + "-" + theme + ".png")
            geometry = check_wall_layout()
            if geometry:
                wall_measurements.append({"viewport": [width, height], "path": path, **geometry})
                for selector, disclosure in (
                    (".home-health-details summary", "#tablet-system-status"),
                    ("#home-decision summary", "#home-decision .decision-note"),
                ):
                    page.locator(selector).click()
                    box = page.locator(disclosure).bounding_box()
                    require(
                        box is not None and box["y"] >= 0 and box["y"] + box["height"] <= height,
                        "wall diagnostics must open inside the viewport",
                    )
                    expanded = check_wall_layout()
                    require(
                        expanded["camera"] == geometry["camera"] and expanded["button"] == geometry["button"],
                        "diagnostics must overlay without moving the camera or gate target",
                    )
                    page.locator(selector).click()
                fixture["unfamiliar"] = False
                page.evaluate("state.homeStatus.unfamiliar = []; renderHomeArrivals()")
                screenshot("home-" + name + "-usual.png")
                fixture["unfamiliar"] = True
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
        for width, height, path in (
            (1024, 704, "/"),
            (1024, 680, "/"),
            (800, 480, "/fullscreen"),
            (390, 664, "/"),
        ):
            page.set_viewport_size({"width": width, "height": height})
            page.goto("http://gate.test" + path, wait_until="domcontentloaded")
            ready()
            require(
                page.locator("#tablet-stream-frame").bounding_box()["height"] >= 100,
                "legacy image sizing fallback collapsed the camera",
            )
            # Force summary's block rendering, as in iOS 12: the child must
            # supply flex layout rather than relying on summary doing so.
            page.add_style_tag(content=".home-decision summary { display: block !important; }")
            screenshot("home-legacy-layout-" + str(width) + "x" + str(height) + ".png")
        fixture["legacy_layout"] = False
        # Home's viewport contract must not constrain other tabs. Returning
        # after scrolling Stats must restore a genuinely unscrolled Home.
        page.set_viewport_size({"width": 1024, "height": 748})
        page.goto("http://gate.test/", wait_until="domcontentloaded")
        ready()
        page.locator(".tab[data-tab=stats]").click()
        page.wait_for_function("document.getElementById('stat-recognised').textContent === '10'")
        require(
            not page.locator("body").evaluate("body => body.classList.contains('home-active')"),
            "Home viewport sizing leaked into Stats",
        )
        page.locator("#tab-stats details").first.click()
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        require(page.evaluate("scrollY > 0"), "Stats must remain scrollable")
        page.locator(".brand").click()
        ready()
        check_wall_layout()
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
    print(json.dumps({"ok": True, "wall_layouts": wall_measurements, **counters}))


if __name__ == "__main__":
    main()
