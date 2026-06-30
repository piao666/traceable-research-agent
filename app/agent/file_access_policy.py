"""Shared file-reader path policy for planning, execution, and tools."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from app.config import Settings, settings


ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = (ROOT / "workspace" / "docs").resolve()
CONFIRMATION_REASON_OUTSIDE_ALLOWED_ROOTS = "file_reader_path_outside_allowed_roots"


def _split_roots(value: str | None) -> list[str]:
    raw = value or "workspace/docs"
    return [item.strip() for item in re.split(r"[;\n]+", raw) if item.strip()]


def allowed_roots(settings_obj: Settings = settings) -> list[Path]:
    """Return configured file-reader roots, keeping workspace/docs as fallback."""

    roots: list[Path] = []
    for item in _split_roots(settings_obj.file_reader_allowed_roots):
        path = Path(item)
        if not path.is_absolute():
            path = ROOT / path
        resolved = path.resolve()
        if resolved not in roots:
            roots.append(resolved)
    if DOCS_ROOT not in roots:
        roots.insert(0, DOCS_ROOT)
    return roots


def _looks_repo_relative(raw_path: str) -> bool:
    normalized = raw_path.replace("\\", "/").lstrip("./")
    return normalized.startswith(("workspace/", "docs/", "app/", "scripts/", "frontend/"))


def resolve_file_reader_path(raw_path: str) -> Path:
    """Resolve file_reader paths while preserving docs-relative compatibility."""

    text = str(raw_path or "").strip()
    path = Path(text)
    if path.is_absolute():
        return path.resolve()
    normalized = text.replace("\\", "/").lstrip("./")
    if normalized.startswith("workspace/docs/"):
        return (ROOT / normalized).resolve()
    if normalized.startswith("docs/"):
        return (DOCS_ROOT / normalized.removeprefix("docs/")).resolve()
    if _looks_repo_relative(text):
        return (ROOT / text).resolve()
    return (DOCS_ROOT / path).resolve()


def path_within_root(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    root = root.resolve()
    return resolved == root or root in resolved.parents


def find_allowed_root(path: Path, settings_obj: Settings = settings) -> Path | None:
    for root in allowed_roots(settings_obj):
        if path_within_root(path, root):
            return root
    return None


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def confirmation_details_for_path(
    raw_path: str,
    settings_obj: Settings = settings,
) -> dict[str, Any]:
    resolved = resolve_file_reader_path(raw_path)
    allowed = find_allowed_root(resolved, settings_obj)
    return {
        "reason": CONFIRMATION_REASON_OUTSIDE_ALLOWED_ROOTS,
        "requested_path": raw_path,
        "resolved_path": str(resolved),
        "display_path": display_path(resolved),
        "allowed": allowed is not None,
        "allowed_root": str(allowed) if allowed else None,
        "allowed_roots": [str(root) for root in allowed_roots(settings_obj)],
        "requires_confirmation": allowed is None
        and settings_obj.file_reader_hitl_outside_allowed_roots,
        "confirmation_scope": "single_file_path",
    }


def _normalize_for_match(value: str) -> str:
    return os.path.normcase(str(Path(value).resolve()))


def approved_file_reader_paths(plan: dict[str, Any] | None) -> set[str]:
    confirmation = plan.get("confirmation") if isinstance(plan, dict) else None
    if not isinstance(confirmation, dict) or not confirmation.get("approved"):
        return set()
    values = confirmation.get("approved_file_reader_paths")
    if not isinstance(values, list):
        single = confirmation.get("approved_file_reader_path")
        values = [single] if single else []
    approved: set[str] = set()
    for item in values:
        if not item:
            continue
        try:
            approved.add(_normalize_for_match(str(item)))
        except OSError:
            continue
    return approved


def is_path_approved(plan: dict[str, Any] | None, resolved_path: Path) -> bool:
    return _normalize_for_match(str(resolved_path)) in approved_file_reader_paths(plan)


def file_reader_execution_arguments(
    arguments: dict[str, Any],
    plan: dict[str, Any] | None,
) -> dict[str, Any]:
    """Attach a narrow per-file approval token for the tool handler."""

    prepared = dict(arguments or {})
    raw_path = str(prepared.get("path") or "").strip()
    if not raw_path:
        return prepared
    resolved = resolve_file_reader_path(raw_path)
    if find_allowed_root(resolved) is not None:
        return prepared
    if is_path_approved(plan, resolved):
        prepared["_approved_file_reader_path"] = str(resolved)
    return prepared
