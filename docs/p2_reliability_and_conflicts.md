# P2 Reliability And Conflict Reconciliation

P2 scores every Claim-Evidence edge, deduplicates dependent sources, detects
deterministic conflicts, and persists an auditable Claim resolution. It builds
on the immutable P1 provenance graph.

## Versioned Policy

The default policy is `config/source_policy.v1.json`. Runtime configuration:

| Setting | Default | Purpose |
| --- | --- | --- |
| `EVIDENCE_REASONING_ENABLED` | `true` | Enables P2 materialization |
| `SOURCE_POLICY_PATH` | `config/source_policy.v1.json` | Selects the versioned policy |

The policy defines source classes, domain classification, allowed/blocked
domains, Claim-type keywords, freshness windows, minimum source quality,
minimum independent sources, and conflict thresholds. Changing policy content
does not require core code changes. The policy hash and
`REASONING_ENGINE_VERSION` jointly identify a reasoning run, so rule changes do
not silently reuse prior scores.

## Reliability Score

The initial engineering prior is:

```text
score = authority * 0.25
      + traceability * 0.20
      + freshness * 0.15
      + relevance * 0.20
      + independence * 0.10
      + extraction_completeness * 0.10
```

Every dimension, weight, source class, source cluster, evaluation time, policy
version, engine version, and rationale is persisted. Freshness uses the
immutable snapshot evaluation time rather than wall-clock query time, making
recalculation deterministic.

Source independence uses exact passage duplication first, then organization,
then canonical URI. Ten mirrors of one passage therefore contribute one
independent support cluster. These weights are not scientifically calibrated;
P4 must calibrate them against a labeled dataset and run ablation studies.

## Fact And Conflict Rules

P2 normalizes percentages, CNY scales, common English count scales, year/quarter
scope, and positive/negative direction before comparison. A contradiction is
only emitted when the text is sufficiently related to the same fact:

- `supports`: normalized values agree, or relevant text supports the Claim.
- `refutes`: comparable values or relevant polarities conflict.
- `contextualizes`: time, unit, subject, or other scope is not comparable.

Claim resolution states are `no_conflict`, `resolved_by_scope`,
`resolved_by_authority`, `resolved_by_recency`, `unresolved`, and
`requires_human`. Conflicting values are never averaged. Support and refutation
quality are aggregated once per independent source cluster.

High confidence requires both the Claim-type minimum source reliability and
minimum independent source count. Failure to pass that gate caps confidence at
0.69. `unresolved` and `requires_human` have stricter caps and are rendered in
the final-answer warning plus the report's conflict-and-limitations section.

An optional LLM relation response must match the strict structured relation
contract. Invalid output is rejected; it cannot select a winning side.

## Persistence And API

Alembic revision `0003_evidence_reasoning` adds:

- `evidence_reasoning_runs`
- `evidence_reliability_scores`
- `claim_resolutions`

`GET /api/tasks/{run_id}/evidence/v2` now includes `reasoning`,
`reliability_scores`, and `resolutions`. Historical policy runs remain in the
database; the API explicitly selects the run produced by the configured policy
instead of relying on timestamp ordering.

All application startup paths now use Alembic as the schema authority. Legacy
unversioned or `create_all`-ahead demo databases are classified and stamped only
when their complete table set matches a known revision; partial schemas fail
startup.

## Verification

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe scripts\smoke_provenance_v2.py
.venv\Scripts\python.exe scripts\smoke_reasoning_capacity.py
.venv\Scripts\python.exe scripts\smoke_final_project.py
```

Verified scenarios include opposite numeric values, different time scopes,
unit conversion, ten reposts, official correction, SQL versus blog conflict,
moderate unresolved conflict, and high-quality conflict requiring human review.
The 10,000-edge Claim audit query measured p95 124.77ms locally against the
300ms target. The forced-offline aggregate suite passed 24/24 checks.

## Current Boundaries

P2 still reasons over P1 Plan-derived Claims. It does not yet extract and align
every free-text sentence in the final report, nor does it prove that the
initial weights are statistically calibrated. Those remain P4 evaluation and
future extractor work.
