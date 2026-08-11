from __future__ import annotations

import argparse
import json
from pathlib import Path

from .db import build_database, database_stats, search_records
from .mobile import build_mobile_database
from .verification import verify_database


DEFAULT_DB = Path("data/db/dur.sqlite")
DEFAULT_CATALOG_DB = Path("data/db/catalog.sqlite")
DEFAULT_MOBILE_DB = Path("data/db/mobile.sqlite")
DEFAULT_RAW = Path("data/raw")
DEFAULT_KIDS = Path("data/kids")


def _emit(payload, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, list):
        for row in payload:
            print(json.dumps(row, ensure_ascii=False))
    else:
        for key, value in payload.items():
            if key == "categories":
                print("categories:")
                for row in value:
                    print(f"  - {row['category']} / {row['source_kind']}: {row['rows']:,}")
            elif isinstance(value, int):
                print(f"{key}: {value:,}")
            else:
                print(f"{key}: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="medicine-dur", description="Build and query a local Korean DUR SQLite database")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build SQLite DB from downloaded DUR files")
    build.add_argument("--db", type=Path, default=DEFAULT_DB)
    build.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    build.add_argument("--kids-dir", type=Path, default=DEFAULT_KIDS)
    build.add_argument("--json", action="store_true")
    build.add_argument("--quiet", action="store_true", help="Suppress import progress output")

    stats = sub.add_parser("stats", help="Show DB row counts and source coverage")
    stats.add_argument("--db", type=Path, default=DEFAULT_DB)
    stats.add_argument("--json", action="store_true")

    search = sub.add_parser("search", help="Search ingredient or product names")
    search.add_argument("term")
    search.add_argument("--db", type=Path, default=DEFAULT_DB)
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--json", action="store_true")

    verify = sub.add_parser("verify", help="Verify DUR dataset release readiness and identity")
    verify.add_argument("--db", type=Path, default=DEFAULT_DB)
    verify.add_argument("--max-age-days", type=int, default=730)
    verify.add_argument("--max-snapshot-age-days", type=int, default=90)
    verify.add_argument("--json", action="store_true")

    mobile = sub.add_parser("mobile-build", help="Build verified compact Android reference DB")
    mobile.add_argument("--dur-db", type=Path, default=DEFAULT_DB)
    mobile.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)
    mobile.add_argument("--output", type=Path, default=DEFAULT_MOBILE_DB)
    mobile.add_argument("--manifest", type=Path)
    mobile.add_argument("--json", action="store_true")

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "build":
        result = build_database(args.db, args.raw_dir, args.kids_dir, progress=not args.quiet)
        _emit(result, args.json)
    elif args.command == "stats":
        _emit(database_stats(args.db), args.json)
    elif args.command == "search":
        if args.limit < 1:
            raise SystemExit("--limit must be >= 1")
        _emit(search_records(args.db, args.term, limit=args.limit), args.json)
    elif args.command == "verify":
        result = verify_database(
            args.db,
            max_age_days=args.max_age_days,
            max_snapshot_age_days=args.max_snapshot_age_days,
        )
        _emit(result, args.json)
        return 0 if result["status"] == "verified" else 2
    elif args.command == "mobile-build":
        result = build_mobile_database(
            args.dur_db,
            args.catalog_db,
            args.output,
            manifest_path=args.manifest,
        )
        _emit(result, args.json)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
