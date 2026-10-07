#!/usr/bin/env python3
"""Optional, read-only browser smoke check against an explicitly chosen server.

Install Playwright into the test environment and install Google Chrome, then run:
    .venv/bin/python tests/browser_smoke.py --base-url http://HOST
    .venv/bin/python tests/browser_smoke.py --base-url http://HOST --ipad --throttle

This is a manual integration check, not part of pytest. The iPad option uses an
iOS 12 user agent and tablet viewport in Chrome; it does not emulate old WebKit.
Throttling applies 4x CPU slowdown, 50 ms latency and 1.25 MB/s in each direction.
Non-GET/HEAD requests are blocked at the browser context. Only navigation and
history filters are clicked. Output contains counts, timings and status only.
"""

import argparse
import json
import time
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

IPAD_USER_AGENT = (
    "Mozilla/5.0 (iPad; CPU OS 12_5_8 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/12.1.2 Mobile/15E148 Safari/604.1"
)


class SmokeFailure(Exception):
    """A fixed, nonsensitive check label safe to include in the final report."""


def require(condition, label):
    if not condition:
        raise SmokeFailure(label)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True, help="Explicit HTTP(S) origin to check")
    parser.add_argument("--ipad", action="store_true", help="Exercise the iOS 12 user-agent path in Chrome")
    parser.add_argument("--throttle", action="store_true", help="Apply 4x CPU and slower network conditions")
    args = parser.parse_args()
    parsed = urlsplit(args.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        parser.error("--base-url must be an HTTP(S) origin without embedded credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        parser.error("--base-url must contain only an origin, without a path, query or fragment")
    base_url = args.base_url.rstrip("/")
    summary = {"ok": False, "ipad_user_agent": args.ipad, "throttled": args.throttle}
    stage = "browser_start"
    counts = {"blocked_writes": 0, "original_requests": 0, "preview_requests": 0, "page_errors": 0}
    requests_by_path = {}
    history_times = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            options = {"viewport": {"width": 1024, "height": 768}, "service_workers": "block"}
            if args.ipad:
                options.update(user_agent=IPAD_USER_AGENT, has_touch=True, device_scale_factor=2)
            context = browser.new_context(**options)

            def guard(route):
                if route.request.method not in {"GET", "HEAD"}:
                    counts["blocked_writes"] += 1
                    route.abort("blockedbyclient")
                else:
                    route.continue_()

            def record_request(request):
                path = urlsplit(request.url).path
                requests_by_path[path] = requests_by_path.get(path, 0) + 1
                if path.startswith("/images/"):
                    counts["original_requests"] += 1
                elif path.startswith("/previews/"):
                    counts["preview_requests"] += 1

            context.route("**/*", guard)
            context.on("request", record_request)
            context.add_init_script("""
                window.__gateSmoke = { homeLoads: 0 };
                document.addEventListener('load', function(event) {
                  const image = event.target;
                  if (image && image.tagName === 'IMG' && image.closest('#tablet-stream-frame')) {
                    window.__gateSmoke.homeLoads += 1;
                  }
                }, true);
            """)
            page = context.new_page()
            page.set_default_timeout(30000)
            page.on("pageerror", lambda error: counts.__setitem__("page_errors", counts["page_errors"] + 1))
            if args.throttle:
                session = context.new_cdp_session(page)
                session.send("Emulation.setCPUThrottlingRate", {"rate": 4})
                session.send("Network.enable")
                session.send(
                    "Network.emulateNetworkConditions",
                    {
                        "offline": False,
                        "latency": 50,
                        "downloadThroughput": 1_250_000,
                        "uploadThroughput": 1_250_000,
                    },
                )

            stage = "homepage_stream"
            started = time.monotonic()
            response = page.goto(base_url + "/", wait_until="domcontentloaded")
            require(response is not None and response.ok, "homepage_http_status")
            page.wait_for_function("""() => {
                const image = document.querySelector('#tablet-stream-frame img');
                return image && image.naturalWidth > 0 && window.__gateSmoke.homeLoads > 0;
            }""")
            summary["first_frame_ms"] = round((time.monotonic() - started) * 1000)
            if args.ipad:
                require(page.evaluate("typeof LEGACY_IOS !== 'undefined' && LEGACY_IOS"), "legacy_user_agent_detection")
            stream_path = page.locator("#tablet-stream-frame img").evaluate("image => new URL(image.src).pathname")
            stage = "home_status"
            page.wait_for_function(r"""() => {
                const badge = document.getElementById('tablet-stream-status');
                return state.homeStatus && state.homeFrame && state.homeFrame.loadedAt !== null &&
                    badge.classList.contains('status-ok') && /^View \d+\.\d FPS$/.test(badge.textContent);
            }
            """)
            home = page.evaluate("""() => ({
                arrivals: document.querySelectorAll('#tablet-timeline-list .tablet-timeline-row').length,
                unfamiliar: document.querySelectorAll('#tablet-timeline-list .arrival-kind').length,
                previewOnly: Array.from(document.querySelectorAll('#tablet-timeline-list img')).every(image =>
                    (image.getAttribute('src') || '').startsWith('/previews/'))
            })""")
            require(home["arrivals"] <= 2 and home["unfamiliar"] <= home["arrivals"], "home_arrivals_bounded")
            require(home["previewOnly"], "home_arrival_preview_urls")
            summary.update(
                home_status_checked=True,
                home_arrival_count=home["arrivals"],
                home_known_count=home["arrivals"] - home["unfamiliar"],
                home_unfamiliar_count=home["unfamiliar"],
            )
            stage = "homepage_stream"
            before = page.evaluate("window.__gateSmoke.homeLoads")
            sample_start = time.monotonic()
            page.wait_for_timeout(8000)
            elapsed = time.monotonic() - sample_start
            home_loads = page.evaluate("window.__gateSmoke.homeLoads") - before
            require(home_loads > 0, "stream_keeps_refreshing")
            summary.update(home_loads_8s=home_loads, home_load_fps=round(home_loads / elapsed, 2))

            def wait_history():
                page.wait_for_function(
                    "typeof state !== 'undefined' && state.eventsLoaded && !state.loadingCandidateEvents"
                )
                bounded = page.evaluate("""() => ({
                    events: state.events.length,
                    cards: document.querySelectorAll('#events-list .event-card').length,
                    page: state.eventsPage,
                    previewUrls: Array.from(document.querySelectorAll('#events-list img')).every(image => {
                        const value = image.getAttribute('src') || image.dataset.previewSrc || '';
                        return value.startsWith('/previews/');
                    })
                })""")
                require(bounded["events"] <= 30 and bounded["cards"] <= 30, "history_bounded")
                require(bounded["previewUrls"], "history_preview_urls")
                summary["max_history_events"] = max(summary.get("max_history_events", 0), bounded["events"])
                summary["max_history_cards"] = max(summary.get("max_history_cards", 0), bounded["cards"])
                return bounded

            def history_action(action, expected=None):
                start = time.monotonic()
                with page.expect_response(lambda result: urlsplit(result.url).path == "/api/history") as pending:
                    action()
                result = pending.value
                require(result.ok, "history_http_status")
                if expected:
                    query = parse_qs(urlsplit(result.url).query)
                    require(all(query.get(key) == [value] for key, value in expected.items()), "history_filter_request")
                bounded = wait_history()
                history_times.append(round((time.monotonic() - start) * 1000))
                return bounded

            stage = "history_navigation"
            history_action(lambda: page.locator('.tab[data-tab="candidates"]').click())
            history_action(lambda: page.locator("#history-window").select_option("all"), {"window": "all"})
            summary["older_newer_checked"] = False
            if page.locator("#history-next").is_enabled():
                older = history_action(lambda: page.locator("#history-next").click())
                require(older["page"] == 1, "older_page_navigation")
                newer = history_action(lambda: page.locator("#history-prev").click())
                require(newer["page"] == 0, "newer_page_navigation")
                summary["older_newer_checked"] = True

            stage = "history_filters"
            history_action(lambda: page.locator("#history-window").select_option("7d"), {"window": "7d"})
            history_action(lambda: page.locator("#history-window").select_option("all"), {"window": "all"})
            history_action(
                lambda: page.locator('#tab-candidates .chip[data-kind="unmatched"]').click(), {"kind": "recognised"}
            )
            history_action(lambda: page.locator('#tab-candidates .chip[data-kind="unmatched"]').click())
            history_action(
                lambda: page.locator('#tab-candidates .chip[data-kind="recognised"]').click(), {"kind": "unmatched"}
            )
            history_action(lambda: page.locator('#tab-candidates .chip[data-kind="recognised"]').click())
            summary["history_filter_checks"] = 6

            stage = "offscreen_stream_pause"
            # Allow the single request already in flight when the tab changed
            # to finish before checking for any further hidden-stream traffic.
            page.wait_for_timeout(1500)
            paused_requests = requests_by_path.get(stream_path, 0)
            paused_home_requests = requests_by_path.get("/api/home-status", 0)
            paused_loads = page.evaluate("window.__gateSmoke.homeLoads")
            page.wait_for_timeout(2000)
            summary["offscreen_stream_requests_2s"] = requests_by_path.get(stream_path, 0) - paused_requests
            require(summary["offscreen_stream_requests_2s"] == 0, "offscreen_stream_requests")
            require(page.evaluate("window.__gateSmoke.homeLoads") == paused_loads, "offscreen_stream_loads")
            require(
                requests_by_path.get("/api/home-status", 0) == paused_home_requests, "offscreen_home_status_requests"
            )

            stage = "live_diagnostics"
            # The homepage frame above proves initTabs has finished before
            # clicking Live; an immediate click during startup has no handler.
            page.locator('.tab[data-tab="stream"]').click()
            page.wait_for_function("""() => {
                const frame = document.querySelector('#stream-frame img');
                const lag = document.getElementById('stream-lag');
                return isTabActive('stream') && frame && frame.naturalWidth > 0 &&
                    lag && /^Lag: [0-9]+\\.[0-9]s$/.test(lag.textContent) &&
                    document.getElementById('stream-health').textContent === 'Health: OK';
            }""")
            require(requests_by_path.get("/api/stream-lag", 0) > 0, "live_lag_request_missing")
            summary["live_diagnostics_checked"] = True

            stage = "health"
            health = page.evaluate("""async () => {
                const response = await fetch('/api/healthz');
                const result = await response.json();
                return { status: response.status, ok: result.ok === true,
                    fresh: result.stream && result.stream.fresh === true };
            }""")
            require(health["status"] == 200 and health["ok"] and health["fresh"], "health_not_ready")
            require(counts["original_requests"] == 0, "eager_original_requests")
            require(counts["page_errors"] == 0, "javascript_page_errors")
            require(counts["blocked_writes"] == 0, "unexpected_mutating_requests")
            summary.update(ok=True, health_status=health["status"], health_fresh=health["fresh"])
            context.close()
            browser.close()
    except Exception as error:
        # Browser errors may contain URLs, response content or page text. Keep
        # the integration report safe to paste without printing those details.
        summary["failed_stage"] = stage
        summary["failure"] = str(error) if isinstance(error, SmokeFailure) else type(error).__name__
    summary.update(counts)
    summary["history_load_ms"] = history_times
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
