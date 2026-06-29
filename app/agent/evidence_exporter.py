"""Evidence bundle export helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agent.evidence import EvidenceBundle


ROOT = Path(__file__).resolve().parents[2]
EXPORT_ROOT = ROOT / "workspace" / "exports"

_FORMAT_EXTENSIONS = {
    "json": "json",
    "jsonl": "jsonl",
    "markdown": "md",
}
_FORMAT_MEDIA_TYPES = {
    "json": "application/json",
    "jsonl": "application/x-ndjson",
    "markdown": "text/markdown",
}
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{8,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
)


@dataclass(frozen=True)
class EvidenceExportResult:
    run_id: str
    format: str
    export_path: str
    item_count: int
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "format": self.format,
            "export_path": self.export_path,
            "item_count": self.item_count,
            "created_at": self.created_at,
        }


def normalize_export_format(value: str | None) -> str:
    """Return a supported export format name."""

    normalized = (value or "json").strip().lower()
    if normalized in {"md", "markdown"}:
        return "markdown"
    if normalized in _FORMAT_EXTENSIONS:
        return normalized
    raise ValueError(f"Unsupported evidence export format: {value}")


def export_evidence_bundle(
    bundle: EvidenceBundle,
    export_format: str = "json",
    export_root: Path = EXPORT_ROOT,
) -> EvidenceExportResult:
    """Write an EvidenceBundle export artifact under workspace/exports."""

    normalized_format = normalize_export_format(export_format)
    created_at = datetime.now(timezone.utc).isoformat()
    export_root.mkdir(parents=True, exist_ok=True)
    target = _export_path(export_root, bundle.run_id, normalized_format)
    payload = sanitize_export_data(bundle.to_dict())

    if normalized_format == "json":
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    elif normalized_format == "jsonl":
        lines = [
            json.dumps(item, ensure_ascii=False, default=str)
            for item in payload.get("evidence_items", [])
            if isinstance(item, dict)
        ]
        target.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
    else:
        target.write_text(_render_markdown(payload, created_at), encoding="utf-8")

    return EvidenceExportResult(
        run_id=bundle.run_id,
        format=normalized_format,
        export_path=target.relative_to(ROOT).as_posix(),
        item_count=int(payload.get("total_evidence_items") or 0),
        created_at=created_at,
    )


def export_filename(run_id: str, export_format: str) -> str:
    normalized_format = normalize_export_format(export_format)
    safe_run_id = re.sub(r"[^A-Za-z0-9_-]", "_", run_id)[:96] or "unknown"
    return f"evidence_{safe_run_id}.{_FORMAT_EXTENSIONS[normalized_format]}"


def export_media_type(export_format: str) -> str:
    normalized_format = normalize_export_format(export_format)
    return _FORMAT_MEDIA_TYPES[normalized_format]


def resolve_export_path(export_path: str, export_root: Path = EXPORT_ROOT) -> Path:
    """Resolve a relative export path and ensure it stays under workspace/exports."""

    target = (ROOT / export_path).resolve()
    root = export_root.resolve()
    if root != target and root not in target.parents:
        raise ValueError("Evidence export path escaped workspace/exports")
    return target


def read_export_text(export_path: str) -> str:
    target = resolve_export_path(export_path)
    return target.read_text(encoding="utf-8")


def sanitize_export_data(value: Any) -> Any:
    """Remove obvious secret-bearing keys and redact common token-shaped values."""

    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                continue
            sanitized[key_text] = sanitize_export_data(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_export_data(item) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_VALUE_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _export_path(export_root: Path, run_id: str, export_format: str) -> Path:
    safe_run_id = re.sub(r"[^A-Za-z0-9_-]", "_", run_id)[:96] or "unknown"
    extension = _FORMAT_EXTENSIONS[export_format]
    target = (export_root / f"evidence_{safe_run_id}.{extension}").resolve()
    root = export_root.resolve()
    if root != target and root not in target.parents:
        raise ValueError("Evidence export path escaped workspace/exports")
    return target


def _render_markdown(bundle: dict[str, Any], created_at: str) -> str:
    lines: list[str] = [
        "# Evidence Packet",
        "",
        f"* Run ID: `{bundle.get('run_id')}`",
        f"* Created at: `{created_at}`",
        f"* Evidence items: `{bundle.get('total_evidence_items', 0)}`",
        "",
        "## Task",
        "",
        str(bundle.get("task") or ""),
        "",
    ]

    warnings = [str(item) for item in bundle.get("warnings") or []]
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"* {warning}" for warning in warnings)
        lines.append("")

    groups = [item for item in bundle.get("source_groups") or [] if isinstance(item, dict)]
    if groups:
        lines.extend(["## Source Groups", ""])
        for group in groups:
            lines.append(
                f"* `{group.get('source_type')}`: {group.get('count', 0)} items "
                f"(mock={group.get('mock_count', 0)}, "
                f"fallback={group.get('fallback_count', 0)}, "
                f"unsupported={group.get('unsupported_count', 0)})"
            )
        lines.append("")

    _append_claims(lines, "Claim-Evidence Map", bundle.get("claims") or [])
    _append_claims(lines, "Unsupported Or Limited Claims", bundle.get("unsupported_claims") or [])

    items = [item for item in bundle.get("evidence_items") or [] if isinstance(item, dict)]
    lines.extend(["## Evidence Items", ""])
    if not items:
        lines.extend(["No structured evidence items were extracted.", ""])
    for item in items:
        flags = []
        if item.get("is_mock"):
            flags.append("mock")
        if item.get("is_fallback"):
            flags.append("fallback")
        if item.get("unsupported_reason"):
            flags.append("unsupported")
        suffix = f" ({', '.join(flags)})" if flags else ""
        lines.extend(
            [
                f"### {item.get('evidence_id')} {item.get('title')}{suffix}",
                "",
                f"* Tool: `{item.get('tool_name')}`",
                f"* Step: `{item.get('step_no')}`",
                f"* Source type: `{item.get('source_type')}`",
                f"* Source ref: `{item.get('source_ref') or ''}`",
                f"* Status: `{item.get('status')}`",
                f"* Confidence: `{item.get('confidence')}`",
                "",
                str(item.get("snippet") or ""),
                "",
            ]
        )
    return "\n".join(lines)


def _append_claims(lines: list[str], title: str, claims: list[Any]) -> None:
    claim_dicts = [item for item in claims if isinstance(item, dict)]
    if not claim_dicts:
        return
    lines.extend([f"## {title}", ""])
    for claim in claim_dicts:
        evidence = ", ".join(f"`{item}`" for item in claim.get("evidence_ids") or []) or "`<none>`"
        lines.append(
            f"* `{claim.get('claim_id')}` {claim.get('claim')} "
            f"- support=`{claim.get('support_level')}`, evidence={evidence}"
        )
        if claim.get("notes"):
            lines.append(f"  Notes: {claim.get('notes')}")
    lines.append("")
