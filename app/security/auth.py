"""Optional demo API-key authentication for FastAPI routes."""

from hmac import compare_digest

from fastapi import HTTPException, Request

from app.config import Settings, settings


def get_api_key_from_request(
    request: Request,
    settings_obj: Settings = settings,
) -> str | None:
    """Read an API key from the configured header or a Bearer credential."""

    header_value = request.headers.get(settings_obj.auth_header_name)
    if header_value and header_value.strip():
        return header_value.strip()

    authorization = request.headers.get("Authorization", "").strip()
    scheme, separator, credential = authorization.partition(" ")
    if separator and scheme.lower() == "bearer" and credential.strip():
        return credential.strip()
    return None


def require_api_key(request: Request) -> None:
    """Enforce the configured demo API key when authentication is enabled."""

    if not settings.auth_enabled:
        return
    if not settings.demo_api_key:
        raise HTTPException(
            status_code=503,
            detail="API key auth is enabled but DEMO_API_KEY is not configured.",
        )

    request_key = get_api_key_from_request(request, settings)
    if request_key is None:
        raise HTTPException(status_code=401, detail="Missing API key.")
    if not compare_digest(request_key, settings.demo_api_key):
        raise HTTPException(status_code=403, detail="Invalid API key.")
