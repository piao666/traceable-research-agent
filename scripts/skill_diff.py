"""Compare two versions of a Skill JSON file and output a Markdown diff.

Usage:
    python scripts/skill_diff.py <skill_name> <v1_file> <v2_file>
    python scripts/skill_diff.py deep_web_research workspace/skills/deep_web_research.json workspace/skills/deep_web_research_v2.json

Output: Markdown table showing changes in required_tools, parameters, and steps.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Fix Windows GBK encoding for emoji output
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_skill(path: Path) -> dict[str, Any]:
    """Load and validate a Skill JSON file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Root element is not a JSON object in {path}")
    return raw


def _format_arguments(args: Any) -> str:
    """Format arguments dict for display."""
    if not args:
        return "—"
    if isinstance(args, dict):
        items = []
        for k, v in args.items():
            if isinstance(v, str) and len(str(v)) > 60:
                v = str(v)[:57] + "..."
            items.append(f"{k}={v}")
        return ", ".join(items) if items else "—"
    return str(args)[:80]


def _format_parameters(params: dict[str, Any]) -> str:
    """Format parameters dict for display."""
    if not params:
        return "—"
    items = []
    for k, pdef in params.items():
        ptype = pdef.get("type", "?")
        req = "*" if pdef.get("required") else ""
        items.append(f"{k}: {ptype}{req}")
    return ", ".join(items) if items else "—"


def diff_skills(v1: dict[str, Any], v2: dict[str, Any]) -> str:
    """Compare two skill versions and return a Markdown diff report."""
    name = v1.get("name") or v2.get("name") or "unknown"
    ver1 = v1.get("version", "?")
    ver2 = v2.get("version", "?")

    lines: list[str] = [
        f"# Skill Diff: {name}",
        "",
        f"* **v1**: {ver1}",
        f"* **v2**: {ver2}",
        "",
    ]

    # ── Metadata ──────────────────────────────────────────────────────
    meta_changes: list[str] = []
    for field in ("name", "version", "description"):
        old_val = v1.get(field, "")
        new_val = v2.get(field, "")
        if old_val != new_val:
            meta_changes.append(f"* {field}: `{old_val}` → `{new_val}`")
    if meta_changes:
        lines.append("## Metadata Changes")
        lines.append("")
        lines.extend(meta_changes)
        lines.append("")

    # ── Required tools ────────────────────────────────────────────────
    old_tools = set(v1.get("required_tools") or [])
    new_tools = set(v2.get("required_tools") or [])
    added_tools = new_tools - old_tools
    removed_tools = old_tools - new_tools
    if added_tools or removed_tools:
        lines.append("## Required Tools Changes")
        lines.append("")
        lines.append("| Change | Tool |")
        lines.append("| --- | --- |")
        for t in sorted(added_tools):
            lines.append(f"| ✅ Added | `{t}` |")
        for t in sorted(removed_tools):
            lines.append(f"| ❌ Removed | `{t}` |")
        lines.append("")
    else:
        lines.append(f"## Required Tools (unchanged: {len(old_tools)} tools)")
        lines.append("")

    # ── Parameters ────────────────────────────────────────────────────
    old_params = v1.get("parameters") or {}
    new_params = v2.get("parameters") or {}
    param_keys = set(old_params) | set(new_params)
    if param_keys:
        param_rows: list[str] = []
        for key in sorted(param_keys):
            old = old_params.get(key, {})
            new = new_params.get(key, {})
            old_type = old.get("type", "?")
            new_type = new.get("type", "?")
            old_req = old.get("required", False)
            new_req = new.get("required", False)
            old_default = old.get("default")
            new_default = new.get("default")
            changes: list[str] = []
            if old_type != new_type:
                changes.append(f"type: {old_type}→{new_type}")
            if old_req != new_req:
                changes.append(f"required: {old_req}→{new_req}")
            if str(old_default) != str(new_default):
                changes.append(f"default: {old_default}→{new_default}")
            if key not in old_params:
                changes.append("**NEW**")
            if key not in new_params:
                changes.append("**REMOVED**")
            if changes:
                param_rows.append(f"| {key} | {', '.join(changes)} |")
        if param_rows:
            lines.append("## Parameter Changes")
            lines.append("")
            lines.append("| Parameter | Changes |")
            lines.append("| --- | --- |")
            lines.extend(param_rows)
            lines.append("")
        else:
            lines.append("## Parameters (unchanged)")
            lines.append("")

    # ── Steps ─────────────────────────────────────────────────────────
    old_steps = v1.get("steps") or []
    new_steps = v2.get("steps") or []
    max_steps = max(len(old_steps), len(new_steps))

    step_rows: list[str] = []
    for i in range(max_steps):
        old_step = old_steps[i] if i < len(old_steps) else None
        new_step = new_steps[i] if i < len(new_steps) else None

        old_tool = (old_step or {}).get("tool_name", "—")
        new_tool = (new_step or {}).get("tool_name", "—")
        old_goal = (old_step or {}).get("goal", "—")
        new_goal = (new_step or {}).get("goal", "—")

        changes: list[str] = []
        if old_step is None:
            changes.append(f"+ **NEW**: {new_tool}")
        elif new_step is None:
            changes.append(f"- **REMOVED**: {old_tool}")
        else:
            if old_tool != new_tool:
                changes.append(f"tool: {old_tool}→{new_tool}")
            if old_goal != new_goal:
                changes.append("goal changed")
            old_args = _format_arguments(old_step.get("arguments"))
            new_args = _format_arguments(new_step.get("arguments"))
            if old_args != new_args:
                changes.append("arguments changed")

        if changes:
            step_rows.append(f"| {i + 1} | {', '.join(changes)} |")

    if step_rows:
        lines.append("## Step Changes")
        lines.append("")
        lines.append("| Step # | Changes |")
        lines.append("| --- | --- |")
        lines.extend(step_rows)
        lines.append("")
    else:
        lines.append(f"## Steps (unchanged: {len(old_steps)} steps)")
        lines.append("")

    # ── Changelog ─────────────────────────────────────────────────────
    new_changelog = v2.get("changelog")
    if new_changelog and isinstance(new_changelog, list):
        lines.append("## v2 Changelog")
        lines.append("")
        for entry in new_changelog:
            lines.append(
                f"* **v{entry.get('version', '?')}** ({entry.get('date', '?')}): "
                f"{entry.get('changes', '')}"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Skill version diff")
    parser.add_argument("skill_name", help="Skill name")
    parser.add_argument("v1", help="Path to v1 JSON file")
    parser.add_argument("v2", help="Path to v2 JSON file")
    args = parser.parse_args()

    v1_path = Path(args.v1)
    v2_path = Path(args.v2)

    if not v1_path.exists():
        print(f"v1 file not found: {v1_path}", file=sys.stderr)
        return 1
    if not v2_path.exists():
        print(f"v2 file not found: {v2_path}", file=sys.stderr)
        return 1

    try:
        v1 = load_skill(v1_path)
        v2 = load_skill(v2_path)
    except Exception as e:
        print(f"Failed to load skill files: {e}", file=sys.stderr)
        return 1

    report = diff_skills(v1, v2)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
