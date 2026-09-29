# Small performance experiments before changing recognizer

Read-only measurements on the deployed Pi 5, 2026-09-29. A twenty-second quiet
scene is useful for locating the work, but is not a summer or busy-arrival test.
Recognition and preview settings were only inspected; no performance experiment
was run as part of the web-interface changes.

| Component | CPU, where 100% is one core |
|---|---:|
| OpenALPR child | 146.8% |
| FFmpeg live preview | 12.7% |
| JPEG writer | 0.4% |
| nginx | 0.25% |

OpenALPR used about 37% of the four-core machine's capacity. Temperature was
56.75°C, the fan was running around 5,804 RPM and the throttling flags were zero.
Preview encoding represents only about 8% of the measured recognition/preview
CPU combined. Optimising it can help displays without removing most Pi heat.

The camera supplies H.264 at 1280×720, with an average rate of 10fps reported by
the probe. The shared JPEG preview is 960×540 at 10fps and FFmpeg quality 7.
OpenALPR is already a Release `-O3` build, with four analysis workers and a
1280×720 detection-input ceiling. OpenCV internal parallelism is already disabled.

## Reliability takes priority

These are optional experiments, not settings to deploy with the screen changes.
The owner reports exceptionally good recognition; there is no measured accuracy
percentage with an independently counted arrival total. Preserve that behaviour
and require evidence of a useful gain before a production trial. No mask,
thread-limit, camera or JPEG-profile change has been made for this review.

### Smaller JPEGs: test screen smoothness first

A smaller JPEG can reduce transfer and decoding time on the wall-mounted iPad
and Pi touchscreen. The image loader already waits for each load to finish and
includes its duration in the frame interval. If download/decode takes more than
100ms, fewer than ten images can finish per second; fewer pixels may help that
bottleneck. It will not fix a slow source, poor Wi-Fi or every browser issue.

