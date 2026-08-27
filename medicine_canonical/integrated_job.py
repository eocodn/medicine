from __future__ import annotations

from .canonical_job import canonical_source_input_files
from .job_lifecycle import fingerprint_inputs
from .schema import SCHEMA_VERSION
from .source_layout import MfdsSourceLayout
from .substance_job import substance_external_input_files
from .substance_schema import SUBSTANCE_SCHEMA_VERSION


INTEGRATED_BUILD_JOB_VERSION = 1


def integrated_build_input_fingerprint(
    source_layout: MfdsSourceLayout,
    substance_raw_dir,
) -> str:
    files = {
        **{
            f"canonical:{label}": path
            for label, path in canonical_source_input_files(source_layout).items()
        },
        **{
            f"substance:{label}": path
            for label, path in substance_external_input_files(substance_raw_dir).items()
        },
    }
    return fingerprint_inputs(
        files,
        context={
            "job_version": INTEGRATED_BUILD_JOB_VERSION,
            "canonical_schema_version": SCHEMA_VERSION,
            "substance_schema_version": SUBSTANCE_SCHEMA_VERSION,
        },
    )


__all__ = ["INTEGRATED_BUILD_JOB_VERSION", "integrated_build_input_fingerprint"]