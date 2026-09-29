# Small performance experiments before changing recognizer

Read-only measurements on the deployed Pi 5, 2026-09-29. A twenty-second quiet
scene is useful for locating the work, but is not a summer or busy-arrival test.
No production settings or services were changed for this review.

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

## Experiments worth trying

1. **A generously bounded detection mask.** The daemon ignores the exposed
   `roi=` option, but the installed recognizer supports `detection_mask_image`
   in `openalpr.conf`. It reduces the search rectangle before the detector runs,
   while restoring detection coordinates into the original frame. This offers
   a configuration experiment before considering a cropping refactor. Keep the
   actual image/mask private. Use daytime, night-time and approach-edge examples
   before excluding any part of the scene. Compare CPU, earliest usable plate,
   missed arrivals and wrong matches. Savings have not yet been measured.
   [Detector source](https://raw.githubusercontent.com/openalpr/openalpr/736ab0e608cf9b20d92f36a873bb1152240daa98/src/openalpr/detection/detector.cpp)

2. **A smaller shared preview at the same FPS.** Compare 800×450 or 768×432 with
   the current 960×540 using the actual screens. Measure JPEG size, distinct
   displayed frames and visible detail as well as encoder CPU. Keep event
   evidence at its existing size. Start with one shared profile: encoding extra
   simultaneous profiles could increase CPU. Expect modest Pi savings; an old
   display may benefit more from less download/decode work.

3. **Limit nested OCR threads experimentally.** The installed Tesseract 5.5
   library loads OpenMP and no thread limit is configured. Compare
   `OMP_THREAD_LIMIT=1` against the existing setting with the same private OCR
   examples and concurrent-arrival workload. OpenALPR already has four workers.
   There was no obvious thread storm in the quiet-scene sample, so this is a
   cheap A/B experiment, not a promised idle-CPU saving. Verify recognition
   output and latency, not CPU alone.
   [Tesseract guidance](https://tesseract-ocr.github.io/tessdoc/FAQ.html#can-i-increase-speed-of-ocr)

4. **Resolution/cadence only after those checks.** Detection input limits work,
   but reducing them can remove distant plate detail. Fewer analysis workers
   need not reduce work if the same frames are still processed. The apparent
   daemon video-buffer FPS argument is stored but not enforced in the installed
   source. A real analysis-rate limiter needs a daemon patch and validation;
   changing the shared camera rate would also reduce the display frame rate.

Use a small labelled set of private captures/jobs for these comparisons;
starting with a research-scale replay framework is unnecessary. Recognition
experiments must disable GPIO and notifications. Preserve the original files
and effective configuration so an unsuccessful comparison is easy to reverse.

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
