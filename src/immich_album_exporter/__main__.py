from __future__ import annotations

import argparse
import logging
import sys

from .config import load_configs
from .importer import build_runner


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="immich-album-exporter")
    parser.add_argument("command", choices=["sync-once", "worker"])
    parser.add_argument("--config", default="/config/config.yml")
    args = parser.parse_args(argv)

    configure_logging()
    configs = load_configs(args.config)
    runner = build_runner(configs)

    try:
        if args.command == "sync-once":
            if not runner.run_once():
                # Album enumeration failures must not look like a successful (empty) run.
                return 1
        else:
            runner.run_forever()
    finally:
        runner.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
