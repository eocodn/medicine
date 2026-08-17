from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .release import RELEASE_PREFIX, decompress_snapshot, prepare_release, sha256_file
from .release_signing import (
    KmsReleaseSigner,
    ReleaseSigner,
    encode_signed_envelope,
    verify_signed_envelope,
)
from .release_signing_runtime import release_sequence_from_env, release_signer_from_env

LATEST_KEY = f"{RELEASE_PREFIX}/latest.json"
MAX_PATCH_BASES = 3
# Current full + at most two history fulls. Older clients fall back to the current full gzip.
FULL_SNAPSHOT_RETENTION = 3
FULL_PREFIX = f"{RELEASE_PREFIX}/full/"
PATCH_PREFIX = f"{RELEASE_PREFIX}/patch/"


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = response.get("Error", {}).get("Code")
    return status == 404 or code in {"404", "NoSuchKey", "NotFound"}


def _precondition_failed(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = response.get("Error", {}).get("Code")
    return status == 412 or code in {"412", "PreconditionFailed"}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_body_bytes(body) -> bytes:
    return body.read()


def _read_latest(
    client,
    bucket: str,
    latest_key: str,
    *,
    trusted_public_keys: dict[str, bytes],
) -> tuple[bytes | None, str | None, dict | None, int | None, bool]:
    try:
        response = client.get_object(Bucket=bucket, Key=latest_key)
    except Exception as exc:
        if _not_found(exc):
            return None, None, None, None, False
        raise
    raw = _read_body_bytes(response["Body"])
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("remote latest manifest is invalid JSON") from exc
    if isinstance(payload, dict) and "envelope_version" in payload:
        verified = verify_signed_envelope(raw, trusted_public_keys)
        return (
            raw,
            response.get("ETag"),
            verified["manifest"],
            verified["release_sequence"],
            True,
        )
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("remote latest manifest schema is unsupported")
    return raw, response.get("ETag"), payload, None, False


def _download_to_file(client, bucket: str, key: str, output: Path) -> dict:
    response = client.get_object(Bucket=bucket, Key=key)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("wb") as handle:
            body = response["Body"]
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"sha256": sha256_file(output), "size_bytes": output.stat().st_size}


def _validate_remote_full(client, bucket: str, latest: dict, work_dir: Path) -> tuple[Path, str]:
    full = latest.get("full")
    target = latest.get("target")
    dataset_id = latest.get("dataset_id")
    if not isinstance(full, dict) or full.get("compression") != "gzip":
        raise ValueError("remote latest manifest has no supported full snapshot")
    if not isinstance(target, dict) or not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("remote latest manifest is missing target identity")
    for container, key in ((full, "key"), (full, "sha256"), (target, "sha256")):
        if not isinstance(container.get(key), str) or not container[key]:
            raise ValueError(f"remote latest manifest is missing {key}")
    if not isinstance(full.get("size_bytes"), int) or full["size_bytes"] < 0:
        raise ValueError("remote full snapshot size is invalid")
    if not isinstance(target.get("size_bytes"), int) or target["size_bytes"] < 0:
        raise ValueError("remote target size is invalid")

    archive = work_dir / "previous.sqlite.gz"
    downloaded = _download_to_file(client, bucket, full["key"], archive)
    if downloaded["size_bytes"] != full["size_bytes"] or downloaded["sha256"] != full["sha256"]:
        raise RuntimeError("remote full snapshot artifact does not match latest manifest")
    previous = work_dir / "previous.sqlite"
    decompressed = decompress_snapshot(archive, previous)
    if decompressed["size_bytes"] != target["size_bytes"] or decompressed["sha256"] != target["sha256"]:
        raise RuntimeError("remote full snapshot target does not match latest manifest")
    return previous, dataset_id


def _head_optional(client, bucket: str, key: str) -> dict | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _not_found(exc):
            return None
        raise


def _verify_head(head: dict, *, size_bytes: int, sha256: str, key: str) -> None:
    if head.get("ContentLength") != size_bytes:
        raise RuntimeError(f"remote object size mismatch for {key}")
    if (head.get("Metadata") or {}).get("sha256") != sha256:
        raise RuntimeError(f"remote object hash metadata mismatch for {key}")


