#!/usr/bin/env python3
import argparse
import os
import sys
import time


def iter_jpegs(stream):
    buffer = bytearray()
    start = -1
    while True:
        chunk = stream.read(32768)
        if not chunk:
            return
        buffer.extend(chunk)
        while True:
            if start == -1:
                start = buffer.find(b"\xff\xd8")
                if start == -1:
                    buffer.clear()
                    break
            end = buffer.find(b"\xff\xd9", start + 2)
            if end == -1:
                if start > 0:
                    buffer = buffer[start:]
                    start = 0
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
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", help="Path to stream.jpg")
    parser.add_argument("--min-interval-ms", type=int, default=0)
    args = parser.parse_args()

    last_write = 0.0
    for jpeg in iter_jpegs(sys.stdin.buffer):
        now = time.monotonic() * 1000
        if args.min_interval_ms and now - last_write < args.min_interval_ms:
            continue
        try:
            write_atomic(args.output, jpeg)
            last_write = now
        except Exception:
            continue


if __name__ == "__main__":
    main()
