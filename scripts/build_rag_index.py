"""Build the local RAG index from workspace/docs."""

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.rag.build_index import build_local_index


if __name__ == "__main__":
    result = build_local_index()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("success"):
        raise SystemExit(1)
