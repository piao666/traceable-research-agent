"""Load local text documents from workspace/docs."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict


class LoadedDocument(TypedDict):
    source: str
    text: str
    metadata: dict[str, str | int]


SUPPORTED_EXTENSIONS = {".md", ".txt"}


def load_documents(docs_dir: str | Path = "workspace/docs") -> list[LoadedDocument]:
    """Read .md and .txt files under docs_dir."""

    root = Path(docs_dir).resolve()
    if not root.exists():
        return []

    documents: list[LoadedDocument] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        text = resolved.read_text(encoding="utf-8")
        relative = resolved.relative_to(root).as_posix()
        documents.append(
            {
                "source": relative,
                "text": text,
                "metadata": {
                    "source": relative,
                    "extension": resolved.suffix.lower(),
                    "chars": len(text),
                },
            }
        )
    return documents
