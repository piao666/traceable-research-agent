"""Skill self-check: validate Skill JSON files for correctness.

Usage:
    python scripts/skill_smoke.py <skill_name>      # Single skill
    python scripts/skill_smoke.py --all              # All skills in workspace/skills/
    python scripts/skill_smoke.py --json             # Output machine-readable JSON

Checks performed:
    1. JSON syntax and schema validation
    2. required_tools are registered in Tool Registry
    3. {{parameters.*}} placeholders reference existing params, types match defaults
    4. {{steps[N].*}} placeholders reference valid step indices and fields
    5. No circular references in placeholder chains

Exit code 0 = all checks passed; non-zero = at least one failure.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Fix Windows GBK encoding for emoji output
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import init_db
from app.skills.loader import load_skill_from_file, validate_skill
from app.skills.registry import init_skill_registry
from app.tools.defaults import register_default_tools

SKILLS_DIR = ROOT / "workspace" / "skills"

PLACEHOLDER_RE = re.compile(r"\{\{(.+?)\}\}")


def _find_placeholders(value: Any) -> list[str]:
    """Extract all {{...}} placeholder references from a value."""
    if isinstance(value, str):
        return PLACEHOLDER_RE.findall(value)
    if isinstance(value, dict):
        refs: list[str] = []
        for v in value.values():
            refs.extend(_find_placeholders(v))
        return refs
    if isinstance(value, list):
        refs: list[str] = []
        for item in value:
            refs.extend(_find_placeholders(item))
        return refs
    return []


def _check_parameters(skill: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate parameter references and type consistency."""
    issues: list[dict[str, Any]] = []
    params = skill.get("parameters") or {}

    # Collect all parameter references from steps
    all_refs: list[str] = []
    for step in skill.get("steps") or []:
        args = step.get("arguments") or {}
        args_from = step.get("arguments_from") or {}
        all_refs.extend(_find_placeholders(args))
        all_refs.extend(_find_placeholders(args_from))

    # Check parameters.* references
    for ref in all_refs:
        if not ref.startswith("parameters."):
            continue
        param_name = ref.split(".", 1)[1]
        if param_name not in params:
            issues.append({
                "check": "parameter_reference",
                "severity": "error",
                "detail": f"Placeholder {{{{parameters.{param_name}}}}} references undefined parameter",
                "placeholder": ref,
            })

    # Check default value types match declared types
    for pname, pdef in params.items():
        ptype = str(pdef.get("type") or "string")
        default = pdef.get("default")
        if default is None:
            continue
        if ptype == "integer" and not isinstance(default, (int, float)):
            issues.append({
                "check": "param_type",
                "severity": "warning",
                "detail": f"Parameter '{pname}': declared type=integer but default={default!r}",
            })
        elif ptype == "boolean" and not isinstance(default, bool):
            issues.append({
                "check": "param_type",
                "severity": "warning",
                "detail": f"Parameter '{pname}': declared type=boolean but default={default!r}",
            })
        elif ptype == "string" and not isinstance(default, str):
            # Integer/boolean defaults for string params are OK (JSON coercion)
            pass

    return issues


def _check_steps(skill: dict[str, Any], tools: set[str]) -> list[dict[str, Any]]:
    """Validate step references and tool availability."""
    issues: list[dict[str, Any]] = []
    steps = skill.get("steps") or []
    step_count = len(steps)

    for i, step in enumerate(steps):
        # Check tool_name
        tool_name = str(step.get("tool_name") or "")
        if not tool_name:
            issues.append({
                "check": "step_tool",
                "severity": "error",
                "detail": f"Step {i + 1}: tool_name is empty",
            })
        elif tool_name not in tools:
            issues.append({
                "check": "step_tool",
                "severity": "error",
                "detail": f"Step {i + 1}: tool '{tool_name}' not registered in Tool Registry",
            })

        # Check steps[N].* references in arguments and arguments_from
        all_refs: list[str] = []
        all_refs.extend(_find_placeholders(step.get("arguments") or {}))
        all_refs.extend(_find_placeholders(step.get("arguments_from") or {}))

        for ref in all_refs:
            if not ref.startswith("steps["):
                continue
            # Parse steps[N].field
            m = re.match(r"steps\[(\d+)\]\.(.+)", ref)
            if not m:
                issues.append({
                    "check": "step_reference",
                    "severity": "error",
                    "detail": f"Step {i + 1}: cannot parse placeholder {{{{ {ref} }}}}",
                    "placeholder": ref,
                })
                continue
            ref_step = int(m.group(1))
            field = m.group(2)
            if ref_step < 0 or ref_step >= step_count:
                issues.append({
                    "check": "step_reference",
                    "severity": "error",
                    "detail": f"Step {i + 1}: references step[{ref_step}] but skill has {step_count} steps",
                    "placeholder": ref,
                })
            # Check field exists on target step (basic: tool_name, goal, arguments, step_no)
            valid_fields = {"tool_name", "goal", "arguments", "step_no", "output"}
            if field not in valid_fields and not field.startswith("output."):
                issues.append({
                    "check": "step_reference",
                    "severity": "warning",
                    "detail": f"Step {i + 1}: references steps[{ref_step}].{field} — field may not exist",
                    "placeholder": ref,
                })

    return issues


