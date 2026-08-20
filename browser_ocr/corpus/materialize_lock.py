from __future__ import annotations

import argparse
import fcntl
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ocr-corpus-materialize-lock")
    parser.add_argument("--path", required=True)
    args = parser.parse_args(argv)
    path = Path(args.path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "busy", "path": str(path)}, sort_keys=True), flush=True)
            return 2
        print(json.dumps({"status": "locked", "path": str(path)}, sort_keys=True), flush=True)
        sys.stdin.buffer.read()
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())