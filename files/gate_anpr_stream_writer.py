#!/usr/bin/env python3
import argparse
import os
import sys
import time


def iter_jpegs(stream):
    buffer = bytearray()
    start = -1
    # BufferedReader.read(n) can wait for bytes from the following frame to
    # fill n. read1 returns the bytes already available from the pipe instead.
    read = getattr(stream, "read1", stream.read)
    while True:
        chunk = read(32768)
        if not chunk:
            return
        buffer.extend(chunk)
        while True:
            if start == -1:
                start = buffer.find(b"\xff\xd8")
                if start == -1:
                    # The JPEG start marker can straddle two pipe reads.
                    buffer[:] = b"\xff" if buffer.endswith(b"\xff") else b""
                    break
            end = buffer.find(b"\xff\xd9", start + 2)
            if end == -1:
                if start > 0:
                    buffer = buffer[start:]
                    start = 0
                if len(buffer) > 16 * 1024 * 1024:
                    # A corrupt encoder output must not grow memory forever.
                    buffer.clear()
                    start = -1
                break
            jpeg = bytes(buffer[start : end + 2])
            yield jpeg
            buffer = buffer[end + 2 :]
            start = -1


def write_atomic(path, payload):
    directory = os.path.dirname(path)
    base = os.path.basename(path)
    tmp_path = os.path.join(directory, f".{base}.tmp")
    with open(tmp_path, "wb") as handle:
        handle.write(payload)
    # This is an ephemeral preview, normally on tmpfs. Atomic replacement is
    # sufficient; durable per-frame fsyncs only add latency and disk writes.
    os.replace(tmp_path, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", help="Path to stream.jpg")
    parser.add_argument("--min-interval-ms", type=int, default=0)
    args = parser.parse_args()

    last_write = 0.0
    last_error = None
    for jpeg in iter_jpegs(sys.stdin.buffer):
        now = time.monotonic() * 1000
        if args.min_interval_ms and now - last_write < args.min_interval_ms:
            continue
        try:
            write_atomic(args.output, jpeg)
            last_write = now
        except OSError as exc:
            if last_error is None or now - last_error >= 10000:
                print(f"Unable to publish stream JPEG: {exc}", file=sys.stderr, flush=True)
                last_error = now


if __name__ == "__main__":
    main()
