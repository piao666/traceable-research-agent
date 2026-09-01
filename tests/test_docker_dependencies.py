"""Contracts for API-only Docker installs and compatible local manifests."""

from pathlib import Path
import re
import tempfile
import unittest

from scripts.requirements_manifest import read_pinned_requirements
from scripts.smoke_docker_config import main as smoke_docker_config


ROOT = Path(__file__).resolve().parents[1]


class DockerDependencyTests(unittest.TestCase):
    def test_full_environment_preserves_existing_direct_pins(self) -> None:
        self.assertEqual(read_pinned_requirements(ROOT / "requirements.txt"), {
            "alembic": "1.18.5", "beautifulsoup4": "4.13.4", "fastapi": "0.139.2",
            "httpx": "0.28.1", "pydantic": "2.13.4", "python-docx": "1.2.0",
            "python-dotenv": "1.2.2", "reportlab": "5.0.0", "requests": "2.34.2",
            "sqlalchemy": "2.0.51", "pymupdf": "1.25.5", "sqlglot": "30.13.0",
            "streamlit": "1.60.0", "uvicorn": "0.51.0", "pytest": "9.1.1",
        })

    def test_optional_packages_are_not_api_requirements(self) -> None:
        api = read_pinned_requirements(ROOT / "requirements/api.txt")
        self.assertTrue({"fastapi", "uvicorn", "alembic", "pymupdf", "reportlab"} <= api.keys())
        self.assertFalse({"streamlit", "pytest", "pyarrow", "pandas", "numpy", "pydeck"} & api.keys())
        self.assertEqual(read_pinned_requirements(ROOT / "requirements/streamlit.txt"), {"streamlit": "1.60.0"})
        self.assertEqual(read_pinned_requirements(ROOT / "requirements/dev.txt"), {"pytest": "9.1.1"})

    def test_api_build_ancestry_never_installs_optional_packages(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        stages: dict[str, tuple[str, str]] = {}
        matches = list(re.finditer(r"^FROM (\S+) AS (\S+)\s*$", dockerfile, re.M))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(dockerfile)
            stages[match[2]] = (match[1], dockerfile[match.end():end])
        body = ""
        stage = "api"
        while stage in stages:
            stage, instructions = stages[stage]
            body = instructions + body
        self.assertIn("-r requirements/api.txt", body)
        self.assertNotIn("-r requirements.txt", body)
        self.assertNotIn("requirements/streamlit.txt", body)
        self.assertNotIn("requirements/dev.txt", dockerfile)
        self.assertLess(body.index("pip==26.2.1"), body.index("PIP_RESUME_RETRIES=10"))
        self.assertLess(body.index("-r requirements/api.txt"), body.index("COPY . ."))
        self.assertEqual(stages["light"][0], "streamlit")
        self.assertIn('CMD ["python", "scripts/docker_entrypoint.py"]', stages["light"][1])
        self.assertIn('CMD ["streamlit", "run"', stages["streamlit"][1])

    def test_compose_selects_separate_runtime_targets(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        api = compose.split("  api:\n", 1)[1].split("\n  streamlit:", 1)[0]
        streamlit = compose.split("  streamlit:\n", 1)[1].split("\n  web:", 1)[0]
        self.assertIn("target: api", api)
        self.assertIn("target: streamlit", streamlit)
        self.assertIn("./workspace:/app/workspace", api)
        self.assertIn("./workspace:/app/workspace", streamlit)

    def test_docker_static_smoke(self) -> None:
        smoke_docker_config()

    def test_manifest_rejects_unpinned_dependencies_and_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "requirements.txt"
            for invalid in ("requests>=2", "-r requirements.txt", "requests==1\nrequests==2"):
                with self.subTest(invalid=invalid):
                    manifest.write_text(invalid, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        read_pinned_requirements(manifest)

    def test_manifest_resolves_relative_includes_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "groups").mkdir()
            (root / "requirements.txt").write_text("# local\n-r groups/api.txt\n", encoding="utf-8")
            (root / "groups/api.txt").write_text("uvicorn[standard]==0.51.0 # runtime\n", encoding="utf-8")
            self.assertEqual(read_pinned_requirements(root / "requirements.txt"), {"uvicorn": "0.51.0"})


if __name__ == "__main__":
    unittest.main()
