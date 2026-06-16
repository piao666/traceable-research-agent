"""Simple character-based chunker."""

from __future__ import annotations

from typing import TypedDict

from app.rag.loader import LoadedDocument


class TextChunk(TypedDict):
    chunk_id: str
    source: str
    text: str
    metadata: dict[str, str | int]


def chunk_documents(
    documents: list[LoadedDocument],
    chunk_size: int = 500,
    chunk_overlap: int = 80,
) -> list[TextChunk]:
    """Split documents into overlapping character chunks."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")

    chunks: list[TextChunk] = []
    for document in documents:
        text = document["text"]
        source = document["source"]
        start = 0
        index = 0
        step = chunk_size - chunk_overlap
        while start < len(text):
            chunk_text = text[start : start + chunk_size].strip()
            if chunk_text:
                chunks.append(
                    {
                        "chunk_id": f"{source}#{index}",
                        "source": source,
                        "text": chunk_text,
                        "metadata": {
                            "source": source,
                            "start": start,
                            "end": min(start + chunk_size, len(text)),
                            "chunk_index": index,
                        },
                    }
                )
            start += step
            index += 1
    return chunks
