from __future__ import annotations

import random
from typing import Callable


FIELD_ROLES = ("dose", "frequency", "duration", "instruction", "schedule")
PRODUCT_VOCABULARIES = frozenset({"train", "unseen"})
WORDING_VOCABULARIES = frozenset({"train", "unseen"})

_TRAIN_PRODUCT_SYLLABLES = (
    "가", "나", "다", "라", "마", "바", "사", "아", "거", "너",
    "더", "러", "머", "버", "서", "어", "고", "노", "도", "로",
)
_UNSEEN_PRODUCT_SYLLABLES = ("자", "차", "카", "타", "파", "하", "저", "처", "커", "터")
_PRODUCT_SUFFIXES = ("정", "캡슐", "시럽")


def _require_partition(value: str, allowed: frozenset[str], label: str) -> str:
    if value not in allowed:
        raise ValueError(f"Parser v5 {label} vocabulary is unsupported")
    return value


def product_name_support(partition: str) -> tuple[str, ...]:
    selected = _require_partition(partition, PRODUCT_VOCABULARIES, "product")
    syllables = _TRAIN_PRODUCT_SYLLABLES if selected == "train" else _UNSEEN_PRODUCT_SYLLABLES
    return tuple(
        f"{first}{second}{suffix}"
        for first in syllables
        for second in syllables
        for suffix in _PRODUCT_SUFFIXES
    )


def sample_product_name(rng: random.Random, partition: str) -> str:
    selected = _require_partition(partition, PRODUCT_VOCABULARIES, "product")
    syllables = _TRAIN_PRODUCT_SYLLABLES if selected == "train" else _UNSEEN_PRODUCT_SYLLABLES
    return f"{rng.choice(syllables)}{rng.choice(syllables)}{rng.choice(_PRODUCT_SUFFIXES)}"


def _sample_train_fields(rng: random.Random) -> dict[str, str]:
    quantity = rng.choice(("0.5", "1", "2", "3", "5", "10"))
    dose = f"{quantity}{rng.choice(('정', '캡슐', 'mL'))}"

    count = rng.randint(1, 4)
    frequency = rng.choice((
        f"1일 {count}회",
        f"하루 {count}번",
        f"매일 {count}회",
    ))

    if rng.random() < 0.75:
        duration = f"{rng.choice((3, 5, 7, 10, 14, 21, 30))}일분"
    else:
        duration = f"{rng.choice((1, 2, 3, 4))}주분"

    condition = rng.choice(("식전", "식후", "공복에", "증상시", "필요시"))
    action = rng.choice(("복용", "드세요", "물과 함께 복용"))
    instruction = f"{condition} {action}"

    times = ["아침", "점심", "저녁", "취침 전", "기상 후"]
    rng.shuffle(times)
    schedule = " ".join(times[: rng.randint(1, 3)])
    return {
        "dose": dose,
        "frequency": frequency,
        "duration": duration,
        "instruction": instruction,
        "schedule": schedule,
    }


def _sample_unseen_fields(rng: random.Random) -> dict[str, str]:
    quantity = rng.choice(("반", "한", "두", "세", "다섯", "열"))
    dose = f"{quantity} {rng.choice(('알', '정', '캡슐', '밀리리터'))}"

    count = rng.choice(("한", "두", "세", "네"))
    frequency = rng.choice((
        f"하루 {count} 차례",
        f"매일 {count} 번",
        f"격일 {count}회",
    ))

    duration = rng.choice((
        "사흘 동안",
        "닷새 동안",
        "열흘 동안",
        "보름 동안",
        "두 주 복용",
        "세 주 복용",
        "한 달치",
        "두 달치",
    ))

    condition = rng.choice(("식사 직전", "식사 직후", "빈속일 때", "증상이 있을 때", "필요할 때"))
    action = rng.choice(("복용하세요", "드십시오", "약을 드세요"))
    instruction = f"{condition} {action}"

    times = ["기상 직후", "오전 중", "정오 무렵", "저녁 식사 뒤", "잠들기 직전"]
    rng.shuffle(times)
    chosen = times[: rng.randint(1, 3)]
    connector = rng.choice((" 및 ", "과 "))
    schedule = connector.join(chosen)
    return {
        "dose": dose,
        "frequency": frequency,
        "duration": duration,
        "instruction": instruction,
        "schedule": schedule,
    }


def sample_medication_fields(rng: random.Random, partition: str) -> dict[str, str]:
    selected = _require_partition(partition, WORDING_VOCABULARIES, "wording")
    return _sample_train_fields(rng) if selected == "train" else _sample_unseen_fields(rng)


def _date(rng: random.Random, *, year_low: int = 2024, year_high: int = 2029) -> str:
    return f"{rng.randint(year_low, year_high):04d}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"


def _phone(rng: random.Random, prefix: str) -> str:
    return f"{prefix}-{rng.randint(100, 999):03d}-{rng.randint(1000, 9999):04d}"


