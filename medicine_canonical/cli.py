from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .build import (
    assemble_canonical_database,
    build_canonical_database,
    canonical_stats,
    sync_reference_sources,
    verify_canonical_database,
)
from .inspection import canonical_product_criteria
from .integrated_build import assemble_integrated_databases, build_integrated_databases
from .mobile import build_mobile_database
from .reference_contracts.registry import build_supported_contract_window
from medicine_app.reference_update import verify_reference_database
from .release import apply_chunk_patch, prepare_release
from .release_r2 import download_object_from_env
from .release_r2_public import audit_public_bucket_from_env
from .release_window import (
    build_and_publish_contract_window_from_env,
    publish_contract_directory_from_env,
    publish_contract_window_from_env,
)
from .release_signing import verify_signed_envelope
from .source_layout import MfdsSourceLayout
from .substance_build import (
    assemble_substance_database,
    rebuild_substance_database,
)
from .substance_inspection import (
    substance_stats,
    substance_unparsed_rows,
    substance_unsolved_rows,
    verify_substance_database,
)
from .substance_sources import sync_substance_identity_sources

DEFAULT_DB = Path("data/db/canonical.sqlite")
DEFAULT_RAW = Path("data/canonical/raw")
DEFAULT_SUBSTANCE_DB = Path("data/db/canonical_substances.sqlite")
DEFAULT_SUBSTANCE_RAW = Path("data/canonical/substances")
DEFAULT_MFDS_INGREDIENT_RAW = MfdsSourceLayout.from_roots(DEFAULT_RAW).ingredient_dir


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def _reference_progress(event: dict[str, object]) -> None:
    print(
        json.dumps({"reference_progress": event}, ensure_ascii=False, separators=(",", ":")),
        file=sys.stderr,
        flush=True,
    )


