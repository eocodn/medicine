from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MfdsIngredientEndpoint:
    category: str
    filename: str
    rule_field: str | None = None
    rule_required: bool = True


MFDS_INGREDIENT_ENDPOINTS: dict[str, MfdsIngredientEndpoint] = {
    "getUsjntTabooInfoList02": MfdsIngredientEndpoint(
        "combination_contraindication", "dur_ingredient_combination.jsonl"
    ),
    "getSpcifyAgrdeTabooInfoList02": MfdsIngredientEndpoint(
        "age_contraindication", "dur_ingredient_age.jsonl", "AGE_BASE"
    ),
    "getPwnmTabooInfoList02": MfdsIngredientEndpoint(
        "pregnancy_contraindication", "dur_ingredient_pregnancy.jsonl", "GRADE"
    ),
    "getCpctyAtentInfoList02": MfdsIngredientEndpoint(
        "dose_caution", "dur_ingredient_dose.jsonl", "MAX_QTY", False
    ),
    "getMdctnPdAtentInfoList02": MfdsIngredientEndpoint(
        "duration_caution", "dur_ingredient_duration.jsonl", "MAX_DOSAGE_TERM"
    ),
    "getOdsnAtentInfoList02": MfdsIngredientEndpoint(
        "elderly_caution", "dur_ingredient_elderly.jsonl"
    ),
    "getEfcyDplctInfoList02": MfdsIngredientEndpoint(
        "therapeutic_duplication_caution", "dur_ingredient_duplication.jsonl", "EFFECT_CODE"
    ),
}


__all__ = ["MFDS_INGREDIENT_ENDPOINTS", "MfdsIngredientEndpoint"]
