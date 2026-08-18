from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass


_ANNOTATION_RE = re.compile(r"\(\s*분류번호\s*:[^)]+\)\s*$", re.IGNORECASE)
_TRAILING_PAREN_RE = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")
_KNOWN_DOSAGE_QUALIFIERS = frozenset({"주사제", "정제", "경구제", "경구용", "경구"})

# This table is intentionally small and reviewed. It affects link identity only;
# the source MFDS codes stored in product_rules are never rewritten.
LINK_CODE_EQUIVALENCES = {
    "D001289": "D000274",  # Ketorolac
    "D000195": "D000982",  # Naproxen
    "D000983": "D000309",  # Piroxicam
    "D000904": "D000719",  # Mizolastine
}

# Controlled terminal modifiers used only to derive an active-moiety candidate.
# A stripped candidate is accepted only when the official DUR identity graph
# resolves it to one canonical code in the relevant category.
_SUFFIX_PHRASES = (
    "extended-release granules",
    "enteric-coated granules",
    "dried sodium carbonate",
    "solid dispersions",
)
_SUFFIX_TOKENS = frozenset({
    "hydrochloride", "hydrobromide", "oxalate", "succinate", "tartrate",
    "mesylate", "sulfate", "citrate", "fumarate", "maleate", "acetate",
    "propionate", "nitrate", "palmitate", "stearate", "ethanolate",
    "sodium", "potassium", "calcium", "magnesium", "hydrate",
    "monohydrate", "dihydrate", "trihydrate", "sesquihydrate",
    "micronized", "granules",
})


@dataclass(frozen=True)
class IngredientAtom:
    names: tuple[str, ...]
    qualifier: str | None = None
    explicit_alternatives: bool = False
    preprocessed: bool = False


def canonicalize_link_ingredient_code(value: object) -> str:
    code = str(value or "").strip()
    return LINK_CODE_EQUIVALENCES.get(code, code)


def _normalize_top_level_locant_slashes(text: str) -> str:
    """Normalize only the reviewed `-N/S-` style chemical locant separator.

    MFDS permit composition uses `/` both between ingredients and inside a small
    chemical-locant spelling family.  The DUR item feed spells the latter with
    a comma (for example Methyl-N,S-Diacetylcysteine).  Only a top-level slash
    bracketed by one-letter locants is equivalent to that comma; every other
    slash remains significant composition/source text.
    """
    chars = list(text)
    stack: list[str] = []
    closing = {")": "(", "]": "[", "}": "{"}
    for index, char in enumerate(chars):
        if char in "([{":
            stack.append(char)
            continue
        if char in closing:
            if stack and stack[-1] == closing[char]:
                stack.pop()
            continue
        if (
            char == "/"
            and not stack
            and index >= 2
            and index + 2 < len(chars)
            and chars[index - 2] == "-"
            and chars[index - 1].isalpha()
            and chars[index + 1].isalpha()
            and chars[index + 2] == "-"
        ):
            chars[index] = ","
    return "".join(chars)