def _put_immutable(client, bucket: str, key: str, path: Path, *, content_type: str) -> None:
    expected_size = path.stat().st_size
    expected_sha = sha256_file(path)
    existing = _head_optional(client, bucket, key)
    if existing is not None:
        _verify_head(existing, size_bytes=expected_size, sha256=expected_sha, key=key)
        return
    try:
        with path.open("rb") as handle:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=handle,
                ContentType=content_type,
                Metadata={"sha256": expected_sha},
                custom_headers={"If-None-Match": "*"},
            )
    except Exception as exc:
        if not _precondition_failed(exc):
            raise
    head = client.head_object(Bucket=bucket, Key=key)
    _verify_head(head, size_bytes=expected_size, sha256=expected_sha, key=key)


def _put_latest(
    client,
    bucket: str,
    latest_key: str,
    body: bytes,
    *,
    previous_etag: str | None,
) -> None:
    conditional = {"If-Match": previous_etag} if previous_etag else {"If-None-Match": "*"}
    try:
        client.put_object(
            Bucket=bucket,
            Key=latest_key,
            Body=body,
            ContentType="application/json",
            Metadata={"sha256": _sha256_bytes(body)},
            custom_headers=conditional,
        )
    except Exception as exc:
        if _precondition_failed(exc):
            raise RuntimeError("remote latest manifest changed during publication") from exc
        raise
    response = client.get_object(Bucket=bucket, Key=latest_key)
    round_trip = _read_body_bytes(response["Body"])
    if round_trip != body:
        raise RuntimeError("remote latest manifest does not match published body")
    metadata = response.get("Metadata") or {}
    if metadata.get("sha256") != _sha256_bytes(body):
        raise RuntimeError("remote latest manifest hash metadata does not match")


def _list_prefix_keys(client, bucket: str, prefix: str) -> set[str]:
    keys: set[str] = set()
    continuation_token: str | None = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**kwargs)
        for item in response.get("Contents") or []:
            key = item.get("Key")
            if isinstance(key, str) and key.startswith(prefix):
                keys.add(key)
        if not response.get("IsTruncated"):
            return keys
        continuation_token = response.get("NextContinuationToken")
        if not isinstance(continuation_token, str) or not continuation_token:
            raise RuntimeError(f"remote object listing for {prefix} is truncated without continuation token")


def _release_artifact_inventory(client, bucket: str) -> set[str]:
    return _list_prefix_keys(client, bucket, FULL_PREFIX) | _list_prefix_keys(
        client, bucket, PATCH_PREFIX
    )


def _manifest_release_keys(manifest: dict) -> set[str]:
    keys: set[str] = set()
    full = manifest.get("full")
    if not isinstance(full, dict) or not isinstance(full.get("key"), str):
        raise ValueError("release manifest is missing current full snapshot key")
    keys.add(full["key"])

    history = manifest.get("history") or []
    if not isinstance(history, list):
        raise ValueError("release manifest history must be a list")
    for entry in history:
        if not isinstance(entry, dict):
            raise ValueError("release manifest history entry is invalid")
        history_full = entry.get("full")
        if not isinstance(history_full, dict) or not isinstance(history_full.get("key"), str):
            raise ValueError("release manifest history is missing full snapshot key")
        keys.add(history_full["key"])

    patches = manifest.get("patches") or []
    if not isinstance(patches, list):
        raise ValueError("release manifest patches must be a list")
    for patch in patches:
        if not isinstance(patch, dict) or not isinstance(patch.get("key"), str):
            raise ValueError("release manifest patch entry is invalid")
        keys.add(patch["key"])
    return keys


