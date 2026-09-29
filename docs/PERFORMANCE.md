# Measured performance work — 29 September 2026

The wall displays and recognition reliability take priority. HTTPS remains
optional. No detection mask, camera setting or recognition-input change is
included in the display work below.

## Where the CPU goes

An initial twenty-second quiet-scene sample measured OpenALPR at 146.8% of one
core, preview FFmpeg at 12.7%, the former JPEG writer at 0.4%, and nginx at 0.25%.
Recognition therefore used about 37% of this four-core Pi's total capacity.
Temperature was 56.75°C with working cooling and no throttling flags. This is
not a summer or busy-arrival benchmark.

Recognition receives the camera's **2688×1520 H.264 main feed**. The independent
preview receives **1280×720 H.264** and publishes the main JPEG at **960×540,
10fps, FFmpeg quality 7**. Earlier notes conflated those two camera inputs.
OpenALPR is a Release `-O3` build with four analysis workers and a 1280×720
maximum detection input; OpenCV internal parallelism is already disabled.

## Smaller display JPEGs

The optional shared profile publisher keeps one camera connection and decoder,
then creates three atomic RAM images from the same decoded frame:

| Screen | JPEG | Pixels versus the main JPEG |
|---|---:|---:|
| Live tab (`#stream`) and larger desktop Home | 960×540 | unchanged |
| iPad / smaller-screen Home | 800×450 | 30.6% fewer |
| Pi `/fullscreen` | 640×360 | 55.6% fewer |

The configured main dimensions remain authoritative; profiles never upscale
its width. These deployment-specific dimensions are not forced public defaults.
Profile generation defaults off and works with ordinary HTTP or optional HTTPS.
It changes neither the displayed camera size nor retained recognition evidence.
The image remains uncropped. nginx serves the files directly with caching
disabled, falling back to the main image when a profile is unavailable.

Extra encodes have a server CPU cost, even though smaller downloads and fewer
pixels can make old clients smoother. This is a display experiment, not a claim
of a large cooling improvement. Home's existing disclosure now reports actual
visible CSS viewport, loaded JPEG size and a bounded five-second image-load
rate. That rate can include repeated camera frames. The configured 10fps output
also cannot create extra captured motion from a slower source.

A five-second synthetic test on the actual Pi produced 47/46/46 distinct frames
for the three outputs, with the expected dimensions and **zero partial JPEGs**
while repeatedly opening the files during replacement. Automated browser checks
cover the reduced Safari viewport and profile selection, including stale `vh`
and the old summary-layout fallback. Those tests use Chrome; a user-agent
string does not reproduce physical iOS 12 WebKit.

A private rollback preserves the original preview unit, profile settings and
web files. The targeted `--tags web,preview` deployment leaves recognition and
the gate worker running. Setting `GATE_WEB_STREAM_PROFILES=0` with the same tags
returns every screen to the main JPEG without changing recognition.

### Deployed measurements

Over a twenty-second live sample, each output contained **9.89 distinct JPEGs
per second**. Mean sizes were 26,667 bytes (main), 20,259 (tablet) and 14,152
(kiosk): about 24% and 47% less transfer respectively. A quiet camera still has
sensor noise; distinct JPEG bytes do not establish useful motion cadence.

Preview service CPU rose from **13.38% to 22.13% of one core**, an increase of
about **2.2 percentage points of whole-Pi capacity**. Recognition measured
146.28% before and 147.91% after. These are short windows, not a controlled
thermal study. Archive indexing was running separately at low priority during
this work; the 63.9°C sample is not attributable to preview profiles alone.
After archive processing stopped, a second twenty-second sample measured
21.75% preview CPU, 144.37% recognition CPU, 59.5°C, and 9.85–9.95 distinct
JPEGs/sec across the profiles.

Recognition and gate-worker PIDs, their configuration hashes and all unrelated
environment settings were unchanged by the deployment. All six checked services
were active, twelve deployed files matched local hashes and the frames were
fresh. The independent preview restoration timer was then cancelled; the private
backup remains available.

Live read-only Chrome samples completed **9.6–9.9 image loads/sec** across the
iPad, Pi and desktop viewport cases. The owner confirmed that the physical iPad
now renders perfectly. Its actual image-load rate has not been reported; physical
wall-device smoothness may differ from the Chrome measurements.

## OCR threads: trial completed, original setting restored

The owner authorised a short quiet-period live comparison. An independent timed
restoration was tested and armed first. The gate worker was stopped during the
recognizer changes, queued results were checked, and no GPIO or notification
jobs were submitted. Original configuration and unit hashes were retained.

| Condition | Sample | Recognition CPU, 100% = one core |
|---|---:|---:|
| Unset, initial window | 35s | 136.31% |
| `OMP_THREAD_LIMIT=1`, after warm-up | 35s | 144.88% |
| Unset again, after warm-up | 35s | 144.76% |