def normalize_ingredient_identity(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = _ANNOTATION_RE.sub("", text)
    text = _normalize_top_level_locant_slashes(text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([+/])\s*", r"\1", text)
    return text.strip()


def active_moiety_key(value: object) -> str:
    text = normalize_ingredient_identity(value)
    if not text or "/" in text or "+" in text:
        return text
    match = _TRAILING_PAREN_RE.match(text)
    if match and match.group(1).strip().startswith("dilute "):
        text = match.group(1).strip()
    was_dilute = text.startswith("dilute ")
    if was_dilute:
        text = text[len("dilute "):].strip()
        if text.endswith(" solution"):
            text = text[: -len(" solution")].strip()
    changed = True
    while changed and text:
        changed = False
        for phrase in _SUFFIX_PHRASES:
            suffix = " " + phrase
            if text.endswith(suffix):
                text = text[: -len(suffix)].strip()
                changed = True
                break
        if changed:
            continue
        parts = text.split()
        if len(parts) > 1 and parts[-1] in _SUFFIX_TOKENS:
            text = " ".join(parts[:-1]).strip()
            changed = True
    return text


def _split_expression(value: object) -> list[str]:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return []
    parts: list[str] = []
    current: list[str] = []
    stack: list[str] = []
    closing = {")": "(", "]": "[", "}": "{"}
    for index, ch in enumerate(text):
        if ch in "([{":
            stack.append(ch)
        elif ch in closing and stack and stack[-1] == closing[ch]:
            stack.pop()
        locant_slash = (
            ch == "/"
            and not stack
            and index >= 2
            and index + 2 < len(text)
            and text[index - 2] == "-"
            and text[index - 1].isalpha()
            and text[index + 1].isalpha()
            and text[index + 2] == "-"
        )
        if not stack and ch in "+/" and not locant_slash:
            piece = "".join(current).strip()
            if piece:
                parts.append(piece)
            current = []
        else:
            current.append(ch)
    piece = "".join(current).strip()
    if piece:
        parts.append(piece)
    return parts


def parse_ingredient_atom(value: object) -> IngredientAtom:
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    raw = _ANNOTATION_RE.sub("", raw).strip()
    if not raw:
        return IngredientAtom(())
    match = _TRAILING_PAREN_RE.match(raw)
    if not match:
        name = normalize_ingredient_identity(raw)
        return IngredientAtom((name,) if name else ())

    base_raw, annotation_raw = match.groups()
    base = normalize_ingredient_identity(base_raw)
    annotation = normalize_ingredient_identity(annotation_raw)
    if not base:
        return IngredientAtom(())
    if annotation in _KNOWN_DOSAGE_QUALIFIERS:
        return IngredientAtom((base,), qualifier=annotation, preprocessed=True)
    if "포함" in annotation:
        alias = normalize_ingredient_identity(annotation.split("포함", 1)[0])
        names = tuple(dict.fromkeys(name for name in (base, alias) if name))
        return IngredientAtom(names, explicit_alternatives=len(names) > 1, preprocessed=True)
    if annotation and not re.search(r"[가-힣]", annotation) and re.fullmatch(
        r"[a-z0-9 .,'\-]+", annotation
    ):
        names = tuple(dict.fromkeys((base, annotation)))
        return IngredientAtom(names, explicit_alternatives=True, preprocessed=True)
    full = normalize_ingredient_identity(raw)
    return IngredientAtom((full,) if full else ())


def parse_ingredient_expression(value: object) -> tuple[IngredientAtom, ...]:
    return tuple(parse_ingredient_atom(part) for part in _split_expression(value) if part.strip())


class IdentityResolver:
    """Resolve permit composition names against authoritative MFDS DUR codes."""

    def __init__(self) -> None:
        self._exact_global: dict[str, set[str]] = defaultdict(set)
        self._exact_category: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._active_global: dict[str, set[str]] = defaultdict(set)
        self._active_category: dict[tuple[str, str], set[str]] = defaultdict(set)

    def add(self, category: object, name_en: object, code: object) -> None:
        category_text = str(category or "").strip()
        normalized_name = normalize_ingredient_identity(name_en)
        normalized_code = canonicalize_link_ingredient_code(code)
        if not normalized_name or not normalized_code:
            return
        self._exact_global[normalized_name].add(normalized_code)
        self._exact_category[(category_text, normalized_name)].add(normalized_code)
        active = active_moiety_key(normalized_name)
        if active and active != normalized_name:
            self._active_global[active].add(normalized_code)
            self._active_category[(category_text, active)].add(normalized_code)

    def resolve_permit_composition(
        self,
        value: object,
        category: str | None = None,
    ) -> frozenset[str] | None:
        atoms = parse_ingredient_expression(value)
        if not atoms:
            return None
        codes: set[str] = set()
        for atom in atoms:
            if len(atom.names) != 1 or atom.qualifier:
                return None
            name = atom.names[0]
            exact = (
                set(self._exact_category.get((category, name), set()))
                if category is not None
                else set(self._exact_global.get(name, set()))
            )
            if len(exact) == 1:
                codes.update(exact)
                continue
            if exact:
                return None
            active_name = active_moiety_key(name)
            if category is not None:
                active = set(self._exact_category.get((category, active_name), set()))
                active.update(self._active_category.get((category, active_name), set()))
            else:
                active = set(self._exact_global.get(active_name, set()))
                active.update(self._active_global.get(active_name, set()))
            if len(active) != 1:
                return None
            codes.update(active)
        return frozenset(codes) if codes else None


__all__ = [
    "IdentityResolver",
    "IngredientAtom",
    "LINK_CODE_EQUIVALENCES",
    "active_moiety_key",
    "canonicalize_link_ingredient_code",
    "normalize_ingredient_identity",
    "parse_ingredient_atom",
    "parse_ingredient_expression",
]
