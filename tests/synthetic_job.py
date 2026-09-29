#!/usr/bin/env python3
import argparse
import json
import time
import uuid

import greenstalk


def build_job(plate, epoch_ms):
    return {
        "uuid": str(uuid.uuid4()),
        "epoch_time": epoch_ms,
        "processing_time_ms": 50,
        "results": [
            {
                "candidates": [
                    {"plate": plate, "confidence": 85.0},
                ],
            }
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Preview a synthetic ALPR job; enqueue only with --enqueue.")
    parser.add_argument(
        "--enqueue", action="store_true", help="Send to the worker queue: an allowed plate can activate the gate"
    )
    parser.add_argument("--plate", default="A1ABC", help="Plate string to send")
    parser.add_argument(
        "--delay",
        type=int,
        default=0,
        help="Offset in seconds added to current epoch_time "
        "(default 0 = now, so the worker accepts it immediately; "
        "use a negative value to backdate)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Beanstalkd host")
    parser.add_argument("--port", type=int, default=11300, help="Beanstalkd port")
    parser.add_argument("--tube", default="alprd", help="Beanstalkd tube")
    args = parser.parse_args()

    epoch_ms = int((time.time() + args.delay) * 1000)
    job = build_job(args.plate, epoch_ms)

    if not args.enqueue:
        print(json.dumps(job, indent=2))
        return

    client = greenstalk.Client((args.host, args.port))
    client.use(args.tube)
    client.put(json.dumps(job))
    print("Enqueued job for plate %s (delay=%ss)" % (args.plate, args.delay))


if __name__ == "__main__":
    main()
