from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


MFDS_PERMIT_API_BASE = (
    "https://apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService07/getDrugPrdtPrmsnInq07"
)
MFDS_DUR_ITEM_API_BASE = "https://apis.data.go.kr/1471000/DURPrdlstInfoService03"
MFDS_DUR_INGREDIENT_API_BASE = "https://apis.data.go.kr/1471000/DURIrdntInfoService03"

MFDS_PERMIT_SOURCE_FAMILY = "mfds_permit_api"
MFDS_DUR_ITEM_SOURCE_FAMILY = "mfds_dur_item_api"
MFDS_DUR_INGREDIENT_SOURCE_FAMILY = "mfds_dur_ingredient_api"


@dataclass(frozen=True, slots=True)
class MfdsSourceSpec:
    dataset_key: str
    source_family: str
    api_base: str
    filename: str
    operation: str | None = None
    category: str | None = None
    kind: str | None = None
    rule_field: str | None = None
    rule_required: bool = True

    @property
    def source_locator(self) -> str:
        return f"{self.api_base}/{self.operation}" if self.operation else self.api_base


PERMIT_SOURCE = MfdsSourceSpec(
    dataset_key="mfds_permit:products",
    source_family=MFDS_PERMIT_SOURCE_FAMILY,
    api_base=MFDS_PERMIT_API_BASE,
    filename="mfds_permit_products.jsonl",
    kind="permit",
)


def _dur_item_source(
    operation: str, category: str, kind: str, filename: str
) -> MfdsSourceSpec:
    return MfdsSourceSpec(
        dataset_key=f"mfds_dur:{operation}",
        source_family=MFDS_DUR_ITEM_SOURCE_FAMILY,
        api_base=MFDS_DUR_ITEM_API_BASE,
        filename=filename,
        operation=operation,
        category=category,
        kind=kind,
    )


def _dur_ingredient_source(
    operation: str,
    category: str,
    filename: str,
    rule_field: str | None = None,
    rule_required: bool = True,
) -> MfdsSourceSpec:
    return MfdsSourceSpec(
        dataset_key=f"mfds_dur_ingredient:{operation}",
        source_family=MFDS_DUR_INGREDIENT_SOURCE_FAMILY,
        api_base=MFDS_DUR_INGREDIENT_API_BASE,
        filename=filename,
        operation=operation,
        category=category,
        kind="ingredient_rule",
        rule_field=rule_field,
        rule_required=rule_required,
    )


MFDS_DUR_ITEM_SOURCES = (
    _dur_item_source(
        "getUsjntTabooInfoList03", "combination_contraindication", "rule", "dur_combination.jsonl"
    ),
    _dur_item_source(
        "getSpcifyAgrdeTabooInfoList03", "age_contraindication", "rule", "dur_age.jsonl"
    ),
    _dur_item_source(
        "getPwnmTabooInfoList03", "pregnancy_contraindication", "rule", "dur_pregnancy.jsonl"
    ),
    _dur_item_source("getCpctyAtentInfoList03", "dose_caution", "rule", "dur_dose.jsonl"),
    _dur_item_source(
        "getMdctnPdAtentInfoList03", "duration_caution", "rule", "dur_duration.jsonl"
    ),
    _dur_item_source("getOdsnAtentInfoList03", "elderly_caution", "rule", "dur_elderly.jsonl"),
    _dur_item_source(
        "getEfcyDplctInfoList03",
        "therapeutic_duplication_caution",
        "rule",
        "dur_duplication.jsonl",
    ),
    _dur_item_source(
        "getDurPrdlstInfoList03", "dur_product_info", "flags", "dur_product_info.jsonl"
    ),
    _dur_item_source(
        "getSeobangjeongPartitnAtentInfoList03",
        "split_caution",
        "split",
        "dur_split.jsonl",
    ),
)

