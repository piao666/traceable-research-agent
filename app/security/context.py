"""Non-persistent tenant and user context extraction."""

from dataclasses import dataclass
import re

from fastapi import Request

from app.config import Settings, settings


CONTEXT_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,80}")


@dataclass(frozen=True)
class RequestContext:
    tenant_id: str
    user_id: str
    auth_enabled: bool


def _clean_context_id(value: str | None, default: str) -> str:
    candidate = (value or "").strip()
    if CONTEXT_ID_PATTERN.fullmatch(candidate):
        return candidate
    return default


def get_request_context(
    request: Request,
    settings_obj: Settings = settings,
) -> RequestContext:
    """Extract sanitized request-scoped identity without database persistence."""

    return RequestContext(
        tenant_id=_clean_context_id(
            request.headers.get(settings_obj.tenant_header_name),
            settings_obj.default_tenant_id,
        ),
        user_id=_clean_context_id(
            request.headers.get(settings_obj.user_header_name),
            settings_obj.default_user_id,
        ),
        auth_enabled=settings_obj.auth_enabled,
    )


def require_request_context(request: Request) -> RequestContext:
    """Attach sanitized tenant/user context to the current request only."""

    context = get_request_context(request, settings)
    request.state.request_context = context
    return context
