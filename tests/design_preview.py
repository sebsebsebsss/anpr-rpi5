#!/usr/bin/env python3
"""Preview the selected wall-screen interface with read-only synthetic fixtures.

Run: python3 tests/design_preview.py [--port 8765]
Open: http://127.0.0.1:8765/

Uses only Python's standard library. Never imports the application, reads local
configuration/history, connects to a Pi or operates hardware. Every write is
rejected. The server intentionally binds only to the local loopback address.
"""

import argparse
import json
import mimetypes
import re
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "files" / "web" / "static"
ALTERNATIVE = ROOT / "docs" / "design" / "alternative.html"
PI_TIMEZONE = ZoneInfo("Europe/London")
STARTED_AT = time.time()
SERVICES = dict.fromkeys(("gate_anpr_web", "gate_anpr", "alprd", "stream_jpeg", "beanstalkd"), "active")
PUBLIC_ASSETS = {
    "/app.js": STATIC / "app.js",
    "/styles.css": STATIC / "styles.css",
    "/stats.css": STATIC / "stats.css",
    "/fonts/space-grotesk.woff2": STATIC / "fonts" / "space-grotesk.woff2",
    "/static/icons/icon-192.png": STATIC / "icons" / "icon-192.png",
    "/static/icons/icon-512.png": STATIC / "icons" / "icon-512.png",
}

