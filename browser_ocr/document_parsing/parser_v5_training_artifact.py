from __future__ import annotations

from pathlib import Path


def parser_v5_checkpoint_reference(root: Path, checkpoint: Path) -> str:
    """Return the portable checkpoint reference persisted in a training artifact."""
    resolved_root = root.resolve()
    resolved_checkpoint = checkpoint.resolve()
    try:
        relative = resolved_checkpoint.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("Parser v5 checkpoint must stay inside the training output root") from exc
    return relative.as_posix()


def resolve_parser_v5_checkpoint(result_path: str | Path, reference: object) -> Path:
    """Resolve a result-relative checkpoint while preventing host/container path leakage."""
    result = Path(result_path).resolve()
    text = str(reference or "").strip()
    if not text:
        raise ValueError("Parser v5 training result checkpoint reference is required")
    relative = Path(text)
    if relative.is_absolute():
        raise ValueError("Parser v5 training result checkpoint reference must be relative")
    root = result.parent
    checkpoint = (root / relative).resolve()
    try:
        checkpoint.relative_to(root)
    except ValueError as exc:
        raise ValueError("Parser v5 training result checkpoint reference escapes the result root") from exc
    return checkpoint


__all__ = ["parser_v5_checkpoint_reference", "resolve_parser_v5_checkpoint"]