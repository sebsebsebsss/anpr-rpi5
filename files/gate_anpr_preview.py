#!/usr/bin/env python3
"""Publish shared, atomic JPEG profiles from one camera decoder into RAM."""

import argparse
import math
import os
from pathlib import Path


def dimensions(env, key, default):
    value = int(env.get(key, default))
    if not 16 <= value <= 4096 or value % 2:
        raise ValueError(f"{key} must be an even integer between 16 and 4096")
    return value


def build_command(env, output, *, ffmpeg="/usr/bin/ffmpeg", input_args=None):
    width = dimensions(env, "GATE_WEB_STREAM_WIDTH", 1280)
    height = dimensions(env, "GATE_WEB_STREAM_HEIGHT", 720)
    fps = float(env.get("GATE_WEB_STREAM_FPS", "5"))
    if not math.isfinite(fps) or not 0 < fps <= 30:
        raise ValueError("GATE_WEB_STREAM_FPS must be greater than zero and at most 30")
    enabled = env.get("GATE_WEB_STREAM_PROFILES", "0")
    if enabled not in {"0", "1"}:
        raise ValueError("GATE_WEB_STREAM_PROFILES must be 0 or 1")
    output = Path(output)
    if not output.is_absolute():
        raise ValueError("JPEG output must be an absolute path")
    if output.name in {"stream-tablet.jpg", "stream-kiosk.jpg"}:
        raise ValueError("Main JPEG output cannot use a reserved profile filename")
    if input_args is None:
        source = env.get("GATE_WEB_STREAM_RTSP_URL", "").strip()
        if not source:
            raise ValueError("GATE_WEB_STREAM_RTSP_URL is required")
        input_args = [
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-analyzeduration",
            "0",
            "-probesize",
            "32k",
            "-max_delay",
            "0",
            "-rtsp_transport",
            "tcp",
            "-i",
            source,
        ]
    profiles = [("full", output)]
    graph = f"[0:v]fps={fps:g},scale={width}:{height}"
    if enabled == "1":
        tablet = dimensions(env, "GATE_WEB_STREAM_TABLET_WIDTH", 800)
        kiosk = dimensions(env, "GATE_WEB_STREAM_KIOSK_WIDTH", 640)
        profiles += [
            ("tablet", output.with_name("stream-tablet.jpg")),
            ("kiosk", output.with_name("stream-kiosk.jpg")),
        ]
        graph += ",split=3[full][tablet_in][kiosk_in];"
        graph += f"[tablet_in]scale=w='min({tablet},iw)':h=-2[tablet];"
        graph += f"[kiosk_in]scale=w='min({kiosk},iw)':h=-2[kiosk]"
    else:
        graph += "[full]"
    command = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin", "-y"]
    command += input_args
    command += ["-filter_complex_threads", "1", "-filter_complex", graph]
    for label, target in profiles:
        # image2's temporary-file/rename mode never exposes a partial frame.
        # One shared output per profile avoids encoding work per connected screen.
        command += [
            "-map",
            f"[{label}]",
            "-an",
            "-c:v",
            "mjpeg",
            "-threads",
            "1",
            "-q:v",
            "7",
            "-f",
            "image2",
            "-update",
            "1",
            "-atomic_writing",
            "1",
            str(target),
        ]
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", help="Existing full-size RAM JPEG path")
    args = parser.parse_args()
    try:
        command = build_command(os.environ, args.output)
    except (ValueError, TypeError) as exc:
        # Configuration errors identify keys, never the private camera URL.
        parser.error(str(exc))
    if os.environ.get("GATE_WEB_STREAM_PROFILES", "0") == "0":
        # Existing clients then receive nginx's full-size fallback, not an old
        # frozen profile left behind by an earlier enabled configuration.
        for name in ("stream-tablet.jpg", "stream-kiosk.jpg"):
            Path(args.output).with_name(name).unlink(missing_ok=True)
    os.execv(command[0], command)


if __name__ == "__main__":
    main()
