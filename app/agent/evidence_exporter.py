"""Evidence bundle export helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agent.evidence import EvidenceBundle
from app.security.redaction import redact_sensitive_data


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

    return redact_sensitive_data(value, drop_sensitive_keys=True)


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
        "# 证据包",
        "",
        f"* Run ID: `{bundle.get('run_id')}`",
        f"* 创建时间 (`created_at`): `{created_at}`",
        f"* 证据条目数 (`total_evidence_items`): `{bundle.get('total_evidence_items', 0)}`",
        "",
        "## 任务",
        "",
        str(bundle.get("task") or ""),
        "",
    ]

    warnings = [str(item) for item in bundle.get("warnings") or []]
    if warnings:
        lines.extend(["## 警告", ""])
        lines.extend(f"* {warning}" for warning in warnings)
        lines.append("")

    groups = [item for item in bundle.get("source_groups") or [] if isinstance(item, dict)]
    if groups:
        lines.extend(["## 来源分组", ""])
        for group in groups:
            lines.append(
                f"* `{group.get('source_type')}`: {group.get('count', 0)} 条 "
                f"(mock={group.get('mock_count', 0)}, "
                f"fallback={group.get('fallback_count', 0)}, "
                f"unsupported={group.get('unsupported_count', 0)})"
            )
        lines.append("")

    _append_claims(lines, "结论-证据映射", bundle.get("claims") or [])
    _append_claims(lines, "未支持或受限结论", bundle.get("unsupported_claims") or [])

    items = [item for item in bundle.get("evidence_items") or [] if isinstance(item, dict)]
    lines.extend(["## 证据条目", ""])
    if not items:
        lines.extend(["未抽取到结构化证据条目。", ""])
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
                f"* 工具 (`tool_name`): `{item.get('tool_name')}`",
                f"* 步骤 (`step_no`): `{item.get('step_no')}`",
                f"* 来源类型 (`source_type`): `{item.get('source_type')}`",
                f"* 来源引用 (`source_ref`): `{item.get('source_ref') or ''}`",
                f"* 状态 (`status`): `{item.get('status')}`",
                f"* 置信度 (`confidence`): `{item.get('confidence')}`",
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
            f"- 支持程度=`{claim.get('support_level')}`, 证据={evidence}"
        )
        if claim.get("notes"):
            lines.append(f"  说明: {claim.get('notes')}")
    lines.append("")
