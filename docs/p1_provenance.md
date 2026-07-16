# P1 Claim-Level Provenance

P1 adds an immutable, queryable evidence graph while keeping the existing V1
evidence API available.

## Data Chain

```text
ToolTrace -> SourceDocument -> SourceSnapshot -> EvidencePassage
          -> EvidenceAssertion -> ResearchClaim -> ClaimEvidenceEdge
          -> Citation -> ReportClaim
```

The SQLite database stores graph identities, locators, hashes, and extracted
fields. Raw tool output is gzip-compressed under `workspace/artifacts/` using
its SHA-256 digest as the immutable object identity. Reading an artifact
recomputes the digest and rejects tampered content.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `EVIDENCE_PIPELINE_VERSION` | `v2` | Enables V2 materialization; use `v1` for rollback |
| `EVIDENCE_EXTRACTOR_VERSION` | `v2-rule-1` | Auditable extractor identity |
| `EVIDENCE_ARTIFACT_ROOT` | `workspace/artifacts` | Content-addressed raw output root |
| `EVIDENCE_PASSAGE_MAX_CHARS` | `4000` | Maximum text retained in a passage row |

Run `alembic upgrade head` to create the P1 schema. Migration
`0002_claim_provenance_schema` is repeatable through Alembic's revision state;
back up the SQLite file before applying it to retained data.

The Docker entrypoint runs `scripts/migrate_database.py` before demo data
initialization. It upgrades fresh or versioned databases and bootstraps legacy
unversioned V1/V2 demo files. A partial legacy schema is rejected instead of
being stamped speculatively.

## API And Report Contract

- `GET /api/tasks/{run_id}/evidence` keeps the V1 response unchanged.
- `GET /api/tasks/{run_id}/evidence/v2` returns the complete graph and integrity
  flags for passage, assertion, edge, and citation resolution.
- Web, RAG, SQL, GitHub, and file evidence use source-specific locators.
- Reporter renders citations from persisted `CIT-*` objects. LLM synthesis that
  omits citations or emits an unknown citation ID is rejected and falls back to
  deterministic output.

Existing completed V2 runs are immutable: changing the extractor setting does
not silently rewrite them. A future re-extraction workflow must create an
explicit new pipeline version.

## Verification

```powershell
.venv\Scripts\python.exe -m unittest tests.test_p1_provenance -v
.venv\Scripts\python.exe scripts\smoke_provenance_v2.py
.venv\Scripts\python.exe scripts\smoke_provenance_capacity.py
.venv\Scripts\python.exe scripts\smoke_alembic_sql_parser.py
```

Verified on 2026-07-16:

- Four source locator contracts and artifact tamper detection passed.
- V1 and V2 APIs completed the same offline task; repeated V2 reads preserved
  citation identities.
- Every materialized passage and assertion resolved to a ToolTrace, and every
  citation resolved to a passage and report claim.
- Capacity cases at 20, 100, and 1000 evidence items used exactly nine SQL
  statements per graph read. The 1000-item local query p95 was 206.4ms.
- Alembic upgraded an empty database through revisions 0001 and 0002, with no
  metadata drift detected afterward.
- Docker bootstrapped an unversioned legacy demo database to revision 0002;
  restart repeated the migration idempotently and retained V2 citations.

## P1 Boundaries

The current `ResearchClaim` records are derived from plan claims, and scalar
extraction is intentionally conservative. P1 does not claim calibrated source
reliability, semantic conflict resolution, or final-report free-text claim
extraction; those are P2 concerns.
