from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def schema_lock(db_path: Path) -> Iterator[None]:
    # SQLite serializes DDL after a connection is established, but concurrent
    # legacy migrations can still race while toggling WAL and adding columns.
    # A retained per-database lock file provides a cross-process boundary.
    lock_path = db_path.with_name(db_path.name + ".schema.lock")
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


__all__ = ["schema_lock"]