def _cleanup_release_artifacts(
    client,
    bucket: str,
    *,
    manifest: dict,
    initial_inventory: set[str],
    expected_latest_raw: bytes,
    latest_key: str,
    trusted_public_keys: dict[str, bytes],
) -> dict:
    # Retention is post-commit only. Re-read authoritative latest before deleting any
    # pre-existing artifact so a competing state transition cannot be cleaned against.
    current_raw, _, _, _, _ = _read_latest(
        client,
        bucket,
        latest_key,
        trusted_public_keys=trusted_public_keys,
    )
    if current_raw != expected_latest_raw:
        raise RuntimeError("remote latest manifest changed before retention cleanup")

    keep = _manifest_release_keys(manifest)
    stale = sorted(initial_inventory - keep)
    deleted: list[str] = []
    for key in stale:
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception as exc:
            raise RuntimeError(f"remote retention cleanup failed deleting {key}") from exc
        if _head_optional(client, bucket, key) is not None:
            raise RuntimeError(f"remote retention cleanup did not delete {key}")
        deleted.append(key)
    return {
        "deleted": deleted,
        "retained_full": sum(1 for key in keep if key.startswith(FULL_PREFIX)),
        "retained_patches": sum(1 for key in keep if key.startswith(PATCH_PREFIX)),
    }


def _history_entry(release: dict) -> dict:
    return {
        "dataset_id": release["dataset_id"],
        "target": dict(release["target"]),
        "full": dict(release["full"]),
    }


def _recent_release_bases(latest: dict) -> list[dict]:
    bases = [_history_entry(latest)]
    history = latest.get("history") or []
    if not isinstance(history, list):
        raise ValueError("remote latest manifest history must be a list")
    for entry in history:
        if not isinstance(entry, dict):
            raise ValueError("remote latest manifest history entry is invalid")
        bases.append(_history_entry(entry))
        if len(bases) >= MAX_PATCH_BASES:
            break
    return bases


def publish_release(
    client,
    bucket: str,
    target_db: str | Path,
    mobile_manifest_path: str | Path,
    output_dir: str | Path,
    *,
    signer: ReleaseSigner | KmsReleaseSigner,
    release_sequence: int,
    created_at: str | None = None,
    latest_key: str = LATEST_KEY,
) -> dict:
    if not bucket:
        raise ValueError("R2 bucket is required")
    target = Path(target_db)
    mobile_manifest_file = Path(mobile_manifest_path)
    mobile_manifest = json.loads(mobile_manifest_file.read_text(encoding="utf-8"))
    dataset_id = mobile_manifest.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("mobile manifest dataset_id is required")
    if (
        not isinstance(release_sequence, int)
        or isinstance(release_sequence, bool)
        or release_sequence <= 0
        or release_sequence > (1 << 63) - 1
    ):
        raise ValueError("release_sequence must be a positive signed 64-bit integer")

    trusted_public_keys = {signer.key_id: signer.public_key_pem()}

    initial_raw, initial_etag, previous_latest, previous_sequence, previous_signed = _read_latest(
        client,
        bucket,
        latest_key,
        trusted_public_keys=trusted_public_keys,
    )
    # Snapshot only pre-existing objects so cleanup can never delete artifacts uploaded
    # concurrently after this publisher began.
    initial_inventory = _release_artifact_inventory(client, bucket)
    if previous_latest is not None and previous_latest.get("dataset_id") == dataset_id:
        if not previous_signed:
            signed_body = encode_signed_envelope(
                signer.sign_payload(initial_raw, release_sequence=release_sequence)
            )
            _put_latest(
                client,
                bucket,
                latest_key,
                signed_body,
                previous_etag=initial_etag,
            )
            cleanup = _cleanup_release_artifacts(
                client,
                bucket,
                manifest=previous_latest,
                initial_inventory=initial_inventory,
                expected_latest_raw=signed_body,
                latest_key=latest_key,
                trusted_public_keys=trusted_public_keys,
            )
            return {
                "status": "migrated",
                "dataset_id": dataset_id,
                "release_sequence": release_sequence,
                "manifest": previous_latest,
                "cleanup": cleanup,
            }
        cleanup = _cleanup_release_artifacts(
            client,
            bucket,
            manifest=previous_latest,
            initial_inventory=initial_inventory,
            expected_latest_raw=initial_raw,
            latest_key=latest_key,
            trusted_public_keys=trusted_public_keys,
        )
        return {
            "status": "unchanged",
            "dataset_id": dataset_id,
            "release_sequence": previous_sequence,
            "manifest": previous_latest,
            "cleanup": cleanup,
        }
    if previous_signed and previous_sequence is not None and release_sequence <= previous_sequence:
        raise ValueError(
            "release_sequence must be greater than the currently published release sequence"
        )

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root, prefix="previous-") as temporary_dir:
        previous_bases: list[dict] = []
        new_history: list[dict] = []
        if previous_latest is not None:
            release_bases = _recent_release_bases(previous_latest)
            new_history = release_bases[: FULL_SNAPSHOT_RETENTION - 1]
            for index, release_base in enumerate(release_bases):
                base_dir = Path(temporary_dir) / f"base-{index}"
                base_dir.mkdir(parents=True, exist_ok=True)
                previous_db, previous_dataset_id = _validate_remote_full(
                    client, bucket, release_base, base_dir
                )
                previous_bases.append(
                    {"db_path": str(previous_db), "dataset_id": previous_dataset_id}
                )
        prepared = prepare_release(
            target,
            mobile_manifest_file,
            root,
            previous_bases=previous_bases,
            history=new_history,
            created_at=created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )

    manifest = prepared["manifest"]
    full_entry = manifest["full"]
    _put_immutable(
        client,
        bucket,
        full_entry["key"],
        Path(prepared["full_path"]),
        content_type="application/gzip",
    )
    for patch in manifest["patches"]:
        _put_immutable(
            client,
            bucket,
            patch["key"],
            root / patch["key"],
            content_type="application/octet-stream",
        )

    current_raw, current_etag, _, _, _ = _read_latest(
        client,
        bucket,
        latest_key,
        trusted_public_keys=trusted_public_keys,
    )
    if current_raw != initial_raw or current_etag != initial_etag:
        raise RuntimeError("remote latest manifest changed during publication")
    manifest_payload = Path(prepared["manifest_path"]).read_bytes()
    manifest_body = encode_signed_envelope(
        signer.sign_payload(manifest_payload, release_sequence=release_sequence)
    )
    _put_latest(
        client,
        bucket,
        latest_key,
        manifest_body,
        previous_etag=initial_etag,
    )
    cleanup = _cleanup_release_artifacts(
        client,
        bucket,
        manifest=manifest,
        initial_inventory=initial_inventory,
        expected_latest_raw=manifest_body,
        latest_key=latest_key,
        trusted_public_keys=trusted_public_keys,
    )
    return {
        "status": "published",
        "dataset_id": dataset_id,
        "release_sequence": release_sequence,
        "manifest": manifest,
        "cleanup": cleanup,
    }


