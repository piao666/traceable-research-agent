"""Immutable, replayable model decision envelopes shared by research stages."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.evidence.artifact_store import ArtifactStore
from app.security.redaction import redact_sensitive_data


def retain_decision(kind: str, inputs: dict[str, Any], outputs: dict[str, Any],
                    *, db: Any = None, run_id: str | None = None,
                    root: str | None = None, parent: str | None = None,
                    record_usage: bool = False) -> dict[str, Any]:
    """Persist before applying a verdict; cache hits point at the original envelope."""
    from app.agent.budget import current_budget
    from app.config import Settings
    from app.trace.logger import record_trace_event
    runtime = current_budget()
    if runtime is not None:
        db, run_id = db or runtime.db, run_id or runtime.run_id
    payload = {"version": "decision-audit-v1", "kind": kind,
               "inputs": inputs, "outputs": outputs, "parent_decision": parent}
    safe = redact_sensitive_data(payload)
    record = ArtifactStore(Path(root or Settings.from_env().evidence_artifact_root)).put_text(
        json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    reference = {"decision_sha256": record.content_hash, "artifact_path": record.artifact_path,
                 "version": payload["version"], "redaction_changed": safe != payload}
    if db is not None and run_id:
        from app.llm.cost import estimate_cost_from_tokens, PRICING
        usage = (outputs.get("usage") or {}) if record_usage else {}
        token_in, token_out = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        provider, model = str(outputs.get("provider") or ""), str(outputs.get("model") or "")
        known_price = model.lower() in PRICING.get(provider.lower(), {})
        cost = estimate_cost_from_tokens(provider, model, token_in, token_out) if known_price else None
        reference.update(usage_recorded=record_usage, token_in=token_in, token_out=token_out,
                         cost_evaluable=cost is not None if record_usage else None)
        record_trace_event(db, run_id, 0, kind, "success", {"parent_decision": parent},
                           "Immutable decision envelope retained.", reference,
                           token_in=token_in, token_out=token_out, estimated_cost=cost or 0.0)
    return reference
