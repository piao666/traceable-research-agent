"""Safe local file reader tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent.file_access_policy import (
    DOCS_ROOT,
    allowed_roots,
    display_path,
    find_allowed_root,
    resolve_file_reader_path,
)
from app.tools.base import ToolResult


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAX_CHARS = 8000
MAX_CHARS_LIMIT = 20000
SUPPORTED_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".py", ".log"}
DAY6_NOT_IMPLEMENTED_EXTENSIONS = {".docx", ".pdf", ".xlsx"}


def _failure(
    message: str,
    *,
    error_type: str,
    path: str | None = None,
    extension: str | None = None,
    resolved_path: Path | None = None,
) -> ToolResult:
    return ToolResult(
        success=False,
        error_message=message,
        metadata={
            "error_type": error_type,
            "path": path,
            "extension": extension,
            "docs_root": str(DOCS_ROOT),
            "allowed_roots": [str(root) for root in allowed_roots()],
            "resolved_path_summary": str(resolved_path) if resolved_path else None,
        },
    )


def _coerce_max_chars(value: Any) -> int:
    try:
        max_chars = int(value) if value is not None else DEFAULT_MAX_CHARS
    except (TypeError, ValueError):
        max_chars = DEFAULT_MAX_CHARS
    return max(1, min(max_chars, MAX_CHARS_LIMIT))


def _output_path(resolved_path: Path, allowed_root: Path | None) -> str:
    if allowed_root is not None:
        try:
            return resolved_path.relative_to(allowed_root).as_posix()
        except ValueError:
            pass
    try:
        return resolved_path.relative_to(DOCS_ROOT).as_posix()
    except ValueError:
        return display_path(resolved_path)


def _approved_path_value(arguments: dict[str, Any]) -> str | None:
    value = arguments.get("_approved_file_reader_path")
    return str(value).strip() if value else None


def _resolve_allowed_path_for_read(
    raw_path: str,
    arguments: dict[str, Any],
) -> tuple[Path | None, Path | None, bool, ToolResult | None]:
    approved_path = _approved_path_value(arguments)
    resolved = resolve_file_reader_path(raw_path)
    allowed_root = find_allowed_root(resolved)
    if allowed_root is not None:
        return resolved, allowed_root, False, None
    approved = False
    try:
        approved = bool(approved_path) and Path(approved_path).resolve() == resolved
    except OSError:
        approved = False
    if approved:
        return resolved, None, True, None
    error_type = "approval_mismatch" if approved_path else "safety_rejected"
    message = (
        "Approved file path did not match the requested file_reader path."
        if approved_path
        else "Path is outside configured file_reader allowed roots and has not been approved for this run."
    )
    return None, None, False, _failure(
        message,
        error_type=error_type,
        path=raw_path,
        extension=resolved.suffix.lower(),
        resolved_path=resolved,
    )


def read_file(arguments: dict[str, Any]) -> ToolResult:
    """Read a supported text file under workspace/docs."""

    raw_path = str(arguments.get("path") or "").strip()
    max_chars = _coerce_max_chars(arguments.get("max_chars"))

    if not raw_path:
        return _failure("Missing required argument: path.", error_type="invalid_args")

    resolved_path, allowed_root, approved, failure = _resolve_allowed_path_for_read(
        raw_path, arguments
    )
    if failure is not None:
        return failure
    assert resolved_path is not None

    extension = resolved_path.suffix.lower()
    if extension in DAY6_NOT_IMPLEMENTED_EXTENSIONS:
        return _failure(
            "format not implemented in Day6",
            error_type="format_not_implemented",
            path=raw_path,
            extension=extension,
            resolved_path=resolved_path,
        )
    if extension not in SUPPORTED_EXTENSIONS:
        return _failure(
            f"Unsupported file extension: {extension or '<none>'}.",
            error_type="unsupported_format",
            path=raw_path,
            extension=extension,
            resolved_path=resolved_path,
        )
    if not resolved_path.exists() or not resolved_path.is_file():
        return _failure(
            "File not found.",
            error_type="not_found",
            path=raw_path,
            extension=extension,
            resolved_path=resolved_path,
        )

    try:
        text = resolved_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = resolved_path.read_text(encoding="utf-8-sig")
        except Exception as exc:
            return _failure(
                f"Failed to decode file: {exc}",
                error_type="read_error",
                path=raw_path,
                extension=extension,
                resolved_path=resolved_path,
            )
    except Exception as exc:
        return _failure(
            f"Failed to read file: {exc}",
            error_type="read_error",
            path=raw_path,
            extension=extension,
            resolved_path=resolved_path,
        )

    truncated = len(text) > max_chars
    content = text[:max_chars]
    relative_path = _output_path(resolved_path, allowed_root)
    chars_read = len(content)
    return ToolResult(
        success=True,
        output={
            "path": relative_path,
            "content": content,
            "chars_read": chars_read,
            "truncated": truncated,
        },
        output_summary=(
            f"Read {relative_path}: {chars_read} chars"
            + (" (truncated)" if truncated else "")
        ),
        metadata={
            "error_type": None,
            "safe_path": True,
            "approved_outside_allowed_roots": approved,
            "extension": extension,
            "docs_root": str(DOCS_ROOT),
            "allowed_root": str(allowed_root) if allowed_root else None,
            "allowed_roots": [str(root) for root in allowed_roots()],
            "resolved_path_summary": str(resolved_path),
            "max_chars": max_chars,
        },
    )
