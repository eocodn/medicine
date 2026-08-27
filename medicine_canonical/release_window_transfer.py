from __future__ import annotations

from pathlib import Path

from .job_lifecycle import JobLifecycle, fingerprint_inputs
from .release_r2 import _put_immutable
from .release_window_artifacts import PreparedContract, RELEASE_PREFIX


TRANSFER_JOB_VERSION = 1


def _transfers(prepared: dict[int, PreparedContract]) -> list[dict[str, object]]:
    transfers: list[dict[str, object]] = []
    for major, contract in sorted(prepared.items()):
        if contract.full_path is not None:
            full = contract.entry["full"]
            transfers.append(
                {
                    "major": major,
                    "kind": "full",
                    "key": full["key"],
                    "path": contract.full_path,
                    "content_type": "application/gzip",
                }
            )
        patch_ordinal = 0
        for patch in contract.entry["patches"]:
            path = contract.patch_paths.get(patch["key"])
            if path is None:
                continue
            patch_ordinal += 1
            transfers.append(
                {
                    "major": major,
                    "kind": f"patch_{patch_ordinal}",
                    "key": patch["key"],
                    "path": path,
                    "content_type": "application/octet-stream",
                }
            )
    return transfers


def upload_prepared_contracts(
    client,
    bucket: str,
    prepared: dict[int, PreparedContract],
    output_dir: str | Path,
    *,
    progress=None,
) -> None:
    transfers = _transfers(prepared)
    if not transfers:
        return
    files = {
        f"transfer-{index}": transfer["path"]
        for index, transfer in enumerate(transfers, start=1)
    }
    lifecycle = JobLifecycle(
        "reference-publish-transfer",
        Path(output_dir) / RELEASE_PREFIX / ".transfer.checkpoint.json",
        input_fingerprint=fingerprint_inputs(
            files,
            context={
                "job_version": TRANSFER_JOB_VERSION,
                "bucket": bucket,
                "keys": [transfer["key"] for transfer in transfers],
            },
        ),
        progress=progress,
        total_steps=len(transfers),
    )
    lifecycle.started()
    current_phase = "startup"
    checkpointed = lifecycle.artifacts.get("verified_keys", [])
    if not isinstance(checkpointed, list) or not all(
        isinstance(key, str) and key for key in checkpointed
    ):
        lifecycle.discard("verified remote key checkpoint is invalid")
    verified_keys = list(dict.fromkeys(checkpointed))

    try:
        for index, transfer in enumerate(transfers, start=1):
            key = str(transfer["key"])
            current_phase = f"contract_{transfer['major']}_{transfer['kind']}_upload"
            lifecycle.step_started(current_phase, index)

            def transfer_progress(processed: int, total: int) -> None:
                lifecycle.progress_update(current_phase, processed, total=total)
                lifecycle.heartbeat(current_phase)

            _put_immutable(
                client,
                bucket,
                key,
                Path(transfer["path"]),
                content_type=str(transfer["content_type"]),
                progress=transfer_progress,
            )
            if key not in verified_keys:
                verified_keys.append(key)
            lifecycle.checkpoint(current_phase, {"verified_keys": verified_keys})
            lifecycle.step_completed(current_phase, index)
        lifecycle.completed()
    except Exception as exc:
        lifecycle.failed(current_phase, exc)
        raise


__all__ = ["TRANSFER_JOB_VERSION", "upload_prepared_contracts"]