from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .db import catalog_stats, sync_catalog


DEFAULT_DB = Path("data/db/catalog.sqlite")


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="medicine-catalog", description="Sync the MFDS authorized drug product catalog")
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="Synchronize the full MFDS product catalog")
    sync.add_argument("--db", type=Path, default=DEFAULT_DB)
    sync.add_argument("--service-key", default=os.environ.get("DATA_GO_KR_SERVICE_KEY", ""))
    sync.add_argument("--page-size", type=int, default=100)
    sync.add_argument("--quiet", action="store_true")
    sync.add_argument("--json", action="store_true")

    stats = sub.add_parser("stats", help="Show local full-catalog statistics")
    stats.add_argument("--db", type=Path, default=DEFAULT_DB)
    stats.add_argument("--json", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "sync":
        if not args.service_key.strip():
            raise SystemExit("DATA_GO_KR_SERVICE_KEY is required for MFDS full-catalog sync")
        payload = sync_catalog(args.db, service_key=args.service_key, page_size=args.page_size, progress=not args.quiet)
    else:
        payload = catalog_stats(args.db)
    _emit(payload, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
