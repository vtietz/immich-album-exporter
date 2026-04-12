from __future__ import annotations

import argparse
import logging
import sys

from .config import load_config
from .importer import build_importer


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
    config = load_config(args.config)
    importer, client, state = build_importer(config)

    try:
        if args.command == "sync-once":
            importer.run_once()
        else:
            importer.run_forever()
    finally:
        state.close()
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
