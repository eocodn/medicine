from __future__ import annotations

import os
import sys
from pathlib import Path


DEFAULT_PERSONAL_DB = Path("data/db/personal.sqlite")


def _positive_id(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc
    if value <= 0:
        raise SystemExit(f"{name} must be greater than zero")
    return value


def _personal_db_targets(personal_db: Path) -> list[Path]:
    return [
        personal_db,
        Path(f"{personal_db}.schema.lock"),
        Path(f"{personal_db}-wal"),
        Path(f"{personal_db}-shm"),
        Path(f"{personal_db}-journal"),
    ]


def prepare_personal_db_ownership(personal_db: Path, uid: int, gid: int) -> None:
    missing_parents: list[Path] = []
    cursor = personal_db.parent
    while not cursor.exists() and cursor != cursor.parent:
        missing_parents.append(cursor)
        cursor = cursor.parent
    personal_db.parent.mkdir(parents=True, exist_ok=True)
    for path in reversed(missing_parents):
        os.chown(path, uid, gid)
    workspace = Path.cwd().resolve()
    parent = personal_db.parent.resolve()
    if parent == workspace or workspace in parent.parents:
        os.chown(personal_db.parent, uid, gid)
    for path in _personal_db_targets(personal_db):
        if path.exists():
            os.chown(path, uid, gid)


def drop_privileges(uid: int, gid: int) -> None:
    current_uid = os.geteuid()
    current_gid = os.getegid()
    if current_uid == uid and current_gid == gid:
        return
    if current_uid != 0:
        raise SystemExit(
            f"web entrypoint must start as root or target uid/gid; got {current_uid}:{current_gid}"
        )
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)


def main(argv: list[str] | None = None) -> int:
    command = list(sys.argv[1:] if argv is None else argv)
    if not command:
        raise SystemExit("web entrypoint requires a command")

    uid = _positive_id("LOCAL_UID", 1000)
    gid = _positive_id("LOCAL_GID", 1000)
    personal_db = Path(os.environ.get("MEDICINE_PERSONAL_DB", str(DEFAULT_PERSONAL_DB)))
    prepare_personal_db_ownership(personal_db, uid, gid)
    drop_privileges(uid, gid)
    os.environ["HOME"] = "/tmp"
    os.execvp(command[0], command)
    raise AssertionError("execvp returned unexpectedly")


if __name__ == "__main__":
    raise SystemExit(main())