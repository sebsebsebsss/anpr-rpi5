# Performance and display tuning

Recognition and the browser preview have separate camera pipelines. Tune and
measure them separately; display smoothness is not recognition frame rate.
Keep changes reversible and compare the same scene after warm-up. These settings
work over HTTP as well as optional HTTPS.

## Camera input

`ALPRD_STREAM` supplies OpenALPR. `GATE_WEB_STREAM_RTSP_URL` can supply a separate
lower-resolution camera substream to the preview; when omitted it reuses the
recognition URL. Reducing recognition-source resolution can save decoding and
scaling work, but inspect difficult/distant plates before accepting that tradeoff.

The configured detector maximum is 1280×720. The installed OpenALPR implementation
scales width first and does not impose a strict 720-pixel height in every aspect
ratio. Higher-resolution camera input still costs decoding work even when it is
later scaled. A source-resolution change also requires reviewing any detection
mask and candidate-size limits.

The browser cannot display more distinct motion frames than the camera captures.
A 10fps JPEG publisher fed by a 4fps camera can produce repeated images; the
Home FPS pill measures image loads, not independently captured camera frames.

## Preview JPEGs

The live JPEG is atomically published at `/run/gate-anpr/stream.jpg` in RAM and
served at `/static/stream.jpg`. It is recreated after boot; retained recognition
photos remain on disk. The application env controls the main preview:

```ini
GATE_WEB_STREAM_URL=/static/stream.jpg
GATE_WEB_STREAM_JPEG=/run/gate-anpr/stream.jpg
GATE_WEB_STREAM_RTSP_URL=rtsp://user:pass@camera-ip:554/h264Preview_01_sub
GATE_WEB_STREAM_FPS=10
GATE_WEB_STREAM_WIDTH=960
GATE_WEB_STREAM_HEIGHT=540
```

Those are example tuning values, not required defaults. Deploy preserves the
Pi's current stream settings. For an existing installation, put only the settings
you want to change into an extra-vars file such as `/tmp/gate-preview.json`:

```json
{
  "gate_stream_overrides": {
    "GATE_WEB_STREAM_WIDTH": 960,
    "GATE_WEB_STREAM_HEIGHT": 540,
    "GATE_WEB_STREAM_FPS": 10,
    "GATE_WEB_STREAM_PROFILES": 1,
    "GATE_WEB_STREAM_TABLET_WIDTH": 800,
    "GATE_WEB_STREAM_KIOSK_WIDTH": 640
  }
}
```

After [loading controller settings](OPERATIONS.md#deploying-changes), run:

```sh
ansible-playbook -i inventory.ini -u "$GATEPI_USER" site.yml \
  --tags web,preview -e @/tmp/gate-preview.json
```

This changes preview/web services without restarting recognition or the gate
worker. Only those six keys are accepted; other settings and camera credentials
are preserved. Record previous values before tuning so they can be reapplied.

Profiles default off. When enabled, one decoder creates the main JPEG plus
smaller images: tablet width for iPad/small-screen Home, kiosk width for
`/fullscreen`, and the main image for Live (`#stream`). Widths never upscale the
main output. The displayed camera remains uncropped and physically the same size;
nginx falls back to the main image if a profile is unavailable. Custom external
feeds are unchanged.

Extra encodes cost some server CPU in exchange for fewer transferred/decoded
pixels on old screens. To disable them, apply
`{"gate_stream_overrides":{"GATE_WEB_STREAM_PROFILES":0}}` with the same tags;
all screens return to the main JPEG. Restore other saved values separately if
also reverting dimensions or frame rate.

## Detection mask

Masks are optional and **off by default**. In the ignored `ansible.env`, set
`ALPR_DETECTION_MASK_SOURCE` to a private black/white PNG matching the camera's
input dimensions: white retains pixels and black excludes them. Provisioning
installs it at `/etc/openalpr/detection-mask.png`. Keep camera evidence and masks
out of Git. The installed daemon ignores the legacy `ALPRD_ROI` option; use the
supported mask setting for a reviewed detection area.

OpenALPR crops to the mask's bounding rectangle before choosing detector scale.
A narrower crop can increase scale and undo apparent pixel savings. The installed
mask code also omits the inclusive endpoint when calculating its rectangle.
Check the actual search dimensions rather than assuming bitmap area equals work.

`ALPR_MASK_MAX_PLATE_WIDTH_PERCENT` and `ALPR_MASK_MAX_PLATE_HEIGHT_PERCENT`
(defaults 30 and 10) are relative to the cropped rectangle. Calculate compensated
values if the original maximum candidate size should be retained. Otherwise even
plates above the excluded area may stop qualifying. Compare difficult retained
captures and observe real arrivals; historical successes cannot establish safety
for an unseen approach or an independent accuracy rate.

Apply recognition changes in a quiet window with saved configuration and a tested
rollback. Pause automatic gate handling while changing/restarting recognition,
then explicitly restore and check both `alprd` and `gate_anpr`. The worker depends
on `alprd`, so a recognizer restart can stop it. Let old queued captures exceed
the worker's maximum age before resuming a paused comparison; never verify a mask
by injecting allowlisted jobs into the live queue. There is no mask-only deploy tag.

To disable the mask, clear `ALPR_DETECTION_MASK_SOURCE`, reload the controller
environment and provision again. This removes the managed bitmap and restores
30%/10% candidate limits even if compensation values remain in `ansible.env`.
Use the same maintenance and worker-restoration procedure when reverting.

## Measured example and limits

A quiet-scene Pi 5 comparison on 29 September 2026 measured these recognition
loads; 100% means one CPU core, so the four-core Pi has 400% total capacity:

| Recognition configuration | CPU, one-core scale |
|---|---:|
| 2688×1520 camera input, full frame | 145.54% |
| 1920×1080 input, full frame | 133.02% |
| 1920×1080 input, compensated upper-85% mask | 115.92% |

These short sequential samples suggest a 20.4% recognition reduction, or 7.41
percentage points of whole-Pi capacity. They are not a general guarantee, sustained
thermal benchmark or accuracy estimate. A small replay preserved existing valid
matches, but actual arrival observation remains necessary for any installation.
Three preview sizes at about 10fps cost roughly 22% of one core versus 13% for
one size; keeping the extra profiles was useful for the older displays.

A warmed `OMP_THREAD_LIMIT=1` comparison found no useful saving and was reverted;
leave it unset unless a representative experiment demonstrates a benefit. Zero is
not a valid OpenMP thread limit. Hardware video decoding and hardware-assisted OCR
are separate projects: offloading the camera decoder leaves plate search/OCR on
the CPU, while replacing OpenALPR needs a supported recognition model and accuracy
comparison.
