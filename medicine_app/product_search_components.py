from __future__ import annotations


_COMPONENT_SEPARATORS = frozenset(("/", "·", "ㆍ", "∙", "⋅"))
_BRACKET_PAIRS = {
    "(": ")",
    "[": "]",
    "{": "}",
    "<": ">",
    "（": "）",
    "［": "］",
    "｛": "｝",
    "〈": "〉",
}


def _balanced_openings(text: str) -> set[int]:
    """Return opening-bracket positions that participate in a balanced pair.

    Source ingredient text is not guaranteed to have balanced punctuation. An
    unmatched opening bracket must not swallow every later slash-delimited
    component, while genuinely balanced descriptors still protect slashes
    inside their brackets from being treated as composition separators.
    """
    stack: list[tuple[str, int]] = []
    balanced: set[int] = set()
    for index, char in enumerate(text):
        closing = _BRACKET_PAIRS.get(char)
        if closing is not None:
            stack.append((closing, index))
            continue
        if stack and char == stack[-1][0]:
            _closing, opening_index = stack.pop()
            balanced.add(opening_index)
    return balanced


def split_ingredient_components(value: object) -> tuple[str, ...]:
    """Split ingredient composition at separators outside balanced brackets."""
    text = str(value or "")
    if not text:
        return ()
    balanced_openings = _balanced_openings(text)
    components: list[str] = []
    current: list[str] = []
    closing_stack: list[str] = []
    for index, char in enumerate(text):
        if index in balanced_openings:
            closing_stack.append(_BRACKET_PAIRS[char])
            current.append(char)
            continue
        if closing_stack and char == closing_stack[-1]:
            closing_stack.pop()
            current.append(char)
            continue
        if not closing_stack and char in _COMPONENT_SEPARATORS:
            component = "".join(current).strip()
            if component:
                components.append(component)
            current = []
            continue
        current.append(char)
    component = "".join(current).strip()
    if component:
        components.append(component)
    return tuple(components)


__all__ = ["split_ingredient_components"]