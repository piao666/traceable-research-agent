"""Immutable trace-backed read payload; no database session crosses tool threads."""
from dataclasses import dataclass

from app.tools.base import ToolResult


@dataclass(frozen=True)
class SourceSnapshot:
    source_id: str
    trace_id: str
    url: str
    text: str


def read_snapshot(arguments: dict) -> ToolResult:
    snapshot = arguments.get("_source_snapshot")
    if not isinstance(snapshot, SourceSnapshot):
        return ToolResult(success=False, error_message="Source is not available in this run; use its recorded URL.",
                          metadata={"error_type": "not_found", "executed": False})
    try:
        offset = max(0, int(arguments.get("offset", 0)))
        length = min(6000, max(1, int(arguments.get("max_chars", 3000))))
    except (ValueError, TypeError):
        return ToolResult(success=False, error_message="offset/max_chars must be integers.",
                          metadata={"error_type": "invalid_args", "executed": False})
    if offset >= len(snapshot.text):
        return ToolResult(success=False, error_message="Offset is past the available source text.",
                          metadata={"error_type": "invalid_args", "executed": False})
    text = snapshot.text[offset:offset + length]
    return ToolResult(success=True, output={"source_content": {
        "source_id": snapshot.source_id, "origin_trace_id": snapshot.trace_id,
        "url": snapshot.url, "text": text, "offset": offset, "total_chars": len(snapshot.text),
        "next_offset": offset + len(text) if offset + len(text) < len(snapshot.text) else None}},
        output_summary=f"Read {len(text)} stored characters at offset {offset}; no network request or new source.",
        metadata={"read_only": True, "data_source": "persisted_trace", "network_request": False})