def configure_conditional_put_headers(client) -> None:
    events = client.meta.events

    def process_custom_arguments(params, context, **kwargs):
        custom_headers = params.pop("custom_headers", None)
        if custom_headers:
            context["custom_headers"] = custom_headers

    def add_custom_headers(params, context, **kwargs):
        custom_headers = context.get("custom_headers")
        if custom_headers:
            params["headers"].update(custom_headers)

    events.register("before-parameter-build.s3.PutObject", process_custom_arguments)
    events.register("before-call.s3.PutObject", add_custom_headers)


def client_from_env():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is required for R2 publication") from exc
    required = (
        "R2_ENDPOINT",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"missing R2 environment: {', '.join(missing)}")
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    configure_conditional_put_headers(client)
    return client


def download_object_from_env(key: str, output_path: str | Path) -> dict:
    bucket = os.environ.get("R2_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("R2_BUCKET is required")
    if not key.strip():
        raise ValueError("R2 object key is required")
    output = Path(output_path)
    downloaded = _download_to_file(client_from_env(), bucket, key, output)
    return {"bucket": bucket, "key": key, "output_path": str(output), **downloaded}


def publish_release_from_env(
    target_db: str | Path,
    mobile_manifest_path: str | Path,
    output_dir: str | Path,
    *,
    created_at: str | None = None,
) -> dict:
    bucket = os.environ.get("R2_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("R2_BUCKET is required")
    return publish_release(
        client_from_env(),
        bucket,
        target_db,
        mobile_manifest_path,
        output_dir,
        signer=release_signer_from_env(),
        release_sequence=release_sequence_from_env(),
        created_at=created_at,
    )


__all__ = [
    "FULL_SNAPSHOT_RETENTION",
    "LATEST_KEY",
    "MAX_PATCH_BASES",
    "client_from_env",
    "configure_conditional_put_headers",
    "download_object_from_env",
    "publish_release",
    "publish_release_from_env",
    "release_sequence_from_env",
    "release_signer_from_env",
]
