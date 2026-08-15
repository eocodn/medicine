from __future__ import annotations


# Release/runtime allowlist for one complete canonical snapshot. Freshness is a
# separate update policy; this invariant only prevents partial or substituted sources.
EXPECTED_CANONICAL_SOURCE_FAMILIES = {
    "mfds_permit:products": "mfds_permit_api",
    "mfds_dur:getUsjntTabooInfoList03": "mfds_dur_item_api",
    "mfds_dur:getSpcifyAgrdeTabooInfoList03": "mfds_dur_item_api",
    "mfds_dur:getPwnmTabooInfoList03": "mfds_dur_item_api",
    "mfds_dur:getCpctyAtentInfoList03": "mfds_dur_item_api",
    "mfds_dur:getMdctnPdAtentInfoList03": "mfds_dur_item_api",
    "mfds_dur:getOdsnAtentInfoList03": "mfds_dur_item_api",
    "mfds_dur:getEfcyDplctInfoList03": "mfds_dur_item_api",
    "mfds_dur:getDurPrdlstInfoList03": "mfds_dur_item_api",
    "mfds_dur:getSeobangjeongPartitnAtentInfoList03": "mfds_dur_item_api",
    "kids_mfds_xlsx:combination_contraindication": "kids_mfds_xlsx",
    "kids_mfds_xlsx:age_contraindication": "kids_mfds_xlsx",
    "kids_mfds_xlsx:pregnancy_contraindication": "kids_mfds_xlsx",
    "kids_mfds_xlsx:dose_caution": "kids_mfds_xlsx",
    "kids_mfds_xlsx:duration_caution": "kids_mfds_xlsx",
    "kids_mfds_xlsx:elderly_caution": "kids_mfds_xlsx",
    "kids_mfds_xlsx:therapeutic_duplication_caution": "kids_mfds_xlsx",
    "kids_mfds_xlsx:lactation_caution": "kids_mfds_xlsx",
}
EXPECTED_CANONICAL_SOURCE_KEYS = frozenset(EXPECTED_CANONICAL_SOURCE_FAMILIES)


__all__ = ["EXPECTED_CANONICAL_SOURCE_FAMILIES", "EXPECTED_CANONICAL_SOURCE_KEYS"]
