# Screen design review

The selected direction is to tighten the existing interface. The primary
devices are a wall-mounted landscape iPad and a Pi touchscreen. Home and
`/fullscreen` must keep the camera, gate control and recent sightings visible
on one screen, without document scrolling, internal scrolling or clipped
content. The large gate button is essential: preserve or enlarge its touch
area, use available space, and keep the camera uncropped. Phone changes must
not compromise either dedicated display.

The review branch changes the working frontend as follows:

| Screen | Change |
|---|---|
| Phone | Compact two-row navigation, no duplicate clock, a short gate button visible in the first viewport, and compact arrival rows. |
| Landscape iPad | Keep the uncropped camera and large control side by side; remove page-padding overflow; put status in pills with overlay explanations. |
| Small Pi screen | Use the available fullscreen width for the camera and larger gate target; keep recent sightings visible without scrolling. |
| Desktop | Align the status pills with the camera heading and put the last decision beside arrival information. |

Stats now has a time-labelled activity chart with quiet periods, ranked
registration counts, explicit manual-command counts and expandable system
details. Unmatched reads are not an accuracy score, and stored detections are
not confirmed visits. Classification also handles older candidate records
consistently with History. This has working API integration, unlike the
alternative's deliberately simulated extra metrics.

The alternative redesign was rejected because its space allocation and smaller
control do not suit the primary wall screens. It remains an archived study,
not a proposed replacement. The selected changes are independent of HTTPS,
JPEG transport and the recognition engine. Physical legacy-iPad WebKit remains
a separate check from automated browser sizing and user-agent tests.

## Selected interface preview

To preview the actual frontend using synthetic data, run from the repository
root:

```sh
python3 tests/design_preview.py
```

Open <http://127.0.0.1:8765/>. Home is at `/current`, the Pi display at
`/fullscreen`, and Stats at `/current#stats`.
The preview binds only to this machine, uses public static
assets and synthetic API responses, blocks every write and intercepts gate
buttons with a “Preview only” message. It reads no configuration, history,
credentials or camera images and does not contact a Pi. Stop it with Ctrl+C;
use `--port PORT` if 8765 is occupied. The current view's reporting periods
demonstrate layout with the same 32 recognised / 10 unmatched / 2 manual sample
counts; they are not independent real datasets. Plain HTTP is sufficient.

## Archived alternative — rejected

[alternative.html](alternative.html) is a self-contained historical mockup,
with no network requests, private images or real gate commands. It is not
deployed or linked from the selected interface preview.

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
