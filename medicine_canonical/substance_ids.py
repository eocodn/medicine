from __future__ import annotations

import hashlib


def stable_substance_id(normalized_name: str) -> str:
    # Anchor unresolved local identities to the exact normalized source name,
    # not to an external coding system.
    digest = hashlib.sha256(("local-exact\0" + normalized_name).encode("utf-8")).hexdigest()
    return "SUB_" + digest[:20].upper()


def stable_external_substance_id(system: str, value: str) -> str:
    # Keep our primary key opaque while making external-identity convergence
    # deterministic across rebuilds.
    digest = hashlib.sha256((f"external-group\0{system}\0{value}").encode("utf-8")).hexdigest()
    return "SUB_" + digest[:20].upper()


__all__ = ["stable_external_substance_id", "stable_substance_id"]