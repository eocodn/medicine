from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .build import assemble_canonical_database, build_canonical_database, canonical_stats, verify_canonical_database
from .sources import sync_canonical_api_sources

DEFAULT_DB = Path("data/db/canonical.sqlite")
DEFAULT_RAW = Path("data/canonical/raw")
DEFAULT_KIDS = Path("data/kids")


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="medicine-canonical",
        description="Build a canonical Korean medication/DUR database from three official source families",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common_api = argparse.ArgumentParser(add_help=False)
    common_api.add_argument("--service-key", default=os.environ.get("DATA_GO_KR_SERVICE_KEY", ""))
    common_api.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    common_api.add_argument("--permit-page-size", type=int, default=500)
    common_api.add_argument("--dur-page-size", type=int, default=500)
    common_api.add_argument("--workers", type=int, default=8)
    common_api.add_argument("--quiet", action="store_true")
    common_api.add_argument("--json", action="store_true")

    sync = sub.add_parser("sync", parents=[common_api], help="Download current MFDS permit and ITEM_SEQ DUR snapshots")
    sync.set_defaults(command="sync")

    build = sub.add_parser("build", help="Build the canonical DB from existing API snapshots and current XLSX files")
    build.add_argument("--db", type=Path, default=DEFAULT_DB)
    build.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    build.add_argument("--kids-dir", type=Path, default=DEFAULT_KIDS)
    build.add_argument("--json", action="store_true")

    rebuild = sub.add_parser("rebuild", parents=[common_api], help="Sync the APIs and atomically rebuild the canonical DB")
    rebuild.add_argument("--db", type=Path, default=DEFAULT_DB)
    rebuild.add_argument("--kids-dir", type=Path, default=DEFAULT_KIDS)

    stats = sub.add_parser("stats", help="Show canonical DB coverage and source counts")
    stats.add_argument("--db", type=Path, default=DEFAULT_DB)
    stats.add_argument("--json", action="store_true")

    verify = sub.add_parser("verify", help="Verify canonical source policy, schema and SQLite integrity")
    verify.add_argument("--db", type=Path, default=DEFAULT_DB)
    verify.add_argument("--json", action="store_true")
    return parser


def _require_key(value: str) -> str:
    value = value.strip()
    if not value:
        raise SystemExit("DATA_GO_KR_SERVICE_KEY is required")
    return value


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "sync":
        payload = sync_canonical_api_sources(
            args.raw_dir,
            service_key=_require_key(args.service_key),
            permit_page_size=args.permit_page_size,
            dur_page_size=args.dur_page_size,
            workers=args.workers,
            progress=not args.quiet,
        )
    elif args.command == "build":
        payload = assemble_canonical_database(args.db, args.kids_dir, args.raw_dir)
    elif args.command == "rebuild":
        payload = build_canonical_database(
            args.db,
            args.kids_dir,
            raw_dir=args.raw_dir,
            service_key=_require_key(args.service_key),
            permit_page_size=args.permit_page_size,
            dur_page_size=args.dur_page_size,
            api_workers=args.workers,
            progress=not args.quiet,
        )
    elif args.command == "stats":
        payload = canonical_stats(args.db)
    else:
        payload = verify_canonical_database(args.db)
        _emit(payload, args.json)
        return 0 if payload["status"] == "verified" else 2
    _emit(payload, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
