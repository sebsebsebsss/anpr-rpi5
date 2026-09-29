# Gate ANPR roadmap

This records options and priorities for this small LAN/Pi project. It is not a
commitment to implement every idea. Keep stable IDs,
update status and verification notes with each completed change, and link any
GitHub issues here rather than maintaining a second independent list.
Real captures, credentials, domains and household details belong in local files.

Keep plain LAN HTTP fully supported throughout this roadmap. HTTPS/ACME remain
opt-in and disabled by default; core features must work without a domain,
certificates or DNS credentials. Secure-context browser enhancements are optional.

Primary UI requirement: the landscape iPad Home and Pi `/fullscreen` page fit
their wall displays without scrolling or clipped content. Preserve or enlarge
the large gate touch target and show the uncropped camera and latest arrivals.
Phone improvements must preserve these layouts. The alternative redesign is
rejected; the existing interface is the selected direction.

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

The selected tightening and Stats changes (UX-10/UX-11) are deployed. The useful
next reliability batch is REL-01, REL-02 and REL-04.
REL-03 becomes worthwhile when simultaneous vehicles matter at this site.

Performance changes remain optional. A preview-only size comparison (PERF-03)
is the most isolated trial; prepare timed restoration and measure both physical
screens first. OCR thread limits (PERF-05) start with offline correctness checks,
with live changes deferred. A detection mask (PERF-01) requires review and
comparison of every retained historical image before excluding any area.
Measurements, trial limits and restoration requirements are in
[docs/PERFORMANCE.md](docs/PERFORMANCE.md).

Keep storage bounds and dependency upkeep. Defer native apps, new video
transport, adaptive recognition, direction/presence inference and elaborate
release/recovery infrastructure until a household need justifies them.
Vehicle-history backups remain optional.

| ID | Status / effort | Current design work | Acceptance criteria |
|---|---|---|---|
| UX-10 | Deployed; physical-screen observation pending / S/M | Compact status and layouts per screen | Wall displays first: no document/panel scrolling or clipped content, large gate target preserved/enlarged, uncropped camera and two recent arrivals visible; status details overlay; phone layout stays independent; light/dark and legacy sizing checked. |
| UX-11 | Delivered / M | Useful activity statistics | Labelled time/count axes retain quiet periods; ranked counts replace word cloud; consistent legacy categories; no accuracy/visit/physical-open claims; errors, mobile and empty states work. |

A rejected alternative is archived in [docs/design/alternative.html](docs/design/alternative.html).
Its proposed comparison/relay-total metrics are demo-only. The selected
UX-10/UX-11 implementation is on `design/device-layout-and-insights`.

| ID | Priority / effort | Improvement | Acceptance criteria / dependency |
|---|---|---|---|
| REL-01 | High / M | Persist automatic-opening cooldown | Restarting the worker cannot permit a second pulse inside the configured vehicle cooldown; cover uncertain actuation outcomes. |
| REL-02 | High / M | Monitor real recognition progress | Detect a dead child, stalled input or stalled processing even when the parent and preview stay healthy; an empty driveway is not a fault. |
| REL-03 | Conditional / M | Process multiple detected vehicles | Handle each vehicle's candidate list independently; an unknown first vehicle cannot hide an allowed second vehicle; bound relay commands per frame. |
| REL-04 | High / M | Separate actuation from persistence/notification retries | A database/notification failure after a pulse cannot repeat the pulse or silently lose retryable work. |
| REC-01 | Before recognition tuning / S/M | Small offline recognition replay | Start with a representative labelled private image/job set with GPIO/network notifications disabled; compare missed arrivals, wrong decisions and capture-to-command latency. Keep footage private. |
| REC-02 | High / M | Explicit recognition ambiguity policy | Prefer exact identity, reject equal fuzzy ties and expose confusable collisions; compare proposed decisions in shadow mode before changing live policy. Depends on REC-01. |
| REL-05 | Medium / M | Test ordinary outage recovery | Simulated camera loss, interrupted JPEGs and network-late boot recover with bounded retries and clear UI status. |
| REL-06 | Medium / S/M | Bound disposable capture storage | Daily persistent cleanup enforces age/size/free-space limits, catches up after downtime and preserves configuration. |
| REL-07 | Deferred / M | Known-working release rollback | Stage code and dependencies together, validate before switching, retain the previous release and test rollback. |
| REL-08 | Optional / S/M | Complete configuration recovery | Include OpenALPR settings and deployed versions in small private backups; prove restore; optional copy to an existing controller/NAS. Vehicle history stays optional. |
| REL-09 | Medium / S/M | Maintenance status | Show backup/renewal age, certificate expiry, clock sync and repeated restarts; optional one failure/recovery notification. |
| CFG-02 | Medium / S/M | Safer allowlist editing | Unsaved indicators, useful row errors, undo and revision checks prevent silent overwrite from another screen. |
| CFG-03 | Medium / M | Effective configuration view | Redacted active settings and shared validation make local/remote overrides clear; durable writes for infrequently changed configuration. |
| MAINT-01 | Ongoing / S/M | Dependency maintenance | Baseline audit cleared the reported advisories and CI now uses production pins. Repeat audits periodically and test compatible updates. |
| MAINT-02 | Low / S | Ansible fact compatibility | Replace deprecated injected hostname/IP facts with explicit `ansible_facts` access; verify generated nginx aliases and host entries before future Ansible upgrades. |
| MAINT-03 | Low / M | Narrow inherited Python packages | Retain working GPIO access while reducing unrelated system packages visible to the app environment; prove application dependencies and GPIO imports before switching. |

