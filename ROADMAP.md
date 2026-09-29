# Gate ANPR roadmap

This is the canonical backlog for this small LAN/Pi project. Keep stable IDs,
update status and verification notes with each completed change, and link any
GitHub issues here rather than maintaining a second independent list.
Real captures, credentials, domains and household details belong in local files.

Keep plain LAN HTTP fully supported throughout this roadmap. HTTPS/ACME remain
opt-in and disabled by default; core features must work without a domain,
certificates or DNS credentials. Secure-context browser enhancements are optional.

Effort: **S** = contained change, **M** = several components, **L** = experiment or
hardware work. These are relative sizes, not delivery estimates.

## First batch — agreed scope

| ID | Status | Improvement | Acceptance criteria |
|---|---|---|---|
| UX-01 | Delivered | Truthful home-screen status (M) | Distinguish displayed-frame stall, stale camera source and unavailable status; recover automatically; pause hidden polling; work on iOS 12. Service activity is labelled as such. |
| UX-02 | Delivered | Unfamiliar-arrival card (S/M) | Show a recent unmatched capture, preview and age; expire after five minutes even during network failure; keep the last two recognised arrivals; never assert that a vehicle is still present. |
| CFG-01 | Delivered | Forgiving plate entry (S/M) | Ignore presentation whitespace when matching; validate new entries and detect normalised collisions; preserve existing display spelling; errors do not overwrite the saved allowlist. |
| UX-03 | Delivered | Explain recognition decisions (M) | Existing events record match/reason/relay-command outcome; expose bounded, explicitly sampled recent skipped/error decisions; preserve opening, dedupe and notification behaviour; never infer physical gate position. |

## Next reliability and recognition work

Recommended next batch: REL-01 and REL-02 first, then REL-03/REL-04. Build the
private replay harness (REC-01) before changing recognition policy or reducing
analysis CPU. Smaller preview profiles (PERF-03) can be compared independently.

| ID | Priority / effort | Improvement | Acceptance criteria / dependency |
|---|---|---|---|
| REL-01 | High / M | Persist automatic-opening cooldown | Restarting the worker cannot permit a second pulse inside the configured vehicle cooldown; cover uncertain actuation outcomes. |
| REL-02 | High / M | Monitor real recognition progress | Detect a dead child, stalled input or stalled processing even when the parent and preview stay healthy; an empty driveway is not a fault. |
| REL-03 | High / M | Process multiple detected vehicles | Handle each vehicle's candidate list independently; an unknown first vehicle cannot hide an allowed second vehicle; bound relay commands per frame. |
| REL-04 | High / M | Separate actuation from persistence/notification retries | A database/notification failure after a pulse cannot repeat the pulse or silently lose retryable work. |
| REC-01 | High / M | Offline recognition replay | Replay labelled images/clips and captured jobs with GPIO/network notifications disabled; compare missed arrivals, wrong decisions and capture-to-command latency. Keep footage private. |
| REC-02 | High / M | Explicit recognition ambiguity policy | Prefer exact identity, reject equal fuzzy ties and expose confusable collisions; compare proposed decisions in shadow mode before changing live policy. Depends on REC-01. |
| REL-05 | Medium / M | Test ordinary outage recovery | Simulated camera loss, interrupted JPEGs and network-late boot recover with bounded retries and clear UI status. |
| REL-06 | Medium / S/M | Bound disposable capture storage | Daily persistent cleanup enforces age/size/free-space limits, catches up after downtime and preserves configuration. |
| REL-07 | Medium / M | Known-working release rollback | Stage code and dependencies together, validate before switching, retain the previous release and test rollback. |
| REL-08 | Medium / S/M | Complete configuration recovery | Include OpenALPR settings and deployed versions in small private backups; prove restore; optional copy to an existing controller/NAS. Vehicle history stays optional. |
| REL-09 | Medium / S/M | Maintenance status | Show backup/renewal age, certificate expiry, clock sync and repeated restarts; optional one failure/recovery notification. |
| CFG-02 | Medium / S/M | Safer allowlist editing | Unsaved indicators, useful row errors, undo and revision checks prevent silent overwrite from another screen. |
| CFG-03 | Medium / M | Effective configuration view | Redacted active settings and shared validation make local/remote overrides clear; durable writes for infrequently changed configuration. |
| MAINT-01 | Ongoing / S/M | Dependency maintenance | Baseline audit cleared the reported advisories and CI now uses production pins. Repeat audits periodically and test compatible updates. |
| MAINT-02 | Low / S | Ansible fact compatibility | Replace deprecated injected hostname/IP facts with explicit `ansible_facts` access; verify generated nginx aliases and host entries before future Ansible upgrades. |
| MAINT-03 | Low / M | Narrow inherited Python packages | Retain working GPIO access while reducing unrelated system packages visible to the app environment; prove application dependencies and GPIO imports before switching. |

## Performance experiments

