# R6–R8 isolated UI checks

These are fixed, explicitly labelled samples, not research results or deployment
diagnostics. They require no API keys, backend process or historical database.
Use the existing frontend dependencies; do not load a production API into this harness.

From `web/`:

```bash
node qa/smoke.mjs
npm run dev -- --config qa/vite.config.ts
```

Open `http://127.0.0.1:5174/qa/viewport.html` in a supported local browser.
The page embeds the actual React app with width options 1280/1024/768/390/320;
choose a route and scenario. Empty, failing, delayed, long-content and
waiting-human fixtures cover the 11 screens plus session detail. Refreshing a
failed scenario does not make it succeed: switch back to populated to simulate
recovery. Mutations intentionally return an error; successful actions are covered
by component/API tests instead. A served HTML page is not a rendered layout check.

R8 adds `recovery` (GitHub disabled, other sources fetched), `budget` (failed Run,
shared-budget stop, blocked final report) and `legacy` (review notice, absent old
budget) scenarios. Use workbench, evidence and report routes to inspect them.
Recovery/budget fixtures use generated API types; they are not live task results.

## Isolation guarantees

- The QA config explicitly removes inherited API proxies and forces
  `VITE_API_BASE_URL` to the fixture origin, even if a local build variable exists.
- All `/api/*` and `/health` requests are handled by fixture middleware; unknown
  endpoints fail locally and non-GET requests return 409 before any action.
- No SQLite, provider credentials or remote search/model requests are used.
- The normal production build uses `vite.config.ts`, not this config. QA files
  outside `public/` are not production entry points. Do not deploy the QA server.
- The smoke script starts/closes its own localhost server and checks 59 routes/
  methods/scenarios/isolation assertions; it does not drive a browser.
- Keep existing browser drafts safe by using this separate origin/port. No claim
  is made that QA samples match the full OpenAPI schema or external results.

## Manual checklist (pending)

1. For all routes, check 1280px and 390px populated/empty/error/slow states;
   additionally check 1024px and 320px on creation, approval and workbench pages.
2. Use the visible layout-check button to record viewport/document width and
   candidate overflow, then inspect the actual screen. Table scroll regions are
   intentionally excluded from candidate overflow but still need manual review.
3. Tab through the skip link, navigation, form controls, scrollable payloads and
   repeated task actions. Check clear focus outlines and no obscured controls.
4. Check status tabs with arrows, Home and End; verify heading and page-title
   context after navigation. Query-only filter changes should preserve focus.
5. Open confirmation dialogs: title/description announced, initial heading focus,
   Tab/Shift+Tab contained, Escape cancels only while idle, close restores the
   trigger or main content. Reject writes are expected in this QA server.
6. Follow exact citation and Trace links. Ensure keyboard focus lands on the
   selected passage/Trace and does not jump again on subsequent refresh.
7. Check 200% zoom, reduced motion, forced colors, long URLs, safe-area/mobile
   navigation and access to the final controls without obscuration. An iframe
   width is not a physical mobile/touch device test.
8. Compare against accessible current Figma nodes before claiming design fidelity.
9. In R8 scenarios, distinguish tool-local disable/cooldown/input restrictions
   from a root-budget stop; unknown costs must not show as free. Check parent links,
   long source titles/URLs, candidate → Trace links and excerpt-origin labels.
   On a deployed test Run verify snapshot refresh and cancellation; this fixture
   server never performs those mutations or real external recovery.

## Evidence boundary

The current environment's supported browser blocks this local preview with
`ERR_BLOCKED_BY_CLIENT`. Therefore the manual checklist is **not passed** and
no desktop/390px screenshot or layout measurement is supplied. Text-token
contrast tests check 11 configured color pairs numerically, not all rendered
combinations or disabled/focus/control contrast. DOM tests test state/focus logic
with dialog shims, not a real browser or screen reader. Carry these gaps into release acceptance;
do not equate them with final provider or user acceptance.
