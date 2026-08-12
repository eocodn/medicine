from __future__ import annotations

import itertools
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass


_ANNOTATION_RE = re.compile(r"\(\s*분류번호\s*:[^)]+\)\s*$", re.IGNORECASE)
_TRAILING_PAREN_RE = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")
_KNOWN_DOSAGE_QUALIFIERS = frozenset({"주사제", "정제", "경구제", "경구용", "경구"})
_ORAL_FORM_MARKERS = ("정", "캡슐", "과립", "산제", "시럽", "현탁", "경구", "내복")

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


@dataclass(frozen=True)
class ResolvedExpression:
    signatures: tuple[frozenset[str], ...]
    qualifier: str | None
    preprocessed: bool
    ambiguities: tuple[dict, ...]


def canonicalize_link_ingredient_code(value: object) -> str:
    code = str(value or "").strip()
    return LINK_CODE_EQUIVALENCES.get(code, code)


def normalize_ingredient_identity(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = _ANNOTATION_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([+/])\s*", r"\1", text)
    return text.strip()


def normalize_korean_identity(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = re.sub(r"\s+", "", text)
    return text


def active_moiety_key(value: object) -> str:
    text = normalize_ingredient_identity(value)
    if not text or "/" in text or "+" in text:
        return text
    # A small source-observed formulation descriptor rule. Only the "dilute"
    # family is normalized here; arbitrary prefixes/suffixes are not stripped.
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
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        if depth == 0 and ch in "+/":
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
    # Parenthetical Latin/ASCII names are source-declared synonyms, not a global alias rule.
    if annotation and not re.search(r"[가-힣]", annotation) and re.fullmatch(r"[a-z0-9 .,'\-]+", annotation):
        names = tuple(dict.fromkeys((base, annotation)))
        return IngredientAtom(names, explicit_alternatives=True, preprocessed=True)
    # Unknown parenthetical text remains part of the identity; do not guess.
    full = normalize_ingredient_identity(raw)
    return IngredientAtom((full,) if full else ())


def parse_ingredient_expression(value: object) -> tuple[IngredientAtom, ...]:
    return tuple(parse_ingredient_atom(part) for part in _split_expression(value) if part.strip())


def qualifier_applies(qualifier: str | None, dosage_form: object) -> bool:
    if not qualifier:
        return True
    form = normalize_ingredient_identity(dosage_form)
    if not form:
        return False
    if qualifier == "주사제":
        return "주사" in form
    if qualifier == "정제":
        return "정" in form and "주사" not in form
    if qualifier in {"경구제", "경구용", "경구"}:
        return "주사" not in form and any(marker in form for marker in _ORAL_FORM_MARKERS)
    return False


class IdentityResolver:
    def __init__(self) -> None:
        self._exact_global: dict[str, set[str]] = defaultdict(set)
        self._exact_category: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._active_global: dict[str, set[str]] = defaultdict(set)
        self._active_category: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._korean_category: dict[tuple[str, str], set[str]] = defaultdict(set)

    def add(self, category: object, name_en: object, code: object, name_ko: object = None) -> None:
        category_text = str(category or "").strip()
        normalized_name = normalize_ingredient_identity(name_en)
        normalized_code = canonicalize_link_ingredient_code(code)
        if normalized_name and normalized_code:
            self._exact_global[normalized_name].add(normalized_code)
            self._exact_category[(category_text, normalized_name)].add(normalized_code)
            active = active_moiety_key(normalized_name)
            if active and active != normalized_name:
                self._active_global[active].add(normalized_code)
                self._active_category[(category_text, active)].add(normalized_code)
        korean = normalize_korean_identity(name_ko)
        if korean and normalized_code:
            self._korean_category[(category_text, korean)].add(normalized_code)

    def _codes_for_name(self, name: str, category: str | None, *, global_fallback: bool) -> tuple[set[str], bool]:
        exact: set[str] = set()
        active: set[str] = set()
        if category is not None:
            exact.update(self._exact_category.get((category, name), set()))
            # Only a base-like criterion may expand through active-moiety variants.
            if active_moiety_key(name) == name:
                active.update(self._active_category.get((category, name), set()))
        if not exact and not active and global_fallback:
            exact.update(self._exact_global.get(name, set()))
            if active_moiety_key(name) == name:
                active.update(self._active_global.get(name, set()))
        codes = exact | active
        return codes, bool(active - exact)

    def resolve_expression(
        self,
        value: object,
        category: str | None,
        *,
        global_fallback: bool = True,
    ) -> ResolvedExpression:
        atoms = parse_ingredient_expression(value)
        if not atoms:
            return ResolvedExpression((), None, False, ())
        per_atom_codes: list[tuple[str, ...]] = []
        preprocessed = len(atoms) > 1
        ambiguities: list[dict] = []
        qualifier = atoms[0].qualifier if len(atoms) == 1 else None

        for atom in atoms:
            preprocessed = preprocessed or atom.preprocessed
            resolved: set[str] = set()
            atom_used_active = False
            atom_ambiguous: set[str] = set()
            for name in atom.names:
                codes, used_active = self._codes_for_name(name, category, global_fallback=global_fallback)
                atom_used_active = atom_used_active or used_active
                if len(codes) > 1 and not atom.explicit_alternatives:
                    atom_ambiguous.update(codes)
                else:
                    resolved.update(codes)
            if atom_ambiguous:
                ambiguities.append({
                    "ingredient_name": str(value or "").strip() if len(atoms) == 1 else " / ".join(atom.names),
                    "candidate_codes": sorted(atom_ambiguous),
                    "reason": "active_moiety_multiple_codes",
                })
                return ResolvedExpression((), qualifier, True, tuple(ambiguities))
            if not resolved:
                return ResolvedExpression((), qualifier, preprocessed or atom_used_active, tuple(ambiguities))
            preprocessed = preprocessed or atom_used_active
            per_atom_codes.append(tuple(sorted(resolved)))

        signatures = {
            frozenset(choice)
            for choice in itertools.product(*per_atom_codes)
            if choice
        }
        return ResolvedExpression(tuple(sorted(signatures, key=lambda s: tuple(sorted(s)))), qualifier, preprocessed, tuple(ambiguities))

    def resolve_hybrid_expression(
        self,
        value: object,
        category: str | None,
        *,
        global_fallback: bool = True,
    ) -> ResolvedExpression:
        """Resolve components to code tokens, falling back to exact active-name tokens.

        The name fallback is local to the compared expression and is used only when
        no DUR code exists for that component. It never creates a global alias.
        """
        atoms = parse_ingredient_expression(value)
        if not atoms:
            return ResolvedExpression((), None, False, ())
        per_atom_tokens: list[tuple[str, ...]] = []
        qualifier = atoms[0].qualifier if len(atoms) == 1 else None
        preprocessed = len(atoms) > 1
        for atom in atoms:
            alternatives: set[str] = set()
            for name in atom.names:
                codes, used_active = self._codes_for_name(name, category, global_fallback=global_fallback)
                preprocessed = preprocessed or atom.preprocessed or used_active
                if len(codes) == 1:
                    alternatives.add("code:" + next(iter(codes)))
                    continue
                if len(codes) > 1 and not atom.explicit_alternatives:
                    return ResolvedExpression((), qualifier, True, ({
                        "ingredient_name": str(value or "").strip(),
                        "candidate_codes": sorted(codes),
                        "reason": "active_moiety_multiple_codes",
                    },))
                if len(codes) > 1:
                    alternatives.update("code:" + code for code in codes)
                    continue
                active_name = active_moiety_key(name)
                if active_name:
                    alternatives.add("name:" + active_name)
                    preprocessed = True
            if not alternatives:
                return ResolvedExpression((), qualifier, preprocessed, ())
            per_atom_tokens.append(tuple(sorted(alternatives)))
        signatures = {frozenset(choice) for choice in itertools.product(*per_atom_tokens) if choice}
        return ResolvedExpression(tuple(sorted(signatures, key=lambda s: tuple(sorted(s)))), qualifier, preprocessed, ())

    def resolve_permit_composition(self, value: object) -> frozenset[str] | None:
        atoms = parse_ingredient_expression(value)
        if not atoms:
            return None
        codes: set[str] = set()
        for atom in atoms:
            if len(atom.names) != 1 or atom.qualifier:
                return None
            name = atom.names[0]
            exact = set(self._exact_global.get(name, set()))
            if len(exact) == 1:
                codes.update(exact)
                continue
            if exact:
                return None
            active = self._active_global.get(active_moiety_key(name), set()) if active_moiety_key(name) == name else set()
            if len(active) != 1:
                return None
            codes.update(active)
        return frozenset(codes) if codes else None

    def extract_rule_value_korean_signatures(self, value: object, category: str) -> tuple[frozenset[str], ...]:
        text = normalize_korean_identity(value)
        if not text:
            return ()
        matched: set[str] = set()
        # Longest names first avoids a short alias obscuring a more specific salt name.
        names = sorted(
            (name for (cat, name) in self._korean_category if cat == category and len(name) >= 3),
            key=len,
            reverse=True,
        )
        occupied: list[tuple[int, int]] = []
        for name in names:
            start = text.find(name)
            if start < 0:
                continue
            end = start + len(name)
            if any(not (end <= lo or start >= hi) for lo, hi in occupied):
                continue
            codes = self._korean_category[(category, name)]
            if len(codes) != 1:
                continue
            matched.update(codes)
            occupied.append((start, end))
        if not matched:
            return ()
        # The XLSX phrase "A ... 또는 B ..." declares alternatives, not a combination.
        if "또는" in text and len(matched) > 1:
            return tuple(frozenset({code}) for code in sorted(matched))
        return (frozenset(matched),)

    def extract_rule_value_korean_signature(self, value: object, category: str) -> frozenset[str] | None:
        signatures = self.extract_rule_value_korean_signatures(value, category)
        return signatures[0] if len(signatures) == 1 else None
