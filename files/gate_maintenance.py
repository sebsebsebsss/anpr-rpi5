#!/usr/bin/env python3
import argparse

from gate_runtime import configure_logging, prune_old_events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--retention-days", type=int, default=30)
    args = parser.parse_args()

    logger = configure_logging(
        "gate_maintenance",
        log_path="/var/log/gate-anpr/gate-maintenance.log",
    )
    prune_old_events(args.db_path, args.retention_days, logger)


if __name__ == "__main__":
    main()