MFDS_DUR_INGREDIENT_SOURCES = (
    _dur_ingredient_source(
        "getUsjntTabooInfoList02",
        "combination_contraindication",
        "dur_ingredient_combination.jsonl",
    ),
    _dur_ingredient_source(
        "getSpcifyAgrdeTabooInfoList02",
        "age_contraindication",
        "dur_ingredient_age.jsonl",
        "AGE_BASE",
    ),
    _dur_ingredient_source(
        "getPwnmTabooInfoList02",
        "pregnancy_contraindication",
        "dur_ingredient_pregnancy.jsonl",
        "GRADE",
    ),
    _dur_ingredient_source(
        "getCpctyAtentInfoList02",
        "dose_caution",
        "dur_ingredient_dose.jsonl",
        "MAX_QTY",
        False,
    ),
    _dur_ingredient_source(
        "getMdctnPdAtentInfoList02",
        "duration_caution",
        "dur_ingredient_duration.jsonl",
        "MAX_DOSAGE_TERM",
    ),
    _dur_ingredient_source(
        "getOdsnAtentInfoList02",
        "elderly_caution",
        "dur_ingredient_elderly.jsonl",
    ),
    _dur_ingredient_source(
        "getEfcyDplctInfoList02",
        "therapeutic_duplication_caution",
        "dur_ingredient_duplication.jsonl",
        "EFFECT_CODE",
    ),
)


def _index_by_operation(sources: tuple[MfdsSourceSpec, ...]) -> Mapping[str, MfdsSourceSpec]:
    indexed = {source.operation: source for source in sources if source.operation is not None}
    if len(indexed) != len(sources):
        raise RuntimeError("MFDS source manifest has a missing or duplicate operation")
    return MappingProxyType(indexed)


MFDS_DUR_ITEM_SOURCES_BY_OPERATION = _index_by_operation(MFDS_DUR_ITEM_SOURCES)
MFDS_DUR_INGREDIENT_SOURCES_BY_OPERATION = _index_by_operation(MFDS_DUR_INGREDIENT_SOURCES)

MFDS_SOURCE_MANIFEST = (
    PERMIT_SOURCE,
    *MFDS_DUR_ITEM_SOURCES,
    *MFDS_DUR_INGREDIENT_SOURCES,
)
_source_families = {source.dataset_key: source.source_family for source in MFDS_SOURCE_MANIFEST}
if len(_source_families) != len(MFDS_SOURCE_MANIFEST):
    raise RuntimeError("MFDS source manifest has duplicate dataset keys")

MFDS_SOURCE_FAMILIES: Mapping[str, str] = MappingProxyType(_source_families)
MFDS_SOURCE_KEYS = frozenset(MFDS_SOURCE_FAMILIES)
MFDS_SOURCE_FAMILY_ORDER = (
    MFDS_PERMIT_SOURCE_FAMILY,
    MFDS_DUR_ITEM_SOURCE_FAMILY,
    MFDS_DUR_INGREDIENT_SOURCE_FAMILY,
)
MFDS_SOURCE_FAMILY_SET = frozenset(MFDS_SOURCE_FAMILY_ORDER)
MFDS_SOURCE_POLICY = "+".join(MFDS_SOURCE_FAMILY_ORDER)


__all__ = [
    "MFDS_DUR_INGREDIENT_API_BASE",
    "MFDS_DUR_INGREDIENT_SOURCE_FAMILY",
    "MFDS_DUR_INGREDIENT_SOURCES",
    "MFDS_DUR_INGREDIENT_SOURCES_BY_OPERATION",
    "MFDS_DUR_ITEM_API_BASE",
    "MFDS_DUR_ITEM_SOURCE_FAMILY",
    "MFDS_DUR_ITEM_SOURCES",
    "MFDS_DUR_ITEM_SOURCES_BY_OPERATION",
    "MFDS_PERMIT_API_BASE",
    "MFDS_PERMIT_SOURCE_FAMILY",
    "MFDS_SOURCE_FAMILIES",
    "MFDS_SOURCE_FAMILY_ORDER",
    "MFDS_SOURCE_FAMILY_SET",
    "MFDS_SOURCE_KEYS",
    "MFDS_SOURCE_MANIFEST",
    "MFDS_SOURCE_POLICY",
    "MfdsSourceSpec",
    "PERMIT_SOURCE",
]