LANDING = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Gate design previews</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#131813;color:#edf2e9;font:16px/1.6 system-ui,sans-serif}
main{max-width:820px;margin:10vh auto;padding:28px}h1{font-size:36px;line-height:1.1;letter-spacing:-1px}
p{color:#a8b3a2}small{color:#b6e2a6;letter-spacing:.08em}section{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:34px 0}
article{background:#1b241b;border:1px solid #344233;padding:25px;border-radius:14px}h2{font-size:19px;margin:0 0 10px}
a{color:#bdedaa}article>a:first-of-type{display:inline-block;padding:10px 15px;border-radius:7px;background:#b6e2a6;color:#173013;font-weight:600;text-decoration:none}
.secondary{margin-left:12px;font-size:13px}.note{font-size:13px}@media(max-width:620px){main{margin:25px auto;padding:22px}section{grid-template-columns:1fr}h1{font-size:30px}}
</style></head><body><main><small>LOCAL DESIGN PREVIEW</small><h1>The wall screens come first.</h1>
<p>The tightened existing interface preserves the large gate button and single-screen layouts. Resize your browser to check the dedicated displays and the separate phone layout.</p>
<section><article><h2>iPad homepage</h2><p>Camera, large gate control and recent sightings together, with status pills and expandable details.</p><a href="/current">Open Home</a><a class="secondary" href="/current#stats">Open Stats</a></article>
<article><h2>Pi touchscreen</h2><p>The fullscreen page uses the available screen area and keeps the gate control easy to tap.</p><a href="/fullscreen">Open fullscreen</a></article></section>
<p class="note">All data and camera illustrations are synthetic. There is no live connection. Gate buttons cannot operate hardware, and all writes are blocked. Plain HTTP is sufficient.</p>
<p class="note">Stop the preview with Ctrl+C in the terminal.</p></main></body></html>"""

PREVIEW_INJECTION = """
<style>
.design-demo-label{font-size:9px;letter-spacing:.06em;vertical-align:middle;color:#c9e5b6;border:1px solid #546349;border-radius:4px;padding:2px 4px;margin-left:5px}
#design-preview-notice{position:fixed;z-index:10000;bottom:22px;left:50%;transform:translateX(-50%);max-width:calc(100vw - 28px);padding:13px 18px;border-radius:9px;background:#edf2e9;color:#183014;font:14px/1.4 system-ui,sans-serif;box-shadow:0 3px 22px #0005}
</style>
<script>
document.addEventListener('click',function(event){
  var target=event.target.closest ? event.target.closest('#open-gate,#open-gate-tablet') : null;
  if(!target)return;
  event.preventDefault();event.stopImmediatePropagation();
  var notice=document.getElementById('design-preview-notice');
  if(!notice){notice=document.createElement('div');notice.id='design-preview-notice';notice.setAttribute('role','status');document.body.appendChild(notice);}
  notice.textContent='Preview only — no command sent and no gate connected.';notice.hidden=false;
  window.clearTimeout(window.designPreviewNoticeTimer);
  window.designPreviewNoticeTimer=window.setTimeout(function(){notice.hidden=true;},4000);
},true);
</script>
"""


def pi_time(epoch):
    return datetime.fromtimestamp(epoch, PI_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def event(event_id, plate, kind, age):
    """Same moving, synthetic relative-time fixtures as home_browser.py."""
    captured = time.time() - age
    recognised = kind == "recognised"
    return {
        "id": event_id,
        "plate": plate,
        "owner": "Example household" if recognised else "",
        "kind": kind,
        "confidence": 92.4 if recognised else 81.2,
        "processing_ms": 184,
        "seen_at": captured,
        "expires_at": captured + 300,
        "captured_at": pi_time(captured),
        "created_at": pi_time(captured),
        "image_name": "synthetic.svg",
        "image_url": "/images/synthetic.svg",
        "thumbnail_url": "/previews/synthetic.svg?size=160",
        "preview_url": "/previews/synthetic.svg?size=640",
        "detail": {
            "decision": {
                "version": 1,
                "reason": "allowlist_match" if recognised else "not_allowlisted",
                "match_type": "exact" if recognised else "none",
                "relay_command": "pulse_sent" if recognised else "not_requested",
            }
        },
    }


def sample_events():
    return [
        event(3, "NEW789", "unmatched", 120),
        event(2, "TEST123", "recognised", 720),
        event(1, "DEMO456", "recognised", 1800),
    ]


def stats_fixture(window):
    """Representative counts and a complete Pi-local reporting range."""
    end = datetime.fromtimestamp(STARTED_AT, PI_TIMEZONE)
    days = {"24h": 1, "7d": 7, "30d": 30, "all": 90}.get(window, 1)
    start = end - timedelta(days=days)
    hourly = days == 1
    unit = timedelta(hours=1) if hourly else timedelta(days=1)
    first = start.replace(minute=0, second=0, microsecond=0)
    if not hourly:
        first = first.replace(hour=0)
    bucket_count = int((end - first) / unit) + 1
    # There are 44 total records: 32 recognised, 10 unmatched, 2 manual.
    # Keep total/series/rankings internally consistent for every demo period.
    populated = {}
    for fraction, count in ((0.25, 3), (0.4, 6), (0.5, 4), (0.6, 7), (0.75, 12), (0.9, 8), (1.0, 4)):
        index = round((bucket_count - 1) * fraction)
        populated[index] = populated.get(index, 0) + count
    return {
        "counts": {"recognised": 32, "unmatched": 10, "manual_open": 2},
        "timeseries": {
            "bucket": "hour" if hourly else "day",
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "series": [
                {"t": (first + unit * index).strftime("%Y-%m-%d %H:00:00"), "v": count}
                for index, count in sorted(populated.items())
            ],
        },
    }


def api_fixture(path, query):
    now = time.time()
    events = sample_events()
    recognised = [item for item in events if item["kind"] == "recognised"]
    if path == "/api/home-status":
        unfamiliar = [item for item in events if item["kind"] == "unmatched" and item["expires_at"] > now]
        return {
            "server_time": now,
            "services_checked_at": now,
            "stream": {"age_seconds": 1, "fresh": True, "stale_after_seconds": 15},
            "services": SERVICES,
            "recognised": recognised,
            "unfamiliar": unfamiliar,
            "recent_decisions": events[:1],
            "decisions_available": True,
            "decision_sampling": {"per_reason_plate_seconds": 30, "global_seconds": 1, "retained_limit": 200},
        }
    if path == "/api/stats":
        return stats_fixture(query.get("window", ["24h"])[0])
    if path == "/api/stats/insights":
        return {
            "insights": {
                "top_recognised_list": [
                    {"plate": "TEST123", "count": 18},
                    {"plate": "DEMO456", "count": 9},
                    {"plate": "SAMPLE7", "count": 5},
                ],
                "top_unmatched_list": [
                    {"plate": "NEW789", "count": 5},
                    {"plate": "DEMO908", "count": 3},
                    {"plate": "UNKNOWN", "count": 2},
                ],
                "avg_processing_ms": {"overall": 184, "recognised": 172, "unmatched": 222},
                "no_plate": 2,
            }
        }
    if path == "/api/history":
        kinds = query.get("kind", ["recognised,unmatched"])[0].split(",")
        return {"items": [item for item in events if item["kind"] in kinds], "has_more": False, "next_cursor": None}
    if path == "/api/timeline":
        return {"items": recognised, "page": 1, "per_page": 25, "total": len(recognised), "total_pages": 1}
    if path == "/api/events":
        return recognised
    if path == "/api/service-health":
        return {
            "temperature_c": 58.2,
            "services": SERVICES,
            "last_event_age_s": max(0, now - (STARTED_AT - 120)),
            "disk": {"free_pct": 73.0, "free_bytes": 24 * 1024**3},
            "maintenance": {"last_success": pi_time(STARTED_AT - 3600), "age_hours": 1, "stale": False},
            "failed_units": [],
        }
    fixtures = {
        "/api/config": {"stream_fps": 2, "group_window_sec": 60},
        "/api/ui-settings": {"theme_mode": "dark"},
        "/api/stream": {"url": "/static/stream.jpg"},
        "/api/stream-lag": {"lag_ms": 1000},
        "/api/stream-health": {"stream_stale": False, "stream_age_s": 1},
        "/api/gate-cooldown": {"remaining": 0},
        "/api/gate-last-open": {"last_open_ts": STARTED_AT - 540},
        "/api/plates": [{"owner": "Example household", "plates": ["TEST123", "DEMO456"]}],
        "/api/allowlist-status": [],
        "/api/decisions": {"decisions": events[:1], "sampling": {"retained_limit": 200}},
        "/api/logs": {"lines": [pi_time(STARTED_AT) + " INFO Design preview: synthetic data only; no Pi connected."]},
    }
    return fixtures.get(path)


def camera_svg():
    """Reuse only the inline synthetic artwork, never a user image."""
    match = re.search(r'<svg class="camera-scene"[^>]*>(.*?)</svg>', ALTERNATIVE.read_text(), re.DOTALL)
    if not match:
        raise ValueError("Synthetic camera illustration was not found")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 572">'
        + match.group(1)
        + '<rect x="22" y="520" width="430" height="31" rx="6" fill="#172719"/>'
        + '<text x="37" y="541" fill="#e6efdc" font-size="17" font-family="sans-serif">DEMO · Illustrated frame · No live camera</text></svg>'
    ).encode()


class PreviewHandler(BaseHTTPRequestHandler):
    server_version = "GateDesignPreview/1.0"

    def log_message(self, _format, *_args):
        # Frame refreshes would otherwise flood the terminal. Never log headers.
        pass

    def send_content(self, body, content_type="text/html; charset=utf-8", status=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; worker-src 'none'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'none'",
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def reject_write(self):
        self.send_content(
            json.dumps({"error": "Preview only. All writes and gate commands are disabled."}), "application/json", 405
        )

    do_POST = reject_write
    do_PUT = reject_write
    do_PATCH = reject_write
    do_DELETE = reject_write
    do_OPTIONS = reject_write

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            host = urlsplit("http://" + self.headers.get("Host", "")).hostname
        except ValueError:
            host = None
        if host not in {"localhost", "127.0.0.1"}:
            self.send_content("Local preview only", status=403)
            return
        request = urlsplit(self.path)
        path = unquote(request.path)
        if path == "/api/open-gate":
            self.reject_write()
        elif path == "/":
            self.send_content(LANDING)
        elif path == "/alternative":
            self.send_content(ALTERNATIVE.read_bytes())
        elif path in {"/current", "/stats", "/fullscreen"}:
            template = "fullscreen.html" if path == "/fullscreen" else "index.html"
            html = (STATIC / template).read_text().replace("__GATE_API_SHARED_SECRET__", "synthetic-preview-secret")
            html = html.replace("Gate ANPR 🚪", 'Gate <span class="design-demo-label">DEMO</span>')
            html = html.replace("<title>Gate ANPR</title>", "<title>Gate — synthetic design preview</title>")
            html = html.replace("</head>", PREVIEW_INJECTION + "</head>")
            self.send_content(html)
        elif path in {"/static/stream.jpg", "/images/synthetic.svg", "/previews/synthetic.svg"}:
            self.send_content(camera_svg(), "image/svg+xml")
        elif path == "/static/manifest.json":
            self.send_content(
                json.dumps(
                    {
                        "name": "Gate design preview",
                        "short_name": "Gate demo",
                        "start_url": "/current",
                        "display": "standalone",
                    }
                ),
                "application/manifest+json",
            )
        elif path in PUBLIC_ASSETS:
            source = PUBLIC_ASSETS[path]
            body = source.read_bytes()
            if path == "/app.js":
                # A temporary preview must never install the real app's worker.
                body = body.replace(
                    b'navigator.serviceWorker.register("/static/sw.js").catch(() => {});',
                    b"/* Service workers are disabled in the design preview. */",
                )
            self.send_content(body, mimetypes.guess_type(source.name)[0] or "application/octet-stream")
        elif path.startswith("/api/"):
            data = api_fixture(path, parse_qs(request.query))
            if data is None:
                self.send_content(
                    json.dumps({"error": "This endpoint is not part of the design preview."}), "application/json", 404
                )
            else:
                self.send_content(json.dumps(data), "application/json")
        else:
            self.send_content(
                "This page is not part of the design preview. Return to <a href='/'>the comparison</a>.", status=404
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765, help="Loopback port (default: 8765)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
    print(f"Design comparison: http://127.0.0.1:{args.port}/", flush=True)
    print("Synthetic fixtures only. No live gate connection; all writes are blocked. Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
