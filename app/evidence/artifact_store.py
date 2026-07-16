"""Content-addressed immutable gzip artifact storage."""

from __future__ import annotations

import gzip
import hashlib
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ARTIFACT_ROOT = Path("workspace/artifacts")


@dataclass(frozen=True)
class ArtifactRecord:
    content_hash: str
    artifact_path: str
    size_bytes: int
    compressed_size_bytes: int


class ArtifactIntegrityError(ValueError):
    pass


class ArtifactStore:
    def __init__(self, root: Path = DEFAULT_ARTIFACT_ROOT):
        self.root = root.resolve()

    def put_text(self, content: str) -> ArtifactRecord:
        return self.put_bytes(content.encode("utf-8"))

    def put_bytes(self, content: bytes) -> ArtifactRecord:
        content_hash = hashlib.sha256(content).hexdigest()
        target = self._target(content_hash)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_suffix(".tmp")
            with gzip.open(temporary, "wb", compresslevel=9) as handle:
                handle.write(content)
            temporary.replace(target)
        return ArtifactRecord(
            content_hash=content_hash,
            artifact_path=target.relative_to(self.root).as_posix(),
            size_bytes=len(content),
            compressed_size_bytes=target.stat().st_size,
        )

    def read_bytes(self, artifact_path: str, expected_hash: str | None = None) -> bytes:
        target = self.resolve(artifact_path)
        with gzip.open(target, "rb") as handle:
            content = handle.read()
        actual_hash = hashlib.sha256(content).hexdigest()
        if expected_hash and actual_hash != expected_hash:
            raise ArtifactIntegrityError(
                f"Artifact hash mismatch: expected {expected_hash}, got {actual_hash}"
            )
        return content

    def read_text(self, artifact_path: str, expected_hash: str | None = None) -> str:
        return self.read_bytes(artifact_path, expected_hash).decode("utf-8")

    def resolve(self, artifact_path: str) -> Path:
        target = (self.root / artifact_path).resolve()
        if self.root != target and self.root not in target.parents:
            raise ValueError("Artifact path escaped configured root")
        if not target.is_file():
            raise FileNotFoundError(target)
        return target

    def _target(self, content_hash: str) -> Path:
        return self.root / content_hash[:2] / f"{content_hash}.json.gz"
