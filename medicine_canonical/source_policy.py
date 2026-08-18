from __future__ import annotations


CANONICAL_SOURCE_POLICY = (
    "mfds_permit_api+mfds_dur_item_api+mfds_dur_ingredient_api"
)

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
    "mfds_dur_ingredient:getUsjntTabooInfoList02": "mfds_dur_ingredient_api",
    "mfds_dur_ingredient:getSpcifyAgrdeTabooInfoList02": "mfds_dur_ingredient_api",
    "mfds_dur_ingredient:getPwnmTabooInfoList02": "mfds_dur_ingredient_api",
    "mfds_dur_ingredient:getCpctyAtentInfoList02": "mfds_dur_ingredient_api",
    "mfds_dur_ingredient:getMdctnPdAtentInfoList02": "mfds_dur_ingredient_api",
    "mfds_dur_ingredient:getOdsnAtentInfoList02": "mfds_dur_ingredient_api",
    "mfds_dur_ingredient:getEfcyDplctInfoList02": "mfds_dur_ingredient_api",
}
EXPECTED_CANONICAL_SOURCE_KEYS = frozenset(EXPECTED_CANONICAL_SOURCE_FAMILIES)


__all__ = [
    "CANONICAL_SOURCE_POLICY",
    "EXPECTED_CANONICAL_SOURCE_FAMILIES",
    "EXPECTED_CANONICAL_SOURCE_KEYS",
]
