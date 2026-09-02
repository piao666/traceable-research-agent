// @vitest-environment node
import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

const css = readFileSync("src/styles/tokens.css", "utf8");

const colors = Object.fromEntries([...css.matchAll(/--ra-([\w-]+):\s*(#[\da-f]{6});/gi)].map(([, name, value]) => [name, value]));
function luminance(hex) {
  const channels = [1, 3, 5].map(index => {
    const value = parseInt(hex.slice(index, index + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}
it.each([
  ["text", "panel"], ["muted", "bg"], ["faint", "panel"], ["muted", "blue-soft"],
  ["text-on-accent", "accent"], ["accent-strong", "blue-soft"],
  ["status-running", "info-soft"], ["status-green", "green-soft"],
  ["status-warning", "warning-soft"], ["status-purple", "panel-soft"],
  ["status-danger", "danger-soft"],
])("keeps the %s / %s text-token contrast at least 4.5:1", (foreground, background) => {
  const values = [luminance(colors[foreground]), luminance(colors[background])].sort((a, b) => b - a);
  expect((values[0] + 0.05) / (values[1] + 0.05)).toBeGreaterThanOrEqual(4.5);
});