def _canonical_progress(event: dict[str, object]) -> None:
    print(
        json.dumps({"canonical_progress": event}, ensure_ascii=False, separators=(",", ":")),
        file=sys.stderr,
        flush=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="medicine-canonical",
        description="Build a canonical Korean medication/DUR database from official MFDS sources",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common_api = argparse.ArgumentParser(add_help=False)
    common_api.add_argument("--service-key", default=os.environ.get("DATA_GO_KR_SERVICE_KEY", ""))
    common_api.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    common_api.add_argument("--ingredient-raw-dir", type=Path, default=DEFAULT_MFDS_INGREDIENT_RAW)
    common_api.add_argument("--permit-page-size", type=int, default=500)
    common_api.add_argument("--dur-page-size", type=int, default=500)
    common_api.add_argument("--ingredient-page-size", type=int, default=500)
    common_api.add_argument("--workers", type=int, default=8)
    common_api.add_argument("--quiet", action="store_true")
    common_api.add_argument("--json", action="store_true")

    sync = sub.add_parser("sync", parents=[common_api], help="Download the complete MFDS permit, item-level DUR, and ingredient DUR source set")
    sync.set_defaults(command="sync")

    build = sub.add_parser("build", help="Build the canonical DB from preserved MFDS API snapshots")
    build.add_argument("--db", type=Path, default=DEFAULT_DB)
    build.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    build.add_argument("--ingredient-raw-dir", type=Path, default=DEFAULT_MFDS_INGREDIENT_RAW)
    build.add_argument("--json", action="store_true")

    rebuild = sub.add_parser("rebuild", parents=[common_api], help="Sync the APIs and atomically rebuild the canonical DB")
    rebuild.add_argument("--db", type=Path, default=DEFAULT_DB)

    integrated_build = sub.add_parser(
        "integrated-build",
        help="Build source → substances → DUR bridge → product links from preserved snapshots",
    )
    integrated_build.add_argument("--db", type=Path, default=DEFAULT_DB)
    integrated_build.add_argument("--substance-db", type=Path, default=DEFAULT_SUBSTANCE_DB)
    integrated_build.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    integrated_build.add_argument("--substance-raw-dir", type=Path, default=DEFAULT_SUBSTANCE_RAW)
    integrated_build.add_argument("--ingredient-raw-dir", type=Path, default=DEFAULT_MFDS_INGREDIENT_RAW)
    integrated_build.add_argument("--json", action="store_true")

    integrated_rebuild = sub.add_parser(
        "integrated-rebuild",
        parents=[common_api],
        help="Sync MFDS APIs then rebuild source → substances → DUR bridge → product links",
    )
    integrated_rebuild.add_argument("--db", type=Path, default=DEFAULT_DB)
    integrated_rebuild.add_argument("--substance-db", type=Path, default=DEFAULT_SUBSTANCE_DB)
    integrated_rebuild.add_argument("--substance-raw-dir", type=Path, default=DEFAULT_SUBSTANCE_RAW)

    stats = sub.add_parser("stats", help="Show canonical DB coverage and source counts")
    stats.add_argument("--db", type=Path, default=DEFAULT_DB)
    stats.add_argument("--json", action="store_true")

    criteria = sub.add_parser("criteria", help="Show MFDS ingredient criteria linked to one ITEM_SEQ product")
    criteria.add_argument("--db", type=Path, default=DEFAULT_DB)
    criteria.add_argument("--item-seq", required=True)
    criteria.add_argument("--category")
    criteria.add_argument("--limit", type=int, default=100)
    criteria.add_argument("--json", action="store_true")


    mobile_build = sub.add_parser("mobile-build", help="Build compact canonical runtime DB for Android")
    mobile_build.add_argument("--db", type=Path, default=DEFAULT_DB)
    mobile_build.add_argument("--output", type=Path, default=Path("data/db/mobile.sqlite"))
    mobile_build.add_argument("--manifest", type=Path)
    mobile_build.add_argument("--json", action="store_true")

    reference_window_build = sub.add_parser(
        "reference-window-build",
        help="Build every currently supported Reference Contract database",
    )
    reference_window_build.add_argument("--db", type=Path, default=DEFAULT_DB)
    reference_window_build.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/db/reference-contracts"),
    )
    reference_window_build.add_argument(
        "--allow-retired-previous-failure",
        action="store_true",
        help="Surface an unbuildable N-1 candidate for signed-retirement publication",
    )
    reference_window_build.add_argument("--json", action="store_true")

    reference_build_publish = sub.add_parser(
        "reference-build-publish-r2",
        help="Build the supported Reference Contract window and publish the verified bytes to R2",
    )
    reference_build_publish.add_argument("--db", type=Path, default=DEFAULT_DB)
    reference_build_publish.add_argument(
        "--contract-dir",
        type=Path,
        default=Path("data/db/reference-contracts"),
    )
    reference_build_publish.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/reference-release"),
    )
    reference_build_publish.add_argument("--created-at")
    reference_build_publish.add_argument(
        "--retire-previous-contract",
        action="store_true",
        help="Explicitly advance the signed minimum support bound to current N",
    )
    reference_build_publish.add_argument(
        "--allow-retired-previous-failure",
        action="store_true",
        help="Permit an unbuildable N-1 candidate only for an explicit/signed retirement path",
    )
    reference_build_publish.add_argument("--json", action="store_true")

    mobile_verify_runtime = sub.add_parser(
        "mobile-verify-runtime",
        help="Verify a mobile reference DB against on-device runtime policy and release identity",
    )
    mobile_verify_runtime.add_argument("--db", type=Path, required=True)
    mobile_verify_runtime.add_argument("--contract-major", required=True)
    mobile_verify_runtime.add_argument("--dataset-id", required=True)
    mobile_verify_runtime.add_argument("--json", action="store_true")

    verify = sub.add_parser("verify", help="Verify canonical source policy, schema and SQLite integrity")
    verify.add_argument("--db", type=Path, default=DEFAULT_DB)
    verify.add_argument("--json", action="store_true")

    substance_sync = sub.add_parser("substance-sync", help="Download the current FDA GSRS/UNII snapshot via openFDA")
    substance_sync.add_argument("--raw-dir", type=Path, default=DEFAULT_SUBSTANCE_RAW)
    substance_sync.add_argument("--json", action="store_true")

    substance_build = sub.add_parser(
        "substance-build",
        help="Build the parallel canonical substance DB from canonical DUR sources and a preserved UNII snapshot",
    )
    substance_build.add_argument("--db", type=Path, default=DEFAULT_SUBSTANCE_DB)
    substance_build.add_argument("--canonical-db", type=Path, default=DEFAULT_DB)
    substance_build.add_argument("--raw-dir", type=Path, default=DEFAULT_SUBSTANCE_RAW)
    substance_build.add_argument("--json", action="store_true")

    substance_rebuild = sub.add_parser(
        "substance-rebuild",
        help="Sync UNII and atomically rebuild the parallel canonical substance DB",
    )
    substance_rebuild.add_argument("--db", type=Path, default=DEFAULT_SUBSTANCE_DB)
    substance_rebuild.add_argument("--canonical-db", type=Path, default=DEFAULT_DB)
    substance_rebuild.add_argument("--raw-dir", type=Path, default=DEFAULT_SUBSTANCE_RAW)
    substance_rebuild.add_argument("--json", action="store_true")

    substance_stats_parser = sub.add_parser("substance-stats", help="Show canonical substance coverage and unsolved counts")
    substance_stats_parser.add_argument("--db", type=Path, default=DEFAULT_SUBSTANCE_DB)
    substance_stats_parser.add_argument("--json", action="store_true")

    substance_unsolved = sub.add_parser("substance-unsolved", help="Inspect unresolved canonical substance identities")
    substance_unsolved.add_argument("--db", type=Path, default=DEFAULT_SUBSTANCE_DB)
    substance_unsolved.add_argument("--reason")
    substance_unsolved.add_argument("--limit", type=int, default=100)
    substance_unsolved.add_argument("--json", action="store_true")

    substance_unparsed = sub.add_parser(
        "substance-unparsed",
        help="Inspect source ingredient expressions intentionally left unparsed",
    )
    substance_unparsed.add_argument("--db", type=Path, default=DEFAULT_SUBSTANCE_DB)
    substance_unparsed.add_argument("--limit", type=int, default=100)
    substance_unparsed.add_argument("--json", action="store_true")

    substance_verify = sub.add_parser("substance-verify", help="Verify the parallel canonical substance database")
    substance_verify.add_argument("--db", type=Path, default=DEFAULT_SUBSTANCE_DB)
    substance_verify.add_argument("--json", action="store_true")

    release_create = sub.add_parser("release-create", help="Build verified full and exact-byte delta mobile DB artifacts")
    release_create.add_argument("--db", type=Path, default=Path("data/db/mobile.sqlite"))
    release_create.add_argument("--mobile-manifest", type=Path, default=Path("data/db/mobile.manifest.json"))
    release_create.add_argument("--output-dir", type=Path, default=Path("artifacts/reference-release"))
    release_create.add_argument("--previous-db", type=Path)
    release_create.add_argument("--previous-dataset-id")
    release_create.add_argument("--created-at")
    release_create.add_argument("--json", action="store_true")

    release_apply = sub.add_parser("release-apply", help="Apply and verify an exact-byte mobile DB delta")
    release_apply.add_argument("--source", type=Path, required=True)
    release_apply.add_argument("--patch", type=Path, required=True)
    release_apply.add_argument("--output", type=Path, required=True)
    release_apply.add_argument("--json", action="store_true")

    release_verify = sub.add_parser(
        "release-verify-envelope",
        help="Verify a signed reference release manifest envelope",
    )
    release_verify.add_argument("--envelope", type=Path, required=True)
    release_verify.add_argument("--public-key", type=Path, required=True)
    release_verify.add_argument("--key-id", required=True)
    release_verify.add_argument("--minimum-sequence", type=int)
    release_verify.add_argument("--json", action="store_true")

    r2_download = sub.add_parser("r2-download", help="Download one private R2 object using configured credentials")
    r2_download.add_argument("--key", required=True)
    r2_download.add_argument("--output", type=Path, required=True)
    r2_download.add_argument("--json", action="store_true")

    r2_public_audit = sub.add_parser(
        "r2-public-audit",
        help="Fail unless the configured R2 bucket contains only the public reference release namespace",
    )
    r2_public_audit.add_argument("--json", action="store_true")

    release_publish = sub.add_parser("release-publish-r2", help="Prepare and atomically publish a mobile DB release to R2")
    release_publish.add_argument("--db", type=Path, default=Path("data/db/mobile.sqlite"))
    release_publish.add_argument("--mobile-manifest", type=Path, default=Path("data/db/mobile.manifest.json"))
    release_publish.add_argument("--contract-dir", type=Path)
    release_publish.add_argument("--output-dir", type=Path, default=Path("artifacts/reference-release"))
    release_publish.add_argument("--created-at")
    release_publish.add_argument(
        "--retire-previous-contract",
        action="store_true",
        help="Explicitly advance the signed minimum support bound to current N",
    )
    release_publish.add_argument("--json", action="store_true")
    return parser