## Performance experiments

| ID | Effort | Improvement | Success measure / constraint |
|---|---|---|---|
| PERF-01 | Audit complete; mask deferred / M | Supported detector mask | All 13,401 retained files inventoried; 13,400 decodable representations reviewed with 19 full-resolution follow-ups. One incomplete JPEG and three artifact frames limit certainty. Full-width/top 85% is an offline candidate: 14.94% fewer detector pixels, CPU saving unmeasured. No mask applied; require parity and arrival-timing evidence before deployment. |
| PERF-02 | L | Activity-adaptive recognition | Keep a low baseline analysis rate and burst during activity; measure heat/CPU and arrival latency; test slow vehicles, rain and shadows. Depends on REC-01. |
| PERF-03 | Deployed; physical-screen observation pending | Small-screen preview profiles | One decoder publishes main 960×540, tablet 800×450 and kiosk 640×360 at 10fps. Main Live view retained; profiles optional. Pi measured ~9.9 distinct JPEGs/s; actual iPad/Pi smoothness remains to observe. Extra encoding adds about 2 percentage points of whole-Pi CPU. |
| PERF-04 | Deferred / L | Modern video transport | Compare a compressed video relay for capable clients while retaining JPEG support for old displays; justify additional complexity with measurements. |
| PERF-05 | Trial complete; reverted | Limit nested OCR threads | Authorised guarded quiet-scene unset → 1 → unset trial found no meaningful saving (144.88% versus 144.76% of one core). Original settings and services restored. No busy-arrival or OCR-parity claim; see performance notes. |

The source probe reports an average of 10fps. Confirm actual distinct source
frames during movement before selecting a higher preview rate; raising the JPEG
output setting alone cannot create additional source frames.

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

## Safari viewport and JPEG profiles — 2026-09-29

The actual iPad exposed a Safari summary-layout quirk and a smaller visible
height after its address banner appeared. The latest-check disclosure now uses
an ordinary flex child, and fixed wall layouts follow the visible viewport.
Large gate controls, uncropped camera framing and optional HTTPS are retained.

- 353 Python tests, all six JavaScript suites, Ruff, diff and Ansible syntax
  checks passed. Synthetic browser checks cover both themes, unfamiliar arrivals,
  legacy layout fallbacks and Safari-height viewports down to 1024×680.
- Targeted web/preview Ansible deployment: 53 successful tasks, no failures.
  Twelve deployed files match local hashes. Original recognition/worker process
  IDs and configuration hashes are unchanged, as are unrelated env settings.
  All six checked services are active. A private backup remains available; the
  independent timed preview rollback was cancelled after verification.
- Read-only live Chrome checks passed at iPad 1024×704/680, kiosk 800×480 and
  1024×600, and desktop 1600×1000. No document overflow or camera cropping;
  iPad gate buttons are 217×456/432 and the small Pi button is 196×343.
  Latest-check rows stay 36px/32px high. No gate commands were sent.
