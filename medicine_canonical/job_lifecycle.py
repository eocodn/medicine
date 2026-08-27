from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .snapshot_io import canonical_json, sha256_file


ProgressCallback = Callable[[dict[str, object]], None]
CHECKPOINT_SCHEMA_VERSION = 1
DEFAULT_PROGRESS_BAR_WIDTH = 20


def fingerprint_inputs(
    files: Mapping[str, str | Path],
    *,
    context: Mapping[str, object] | None = None,
) -> str:
    payload = {
        "context": dict(sorted((context or {}).items())),
        "files": [
            {
                "label": label,
                "path": str(Path(path)),
                "size_bytes": Path(path).stat().st_size,
                "sha256": sha256_file(path),
            }
            for label, path in sorted(files.items())
        ],
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _progress_bar(current: int, total: int, *, width: int = DEFAULT_PROGRESS_BAR_WIDTH) -> str:
    if total <= 0:
        return "[" + "-" * width + "]"
    bounded = min(max(current, 0), total)
    complete = min(width, round(width * bounded / total))
    return "[" + "#" * complete + "-" * (width - complete) + "]"


class JobLifecycle:
    def __init__(
        self,
        job: str,
        checkpoint_path: str | Path,
        *,
        input_fingerprint: str,
        progress: ProgressCallback | None,
        total_steps: int | None = None,
        heartbeat_interval_seconds: float = 5.0,
    ) -> None:
        self.job = job
        self.checkpoint_path = Path(checkpoint_path)
        self.input_fingerprint = input_fingerprint
        self.progress = progress
        self.total_steps = total_steps
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.completed_phase: str | None = None
        self.artifacts: dict[str, object] = {}
        self._started = time.monotonic()
        self._last_heartbeat = 0.0
        self._load_checkpoint()

    @property
    def resumed(self) -> bool:
        return self.completed_phase is not None

    def started(self) -> None:
        self._emit(
            "started",
            resumed=self.resumed,
            completed_phase=self.completed_phase,
            input_fingerprint=self.input_fingerprint,
        )

    def step_started(self, phase: str, current: int, *, total: int | None = None) -> None:
        self._emit("phase_started", phase=phase)
        self.progress_update(phase, current, total=total)

    def progress_update(self, phase: str, current: int, *, total: int | None = None) -> None:
        resolved_total = total if total is not None else self.total_steps
        payload: dict[str, object] = {"phase": phase, "current": current}
        if resolved_total is not None:
            payload["total"] = resolved_total
            payload["bar"] = _progress_bar(current, resolved_total)
        self._emit("progress", **payload)

    def heartbeat(self, phase: str, *, force: bool = False, **extra: object) -> bool:
        now = time.monotonic()
        if not force and now - self._last_heartbeat < self.heartbeat_interval_seconds:
            return False
        self._last_heartbeat = now
        self._emit("heartbeat", phase=phase, **extra)
        return True

    def checkpoint(self, phase: str, artifacts: Mapping[str, object] | None = None) -> None:
        payload = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "job": self.job,
            "input_fingerprint": self.input_fingerprint,
            "completed_phase": phase,
            "artifacts": dict(artifacts or {}),
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.checkpoint_path.with_name(self.checkpoint_path.name + ".write")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.checkpoint_path)
        self.completed_phase = phase
        self.artifacts = dict(artifacts or {})
        self._emit("checkpoint", phase=phase, checkpoint_path=str(self.checkpoint_path))

    def step_completed(self, phase: str, current: int, *, total: int | None = None) -> None:
        self.progress_update(phase, current, total=total)
        self._emit("phase_completed", phase=phase)

    def failed(self, phase: str, error: BaseException) -> None:
        self._emit(
            "failed",
            phase=phase,
            error=type(error).__name__,
            detail=str(error),
        )

    def completed(self) -> None:
        self.checkpoint_path.unlink(missing_ok=True)
        self.completed_phase = None
        self.artifacts = {}
        self._emit("completed", elapsed_seconds=round(time.monotonic() - self._started, 3))

    def discard(self, reason: str) -> None:
        self.checkpoint_path.unlink(missing_ok=True)
        self.completed_phase = None
        self.artifacts = {}
        self._emit("checkpoint_discarded", reason=reason)
        raise RuntimeError(f"{self.job} checkpoint discarded: {reason}")

    def _load_checkpoint(self) -> None:
        if not self.checkpoint_path.exists():
            return
        try:
            payload = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("checkpoint root must be an object")
            if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
                raise ValueError("unsupported checkpoint schema")
            if payload.get("job") != self.job:
                raise ValueError("checkpoint job does not match")
            completed_phase = payload.get("completed_phase")
            artifacts = payload.get("artifacts", {})
            if not isinstance(completed_phase, str) or not completed_phase:
                raise ValueError("checkpoint completed_phase is invalid")
            if not isinstance(artifacts, dict):
                raise ValueError("checkpoint artifacts must be an object")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.checkpoint_path.unlink(missing_ok=True)
            raise RuntimeError(f"{self.job} checkpoint discarded: malformed checkpoint: {exc}") from exc

        previous_fingerprint = payload.get("input_fingerprint")
        if previous_fingerprint != self.input_fingerprint:
            self.checkpoint_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"{self.job} checkpoint discarded: input fingerprint changed "
                f"from {previous_fingerprint!r} to {self.input_fingerprint!r}"
            )
        self.completed_phase = completed_phase
        self.artifacts = dict(artifacts)

    def _emit(self, status: str, **extra: object) -> None:
        if self.progress is None:
            return
        self.progress({"job": self.job, "status": status, **extra})


@contextmanager
def sqlite_heartbeat(
    database: sqlite3.Connection,
    lifecycle: JobLifecycle,
    phase: str,
    *,
    virtual_machine_steps: int = 100_000,
):
    def heartbeat() -> int:
        lifecycle.heartbeat(phase)
        return 0

    database.set_progress_handler(heartbeat, virtual_machine_steps)
    try:
        yield
    finally:
        database.set_progress_handler(None, 0)


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "JobLifecycle",
    "ProgressCallback",
    "fingerprint_inputs",
    "sqlite_heartbeat",
]