The comparable warmed windows differ by just **0.08% relative**, with the
candidate slightly higher. The first window includes startup variability and
must not be used to claim a gain. There was no useful idle CPU saving; the
thread limit was removed. Both recognition and worker were verified active and
enabled, the queue was empty, original hashes matched, and the temporary
restoration timer/drop-in were removed. Temperatures stayed about 57–59°C with
no throttling flags. Cgroup counters include the recognizer supervisor and child.

This quiet scene did not exercise arriving vehicles, so it establishes neither
OCR-result parity nor busy-scene savings. An isolated offline corpus comparison
would be needed to investigate that separately. It is not worth retaining an
unproven production setting on the strength of this result. The baseline is
**unset**, not zero: OpenMP expects a positive thread limit.
[OpenMP specification](https://www.openmp.org/spec-html/5.0/openmpse58.html),
[Tesseract guidance](https://tesseract-ocr.github.io/tessdoc/FAQ.html#can-i-increase-speed-of-ocr)

## Detection area: audit only

The private inventory includes every retained image, including files without a
current database row. Full-coverage contact sheets and full-resolution inspection
of ambiguous cases support an archive review; they are not a recognition parity
benchmark. Private captures, image names, plates and credentials stay outside
this public repository. No mask is applied by the audit.

The completed inventory contains **13,401 files: 13,294 full-size originals
and 107 cached previews**. Every original is 2688×1520. All **13,400 normally
decodable images** were represented and visually reviewed across 134 contact
sheets; 19 follow-up originals were checked at full resolution and their hashes
matched the source manifest. This is full-archive coverage using audit-resolution
copies, not a claim that every full-size original was downloaded locally.
The final delta check found no added, missing or modified source files.

One original failed normal decoding; permissive recovery still leaves most of
its image missing. Three other originals contain visible encoding artifacts,
including one with an obscured plate. Those cases remain explicit limitations,
not verified successful reads. No files were deleted or repaired on the Pi.

Across interpretable images the camera view remained consistent. Plates occur
near the side edges and approach road, so retain **all width and the top**.
The lowest inspected close-approach plate reaches approximately 70.3% of frame
height; a conservative observed bound is 72%. Keeping the top 85% leaves about
223 source pixels below that observed plate. Excluding the bottom 15% is therefore
a plausible **offline test candidate**, not a production recommendation. Given
the excellent reported hit rate and modest potential benefit, keep full-frame
recognition until a separate parity and timing comparison justifies a change.

The installed resize code is width-first: when source width exceeds 1280, it
uses that width ratio and skips the 720-height branch. Integer output dimensions
therefore make the current 2688×1520 input **1280×723**, despite the nominal
1280×720 configuration. Retaining full width and 85% height (2688×1292) yields
**1280×615**, or **14.94% fewer detector-search pixels**. Original-frame area
falls 15%. This is geometry only; total CPU savings remain unmeasured.

The installed `detection_mask_image` option is relevant; the daemon's `roi=`
option is ignored. OpenALPR crops to the mask's bounding rectangle **before**
choosing its detector scale. A width reduction can increase that scale and
cancel an apparent pixel saving. Therefore both original-image area and effective
scaled detector area must be calculated. Neither predicts an equal percentage
reduction in total CPU: decoding, tracking, OCR and other work remain.
[Installed detector source](https://raw.githubusercontent.com/openalpr/openalpr/736ab0e608cf9b20d92f36a873bb1152240daa98/src/openalpr/detection/detector.cpp)

The saved archive is biased towards scenes the system retained. It cannot prove
safety for an unseen approach or establish an independently measured hit rate.
A candidate still needs unmasked/masked comparisons, generous margins and
separate arrival-timing checks before any live use. Keeping the full frame is
reasonable if the potential saving is small or uncertainty remains.

## Hardware offload has several different meanings

Pi 5 advertises HEVC/H.265 hardware decoding; the current input is H.264.
A compatible camera/decoder change might offload video decoding while keeping
OpenALPR. It would leave plate search and OCR running on the CPU. The installed
OpenALPR GPU detector is CUDA-based, and its OpenCL branch falls back on the
installed OpenCV 4. An AI accelerator would need a supported recognition model
and a different processing path. A larger migration should follow evidence
about the remaining bottleneck, rather than being assumed to solve every stage.
[Pi 5 specifications](https://www.raspberrypi.com/products/raspberry-pi-5/),
[Raspberry Pi camera/video documentation](https://www.raspberrypi.com/documentation/computers/camera_software.html#camera-software)

Compiler optimisation and working cooling were already present, with the
`ondemand` CPU governor. There is no newly discovered switch that can honestly
promise a large free saving.