- iPad Home loads 800×450, Pi `/fullscreen` 640×360 and Live `#stream` retains
  960×540. Live browser samples completed 9.6–9.9 image loads/sec; the Pi
  published about 9.9 distinct JPEGs/sec. Physical-device smoothness remains
  an observation, not a result established by changing a Chrome user agent.
- The existing status disclosure exposes CSS viewport, JPEG dimensions and
  image loads/sec. The owner subsequently confirmed that the physical iPad
  renders perfectly; its actual frame rate has not been reported.
  Profiles default off in public configuration. Detailed CPU
  measurements and the unsuccessful, reverted OCR-thread trial are recorded in
  [performance notes](docs/PERFORMANCE.md).

The retained-image audit also completed: every file was accounted for, all
13,400 decodable images were reviewed through contact sheets, and 19 originals
received full-resolution follow-up. One incomplete JPEG and three visibly damaged
frames are documented privately. A full-width lower 15% exclusion would reduce
detector search pixels by 14.94%, with no measured total-CPU saving. No mask or
recognition policy change was applied; PERF-01 stays deferred pending parity and
timing evidence. Private captures and audit identifiers are excluded from GitHub.

## Wall-screen deployment verification — 2026-09-29

The tightened existing interface is deployed; the alternative is rejected.

- Six JavaScript suites, Ruff, diff and Ansible syntax checks passed. The
  strengthened synthetic browser suite checks document and card overflow,
  primary-content bounds, large touch targets, light/dark, usual and unfamiliar
  arrivals, overlay diagnostics, legacy aspect-ratio fallback and returning
  Home after scrolling Stats. No hidden-overflow workaround is used.
- Web-only Ansible deployment completed with 44 successful tasks and no
  failures. A private copy of the preceding web code was saved on the Pi for
  rollback. All seven changed deployed web files matched local hashes.
- Live read-only Chrome checks passed at iPad 1024×748 and fullscreen
  800×480/1024×600: document dimensions equal the viewport, the camera uses
  `contain`, and gate buttons measure 217×500, 196×343 and 257×463 respectively.
  Synthetic checks also cover fullscreen 1280×800 with a 327×663 button.
  Live Stats loads and returning Home restores the non-scrolling layout.
- Application/recognizer configuration and the allowlist retained their
  hashes. OpenALPR, the gate worker and JPEG producer retained their process
  IDs. All six checked services were active and the shared JPEG was fresh.
- No mask, OCR-thread, JPEG-size, camera or recognition-policy changes were
  made. Verification sent no gate commands or notifications. HTTPS remains
  optional; local synthetic browser checks use plain HTTP.
- These are browser viewport checks, not proof of physical legacy WebKit.
  Reload the existing wall-screen tabs to load the new assets and observe
  them on the physical iPad/Pi during ordinary use.

## Design-review verification — 2026-09-29

Branch: `design/device-layout-and-insights`. UX-10 and UX-11 are implemented
for review, with an independent alternative mockup and a synthetic local
comparison server. Production UI and recognition settings were not changed.

- 337 Python tests passed on macOS/Python 3.14 and Linux/Python 3.11; all six
  JavaScript suites, Ruff lint/format and diff checks passed. New Stats API
  tests cover legacy categories, time bounds, missing plates and read-only
  database access. Linux verification caught and fixed an unclosed test-fixture
  connection whose delayed SQLite checkpoint invalidated byte comparisons.
- Home browser checks cover six viewport sizes, both themes and legacy sizing.
  Stats checks cover four viewport sizes, both themes, empty/error recovery,
  independent health failure, keyboard chart selection and a device clock/time
  zone differing from the Pi. Pending reporting-period races and history
  drill-through periods have JavaScript regression coverage.
- The local comparison and alternative use synthetic data and an illustrated
  camera view. Browser checks found no page errors or unexpected requests;
  the comparison gate control is inert. The alternative's additional metrics
  remain proposed features, not implemented production data.
- A read-only Pi sample identified recognition as the dominant CPU user.
  Detection masks, preview sizing and OCR thread limits are still experiments;
  no savings or physical-device results are claimed for them.
- Physical iPad/Pi layout and real arrival checks remain for any deployment.


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
