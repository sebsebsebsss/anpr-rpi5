# Development and contributions

Preserve LAN operation, old iOS compatibility and relay behaviour. The wall iPad
Home and Pi `/fullscreen` page are the primary interfaces: both must fit without
scrolling or clipped content, with an uncropped camera and a large gate button.
Phone improvements must preserve those dedicated layouts.

HTTP is fully supported. HTTPS/ACME are optional and disabled by default; core
camera, history, allowlist and gate functions require no domain, certificate or
DNS credentials. Secure-context browser features must remain optional additions.

## Local checks

```sh
python3 -m venv .venv
.venv/bin/pip install -r files/requirements.txt pytest ruff
.venv/bin/ruff check files/ tests/
.venv/bin/ruff format --check files/ tests/
.venv/bin/pytest
node tests/stream_refresh.js
node tests/history.js
node tests/gate_controls.js
node tests/web_diagnostics.js
node tests/home_status.js
node tests/stats.js
git diff --check
```

Use Node 22 or newer for tests; delivered browser JavaScript must still work on
iOS 12. Unit tests replace GPIO and notifications with test doubles. Use synthetic
plates and owners in fixtures.

Optional browser checks require Playwright and Chrome:

- `tests/home_browser.py` checks wall/phone layouts, themes and allowlist editing.
- `tests/stats_browser.py` covers reporting periods, charts and failed requests.
- `tests/browser_smoke.py` checks a running site while blocking non-GET/HEAD requests.

Home/Stats fixtures use local assets and synthetic responses, with no Pi or HTTPS
required. Their `--output-dir /tmp/gate-ui-preview` screenshots are safe to share.
An iPad user-agent in Chrome checks the legacy code path; it does not reproduce
physical iOS 12 WebKit. Check the actual wall screens after layout/stream changes.

## Local UI preview

```sh
python3 tests/design_preview.py
```

Open <http://127.0.0.1:8765/> for Home, Pi-screen and Stats links. Use `--port PORT`
if needed and Ctrl+C to stop. The preview binds to loopback, uses synthetic data
and camera illustrations, reads no private configuration and never contacts a Pi.
Writes are blocked and gate buttons show a preview-only message. Plain HTTP works.
Reporting periods reuse sample counts to demonstrate layout, not real datasets.

## Optional Pi test workflows

[Load controller settings](docs/OPERATIONS.md#deploying-changes) first. To test
OpenALPR using a private local photo with a readable plate:

```sh
ansible-playbook -i inventory.ini -u "$GATEPI_USER" site-tests.yml \
  -e run_openalpr_smoketest=true -e openalpr_test_image_path="tests/test.jpeg"
```

To print a synthetic job without submitting it:

```sh
ansible-playbook -i inventory.ini -u "$GATEPI_USER" site-tests.yml \
  -e run_synthetic_job=true -e synthetic_plate=A1ABC -e synthetic_delay=-60
```

`-e synthetic_enqueue=true` (or `--enqueue` on `tests/synthetic_job.py`) submits
to the real worker and may operate the relay/send notifications. Do not use the
real queue for routine regression tests.

## Before publishing or deploying

Stage explicit paths and review the diff, including the branch's ancestry.
Keep camera URLs, DNS tokens, real env files, allowlists, images, databases, logs,
keys and backups out of commits. Removing a file does not erase earlier committed
copies. Push the reviewed branch explicitly; avoid `--all` and `--mirror` when
experimental branches may contain private material. Retain third-party notices.

Deployments preserve remote settings unless deliberately overridden; see
[operations](docs/OPERATIONS.md). Test the changed behaviour and failure modes.
Explain what was measured and what remains unverified: an active service does not
prove recognition progress, and a relay command does not prove gate movement.
Recognition-policy changes need representative private footage and accuracy checks.

## Future work

Keep agreed tasks in GitHub issues with a clear outcome and verification plan.
Useful reliability follow-ups are persisted automatic-opening cooldown across
restarts, monitoring actual recognizer progress, and separating completed relay
commands from database/notification retries. Multi-vehicle handling and ambiguous
fuzzy matches need representative offline replay before changing live decisions.
There is no separate completion diary or duplicate roadmap to maintain.
