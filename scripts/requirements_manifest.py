"""Read this repository's pinned manifests, including relative -r includes."""

from __future__ import annotations

from pathlib import Path
import re


def read_pinned_requirements(path: Path, ancestors: tuple[Path, ...] = ()) -> dict[str, str]:
    path = path.resolve()
    if path in ancestors:
        raise ValueError(f"Cyclic requirements include: {path}")
    packages: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            entries = read_pinned_requirements(path.parent / line[3:].strip(), (*ancestors, path))
        else:
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?==([A-Za-z0-9_.+!-]+)", line)
            if not match:
                raise ValueError(f"Dependency must have an exact pin in {path.name}: {line}")
            name = re.sub(r"[-_.]+", "-", match[1]).lower()
            entries = {name: match[2]}
        for name, version in entries.items():
            if name in packages and packages[name] != version:
                raise ValueError(f"Conflicting versions for {name}")
            packages[name] = version
    return packages
