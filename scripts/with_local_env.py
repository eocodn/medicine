#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import stat
from pathlib import Path
from typing import NoReturn


ALLOWED_KEYS = frozenset(
    {
        "DATA_GO_KR_SERVICE_KEY",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_ENDPOINT",
        "R2_BUCKET",
        "REFERENCE_SIGNING_KEY_ID",
        "REFERENCE_SIGNING_KMS_KEY_VERSION",
        "REFERENCE_RELEASE_SEQUENCE",
        "REFERENCE_SIGNING_TRUSTED_KEYS_FILE",
    }
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Load private local development credentials before exec without putting values in argv."
    )
    result.add_argument(
        "--file",
        type=Path,
        help="credential file path; defaults to $HOME/.config/medicine/dev.env",
    )
    result.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="KEY",
        help="require an allowlisted key to be present after loading; repeatable",
    )
    result.add_argument("command", nargs=argparse.REMAINDER)
    return result


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def resolve_env_file(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser()
    override = os.environ.get("MEDICINE_LOCAL_ENV_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    home = os.environ.get("HOME", "").strip()
    if not home:
        fail("HOME is required when --file and MEDICINE_LOCAL_ENV_FILE are unset")
    return Path(home) / ".config" / "medicine" / "dev.env"


def parse_value(raw: str, *, line_number: int) -> str:
    lexer = shlex.shlex(raw, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        values = list(lexer)
    except ValueError as error:
        fail(f"invalid local environment value on line {line_number}: {error}")
    if len(values) != 1:
        fail(
            f"local environment value on line {line_number} must be one shell-quoted token"
        )
    return values[0]


def load_file(path: Path) -> dict[str, str]:
    if path.is_symlink():
        fail(f"local environment file must not be a symlink: {path}")
    try:
        metadata = path.stat()
    except FileNotFoundError:
        return {}
    if not stat.S_ISREG(metadata.st_mode):
        fail(f"local environment path must be a regular file: {path}")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        fail(f"local environment file must not be accessible by group or other users: {path}")

    loaded: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            fail(f"invalid local environment assignment on line {line_number}")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key not in ALLOWED_KEYS:
            fail(f"unsupported local environment key on line {line_number}: {key}")
        if key in loaded:
            fail(f"duplicate local environment key on line {line_number}: {key}")
        loaded[key] = parse_value(raw_value.strip(), line_number=line_number)
    return loaded


def main() -> None:
    args = parser().parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        fail("a command is required after --")

    unknown_required = [key for key in args.require if key not in ALLOWED_KEYS]
    if unknown_required:
        fail(f"unsupported required local environment key: {unknown_required[0]}")

    child_env = os.environ.copy()
    child_env.update(load_file(resolve_env_file(args.file)))
    for key in args.require:
        if not child_env.get(key, "").strip():
            fail(f"required local environment key is missing: {key}")

    os.execvpe(command[0], command, child_env)


if __name__ == "__main__":
    main()
