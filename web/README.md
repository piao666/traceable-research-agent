# Traceable Research Agent Web

React + TypeScript + Vite frontend for the local-first, single-instance API.

## Run

From the repository root, start FastAPI on port 8000. Then:

```bash
cd web
npm ci
npm run dev
```

Vite proxies `/health` and `/api` to `http://127.0.0.1:8000`. Production can
set `VITE_API_BASE_URL` when the API is served from a different origin. The UI
does not collect credentials and assumes the default local `AUTH_ENABLED=false`
configuration.

## API types

The committed TypeScript declarations are generated from the FastAPI
application. Install the root Python requirements before regeneration:

```bash
cd web
npm run generate:api
```

`src/api/schema.d.ts` contains the complete generated declaration. The intermediate
`openapi.json` is local-only and is not committed.

## Implemented routes and historical design references

All D01–D11 routes have API-connected code. The references below are historical
source annotations, **not current design verification**. A 2026-09-02 check of
the previously linked design file returned an empty page; requesting node `32:2`
returned node-not-found. Preserve the annotations until the current design can
be reconciled. Do not infer that Figma screens or Code Connect were published.

| Screen | Route | Existing source annotation |
|---|---|---|
| D01 Overview | `/` | `32:2` |
| D02 Task list | `/runs` | `32:82` |
| D03 New research | `/research/new` | `32:45` |
| D04 Plan review | `/runs/:id/plan` | `32:142` |
| D05 Workbench | `/runs/:id` | Not verified |
| D06 Evidence | `/runs/:id/evidence` | Not verified |
| D07 Report | `/runs/:id/report` | Not verified |
| D08 Sessions | `/sessions`, `/sessions/:id` | Not verified |
| D09 Memory | `/memory` | Not verified |
| D10 Capabilities | `/capabilities` | Not verified |
| D11 System and quality | `/system` | Not verified |

The D02/D03 numbering here follows the R0–R7 repair plan, correcting the previous
README's reversed task-list/new-research labels; source node attributes did not change.

| Code | Figma node |
|---|---|
| `AppShell` | `28:6` |
| `Button` | `22:3` |
| `StatusChip` | `23:15` |
| `MetricCard` | `24:3` |
| `Panel` | `24:8` |
| `Tab` | `25:13` |
| `TableRow` | `26:3` |
| `TimelineRow` | `26:29` |
| `OptionCard` | `44:32` |
| approval action bar | `40:29` |

R6 adds shared feedback/modal/error-boundary components, darker text/accent/success
tokens, keyboard states and stacked mobile task rows. These are explicit code-side
adjustments, not new Figma mappings. Native `<dialog>` manages the modal background;
component tests stub dialog methods and cannot verify native focus containment.
Code Connect remains unpublished; the earlier account-seat limitation has not
been revalidated in this batch. No new Figma nodes/templates are fabricated.

## R6 verification

```bash
npm run typecheck
npm run lint
npm test
npm run build
node qa/smoke.mjs
```

See [isolated layout QA](qa/README.md) for fixture-only desktop/390px checks.
The supported browser rejected the local preview with `ERR_BLOCKED_BY_CLIENT`
in this environment. Thus no screenshot, layout measurement, zoom, touch or
native keyboard-trap result is claimed. Source changes and mocked DOM tests are
not final accessibility or design acceptance. No dependencies were added.
