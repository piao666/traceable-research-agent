"""Exercise the pinned pip against local truncated and corrupt wheel responses.

No external package index, application installation or Docker daemon is used.
"""

from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
from zipfile import ZipFile


def _pip_supports_resumption() -> bool:
    try:
        major, minor, *_ = version("pip").split(".")
        return (int(major), int(minor)) >= (25, 2)
    except (PackageNotFoundError, ValueError):
        return False


@unittest.skipUnless(_pip_supports_resumption(), "Requires pip>=25.2; Docker and CI pin pip==26.2.1")
class PipDownloadRecoveryTests(unittest.TestCase):
    def _download(self, *, truncate: bool, correct_hash: bool) -> tuple[subprocess.CompletedProcess[str], list[str | None], bytes | None, bytes]:
        buffer = BytesIO()
        with ZipFile(buffer, "w") as wheel:
            wheel.writestr("download_probe/__init__.py", "# Local download test.\n" * 16384)
            wheel.writestr("download_probe-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: download-probe\nVersion: 1.0\n")
            wheel.writestr("download_probe-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            wheel.writestr("download_probe-1.0.dist-info/RECORD", "")
        payload = buffer.getvalue()
        requests: list[str | None] = []
        filename = "download_probe-1.0-py3-none-any.whl"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                range_header = self.headers.get("Range")
                requests.append(range_header)
                start = int(range_header.removeprefix("bytes=").split("-", 1)[0]) if range_header else 0
                self.send_response(206 if range_header else 200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(payload) - start))
                if range_header:
                    self.send_header("Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}")
                self.end_headers()
                end = len(payload) // 2 if truncate and len(requests) == 1 else len(payload)
                self.wfile.write(payload[start:end])
                self.wfile.flush()
                self.close_connection = True

            def log_message(self, format: str, *args: object) -> None:
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with tempfile.TemporaryDirectory() as temporary:
                    digest = hashlib.sha256(payload).hexdigest() if correct_hash else "0" * 64
                    url = f"http://127.0.0.1:{server.server_port}/{filename}#sha256={digest}"
                    # Keep this synthetic localhost transfer independent of host proxy/pip settings.
                    environment = {key: value for key, value in os.environ.items()
                                   if not key.lower().endswith("_proxy") and not key.startswith("PIP_")}
                    result = subprocess.run(
                        [sys.executable, "-m", "pip", "--isolated", "--disable-pip-version-check",
                         "download", "--no-index", "--no-deps", "--no-cache-dir",
                         "--retries", "0", "--resume-retries", "2", "--timeout", "2",
                         "--progress-bar", "off", "--dest", temporary, url],
                        env=environment, capture_output=True, text=True, timeout=30,
                    )
                    artifact = Path(temporary) / filename
                    downloaded = artifact.read_bytes() if artifact.exists() else None
            finally:
                server.shutdown()
                thread.join(timeout=5)
        return result, requests, downloaded, payload

    def test_incomplete_download_is_recovered_and_hash_verified(self) -> None:
        result, requests, downloaded, payload = self._download(truncate=True, correct_hash=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreaterEqual(len(requests), 2, "The fixture must actually interrupt the first response")
        self.assertTrue(any(request is not None for request in requests), "Expected a Range resume request")
        self.assertEqual(downloaded, payload)

    def test_hash_mismatch_still_fails_closed(self) -> None:
        result, _, downloaded, _ = self._download(truncate=False, correct_hash=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DO NOT MATCH THE HASHES", result.stdout + result.stderr)
        self.assertIsNone(downloaded)


if __name__ == "__main__":
    unittest.main()
