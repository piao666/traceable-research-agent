/** Same-process, localhost-only smoke of fixture isolation; not visual QA. */
import assert from "node:assert/strict";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const root = fileURLToPath(new URL("../", import.meta.url));
const server = await createServer({ root, configFile: fileURLToPath(new URL("vite.config.ts", import.meta.url)), server: { host: "127.0.0.1", port: 0, strictPort: false } });
let checks = 0;
try {
  await server.listen();
  const address = server.httpServer.address();
  assert(address && typeof address !== "string");
  const origin = `http://127.0.0.1:${address.port}`;
  assert.deepEqual(server.config.server.proxy, {}); checks++;
  assert.equal(server.config.define["import.meta.env.VITE_API_BASE_URL"], '""'); checks++;
  const paths = ["/health", "/api/tasks", "/api/tasks/fixture", "/api/tasks/fixture/review", "/api/tasks/fixture/plan", "/api/tasks/fixture/trace", "/api/tasks/fixture/evidence", "/api/tasks/fixture/evidence/v2", "/api/reports/fixture", "/api/sessions", "/api/sessions/session-fixture", "/api/memory", "/api/memory/audit", "/api/tools", "/api/skills", "/api/skills/sample_skill", "/api/runtime/capabilities", "/api/runtime/diagnostics", "/api/improvement/stats", "/api/improvement/trend", "/api/improvement/runs/fixture"];
  for (const path of paths) {
    const response = await fetch(origin + path); assert.equal(response.status, 200, path);
    assert.match(response.headers.get("content-type"), /application\/json/); await response.json(); checks++;
  }
  for (const method of ["POST", "PATCH", "DELETE", "PUT"]) {
    const response = await fetch(origin + "/api/tasks", { method });
    assert.equal(response.status, 409); assert.match((await response.json()).detail, /不执行任何写入/); checks++;
  }
  for (const path of paths) {
    const response = await fetch(origin + path, { headers: { Referer: `${origin}/?qa=error` } });
    assert.equal(response.status, 503); await response.json(); checks++;
  }
  const empty = await fetch(origin + "/api/tasks", { headers: { Referer: `${origin}/?qa=empty` } });
  assert.equal((await empty.json()).total, 0); checks++;
  const waiting = await fetch(origin + "/api/tasks/fixture", { headers: { Referer: `${origin}/?qa=waiting` } });
  assert.equal((await waiting.json()).status, "waiting_human"); checks++;
  const scenarioGet = async (path, scenario) => (await fetch(origin + path, { headers: { Referer: `${origin}/?qa=${scenario}` } })).json();
  const recovery = await scenarioGet("/api/tasks/fixture/plan", "recovery");
  assert.equal(recovery.execution_insights.tools[0].status, "disabled"); checks++;
  assert.equal(recovery.execution_insights.source_context.gaps.fetched, 1); checks++;
  assert.equal(recovery.execution_budget.cost_currency, "CNY"); checks++;
  const budget = await scenarioGet("/api/tasks/fixture/plan", "budget");
  assert.equal(budget.execution_budget.stop_reason, "tool_calls"); checks++;
  assert.equal((await scenarioGet("/api/tasks/fixture", "budget")).status, "failed"); checks++;
  assert.equal((await scenarioGet("/api/reports/fixture", "budget")).exists, false); checks++;
  assert.equal((await scenarioGet("/api/tasks/fixture", "legacy")).requires_review, true); checks++;
  assert.equal((await fetch(origin + "/api/unmapped-fixture")).status, 404); checks++;
  const preview = await fetch(origin + "/qa/viewport.html");
  assert.equal(preview.status, 200); assert.match(await preview.text(), /390/); checks++;
  process.stdout.write(`QA fixture isolation: ${checks} checks passed. No browser layout or provider acceptance implied.\n`);
} finally {
  await server.close();
}
