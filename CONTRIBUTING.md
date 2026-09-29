# Development and contributions

Keep the Pi's LAN-first operation, old iOS compatibility and existing relay
behaviour intact. Track proposed work in [ROADMAP.md](ROADMAP.md); use its IDs
in commits or issues. Add acceptance criteria before starting larger changes.

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
git diff --check
```

Use Node 22 or newer for these tests; delivered browser JavaScript must still
work on iOS 12. Unit tests replace GPIO and notifications with test doubles.
Use synthetic plates and owners in new fixtures.

The optional `tests/browser_smoke.py` requires Playwright and Chrome. It blocks
non-GET/HEAD requests. Its iPad option exercises the legacy user-agent path in
Chrome; physical iPad testing is still needed for old WebKit behaviour.

`tests/synthetic_job.py` defaults to a JSON preview. `--enqueue` sends to a real
worker and can activate the gate if the plate is allowed. Do not use the real
queue for routine regression tests.

## Before publishing

- Stage explicit paths and inspect `git diff --cached`.
- Keep real env files, camera URLs, DNS tokens, allowlists, captured images,
  recordings, databases, logs, keys and backups out of commits and issue bodies.
- Check the ancestry of the branch being pushed as well as current files.
  Ignoring or deleting a file does not remove an older committed copy.
- Push the reviewed branch explicitly; avoid `--all` and `--mirror` when local
  experimental branches may contain private configuration.
- Keep local configuration examples generic and retain third-party notices.

Normal deployments preserve the Pi's environment file. Use the documented
explicit replacement/override options when changing runtime settings, and
confirm the effective configuration after deployment.

## Review and deployment

Test the changed behaviour and its failure modes. Explain what was measured,
what was inferred, and what remains unverified. A service marked active does
not prove frames are being analysed; a relay pulse does not prove gate movement.
Recognition-policy changes need representative footage and accuracy checks.