def _require_key(value: str) -> str:
    value = value.strip()
    if not value:
        raise SystemExit("DATA_GO_KR_SERVICE_KEY is required")
    return value


def _mfds_source_layout(args: argparse.Namespace) -> MfdsSourceLayout:
    return MfdsSourceLayout.from_roots(args.raw_dir, args.ingredient_raw_dir)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "sync":
        payload = sync_reference_sources(
            _mfds_source_layout(args),
            service_key=_require_key(args.service_key),
            permit_page_size=args.permit_page_size,
            dur_page_size=args.dur_page_size,
            ingredient_page_size=args.ingredient_page_size,
            workers=args.workers,
            progress=not args.quiet,
        )
    elif args.command == "build":
        payload = assemble_canonical_database(
            args.db,
            _mfds_source_layout(args),
            progress=_canonical_progress,
        )
    elif args.command == "rebuild":
        payload = build_canonical_database(
            args.db,
            source_layout=_mfds_source_layout(args),
            service_key=_require_key(args.service_key),
            permit_page_size=args.permit_page_size,
            dur_page_size=args.dur_page_size,
            ingredient_page_size=args.ingredient_page_size,
            api_workers=args.workers,
            progress=not args.quiet,
            job_progress=None if args.quiet else _canonical_progress,
        )
    elif args.command == "integrated-build":
        payload = assemble_integrated_databases(
            args.db,
            args.substance_db,
            _mfds_source_layout(args),
            args.substance_raw_dir,
            progress=_canonical_progress,
        )
    elif args.command == "integrated-rebuild":
        payload = build_integrated_databases(
            args.db,
            args.substance_db,
            service_key=_require_key(args.service_key),
            source_layout=_mfds_source_layout(args),
            substance_raw_dir=args.substance_raw_dir,
            permit_page_size=args.permit_page_size,
            dur_page_size=args.dur_page_size,
            ingredient_page_size=args.ingredient_page_size,
            api_workers=args.workers,
            progress=not args.quiet,
            job_progress=None if args.quiet else _canonical_progress,
        )
    elif args.command == "stats":
        payload = canonical_stats(args.db)
    elif args.command == "criteria":
        payload = canonical_product_criteria(
            args.db,
            args.item_seq,
            category=args.category,
            limit=args.limit,
        )

    elif args.command == "mobile-build":
        payload = build_mobile_database(
            args.db,
            args.output,
            manifest_path=args.manifest,
            progress=_reference_progress,
        )
    elif args.command == "reference-window-build":
        payload = build_supported_contract_window(
            args.db,
            args.output_dir,
            allow_previous_failure=args.allow_retired_previous_failure,
            progress=_reference_progress,
        )
    elif args.command == "reference-build-publish-r2":
        payload = build_and_publish_contract_window_from_env(
            args.db,
            args.contract_dir,
            args.output_dir,
            created_at=args.created_at,
            retire_previous_contract=args.retire_previous_contract,
            allow_previous_failure=args.allow_retired_previous_failure,
            progress=_reference_progress,
        )
    elif args.command == "mobile-verify-runtime":
        payload = verify_reference_database(
            args.db,
            expected_contract_major=args.contract_major,
            expected_dataset_id=args.dataset_id,
        )
    elif args.command == "substance-sync":
        payload = sync_substance_identity_sources(args.raw_dir)
    elif args.command == "substance-build":
        payload = assemble_substance_database(
            args.db,
            args.canonical_db,
            args.raw_dir,
            progress=_canonical_progress,
        )
    elif args.command == "substance-rebuild":
        payload = rebuild_substance_database(
            args.db,
            args.canonical_db,
            args.raw_dir,
            progress=_canonical_progress,
        )
    elif args.command == "substance-stats":
        payload = substance_stats(args.db)
    elif args.command == "substance-unsolved":
        payload = substance_unsolved_rows(args.db, reason=args.reason, limit=args.limit)
    elif args.command == "substance-unparsed":
        payload = substance_unparsed_rows(args.db, limit=args.limit)
    elif args.command == "substance-verify":
        payload = verify_substance_database(args.db)
        _emit(payload, args.json)
        return 0 if payload["status"] == "verified" else 2
    elif args.command == "release-create":
        created_at = args.created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = prepare_release(
            args.db,
            args.mobile_manifest,
            args.output_dir,
            previous_db=args.previous_db,
            previous_dataset_id=args.previous_dataset_id,
            created_at=created_at,
            progress=_reference_progress,
        )
    elif args.command == "release-apply":
        payload = apply_chunk_patch(args.source, args.patch, args.output)
    elif args.command == "release-verify-envelope":
        verified = verify_signed_envelope(
            args.envelope.read_bytes(),
            {args.key_id: args.public_key.read_bytes()},
            minimum_release_sequence=args.minimum_sequence,
        )
        if verified["manifest"].get("schema_version") != 1:
            raise ValueError("signed release payload schema is unsupported")
        payload = {
            "status": "verified",
            "key_id": verified["key_id"],
            "release_sequence": verified["release_sequence"],
            "manifest": verified["manifest"],
        }
    elif args.command == "r2-download":
        payload = download_object_from_env(args.key, args.output)
    elif args.command == "r2-public-audit":
        payload = audit_public_bucket_from_env()
    elif args.command == "release-publish-r2":
        if args.contract_dir is not None:
            payload = publish_contract_directory_from_env(
                args.contract_dir,
                args.output_dir,
                created_at=args.created_at,
                retire_previous_contract=args.retire_previous_contract,
                progress=_reference_progress,
            )
        else:
            if args.retire_previous_contract:
                raise ValueError("--retire-previous-contract requires --contract-dir")
            payload = publish_contract_window_from_env(
                args.db,
                args.mobile_manifest,
                args.output_dir,
                created_at=args.created_at,
                progress=_reference_progress,
            )
    else:
        payload = verify_canonical_database(args.db)
        _emit(payload, args.json)
        return 0 if payload["status"] == "verified" else 2
    _emit(payload, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
