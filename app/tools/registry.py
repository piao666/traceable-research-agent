"""In-memory Tool Registry foundation."""

from collections.abc import Callable
from typing import Any

from app.tools.base import ToolResult, ToolSpec

ToolHandler = Callable[[dict[str, Any]], ToolResult]

_tool_specs: dict[str, ToolSpec] = {}
_tool_handlers: dict[str, ToolHandler | None] = {}


def register_tool(spec: ToolSpec, handler: ToolHandler | None = None) -> None:
    """Register or replace a tool spec and optional handler."""

    _tool_specs[spec.name] = spec
    _tool_handlers[spec.name] = handler


def get_tool(name: str) -> ToolSpec | None:
    """Return one tool spec by name."""

    return _tool_specs.get(name)


def list_tools() -> list[ToolSpec]:
    """Return all registered tool specs sorted by name for stable API output."""

    return [_tool_specs[name] for name in sorted(_tool_specs)]


def _tool_source(spec: ToolSpec | None) -> str:
    if spec and "mcp_remote" in spec.tags:
        return "mcp_remote"
    return "local"


def _with_registry_metadata(
    name: str,
    spec: ToolSpec | None,
    result: ToolResult,
) -> ToolResult:
    metadata = dict(result.metadata or {})
    metadata.setdefault("tool_name", name)
    metadata.setdefault("tool_source", _tool_source(spec))
    return ToolResult(
        success=result.success,
        output=result.output,
        output_summary=result.output_summary,
        error_message=result.error_message,
        metadata=metadata,
    )


def execute_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
) -> ToolResult:
    """Execute a registered tool or return a stable failure result.

    Day5 intentionally does not write trace rows. Trace persistence is added
    later when real handlers exist.
    """

    spec = get_tool(name)
    if spec is None:
        return ToolResult(
            success=False,
            error_message=f"Tool '{name}' is not registered.",
            metadata={"tool_name": name, "tool_source": "unknown", "error_type": "not_found"},
        )

    if not spec.enabled:
        return ToolResult(
            success=False,
            error_message=f"Tool '{name}' is disabled.",
            metadata={"tool_name": name, "tool_source": _tool_source(spec), "error_type": "disabled"},
        )

    handler = _tool_handlers.get(name)
    if handler is None:
        return ToolResult(
            success=False,
            error_message="Tool handler is not implemented yet.",
            metadata={
                "tool_name": name,
                "tool_source": _tool_source(spec),
                "arguments": arguments or {},
                "error_type": "not_implemented",
            },
        )

    try:
        return _with_registry_metadata(name, spec, handler(arguments or {}))
    except Exception as exc:  # pragma: no cover - handler path is future work
        return ToolResult(
            success=False,
            error_message=str(exc),
            metadata={"tool_name": name, "tool_source": _tool_source(spec), "error_type": "handler_error"},
        )
