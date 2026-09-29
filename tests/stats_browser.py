#!/usr/bin/env python3
"""Optional Stats browser checks using local assets and synthetic intercepted APIs.

Run: .venv/bin/python tests/stats_browser.py [--output-dir /tmp/gate-ui-tightened]
Requires Playwright and Google Chrome. No request reaches a real gate or server.
"""

import argparse
import json
import mimetypes
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

STATIC = Path(__file__).resolve().parents[1] / "files" / "web" / "static"


def require(condition, label):
    if not condition:
        raise AssertionError(label)


def activity(period):
    recognised = [8, 13, 0, 17, 11, 14, 8, 21, 12, 8]
    unmatched = [3, 4, 0, 5, 4, 5, 3, 7, 4, 5]
    manual = [1, 1, 0, 2, 0, 1, 0, 1, 1, 1]
    start = datetime(2026, 9, 20)
    if period == "24h":
        rec, missed, commands = 8, 5, 1
        timeseries = {
            "bucket": "hour",
            "start": "2026-09-28 21:00:00",
            "end": "2026-09-29 21:00:00",
            "series": [
                {"t": "2026-09-29 " + hour, "v": count}
                for hour, count in (("09:00", 4), ("12:00", 2), ("17:00", 7), ("21:00", 1))
            ],
        }
    else:
        offset = 3 if period == "7d" else 0
        rec, missed, commands = sum(recognised[offset:]), sum(unmatched[offset:]), sum(manual[offset:])
        timeseries = {
            "bucket": "day",
            "start": "2026-09-01 21:00:00"
            if period == "30d"
            else (start + timedelta(days=offset)).strftime("%Y-%m-%d 00:00:00"),
            "end": "2026-09-29 21:00:00",
            "series": [
                {
                    "t": (start + timedelta(days=index)).strftime("%Y-%m-%d"),
                    "v": recognised[index] + unmatched[index] + manual[index],
                }
                for index in range(offset, 10)
            ],
        }
    stats = {
        "counts": {"recognised": rec, "unmatched": missed, "candidate": 0, "manual_open": commands},
        "timeseries": timeseries,
    }

    def ranking(total, plates):
        # Synthetic ranking counts sum to the displayed category total.
        first = round(total * 0.38)
        second = round(total * 0.29)
        third = round(total * 0.20)
        return [
            {"plate": plate, "count": count}
            for plate, count in zip(plates, (first, second, third, total - first - second - third))
        ]

    insights = {
        "insights": {
            "top_recognised_list": ranking(rec, ["DEMO123", "TEST456", "SAMPLE7", "EXAMPLE8"]),
            "top_unmatched_list": ranking(missed, ["VISITOR1", "DEMO999", "TEST321", "UNKNOWN"]),
            "avg_processing_ms": {"overall": 318},
            "no_plate": ranking(missed, ["A", "B", "C", "D"])[-1]["count"],
        }
    }
    return stats, insights


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    fixture = {"activity_error": False, "health_error": False}
    requests = []
    counters = {"blocked_writes": 0, "unexpected_requests": 0, "page_errors": 0}

    def intercept(route):
        request = route.request
        parsed = urlsplit(request.url)
        path = parsed.path
        if parsed.hostname != "gate.test":
            counters["unexpected_requests"] += 1
            route.abort()
            return
        if request.method not in {"GET", "HEAD"}:
            counters["blocked_writes"] += 1
            route.abort()
            return
        if path in {"/api/stats", "/api/stats/insights"}:
            period = parse_qs(parsed.query).get("window", ["24h"])[0]
            requests.append((path, period))
            if fixture["activity_error"]:
                route.fulfill(status=503, json={"error": "Synthetic unavailable response"})
            else:
                route.fulfill(json=activity(period)[0 if path == "/api/stats" else 1])
            return
        if path == "/api/service-health":
            if fixture["health_error"]:
                route.fulfill(status=503, json={"error": "Synthetic unavailable response"})
            else:
                route.fulfill(
                    json={
                        "temperature_c": 58.7,
                        "disk": {"free_pct": 68, "free_bytes": 48 * 1073741824},
                        "maintenance": {"last_success": "2026-09-29 03:00:00", "age_hours": 18, "stale": False},
                        "services": {"gate_anpr_web": "active", "gate_anpr": "active", "alprd": "active"},
                        "failed_units": [],
                    }
                )
            return
        defaults = {
            "/api/config": {"stream_fps": 5, "group_window_sec": 60},
            "/api/ui-settings": {"theme_mode": "light"},
            "/api/events": [],
            "/api/gate-cooldown": {"remaining": 0},
            "/api/gate-last-open": {"last_open_ts": None},
        }
        if path in defaults:
            route.fulfill(json=defaults[path])
            return
        if path.startswith("/api/"):
            counters["unexpected_requests"] += 1
            route.abort()
            return
        local = STATIC / ("index.html" if path == "/" else path.removeprefix("/static/").lstrip("/"))
        if local.is_file() and local.resolve().is_relative_to(STATIC.resolve()):
            content = local.read_bytes().replace(b"__GATE_API_SHARED_SECRET__", b"synthetic-test-secret")
            route.fulfill(body=content, content_type=mimetypes.guess_type(local.name)[0] or "application/octet-stream")
        else:
            route.fulfill(status=404, body="")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1366, "height": 900}, timezone_id="Pacific/Honolulu", service_workers="block"
        )
        context.route("**/*", intercept)
        context.add_init_script("""
            const OriginalDate = Date;
            window.Date = class extends OriginalDate {
                constructor(...args) { super(...(args.length ? args : ['2020-01-01T00:00:00Z'])); }
                static now() { return 1577836800000; }
            };
        """)
        page = context.new_page()
        page.on("pageerror", lambda error: counters.__setitem__("page_errors", counters["page_errors"] + 1))
        page.set_default_timeout(10000)

        def loaded(period):
            expected = activity(period)[0]["counts"]
            page.wait_for_function(
                "expected => document.getElementById('stat-recognised').textContent === String(expected)",
                arg=expected["recognised"],
            )
            require(page.locator("#stats-error").is_hidden(), "valid Stats response left an error visible")
            require(page.locator("#stats-chart .stats-bar").count() > 0, "activity bars missing")
            require(page.locator("#stats-chart .stats-axis").count() >= 5, "chart count/time axes missing")
            require(
                page.locator("#stats-recognised-list .stats-ranking-row").count() == 4, "ranked registrations missing"
            )

        def select(period):
            page.locator('#tab-stats .chip[data-window="' + period + '"]').click()
            loaded(period)

        def screenshot(name):
            require(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "horizontal Stats overflow")
            require(page.locator("#stats-chart").bounding_box()["width"] > 200, "chart collapsed")
            if args.output_dir:
                args.output_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(args.output_dir / name), full_page=True)

        page.goto("http://gate.test/#stats", wait_until="domcontentloaded")
        loaded("24h")
        before = len(requests)
        page.locator("#stats-chart .stats-bar:not(.stats-bar-empty)").first.click()
        require(
            page.locator("#stats-chart-reading").inner_text() == "29 Sep, 09:00 · 4 recorded events",
            "chart labels changed with client clock or timezone",
        )
        require(len(requests) == before, "reading a bar unexpectedly fetched activity")
        page.locator("#stats-chart .stats-bar:not(.stats-bar-empty)").first.focus()
        page.keyboard.press("Enter")
        require(len(requests) == before, "keyboard bar selection unexpectedly fetched activity")
        page.set_viewport_size({"width": 390, "height": 844})
        require(len(requests) == before, "resizing unexpectedly fetched activity")
        loaded("24h")
        fixture["health_error"] = True
        select("7d")
        page.wait_for_function("document.getElementById('stats-health-summary').textContent === 'Status unavailable'")
        require(page.locator("#stat-recognised").inner_text() != "—", "health failure hid valid activity")
        fixture["activity_error"] = True
        page.locator('#tab-stats .chip[data-window="30d"]').click()
        page.locator("#stats-error").wait_for(state="visible")
        require(page.locator("#stat-recognised").inner_text() == "—", "failed period retained old totals")
        require(page.locator("#stats-chart .stats-bar").count() == 0, "failed period retained old bars")
        fixture.update(activity_error=False, health_error=False)
        select("30d")
        select("all")
        select("7d")
        for width, height, name in (
            (1366, 900, "desktop"),
            (1024, 748, "ipad"),
            (390, 844, "phone"),
            (320, 568, "small-phone"),
        ):
            page.set_viewport_size({"width": width, "height": height})
            page.reload(wait_until="domcontentloaded")
            loaded("24h")
            select("7d")
            page.evaluate("document.fonts.ready")
            before = len(requests)
            for theme in ("light", "dark"):
                page.evaluate("theme => document.body.classList.toggle('theme-dark', theme === 'dark')", theme)
                page.evaluate(
                    "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                )
                screenshot("stats-" + name + "-" + theme + ".png")
            require(len(requests) == before, "layout/theme change fetched activity")
        browser.close()
    require(
        counters == {"blocked_writes": 0, "unexpected_requests": 0, "page_errors": 0},
        "unexpected request or browser error",
    )
    print(json.dumps({"ok": True, "viewport_sizes": 4, "themes": 2, **counters}))


if __name__ == "__main__":
    main()
