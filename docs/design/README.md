# Screen design review

The recommendation is to tighten the existing interface first. Its camera,
gate control and recent sightings already suit dedicated household screens.
The main problems are mobile spacing, competing status text and statistics
without enough context, rather than a need for a new application framework.

The review branch changes the working frontend as follows:

| Screen | Change |
|---|---|
| Phone | Compact two-row navigation, no duplicate clock, a short gate button visible in the first viewport, and compact arrival rows. |
| Landscape iPad | Keep the uncropped camera and large control side by side; put status in pills and expand explanations only when requested. |
| Small Pi screen | Preserve the fullscreen layout and large touch target; bound camera height so recent sightings remain visible. |
| Desktop | Align the status pills with the camera heading and put the last decision beside arrival information. |

Stats now has a time-labelled activity chart with quiet periods, ranked
registration counts, explicit manual-command counts and expandable system
details. Unmatched reads are not an accuracy score, and stored detections are
not confirmed visits. Classification also handles older candidate records
consistently with History. This has working API integration, unlike the
alternative's deliberately simulated extra metrics.

The alternative below explores flatter surfaces, more restrained typography
and phone bottom navigation. These changes are independent of HTTPS, JPEG
transport and the recognition engine. Neither design has been deployed as
part of this review. Physical legacy-iPad WebKit remains a separate check
from automated browser sizing and user-agent tests.

## Interactive comparison

Open [alternative.html](alternative.html) directly in a browser. It is a
self-contained, responsive design preview: no server, dependencies, network
requests, real camera images, registrations, hostnames or gate commands.
Plain HTTP also works. HTTPS is not required.

To compare this alternative with the actual current frontend using synthetic
data, run from the repository root:

```sh
python3 tests/design_preview.py
```

Open <http://127.0.0.1:8765/> for both choices. The existing Home is at
`/current`, its Stats view at `/current#stats`, and this alternative at
`/alternative`. The preview binds only to this machine, uses public static
assets and synthetic API responses, blocks every write and intercepts gate
buttons with a “Preview only” message. It reads no configuration, history,
credentials or camera images and does not contact a Pi. Stop it with Ctrl+C;
use `--port PORT` if 8765 is occupied. The current view's reporting periods
demonstrate layout with the same 32 recognised / 10 unmatched / 2 manual sample
counts; they are not independent real datasets.

Try Home and Insights, the reporting periods, the light/dark theme, either
status pill, the simulated connection failure, camera expansion, a chart bar
and an unfamiliar-sighting review. The gate button only changes local demo
text. All dates, counts and states are synthetic and deliberately fixed; the
relative times refer to the illustrated 20:48 timeline, not the current clock.

The direction is quieter, with a restrained green accent, compact operational
status and one clear action. Desktop and landscape tablet retain the camera,
control and sightings in one view. Phone navigation moves to the bottom and
the gate action follows the camera, while background diagnostics move into
an explicitly opened detail dialog.

Insights answers when sightings happened, which were unfamiliar and what to
review next. The demo counts represent stored sightings, not visits or
recognition accuracy. Relay-command totals are a proposed metric: production
must first record every actual outcome, including manual commands. These are
not physical opening counts. Period comparisons should use equivalent
elapsed intervals, and plate totals must not be described as distinct vehicle
counts.

This is a design alternative, not a replacement frontend. Before adopting it,
connect real data, preserve the existing legacy-iOS image loader and relay
feedback/cooldown behavior, test on the physical iPad and Pi screens, and
complete accessibility and error-state checks. Actual photographs would replace
the inline vector illustration. There is no new recognition, preview transport
or HTTPS dependency here.
