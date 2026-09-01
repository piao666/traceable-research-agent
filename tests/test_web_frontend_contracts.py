"""Source-level contracts for the Phase 8 React frontend."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


class WebFrontendContractTests(unittest.TestCase):
    def test_d01_to_d04_routes_are_implemented(self) -> None:
        app = (WEB / "src/app/App.tsx").read_text(encoding="utf-8")
        for route in ("/", "/research/new", "/runs", "/runs/:runId/plan"):
            self.assertIn(f'path="{route}"', app)

    def test_real_api_workflow_uses_review_and_async_approval(self) -> None:
        client = (WEB / "src/api/client.ts").read_text(encoding="utf-8")
        for endpoint in (
            '"/health"',
            '"/api/tasks"',
            "/review",
            "/approve-plan?start_async=true",
        ):
            self.assertIn(endpoint, client)

    def test_figma_component_nodes_are_traceable(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (WEB / "src/components").glob("*.tsx")
        )
        for node_id in ("28:6", "22:3", "23:15", "24:3", "24:8", "25:13", "26:3", "26:29", "44:32"):
            self.assertIn(node_id, source)

    def test_product_boundary_excludes_account_and_tenant_ui(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8").lower()
            for path in (WEB / "src").rglob("*.tsx")
        )
        for forbidden in ("login", "sign in", "rbac", "tenant", "cloud sync", "api key input"):
            self.assertNotIn(forbidden, source)

    def test_critical_mobile_path_and_touch_targets_are_present(self) -> None:
        css = (WEB / "src/styles/global.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 560px)", css)
        self.assertIn("min-height: 44px", css)
        self.assertIn(".approval-bar { position: sticky", css)
        self.assertIn("grid-template-columns: 1fr", css)


if __name__ == "__main__":
    unittest.main()
