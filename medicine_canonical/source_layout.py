from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from medicine_reference.mfds_sources import (
    MFDS_DUR_INGREDIENT_SOURCE_FAMILY,
    MFDS_DUR_ITEM_SOURCE_FAMILY,
    MFDS_PERMIT_SOURCE_FAMILY,
    MfdsSourceSpec,
)


@dataclass(frozen=True, slots=True)
class MfdsSourceLayout:
    """Filesystem layout for the authoritative MFDS source snapshot set."""

    product_dir: Path
    ingredient_dir: Path

    @classmethod
    def from_roots(
        cls,
        product_dir: str | Path,
        ingredient_dir: str | Path | None = None,
    ) -> "MfdsSourceLayout":
        product_root = Path(product_dir)
        ingredient_root = (
            Path(ingredient_dir)
            if ingredient_dir is not None
            else product_root.parent / "mfds_ingredient"
        )
        return cls(product_dir=product_root, ingredient_dir=ingredient_root)

    @classmethod
    def for_database(cls, db_path: str | Path) -> "MfdsSourceLayout":
        db = Path(db_path)
        return cls.from_roots(db.parent / f"{db.stem}.sources")

    def root_for(self, source: MfdsSourceSpec) -> Path:
        if source.source_family in {
            MFDS_PERMIT_SOURCE_FAMILY,
            MFDS_DUR_ITEM_SOURCE_FAMILY,
        }:
            return self.product_dir
        if source.source_family == MFDS_DUR_INGREDIENT_SOURCE_FAMILY:
            return self.ingredient_dir
        raise ValueError(
            f"unsupported MFDS source family for layout: {source.source_family!r}"
        )

    def path_for(self, source: MfdsSourceSpec) -> Path:
        return self.root_for(source) / source.filename


__all__ = ["MfdsSourceLayout"]