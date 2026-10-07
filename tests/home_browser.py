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
# Keep a generous gate target without growing below the camera/sightings/stats
# stack. Width minima preserve the existing wall layout; height has a 240px floor.
WALL_BUTTON_MINIMA = {
    (1024, 748, "/"): (216, 240),
    (1024, 704, "/"): (216, 240),
    (1024, 680, "/"): (216, 240),
    (1440, 1000, "/"): (216, 240),
    (800, 480, "/fullscreen"): (195, 240),
    (1024, 600, "/fullscreen"): (256, 240),
    (1280, 800, "/fullscreen"): (326, 240),
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
        "metrics_missing": False,
        "arrival_case": "unmatched_latest",
        "legacy_layout": False,
        "save": "validation",
        "plates": [{"owner": "Example household", "plates": ["TEST123"]}],
    }
    counters = {"blocked_gate_requests": 0, "unexpected_requests": 0, "page_errors": 0}

    def event(event_id, plate, kind, age=30):
        now = time.time()
        return {
            "id": event_id,
            "plate": plate,
            "owner": "Example household" if kind == "recognised" else "",
            "kind": kind,
            "processing_time_ms": None if fixture["metrics_missing"] else (184 if age == 30 else 250),
            "confidence": None if fixture["metrics_missing"] else (92.4 if age == 30 else 88.0),
            "seen_at": now - age,
            "captured_at": datetime.fromtimestamp(now - age).strftime("%Y-%m-%d %H:%M:%S"),
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

    def arrival_specs():
        return {
            "unmatched_latest": [(3, "NEW789", "unmatched", 30), (2, "TEST123", "recognised", 120)],
            "matched_latest": [(2, "TEST123", "recognised", 30), (1, "DEMO456", "recognised", 120)],
            "no_matches": [(3, "NEW789", "unmatched", 30), (1, "OTHER123", "unmatched", 120)],
        }[fixture["arrival_case"]]

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
                    "arrivals": [event(*spec) for spec in arrival_specs()],
                    "metrics": {} if fixture["metrics_missing"] else {"temperature_c": 58.2, "disk_free_pct": 73},
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
            page.wait_for_function(
                """plates => {
                  const rows = Array.from(document.querySelectorAll('#tablet-timeline-list .tablet-timeline-row'));
                  return rows.length === plates.length && rows.every((row, index) =>
                    row.querySelector('.plate')?.textContent.includes(plates[index]));
                }""",
                arg=[spec[1] for spec in arrival_specs()],
            )

        def check_arrival_layout():
            size = page.viewport_size
            wall = size["width"] >= (721 if urlsplit(page.url).path == "/fullscreen" else 900)
            rows = page.evaluate(r"""() => Array.from(document.querySelectorAll(
              '#tablet-timeline-list .tablet-timeline-row'
            )).map(row => {
              const box = row.getBoundingClientRect();
              const visible = element => element.getClientRects().length > 0 &&
                getComputedStyle(element).visibility !== 'hidden';
              return {
                x: box.x, y: box.y, right: box.right, bottom: box.bottom,
                label: row.querySelector('.plate').textContent,
                kind: row.querySelector('.arrival-kind')?.textContent || '',
                relativeAges: Array.from(row.querySelectorAll('[data-tablet-relative]'))
                  .filter(visible).map(element => element.textContent.trim()),
                absoluteTimeVisible: /\b(?:Today|Yesterday)\b|\b\d{1,2}:\d{2}\b/i.test(row.innerText),
                timestampInTitle: [row, ...row.querySelectorAll('[title]')]
                  .some(element => /\b\d{1,2}:\d{2}\b/.test(element.title)),
              };
            })""")
            require(len(rows) == 2, "Latest seen must contain exactly two capture records")
            require(
                page.locator(".tablet-timeline h2").inner_text() == "Latest seen",
                "the sightings heading must include all kinds",
            )
            require(
                page.locator("#home-unfamiliar, #home-decision, #home-arrival-footer").count() == 0,
                "Home must not duplicate captures in separate unfamiliar or latest-check sections",
            )
            for row, spec in zip(rows, arrival_specs()):
                require(spec[1] in row["label"], "Latest seen records must preserve API order")
                require(
                    row["kind"].strip() == ("Unfamiliar" if spec[2] == "unmatched" else ""),
                    "only unmatched records must have an Unfamiliar label",
                )
                require(
                    len(row["relativeAges"]) == 1 and bool(row["relativeAges"][0]),
                    "each capture must show its relative age once",
                )
                require(not row["absoluteTimeVisible"], "captures must not repeat an absolute time")
                require(row["timestampInTitle"], "capture time must remain available in a title")
            first, second = rows
            if wall:
                require(
                    abs(first["y"] - second["y"]) <= 1 and first["right"] <= second["x"] + 1,
                    "wall Latest seen records must share one row side by side",
                )
            else:
                require(
                    abs(first["x"] - second["x"]) <= 1 and first["bottom"] <= second["y"] + 1,
                    "narrow-screen Latest seen records must remain vertically stacked",
                )

        def check_metric_values(live_unavailable=False):
            expected = ("58.2°C", "184 ms", "92.4%", "73%")
            if fixture["metrics_missing"]:
                expected = ("—", "—", "—", "—")
            elif live_unavailable:
                expected = ("—", "184 ms", "92.4%", "—")
            for selector, value in zip(
                ("#home-cpu-temp", "#home-processing", "#home-confidence", "#home-disk-free"), expected
            ):
                require(page.locator(selector).inner_text() == value, selector + " must show the corresponding metric")
            if live_unavailable:
                for selector in ("#home-cpu-temp", "#home-disk-free"):
                    require(
                        "unavailable" in (page.locator(selector).get_attribute("title") or "").lower(),
                        "unavailable live metrics must identify their missing update",
                    )

        def check_metrics_layout():
            size = page.viewport_size
            wall = size["width"] >= (721 if urlsplit(page.url).path == "/fullscreen" else 900)
            require(
                page.locator(".tablet-grid > .home-metrics").count() == 1,
                "the four metrics must share one section in the Home grid",
            )
            geometry = page.evaluate("""() => {
              const section = document.querySelector('.home-metrics');
              const bounds = element => {
                const box = element.getBoundingClientRect();
                return { x: box.x, y: box.y, right: box.right, bottom: box.bottom, height: box.height };
              };
              return {
                section: bounds(section),
                timeline: bounds(document.querySelector('.tablet-timeline')),
                metrics: Array.from(section.querySelectorAll('.home-metric')).map(element => ({
                  ...bounds(element),
                  label: element.querySelector('.home-metric-label').textContent.trim(),
                  labelBounds: bounds(element.querySelector('.home-metric-label')),
                  valueBounds: bounds(element.querySelector('strong')),
                })),
              };
            }""")
            metrics = geometry["metrics"]
            section = geometry["section"]
            require(len(metrics) == 4, "the Home metric section must contain exactly four values")
            require(geometry["timeline"]["bottom"] <= section["y"] + 1, "metrics must appear below Latest seen")
            require(
                all(metric["label"] for metric in metrics)
                and all(metrics[index]["label"].lower().startswith("last") for index in (1, 2)),
                "capture metrics must be labeled as values from the last capture",
            )
            require(
                all(
                    metric["x"] >= section["x"] - 1
                    and metric["right"] <= section["right"] + 1
                    and metric["y"] >= section["y"] - 1
                    and metric["bottom"] <= section["bottom"] + 1
                    for metric in metrics
                ),
                "metric values must stay inside their section",
            )
            require(
                all(
                    child["x"] >= metric["x"] - 1
                    and child["right"] <= metric["right"] + 1
                    and child["y"] >= metric["y"] - 1
                    and child["bottom"] <= metric["bottom"] + 1
                    for metric in metrics
                    for child in (metric["labelBounds"], metric["valueBounds"])
                ),
                "each metric label and value must fit inside their own cell",
            )
            require(
                all(0 <= metric["valueBounds"]["x"] - metric["labelBounds"]["right"] <= 8 for metric in metrics)
                and all(
                    abs(metric["y"] - following["y"]) > 1
                    or following["labelBounds"]["x"] - metric["valueBounds"]["right"]
                    > metric["valueBounds"]["x"] - metric["labelBounds"]["right"]
                    for metric, following in zip(metrics, metrics[1:])
                ),
                "each metric value must be closer to its own label than to the next label",
            )
            if wall:
                require(section["height"] <= 44, "wall metrics must occupy a compact strip")
                require(
                    all(abs(metric["y"] - metrics[0]["y"]) <= 1 for metric in metrics)
                    and all(metrics[index]["right"] <= metrics[index + 1]["x"] + 1 for index in range(3)),
                    "wall metrics must share one horizontal row",
                )
            else:
                require(
                    abs(metrics[0]["y"] - metrics[1]["y"]) <= 1
                    and abs(metrics[2]["y"] - metrics[3]["y"]) <= 1
                    and metrics[0]["right"] <= metrics[1]["x"] + 1
                    and metrics[2]["right"] <= metrics[3]["x"] + 1
                    and metrics[0]["bottom"] <= metrics[2]["y"] + 1
                    and abs(metrics[0]["x"] - metrics[2]["x"]) <= 1
                    and abs(metrics[1]["x"] - metrics[3]["x"]) <= 1,
                    "phone metrics must form two columns and two rows",
                )
            check_metric_values()

        def wall_geometry(fps_number=None):
            return page.evaluate(
                """fpsNumber => {
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
              const image = document.querySelector('#tablet-stream-frame img');
              const imageBox = image.getBoundingClientRect();
              // natural dimensions can briefly clear while the next JPEG
              // loads; the last completed frame still defines the feed.
              const sourceWidth = image.naturalWidth || state.homeFrame.width;
              const sourceHeight = image.naturalHeight || state.homeFrame.height;
              const imageScale = Math.min(
                imageBox.width / sourceWidth, imageBox.height / sourceHeight
              );
              const card = document.querySelector('.tablet-stream');
              const cardBox = card.getBoundingClientRect();
              const cardStyle = getComputedStyle(card);
              const heading = card.querySelector('.card-head');
              const headingBox = heading.getBoundingClientRect();
              const headingStyle = getComputedStyle(heading);
              const frame = document.querySelector('#tablet-stream-frame');
              const frameBox = frame.getBoundingClientRect();
              const frameStyle = getComputedStyle(frame);
              const pixels = value => parseFloat(value) || 0;
              const verticalPadding = pixels(cardStyle.paddingTop) + pixels(cardStyle.paddingBottom) +
                pixels(cardStyle.borderTopWidth) + pixels(cardStyle.borderBottomWidth);
              const verticalMargins = pixels(headingStyle.marginTop) + pixels(headingStyle.marginBottom) +
                pixels(frameStyle.marginTop) + pixels(frameStyle.marginBottom);
              const geometry = {
                documentHeight: document.documentElement.scrollHeight,
                viewportHeight: innerHeight,
                scrollY,
                button: rect('#open-gate-tablet'),
                gateCard: rect('.tablet-gate'),
                metricsStrip: rect('.home-metrics'),
                camera: rect('#tablet-stream-frame'),
                availableVideoWidth: rect('.tablet-timeline').width -
                  pixels(cardStyle.paddingLeft) - pixels(cardStyle.paddingRight),
                videoContentWidth: sourceWidth * imageScale,
                videoContentHeight: sourceHeight * imageScale,
                cameraAspectRatio: frameBox.width / frameBox.height,
                streamCardHeight: cardBox.height,
                expectedStreamCardHeight: verticalPadding + verticalMargins +
                  headingBox.height + pixels(cardStyle.rowGap) + frameBox.height,
                cameraWhiteSpaceBelow: cardBox.bottom - frameBox.bottom -
                  pixels(cardStyle.paddingBottom) - pixels(cardStyle.borderBottomWidth),
                cameraWhiteSpaceBeside: cardBox.width - frameBox.width -
                  pixels(cardStyle.paddingLeft) - pixels(cardStyle.paddingRight) -
                  pixels(cardStyle.borderLeftWidth) - pixels(cardStyle.borderRightWidth),
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
            }""",
                fps_number,
            )

        def check_wall_layout():
            size = page.viewport_size
            path = urlsplit(page.url).path
            if size["width"] < (721 if path == "/fullscreen" else 900):
                return None
            geometry = wall_geometry()
            require(geometry["documentHeight"] <= geometry["viewportHeight"], "wall Home document must not scroll")
            require(geometry["scrollY"] == 0, "wall Home must remain at the top of its viewport")
            require(geometry["panelsFit"], "wall panels must fit without scrolling or hiding overflow")
            require(geometry["contentsFit"], "wall content extends outside its card")
            require(geometry["healthPillsInOneRow"], "wall status pills must remain on one row")
            require(
                abs(geometry["gateCard"]["bottom"] - geometry["metricsStrip"]["bottom"]) <= 1,
                "wall gate card must end at the bottom of the metrics strip",
            )
            require(
                geometry["button"]["bottom"] <= geometry["metricsStrip"]["bottom"] + 1,
                "wall gate button must not grow below the metrics strip",
            )
            require(
                abs(geometry["cameraAspectRatio"] - 16 / 9) <= 0.01,
                "wall camera frame must follow the video aspect ratio",
            )
            require(
                abs(geometry["videoContentWidth"] - geometry["camera"]["width"]) <= 1
                and abs(geometry["videoContentHeight"] - geometry["camera"]["height"]) <= 1,
                "uncropped camera content must fill its frame without internal gutters",
            )
            require(
                abs(geometry["streamCardHeight"] - geometry["expectedStreamCardHeight"]) <= 1,
                "the live-view card must fit its heading, padding, gap and camera",
            )
            require(
                abs(geometry["cameraWhiteSpaceBelow"]) <= 1,
                "the live-view card must not leave blank space below the camera",
            )
            require(
                abs(geometry["cameraWhiteSpaceBeside"]) <= 1,
                "the live-view card must not leave blank space beside the camera",
            )
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
            minimum = WALL_BUTTON_MINIMA.get((size["width"], size["height"], path))
            if minimum:
                require(geometry["button"]["width"] >= minimum[0], "wall gate target became narrower")
                require(geometry["button"]["height"] >= minimum[1], "wall gate target became shorter")
            if (size["width"], size["height"], path) == (800, 480, "/fullscreen"):
                require(geometry["videoContentWidth"] >= 480, "small Pi video content must render at least 480 px wide")
            if (size["width"], size["height"], path) == (1440, 1000, "/"):
                require(
                    abs(geometry["camera"]["width"] - geometry["availableVideoWidth"]) <= 1,
                    "tall desktop camera must fill the available column width",
                )
                require(
                    geometry["metricsStrip"]["bottom"] <= geometry["viewportHeight"] - 64,
                    "tall desktop surplus height must remain below the content cards",
                )
            return geometry

        def screenshot(name):
            require(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "horizontal overflow")
            if urlsplit(page.url).path in {"/", "/fullscreen"}:
                check_arrival_layout()
                check_metrics_layout()
                check_wall_layout()
            if args.output_dir:
                args.output_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(args.output_dir / name), full_page=True)

        page.goto("http://gate.test/", wait_until="domcontentloaded")
        ready()
        screenshot("home-ipad.png")
        fixture["metrics_missing"] = True
        try:
            page.evaluate("fetchHomeStatus()")
            check_metric_values()
            check_metrics_layout()
            check_wall_layout()
        finally:
            fixture["metrics_missing"] = False
            page.evaluate("fetchHomeStatus()")
        check_metric_values()
        page.locator(".home-health-details summary").click()
        require(page.locator("#tablet-system-status").is_visible(), "status disclosure did not open on tap")
        page.locator(".home-health-details summary").click()
        require(page.locator("#tablet-system-status").is_hidden(), "status details should be collapsed initially")
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
        check_metric_values(live_unavailable=True)
        # Simulate elapsed client time while disconnected without generating
        # hundreds of fake frames or making any real network requests.
        page.evaluate("state.homeStatusReceivedAt -= 301000; renderHomeArrivals()")
        require(page.locator("#home-arrival-status").is_visible(), "disconnected history must show an update warning")
        require(
            "last received" in page.locator("#home-arrival-status").inner_text(),
            "the stale-update warning must explain that received captures remain visible",
        )
        check_arrival_layout()
        fixture.update(api_error=False, service_inactive=False)
        ready()

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
            (1440, 1000, "/", "desktop-tall"),
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
                for selector, disclosure in ((".home-health-details summary", "#tablet-system-status"),):
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
        for width, height, path, name in (
            (1024, 704, "/", "ipad"),
            (800, 480, "/fullscreen", "kiosk-small"),
            (390, 844, "/", "phone"),
        ):
            page.set_viewport_size({"width": width, "height": height})
            for arrival_case in ("matched_latest", "no_matches"):
                fixture["arrival_case"] = arrival_case
                page.goto("http://gate.test" + path, wait_until="domcontentloaded")
                ready()
                screenshot("home-" + name + "-" + arrival_case + ".png")
        fixture["arrival_case"] = "unmatched_latest"
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
        # A failed initial status request must not leave old camera dimensions
        # behind when another tab is resized and Home is reopened.
        fixture["api_error"] = True
        page.evaluate("state.homeStatus = null; state.homeStatusError = true; renderHomeArrivals()")
        page.locator(".tab[data-tab=stats]").click()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.locator(".brand").click()
        require(
            page.evaluate("""() => {
              const frame = document.getElementById('tablet-stream-frame').getBoundingClientRect();
              const timeline = document.querySelector('.tablet-timeline').getBoundingClientRect();
              const style = getComputedStyle(document.querySelector('.tablet-stream'));
              const width = timeline.width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
              return Math.abs(frame.width - width) <= 1 && Math.abs(frame.width / frame.height - 16 / 9) < .01;
            }"""),
            "returning Home without a snapshot must resize the camera to the current column",
        )
        fixture["api_error"] = False
        page.evaluate("fetchHomeStatus()")
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
