from __future__ import annotations

from pathlib import Path

from .job_lifecycle import fingerprint_inputs
from .schema import SCHEMA_VERSION
from .snapshot_io import snapshot_metadata_path
from .substance_nomenclature_corpus import APPROVED_NOMENCLATURE_CORPUS_PATH
from .substance_reviewed_relations import APPROVED_FORM_RELATION_CORPUS_PATH
from .substance_schema import SUBSTANCE_SCHEMA_VERSION
from .substance_sources import FDA_GSRS_UNII_NAMES_FILENAME, OPENFDA_UNII_FILENAME
from .substance_typo_corpus import APPROVED_TYPO_CORPUS_PATH


SUBSTANCE_BUILD_JOB_VERSION = 1


def substance_external_input_files(raw_dir: str | Path) -> dict[str, Path]:
    root = Path(raw_dir)
    openfda = root / OPENFDA_UNII_FILENAME
    gsrs = root / FDA_GSRS_UNII_NAMES_FILENAME
    return {
        "openfda_unii": openfda,
        "openfda_unii_metadata": snapshot_metadata_path(openfda),
        "fda_gsrs_names": gsrs,
        "fda_gsrs_names_metadata": snapshot_metadata_path(gsrs),
        "approved_typo_corpus": APPROVED_TYPO_CORPUS_PATH,
        "approved_nomenclature_corpus": APPROVED_NOMENCLATURE_CORPUS_PATH,
        "approved_form_relation_corpus": APPROVED_FORM_RELATION_CORPUS_PATH,
    }


def substance_build_input_fingerprint(
    canonical_db: str | Path,
    raw_dir: str | Path,
) -> str:
    return fingerprint_inputs(
        {
            "canonical_db": Path(canonical_db),
            **substance_external_input_files(raw_dir),
        },
        context={
            "job_version": SUBSTANCE_BUILD_JOB_VERSION,
            "canonical_schema_version": SCHEMA_VERSION,
            "substance_schema_version": SUBSTANCE_SCHEMA_VERSION,
        },
    )


__all__ = [
    "SUBSTANCE_BUILD_JOB_VERSION",
    "substance_build_input_fingerprint",
    "substance_external_input_files",
]