from __future__ import annotations


MAX_PATCH_BASES = 3


def _history_entry(release: dict) -> dict:
    return {
        "dataset_id": release["dataset_id"],
        "target": dict(release["target"]),
        "full": dict(release["full"]),
    }


def recent_release_bases(latest: dict) -> list[dict]:
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


__all__ = ["MAX_PATCH_BASES", "recent_release_bases"]
