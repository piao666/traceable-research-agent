"""Safe local file reader tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.tools.base import ToolResult


ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = (ROOT / "workspace" / "docs").resolve()
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
            "resolved_path_summary": str(resolved_path) if resolved_path else None,
        },
    )


def _coerce_max_chars(value: Any) -> int:
    try:
        max_chars = int(value) if value is not None else DEFAULT_MAX_CHARS
    except (TypeError, ValueError):
        max_chars = DEFAULT_MAX_CHARS
    return max(1, min(max_chars, MAX_CHARS_LIMIT))


def _resolve_allowed_path(raw_path: str) -> tuple[Path | None, ToolResult | None]:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (DOCS_ROOT / candidate).resolve()

    try:
        resolved.relative_to(DOCS_ROOT)
    except ValueError:
        return None, _failure(
            "Path is outside workspace/docs.",
            error_type="safety_rejected",
            path=raw_path,
            extension=resolved.suffix.lower(),
            resolved_path=resolved,
        )
    return resolved, None


def read_file(arguments: dict[str, Any]) -> ToolResult:
    """Read a supported text file under workspace/docs."""

    raw_path = str(arguments.get("path") or "").strip()
    max_chars = _coerce_max_chars(arguments.get("max_chars"))

    if not raw_path:
        return _failure("Missing required argument: path.", error_type="invalid_args")

    resolved_path, failure = _resolve_allowed_path(raw_path)
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
    relative_path = resolved_path.relative_to(DOCS_ROOT).as_posix()
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
            "extension": extension,
            "docs_root": str(DOCS_ROOT),
            "resolved_path_summary": str(resolved_path),
            "max_chars": max_chars,
        },
    )
