# Measured performance work — 29 September 2026

The wall displays and recognition reliability take priority. HTTPS remains
optional. Display tuning leaves camera and recognition settings unchanged.
The separately approved recognition changes are recorded below.

## Where the CPU goes

An initial twenty-second quiet-scene sample measured OpenALPR at 146.8% of one
core, preview FFmpeg at 12.7%, the former JPEG writer at 0.4%, and nginx at 0.25%.
Recognition therefore used about 37% of this four-core Pi's total capacity.
Temperature was 56.75°C with working cooling and no throttling flags. This is
not a summer or busy-arrival benchmark.

At the original baseline, recognition received the camera's **2688×1520 H.264
main feed**. The later 1080p/mask deployment is recorded below. The independent
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

### Sharing the tablet JPEG with the kiosk: measured, left unchanged

A short low-priority comparison encoded the same private fixed JPEG at 10fps
with 3→2→1→2→3 outputs, 80 frames per condition. Mean child-process CPU equivalent
was 17.50% of one core for three outputs, 13.15% for main+tablet, and 8.02% for
main alone. This isolates scaling/encoding; it is not a measurement of live
RTSP decoding or physical-client performance. Removing the kiosk encode would
therefore save approximately **1.09 percentage points of whole-Pi CPU** in this
fixture, with the larger Live image still requiring its own output.

The kiosk would then receive 800×450 instead of 640×360: 56% more decoded pixels
and approximately 43% more JPEG bytes using the earlier live-size samples.
Given the working wall displays and acceptance of the small server CPU cost,
retain the three current sizes. The comparison created only temporary private
images and made no camera, live-preview or recognition changes.

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

## Detection area: archive audit and temporary CPU comparison

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
a plausible **temporary CPU-test candidate**. A guarded quiet-window comparison
can measure CPU savings without first building an offline replay. Before leaving
the mask enabled, separately check recognition parity and arrival timing.

The installed resize code is width-first: when source width exceeds 1280, it
uses that width ratio and skips the 720-height branch. Integer output dimensions
therefore made the original 2688×1520 input **1280×723**, despite the nominal
1280×720 configuration. An ideal full-width 85%-height crop (2688×1292) would
yield 1280×615, or 14.94% fewer detector-search pixels. The installed mask code
subtracts inclusive bounds without adding one, however: that bitmap produces a
2687×1291 search rectangle, then **1280×614**, or **15.08% fewer search pixels**.
It also excludes the last right-edge pixel. These are search-area calculations,
not predictions of equal total-CPU savings.

The installed `detection_mask_image` option is relevant; the daemon's `roi=`
option is ignored. OpenALPR crops to the mask's bounding rectangle **before**
choosing its detector scale. A width reduction can increase that scale and
cancel an apparent pixel saving. The mask also adds full-frame allocation and
bitwise operations. Therefore both original-image area and effective scaled
detector area must be calculated. Neither predicts an equal percentage reduction
in total CPU: decoding, tracking, OCR and other work remain.