The current source probe reports an average of 10fps, with different nominal
rate metadata. Confirm actual distinct source frames during movement before
treating either metadata figure as a measured limit. Simply raising the JPEG
`fps` filter can duplicate input frames, not create more captured motion.
The current browser interval also has an 80ms floor (12.5 requests/second).
[FFmpeg's frame-rate filter](https://ffmpeg.org/ffmpeg-filters.html#fps-1)

For a controlled comparison:

1. Record the effective Pi settings, JPEG size, source cadence and the actual
   iPad/Pi image-load intervals for a short moving scene. Count new source frames
   as well as completed image loads: the existing FPS badge counts loads, so
   fetching the same JPEG twice must not count as better video. A temporary
   diagnostic should identify frames without adding permanent polling or hashing
   work to the old screens. Include button responsiveness and visible detail.
2. Compare **960×540 at 10fps**, **800×450 at 10fps**, then **960×540 at 10fps**
   again, with the same quality setting and screens. Change width and height
   only; recognition input, retained evidence and camera settings stay unchanged.
   Keep a single shared encoder rather than introducing a second camera session
   and encoder during the comparison. The middle profile has about 31% fewer
   pixels, although JPEG byte size and frame-rate gains will not scale exactly.
3. Before changing it, prepare and test restoration of the captured width and
   height; schedule an automatic restoration for the end of the short trial.
   Cancel that restoration only after checking both physical screens. Restart
   only the preview service for a dimension-only trial; retain the current
   10fps setting so the web app needs no configuration restart. A preview restart
   may briefly freeze the picture and must be observed. Do not use a full deploy
   as the experiment switch: its unrelated handlers can restart other services.
4. Keep the smaller profile only if both screens are smoother without losing
   useful detail. If source measurements later show more than 10 distinct fps,
   a higher preview rate is a separate experiment; it may raise CPU, network and
   display work. Do not change the shared camera rate to obtain it during this
   test. Expect modest Pi cooling even if the displays improve substantially.

The deployment's `gate_stream_overrides` already supports width, height and
FPS without replacing camera credentials. A dedicated short-trial/restoration
helper is not yet implemented; the steps above are acceptance criteria, not a
claim that a timed rollback or device benchmark has already run.

### OCR threads: offline comparison before any live change

Compare **the current unset `OMP_THREAD_LIMIT`** with **`OMP_THREAD_LIMIT=1`**.
Zero is not the baseline: OpenMP requires a positive integer, and the behaviour
of invalid values is implementation-defined. Tesseract recommends testing one
thread when multiple OCR jobs compete. The installed Tesseract 5.5 loads OpenMP;
OpenALPR already has four analysis workers. There was no obvious thread storm in
the quiet-scene measurement, so an idle saving is not established.
[OpenMP specification](https://www.openmp.org/spec-html/5.0/openmpse58.html),
[Tesseract guidance](https://tesseract-ocr.github.io/tessdoc/FAQ.html#can-i-increase-speed-of-ocr)

Use this sequence before considering a service change:

1. Freeze a private manifest of input images and a copy of the effective
   recognizer configuration/model versions. Include difficult and easy reads,
   day/night images, small/distant plates, glare and multiple plates. Preserve
   expected results and candidate lists. Keep every input and result on the Pi
   or another explicitly chosen private test host, outside the public repository.
2. Use `/usr/local/bin/alpr -c gb --config <private-copy-of-openalpr.conf> -j`
   on local image paths. Start fresh processes for each condition: baseline
   through `env -u OMP_THREAD_LIMIT`, candidate through
   `env OMP_THREAD_LIMIT=1`. This CLI is installed and supports JSON and local
   image input. It does not run the gate worker or publish jobs to its queue.
   Do not use the synthetic-job enqueue option as an OCR benchmark.
3. Run equal warm-ups and alternate baseline/candidate/baseline on identical
   inputs. First compare recognised text, ranked candidates, confidence and
   detections; investigate every difference. Record wall time and CPU time
   separately. The repository has mocked worker regression tests and a CLI
   smoke check, but no full corpus benchmark/replay harness yet. In particular,
   its generic smoke check uses `eu`; a parity experiment must retain this
   deployment's `gb` settings and runtime data.
4. Run correctness comparisons in an isolated transient service with no network
   or GPIO access (`PrivateNetwork=yes`, `PrivateDevices=yes`), read-only inputs,
   no worker import and a fixed runtime limit. The Pi has cgroup v2's CPU
   controller, so a low-priority test can use `CPUQuota=20%`, `CPUWeight=1` and
   `Nice=19`. This quota is one fifth of **one core**, not 20% of the whole Pi.
   It limits extra CPU work; it does not guarantee zero heat, memory, disk or
   cache impact. Monitor live services and stop only the benchmark unit if they
   worsen. Prefer a spare equivalent Pi for a throughput/burst benchmark.
5. Do not infer daemon throughput or arrival latency from a throttled CLI test.
   Its startup cost and isolation differ from the persistent four-worker
   daemon. Only pursue an on-device performance comparison if correctness holds
   and initial results justify it. A live trial needs a separately recorded
   baseline, a short observation window and restoration prepared beforehand.
   A temporary, uniquely named systemd environment drop-in can set the limit;
   rollback removes **that file only**, reloads systemd and restores the original
   service and any affected dependent worker. The gate worker declares
   `Requires=alprd.service`; a recognizer restart can affect it too. Its current
   in-memory cooldown/deduplication state is another reason to defer this live
   trial until restart behaviour is covered, ideally after ROADMAP REL-01.
   Both applying the limit and rolling it back briefly interrupt recognition.
   Keep the original production configuration untouched until that trial is
   deliberately chosen.

The isolation properties and resource limits need a dry check on this Pi before
implementing a benchmark runner. No transient benchmark service, replay or live
thread-limit trial has been launched. A candidate must preserve observed reads,
avoid worse long-tail latency and deliver a repeatable benefit; lower CPU alone
is not sufficient.
[systemd resource-control definitions](https://github.com/systemd/systemd/blob/v257/man/systemd.resource-control.xml),
[systemd service isolation definitions](https://github.com/systemd/systemd/blob/v257/man/systemd.exec.xml)

### Detection mask: all retained images are a prerequisite

Do not choose a mask from a few examples. Before excluding any part of this
camera view, inventory **every retained historical image** on the Pi, including
files not linked by a current database row. Record path, hash, dimensions and
capture time where available in a private manifest, with a clear inventory
cut-off and any unreadable/missing files. Account for new images collected during
review rather than silently omitting them.

Review every image at sufficient resolution, with a generous proposed mask
overlay, and record its review result. Automated plate-coordinate bounds and
contact sheets can help find outliers but cannot replace full-image inspection
of ambiguous cases. Preserve all approach edges and plate locations, allowing
margin for glare, partial plates, camera movement and vehicles taking a different
line. Compare unmasked and masked recognition on the entire manifest before
considering a live trial; reject lost or delayed useful detections. Where only
isolated stills exist, they cannot establish earliest-detection timing: that
needs an arrival sequence or separately captured test approaches.

The archive is biased towards scenes the current system saved. Even reviewing
all of it cannot prove safety for an unseen approach or count vehicles that
were never recorded. Additional day/night and edge-of-approach observation is
needed, and an unchanged full-frame detector remains an entirely reasonable
choice if the margins or savings are unconvincing. No archive review or mask
experiment has been carried out yet.

The installed recognizer supports `detection_mask_image` in `openalpr.conf`
(unlike the daemon's ignored `roi=` option). It reduces the detector search
rectangle while restoring coordinates into the original frame; savings depend
on the resulting bounding rectangle, not just the number of black pixels.
Keep the mask private and preserve the original configuration for immediate
restoration. [Detector source](https://raw.githubusercontent.com/openalpr/openalpr/736ab0e608cf9b20d92f36a873bb1152240daa98/src/openalpr/detection/detector.cpp)

Resolution/cadence changes are lower priority. Reducing recognition input size
can remove distant plate detail. Fewer analysis workers need not reduce work if
the same frames are still processed. The daemon video-buffer FPS argument is
stored but not enforced in the installed source; a real analysis-rate limiter
needs a daemon patch and validation.

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
