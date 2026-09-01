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

## Figma mapping

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

Screen routes map to D01 `32:2`, D02 `32:45`, D03 `32:82`, and D04 `32:142`.
The component source also carries `data-figma-node` and `data-figma-screen`
attributes to keep visual QA traceable.

Code Connect publication is intentionally not configured yet. Figma reported
that the active account requires an Organization or Enterprise Dev/Full seat.
After the seat is upgraded, run the Code Connect suggestion workflow against
the component page and add parserless `.figma.ts` templates.