def _amount(rng: random.Random) -> str:
    return f"{rng.randrange(1_000, 50_100, 100):,}원"


def _train_distractors(rng: random.Random, kind: str) -> tuple[str, ...]:
    factories: dict[str, tuple[Callable[[], str], ...]] = {
        "patient": (
            lambda: f"환자번호 {rng.randint(1000, 9999)}",
            lambda: f"접수일 {_date(rng)}",
        ),
        "clinic": (
            lambda: f"{rng.choice(('늘봄', '푸른', '새길', '온누리'))}의원",
            lambda: f"대표전화 {_phone(rng, '02')}",
        ),
        "receipt": (
            lambda: f"조제료 {_amount(rng)}",
            lambda: f"본인부담금 {_amount(rng)}",
        ),
        "billing": (
            lambda: f"승인번호 {rng.randint(100000, 999999)}",
            lambda: f"합계 {_amount(rng)}",
        ),
        "header": (
            lambda: rng.choice(("약제 안내문", "복약 안내", "처방 약 안내")),
            lambda: rng.choice(("복용 정보", "약품 사용 안내", "조제 내역")),
        ),
        "warning": (
            lambda: f"1일 {rng.randint(2, 6)}회 이상 복용하지 마세요",
            lambda: rng.choice(("어린이 손이 닿지 않는 곳에 보관", "임의로 용량을 늘리지 마세요")),
        ),
        "storage": (
            lambda: f"실온 {rng.randint(1, 10)}~{rng.randint(20, 35)}도 보관",
            lambda: rng.choice(("직사광선을 피해서 보관", "습기가 적은 곳에 보관")),
        ),
        "legal": (
            lambda: rng.choice(("본 문서는 복약안내를 위한 자료입니다", "처방 변경은 의료진과 상의하세요")),
            lambda: rng.choice(("이 안내는 진료를 대신하지 않습니다", "복용 문의는 전문가와 상담하세요")),
        ),
        "general_context": (
            lambda: f"다음 방문일 {_date(rng)}",
            lambda: f"문의 {_phone(rng, '1588')}",
        ),
    }
    if kind not in factories:
        raise ValueError("Parser v5 distractor kind is unsupported")
    return tuple(factory() for factory in factories[kind])


def _unseen_distractors(rng: random.Random, kind: str) -> tuple[str, ...]:
    factories: dict[str, tuple[Callable[[], str], ...]] = {
        "patient": (
            lambda: f"고객코드 {rng.randint(1000, 9999)}",
            lambda: f"내원일자 {_date(rng)}",
        ),
        "clinic": (
            lambda: f"{rng.choice(('새봄', '한결', '다온', '이음'))}가정의학과",
            lambda: f"상담전화 {_phone(rng, '031')}",
        ),
        "receipt": (
            lambda: f"약제비 {_amount(rng)}",
            lambda: f"수납금액 {_amount(rng)}",
        ),
        "billing": (
            lambda: f"결제코드 {rng.randint(100000, 999999)}",
            lambda: f"총액 {_amount(rng)}",
        ),
        "header": (
            lambda: rng.choice(("복용 방법 안내", "약 복용 설명서", "투약 정보")),
            lambda: rng.choice(("약 사용 설명", "복용 일정", "처방 내용")),
        ),
        "warning": (
            lambda: rng.choice(("정해진 양보다 많이 드시지 마세요", "복용 간격을 임의로 줄이지 마세요")),
            lambda: rng.choice(("유아의 손이 닿지 않게 두세요", "이상 반응이 있으면 상담하세요")),
        ),
        "storage": (
            lambda: rng.choice(("서늘하고 건조한 곳에 두세요", "빛을 피해 밀봉 보관하세요")),
            lambda: rng.choice(("고온 다습한 장소를 피하세요", "용기를 꼭 닫아 보관하세요")),
        ),
        "legal": (
            lambda: rng.choice(("복용 변경 전 전문가에게 문의하세요", "이 안내는 처방을 대신하지 않습니다")),
            lambda: rng.choice(("약에 관한 판단은 의료진과 상의하세요", "안내 내용은 개인별로 달라질 수 있습니다")),
        ),
        "general_context": (
            lambda: f"재방문 예정 {_date(rng)}",
            lambda: f"상담 {_phone(rng, '1661')}",
        ),
    }
    if kind not in factories:
        raise ValueError("Parser v5 distractor kind is unsupported")
    return tuple(factory() for factory in factories[kind])


def sample_distractor_texts(rng: random.Random, kind: str, partition: str) -> tuple[str, ...]:
    selected = _require_partition(partition, WORDING_VOCABULARIES, "wording")
    return _train_distractors(rng, kind) if selected == "train" else _unseen_distractors(rng, kind)


__all__ = [
    "FIELD_ROLES",
    "PRODUCT_VOCABULARIES",
    "WORDING_VOCABULARIES",
    "product_name_support",
    "sample_distractor_texts",
    "sample_medication_fields",
    "sample_product_name",
]