| ID | Effort | Improvement | Success measure / constraint |
|---|---|---|---|
| PERF-01 | M/L | Real recognition ROI | Installed daemon currently ignores the exposed ROI option. Implement actual cropping and prove lower CPU without missed daytime/night-time arrivals using REC-01. |
| PERF-02 | L | Activity-adaptive recognition | Keep a low baseline analysis rate and burst during activity; measure heat/CPU and arrival latency; test slow vehicles, rain and shadows. Depends on REC-01. |
| PERF-03 | S/M | Small-screen preview profile | Compare smaller JPEGs and quality settings on physical screens; report displayed FPS and legibility, not just producer FPS. |
| PERF-04 | L | Modern video transport | Compare a compressed video relay for capable clients while retaining JPEG support for old displays; justify additional complexity with measurements. |

The currently measured preview source supplies about 10 distinct frames/second;
raising the JPEG output setting alone cannot create additional source frames.

## Household features and optional extensions

| ID | Effort | Improvement | Acceptance criteria / dependency |
|---|---|---|---|
| UX-04 | M | Timed pause of automatic opening | Persisted expiry is enforced by the worker, visible everywhere, with immediate resume and manual controls retained. |
| UX-05 | M | Notification preferences | Selected vehicles, unfamiliar arrivals, quiet hours and one alert per visit using the existing provider. |
| UX-06 | S/M | Per-screen profiles | Local layout/text preferences and idle return to Home; preserve old-screen compatibility. |
| UX-07 | M | One useful record per visit | Retain trigger evidence plus best later image and observations; bound image/storage growth. |
| UX-08 | M | Recognition-quality trends | Useful per-vehicle/day-night examples and latency trends; never label allowlist membership as OCR accuracy. Depends on REC-01 for true accuracy. |
| UX-09 | S/M | Plate/owner/date search | Bounded indexed server queries, working filters and stable pagination. |
| ACCESS-01 | M | Expiring guest access | Explicit validity window, clear expiry and worker-side enforcement; no permanent accidental permission. |
| APP-01 | M | Finish installable web app | Verify worker scope, assets/icons and phone layout; clear offline state; never queue gate actions for later. |
| APP-02 | L | Native device integrations | Only if widgets, Shortcuts or Watch controls justify pairing, distribution and ongoing maintenance. |
| INT-01 | M | Optional local automation events | Publish sightings, command outcomes and availability through a configurable integration; never infer presence or gate position from a command. |
| HW-01 | Hardware / M | Actual gate position | Use verified controller feedback or sensors for open/closed state and restrained left-open alerts. |
| HW-02 | L | Arrival/departure tracking | Requires representative driveway footage or extra sensors; distinguish sightings from confirmed movements. |

## Completed foundation

- Legacy-iPad image refresh recovery and JPEGs in RAM.
- Bounded history pagination and image previews.
- Pi 5 lgpio default, shared relay lock and cleanup on failures.
- Optional trusted HTTPS, delegated DNS renewal and reversible short-name homepage redirect.
- Stream diagnostics, navigation races and visible-only Live diagnostics polling.

Each new completion should record its commit, automated checks, deployed checks,
and remaining physical-device or real-vehicle verification here.


## First-batch verification — 2026-09-29

Implementation: [d3d9b9f](https://github.com/sebsebsebsss/anpr-rpi5/commit/d3d9b9f)
on `feat/household-status-and-decisions`. Public baseline:
[3c720f3](https://github.com/sebsebsebsss/anpr-rpi5/commit/3c720f3)
on `chore/public-repo-review`.

- 322 Python tests and all five JavaScript suites passed; Ruff lint/format,
  Ansible syntax and diff checks passed.
- Synthetic Chrome checks covered image/source/API failures, offline expiry,
  decision availability and failed/successful allowlist saves. Both tablet
  layouts fit 1024×768; phone layout had no horizontal overflow at 390px.
  Additional JS cases cover hung requests/bodies, late responses without
  AbortController, and a device clock that differs from the Pi.
- Full Ansible deployment passed: 96 successful tasks, no failures. The saved
  allowlist passed read-only validation before deployment; configuration was
  backed up and retained. Ten deployed application files matched local hashes.
- Live HTTPS browser checks passed on desktop and the throttled iOS 12
  user-agent path: no page errors, no offscreen stream requests, at most 30
  history cards, preview images only, and fresh Home/Live status. The short-name
  redirect and trusted HTTPS certificate still passed.
- The eight-second browser samples observed 5.75 and 5.37 JPEG load events/sec
  respectively. These are Chrome measurements, not physical-iPad results or
  counts of distinct camera frames. The first frames arrived within one second.
- All four monitored services were active. Home status took about 3ms with a
  populated service cache and 28ms for its first sampled request on the Pi.
  The new decision store/API was available, with no natural arrivals recorded
  during verification; decision outcomes and unfamiliar cards used synthetic
  fixtures in the automated checks.
- All 15 application dependencies on the Pi match the production pins and
  satisfy their requirements. A whole-environment `pip check` still reports
  unrelated Debian package metadata from system-site-packages; see MAINT-03.
- Live checks sent no gate commands or notification jobs. Physical iPad WebKit
  behavior and actual arrival/relay behavior remain to be observed in normal
  use. Unit tests cover those decision and relay-call paths with test doubles.