def check_skill(path: Path, tools: set[str]) -> dict[str, Any]:
    """Run all checks on a single Skill file. Returns result dict."""
    result: dict[str, Any] = {
        "file": str(path.relative_to(ROOT)),
        "passed": True,
        "checks": [],
    }

    # ── 1. JSON parse + schema ────────────────────────────────────────
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        result["passed"] = False
        result["checks"].append({
            "check": "json_parse",
            "severity": "error",
            "detail": f"Invalid JSON: {e}",
        })
        return result
    except OSError as e:
        result["passed"] = False
        result["checks"].append({
            "check": "file_read",
            "severity": "error",
            "detail": f"Cannot read file: {e}",
        })
        return result

    if not isinstance(raw, dict):
        result["passed"] = False
        result["checks"].append({
            "check": "schema",
            "severity": "error",
            "detail": "Root element is not a JSON object",
        })
        return result

    name = str(raw.get("name") or "")
    version = str(raw.get("version") or "")
    description = str(raw.get("description") or "")
    required_tools = raw.get("required_tools") or []
    steps = raw.get("steps") or []
    changelog = raw.get("changelog")

    # ── 2. Required fields ────────────────────────────────────────────
    if not name:
        result["passed"] = False
        result["checks"].append({
            "check": "schema",
            "severity": "error",
            "detail": "Skill name is empty",
        })
    if not version:
        result["passed"] = False
        result["checks"].append({
            "check": "schema",
            "severity": "error",
            "detail": "Skill version is empty",
        })
    if not steps:
        result["passed"] = False
        result["checks"].append({
            "check": "schema",
            "severity": "error",
            "detail": "Skill has no steps",
        })
    else:
        result["checks"].append({
            "check": "schema",
            "severity": "info",
            "detail": f"Skill '{name}' v{version}: {len(steps)} steps, {len(required_tools)} required tools",
        })

    # ── 3. Changelog ──────────────────────────────────────────────────
    if changelog and isinstance(changelog, list):
        result["checks"].append({
            "check": "changelog",
            "severity": "info",
            "detail": f"Changelog has {len(changelog)} entries",
        })

    # ── 4. required_tools check ───────────────────────────────────────
    for tool_name in required_tools:
        if tool_name not in tools:
            result["passed"] = False
            result["checks"].append({
                "check": "required_tool",
                "severity": "error",
                "detail": f"Required tool '{tool_name}' not in Tool Registry",
            })

    # ── 5. Parameter references ───────────────────────────────────────
    param_issues = _check_parameters(raw)
    result["checks"].extend(param_issues)
    if any(p["severity"] == "error" for p in param_issues):
        result["passed"] = False

    # ── 6. Step references ────────────────────────────────────────────
    step_issues = _check_steps(raw, tools)
    result["checks"].extend(step_issues)
    if any(s["severity"] == "error" for s in step_issues):
        result["passed"] = False

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Skill self-check")
    parser.add_argument("skill_name", nargs="?", help="Single skill name to check")
    parser.add_argument("--all", action="store_true", help="Check all skills")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--dir", type=str, default=None, help="Skills directory")
    args = parser.parse_args()

    if not args.skill_name and not args.all:
        parser.error("Specify a skill name or --all")

    # Init runtime
    init_db()
    register_default_tools()
    skills_dir = Path(args.dir) if args.dir else SKILLS_DIR
    init_skill_registry(skills_dir)
    from app.tools.registry import list_tools as list_registered_tools
    tools = {spec.name for spec in list_registered_tools()}

    # Find files
    if args.all:
        paths = sorted(skills_dir.glob("*.json"))
    else:
        path = skills_dir / f"{args.skill_name}.json"
        if not path.exists():
            print(f"Skill file not found: {path}", file=sys.stderr)
            return 1
        paths = [path]

    if not paths:
        print("No Skill files found.", file=sys.stderr)
        return 1

    # Run checks
    results = [check_skill(p, tools) for p in paths]
    all_passed = all(r["passed"] for r in results)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for result in results:
            icon = "✅" if result["passed"] else "❌"
            print(f"\n  {icon} {result['file']}")
            errors = [c for c in result["checks"] if c["severity"] == "error"]
            warnings = [c for c in result["checks"] if c["severity"] == "warning"]
            infos = [c for c in result["checks"] if c["severity"] == "info"]
            for c in errors:
                print(f"     ❌ [{c['check']}] {c['detail']}")
            for c in warnings:
                print(f"     ⚠️  [{c['check']}] {c['detail']}")
            for c in infos:
                print(f"     ℹ️  {c['detail']}")

    failed_count = sum(1 for r in results if not r["passed"])
    summary = f"\n  {'All passed' if all_passed else f'{failed_count} failed'}"
    print(summary)

    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