The maximum candidate plate size is proportional to the cropped rectangle. With
the current 10% maximum-height setting, this mask reduces the permitted height
from approximately 152 to 129 source pixels (72 to 61 detector pixels). It can
therefore change which plates qualify even above the mask boundary; a location
audit alone does not establish recognition parity.
[Installed detector source](https://raw.githubusercontent.com/openalpr/openalpr/736ab0e608cf9b20d92f36a873bb1152240daa98/src/openalpr/detection/detector.cpp),
[installed mask source](https://raw.githubusercontent.com/openalpr/openalpr/736ab0e608cf9b20d92f36a873bb1152240daa98/src/openalpr/detection/detectormask.cpp)

The saved archive is biased towards scenes the system retained. It cannot prove
safety for an unseen approach or establish an independently measured hit rate.
A candidate still needs unmasked/masked comparisons, generous margins and
separate arrival-timing checks before leaving a mask enabled for normal use.
A temporary CPU comparison with automatic gate handling paused is a narrower
experiment. Keeping the full frame is reasonable if the saving is small or
uncertainty remains.

### Quiet-window mask trial: useful saving, full frame restored

A guarded full frame → top-85% mask → full frame comparison ran on the Pi.
Each phase restarted the recognizer, warmed for 20 seconds and measured its
entire cgroup for approximately 35 seconds. Camera settings, OCR threads and
preview profiles stayed unchanged. The automatic gate worker was paused; no
jobs appeared, and no gate commands or notifications were issued by the test.

| Phase | Recognition CPU, one-core scale | JPEG producer CPU | End temperature |
| --- | ---: | ---: | ---: |
| Full frame, before | 146.68% | 21.60% | 58.40°C |
| Top-85% mask | 126.47% | 21.86% | 56.20°C |
| Full frame, after | 144.39% | 21.83% | 57.85°C |

Against the mean of the two full-frame windows, recognition used **13.1% less
CPU**. That is **4.77 percentage points of the whole four-core Pi**, rather than
13.1% of its total capacity. The masked result was below both baseline windows;
preview CPU stayed essentially unchanged. No throttling flags appeared. These
short temperature samples do not establish a sustained cooling improvement.

The independent five-minute rollback guard was tested unarmed first. A temporary
`/run` configuration directory and systemd override selected the mask; original
production files were untouched. Restoration stopped recognition, removed the
override and waited 12 seconds so queued captures would fail the worker's
10-second age limit before normal gate handling resumed. Original hashes matched,
recognition and gate worker were active/enabled, the queue was empty, and web and
preview processes kept their original PIDs. Temporary copied configuration, mask,
runner and timers were removed after verification.

This is evidence that the **mask configuration as tested** saves quiet-scene CPU,
not recognition-parity evidence. It includes the changed maximum candidate-size
threshold described above. A useful next comparison would preserve the original
permitted plate size, replay retained close/edge cases against full-frame results,
and remeasure CPU before enabling a mask for ordinary arrivals. Full-frame
recognition was restored after this initial trial; the later rollout is below.

### Optional mask deployment

Public defaults continue to use the full frame. To reproduce an audited mask,
set `ALPR_DETECTION_MASK_SOURCE` in the ignored `ansible.env` to a private PNG
on the controller. The image should match the camera input dimensions, with
white retained areas and black excluded areas. Provisioning installs it as
`/etc/openalpr/detection-mask.png` and selects it in `openalpr.conf`.

`ALPR_MASK_MAX_PLATE_WIDTH_PERCENT` and
`ALPR_MASK_MAX_PLATE_HEIGHT_PERCENT` specify candidate-size limits for the
cropped search rectangle. Their defaults are 30 and 10; calculate compensation
for the actual mask and installed detector before enabling it. A source-resolution
change requires reviewing both the bitmap and those limits. These are explicit
settings, not automatic claims that any mask preserves recognition accuracy.

To disable the mask, clear `ALPR_DETECTION_MASK_SOURCE` and provision again.
This removes the managed mask and restores the original 30% width / 10% height
limits, even if compensation values remain in the private environment file.

Changing this configuration restarts `alprd`. Use a quiet maintenance window and
a tested rollback guard: pause automatic gate handling, apply the configuration,
restart recognition, and explicitly restore the worker and verify both services.
The worker requires `alprd`, so restarting recognition can stop it. When restoring
from a paused-worker comparison, let queued captures exceed the worker's maximum
age before resuming automatic handling; do not test by publishing synthetic
allowlisted jobs. There is intentionally no separate mask-only deployment tag.

### Deployed 1080p input and compensated upper-85% mask

The camera owner changed the main stream to 1920×1080. A fresh stream probe
confirmed H.264 at that resolution, reporting an average rate of 4fps. Two
20-second samples measured recognition at 132.60% and 133.43% of one core with
no mask. The running daemon was healthy; its capture loop uses each frame's
current dimensions and reconnects on failed reads. The daemon was left running
for that initial CPU observation.

After separate approval, an upper-85% detection mask was enabled. The private
1920×1080 bitmap has 919 white rows, accounting for the installed mask's
inclusive-endpoint omission: its actual search rectangle is 1919×918, scaled
to 1280×612. Candidate limits of 30.02% width and 11.78% height preserve the
original maximum candidate size of **384×72 detector pixels**. The public
configuration remains mask-off by default, and neither the private bitmap nor
private environment settings are committed.

Before deployment, 15 difficult retained images were resized to 1080p and
compared offline with the installed recognizer and pure worker matching logic.
All **three existing valid allowlist matches** and **four existing visually
correct top-10 candidates** survived the 85% mask. Two additional images matched
correctly. Changed partial/incorrect guesses initially looked like regressions;
they were not lost valid matches. The daemon actually emits ten candidates from
its daemon defaults; the legacy `topn=5` in `openalpr.conf` does not control it.
A 95% variant lost a correct nighttime candidate, so retaining more pixels is
not automatically safer. This small replay is a sanity check, not an accuracy
estimate or a substitute for observing real arrivals at the new camera setting.

| Configuration | Recognition CPU, one-core scale | Whole-Pi capacity used by recognition |
| --- | ---: | ---: |
| Original 2688×1520, full frame | 145.54% | 36.38% |
| 1920×1080, full frame | 133.02% | 33.25% |
| 1920×1080, compensated upper-85% mask | 115.92% | 28.98% |

The masked measurements were 115.50% and 116.33%, each over 20 seconds after a
20-second warm-up. Together the two changes reduced quiet-scene recognition CPU
by **20.4%**, saving **7.41 percentage points of whole-Pi capacity** against the
original baseline. The mask alone saved 12.9% relative to the 1080p unmasked
observation. Preview CPU remained about 22% of one core; temperature was 56.75°C
and throttling flags were clear. These sequential observations are not a long-term
thermal or busy-arrival benchmark.

Deployment tested an independent unarmed rollback guard, armed a five-minute
restoration timer, then stopped the gate worker and recognizer. Old captures
were allowed to age past the worker's ten-second limit before the new recognizer
and automatic gate handling resumed. Both are active, the queue was empty, and
no recognition errors appeared during the measured interval. Web/preview process
IDs and unrelated configuration hashes were unchanged. The successful setting
was retained and its timer cancelled; the previous full-frame configuration and
restoration script remain in a private backup. Local mask settings are saved for
future provisioning. Live previews remain full-frame and HTTPS remains optional.

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
