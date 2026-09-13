"""Shared Source/View projection for retrieval backends.

The Source is the backend's bounded extraction and owns every stable identity.
The View is only the caller-sized projection returned in ``FetchResult.content``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


DEFAULT_SOURCE_MAX_CHARS = 500_000


@dataclass(frozen=True)
class SourceView:
    source_content: str
    content: str
    source_content_hash: str | None
    source_content_length: int
    source_truncated: bool
    view_truncated: bool

    @property
    def truncated(self) -> bool:
        return self.source_truncated or self.view_truncated

    def metadata(self) -> dict[str, object]:
        return {
            "source_content_length": self.source_content_length,
            "source_truncated_at_backend_limit": self.source_truncated,
            "view_truncated": self.view_truncated,
        }


def build_source_view(
    extracted_content: str,
    max_chars: int,
    *,
    source_max_chars: int | None = DEFAULT_SOURCE_MAX_CHARS,
    source_truncated: bool = False,
) -> SourceView:
    """Build an identity-bearing Source and a non-identity-bearing View."""

    extracted = str(extracted_content or "")
    if source_max_chars is None:
        source_content = extracted
    else:
        bounded = max(0, int(source_max_chars))
        source_truncated = source_truncated or len(extracted) > bounded
        source_content = extracted[:bounded]
    source_hash = (
        hashlib.sha256(source_content.encode("utf-8")).hexdigest()
        if source_content
        else None
    )
    view_limit = max(0, int(max_chars))
    return SourceView(
        source_content=source_content,
        content=source_content[:view_limit],
        source_content_hash=source_hash,
        source_content_length=len(source_content),
        source_truncated=source_truncated,
        view_truncated=len(source_content) > view_limit,
    )


__all__ = ["DEFAULT_SOURCE_MAX_CHARS", "SourceView", "build_source_view"]
