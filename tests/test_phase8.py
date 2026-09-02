"""Phase 8.3 unit tests: pdf_reader tool, page locator, integrity checks."""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch


# ── Helper: create valid PDF bytes using PyMuPDF ─────────────────────────

def _make_minimal_pdf(pages: int = 2) -> bytes:
    """Build a minimal valid PDF with given number of pages using PyMuPDF."""
    import fitz

    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text(
            (72, 72 + i * 12),
            f"Page {i + 1} content. This is test text for PDF extraction.",
            fontsize=11,
        )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_damaged_pdf() -> bytes:
    """Return bytes that start with PDF magic but are otherwise garbage."""
    return b"%PDF-1.4\nThis is not a valid PDF structure.\n"


def _make_non_pdf_bytes() -> bytes:
    """Return bytes that are clearly not PDF."""
    return b"<html><body>Not a PDF</body></html>"


# ── Tests ────────────────────────────────────────────────────────────────

class PdfReaderArgValidationTests(unittest.TestCase):
    """Tests for pdf_reader argument validation."""

    def setUp(self):
        from app.tools.pdf_reader import pdf_read

        self.pdf_read = pdf_read

    def test_rejects_missing_paths(self):
        result = self.pdf_read({})
        self.assertFalse(result.success)
        self.assertIn("paths", result.error_message)

    def test_rejects_empty_paths_list(self):
        result = self.pdf_read({"paths": []})
        self.assertFalse(result.success)
        self.assertIn("paths", result.error_message)

    def test_rejects_non_http_url(self):
        result = self.pdf_read({"paths": ["ftp://example.com/file.pdf"]})
        self.assertTrue(result.success)
        self.assertEqual(result.output["extracted_documents"], 0)
        self.assertEqual(result.output["failed_documents"], 1)
        self.assertIn("error", result.output["documents"][0])

    def test_rejects_private_ip(self):
        for bad_url in ("http://127.0.0.1/test.pdf", "http://10.0.0.5/test.pdf"):
            result = self.pdf_read({"paths": [bad_url]})
            self.assertTrue(result.success)
            self.assertEqual(result.output["extracted_documents"], 0)
            self.assertIn("error", result.output["documents"][0])

    def test_empty_string_path(self):
        result = self.pdf_read({"paths": ["", "  "]})
        self.assertTrue(result.success)
        self.assertEqual(result.output["extracted_documents"], 0)
        self.assertEqual(result.output["failed_documents"], 2)


class PdfReaderExtractionTests(unittest.TestCase):
    """Tests for actual PDF extraction."""

    def setUp(self):
        from app.tools.pdf_reader import _extract_pdf

        self._extract_pdf = _extract_pdf

    def test_extracts_text_from_valid_pdf(self):
        pdf_bytes = _make_minimal_pdf(pages=2)
        result = self._extract_pdf(pdf_bytes, max_pages=10, max_chars=50000, ocr_enabled=False)
        self.assertIsNone(result.get("error"))
        self.assertEqual(result["total_pages"], 2)
        self.assertEqual(result["extracted_pages"], 2)
        self.assertFalse(result["integrity"]["damaged"])
        self.assertFalse(result["integrity"]["truncated"])
        self.assertEqual(result["content_basis"], "full_text")

    def test_page_numbers_are_correct(self):
        pdf_bytes = _make_minimal_pdf(pages=3)
        result = self._extract_pdf(pdf_bytes, max_pages=10, max_chars=50000, ocr_enabled=False)
        self.assertEqual(len(result["pages"]), 3)
        page_numbers = [p["page_number"] for p in result["pages"]]
        self.assertEqual(page_numbers, [1, 2, 3])

    def test_truncated_at_max_pages(self):
        pdf_bytes = _make_minimal_pdf(pages=5)
        result = self._extract_pdf(pdf_bytes, max_pages=2, max_chars=50000, ocr_enabled=False)
        self.assertEqual(len(result["pages"]), 2)
        self.assertTrue(result["integrity"]["truncated"])
        self.assertEqual(result["content_basis"], "partial")

    def test_damaged_pdf_detected(self):
        damaged = _make_damaged_pdf()
        result = self._extract_pdf(damaged, max_pages=10, max_chars=50000, ocr_enabled=False)
        self.assertTrue(result["integrity"]["damaged"])
        self.assertIsNotNone(result.get("error"))
        self.assertEqual(result["extracted_pages"], 0)
        self.assertEqual(result["content_basis"], "snippet_only")

    def test_non_pdf_bytes_rejected(self):
        non_pdf = _make_non_pdf_bytes()
        result = self._extract_pdf(non_pdf, max_pages=10, max_chars=50000, ocr_enabled=False)
        # PyMuPDF may still try to open — but it should report damage
        self.assertTrue(result["integrity"].get("damaged", False) or result.get("error"))

    def test_empty_pdf_pages_handled(self):
        """Single-page PDF with minimal content."""
        pdf_bytes = _make_minimal_pdf(pages=1)
        result = self._extract_pdf(pdf_bytes, max_pages=10, max_chars=50000, ocr_enabled=False)
        self.assertEqual(result["total_pages"], 1)
        self.assertEqual(len(result["pages"]), 1)
        self.assertIsNone(result.get("error"))

    def test_extraction_method_recorded(self):
        pdf_bytes = _make_minimal_pdf(pages=2)
        result = self._extract_pdf(pdf_bytes, max_pages=10, max_chars=50000, ocr_enabled=False)
        for page in result["pages"]:
            self.assertIn(page.get("extraction_method"), ("native", "ocr", "none"))
        self.assertIn(result["extraction_method"], ("native", "ocr", "mixed", "none"))

    def test_respects_max_chars(self):
        pdf_bytes = _make_minimal_pdf(pages=5)
        result = self._extract_pdf(pdf_bytes, max_pages=10, max_chars=30, ocr_enabled=False)
        # Should only read a small amount of text across pages
        total_chars = sum(p.get("char_count", 0) for p in result["pages"])
        self.assertLessEqual(total_chars, 30)

    def test_integrity_fields_present(self):
        pdf_bytes = _make_minimal_pdf(pages=2)
        result = self._extract_pdf(pdf_bytes, max_pages=10, max_chars=50000, ocr_enabled=False)
        integrity = result["integrity"]
        for key in ("damaged", "truncated", "declared_pages", "actual_pages", "traversed_pages"):
            self.assertIn(key, integrity, f"integrity missing key: {key}")
        self.assertEqual(integrity["actual_pages"], 2)
        self.assertEqual(integrity["traversed_pages"], 2)

    def test_metadata_extracted(self):
        pdf_bytes = _make_minimal_pdf(pages=1)
        result = self._extract_pdf(pdf_bytes, max_pages=10, max_chars=50000, ocr_enabled=False)
        self.assertIsInstance(result.get("metadata"), dict)

    def test_content_basis_marked(self):
        pdf_bytes = _make_minimal_pdf(pages=2)
        result = self._extract_pdf(pdf_bytes, max_pages=10, max_chars=50000, ocr_enabled=False)
        self.assertIn(result["content_basis"], ("full_text", "partial", "snippet_only"))

    def test_text_density_computed(self):
        pdf_bytes = _make_minimal_pdf(pages=2)
        result = self._extract_pdf(pdf_bytes, max_pages=10, max_chars=50000, ocr_enabled=False)
        for page in result["pages"]:
            self.assertIn("text_density", page)
            self.assertIsInstance(page["text_density"], float)


class PdfReaderLocatorTests(unittest.TestCase):
    """Tests for PDF page locator integration with evidence normalizers."""

    def test_passage_locator_pdf_kind(self):
        from app.evidence.normalizers import passage_locator
        from app.agent.evidence import EvidenceItem

        item = EvidenceItem(
            evidence_id="pdf-ev-001",
            run_id="run-001",
            trace_id="trace-001",
            step_no=1,
            tool_name="pdf_reader",
            source_type="pdf",
            source_ref="https://arxiv.org/pdf/2301.00001.pdf",
            title="Test Paper",
            snippet="PDF content snippet",
            status="extracted",
            confidence="high",
            metadata={
                "page_number": 3,
                "document_title": "Test Paper",
                "extraction_method": "native",
                "char_offset": 150,
            },
        )
        locator = passage_locator(item, {})
        self.assertEqual(locator["kind"], "pdf")
        self.assertEqual(locator["page_number"], 3)
        self.assertEqual(locator["document_title"], "Test Paper")
        self.assertEqual(locator["extraction_method"], "native")
        self.assertIn("pdf_path", locator)

    def test_passage_locator_pdf_source_type(self):
        """pdf source_type triggers PDF locator even without explicit tool_name."""
        from app.evidence.normalizers import passage_locator
        from app.agent.evidence import EvidenceItem

        item = EvidenceItem(
            evidence_id="pdf-ev-002",
            run_id="run-002",
            trace_id="trace-002",
            step_no=2,
            tool_name="",
            source_type="pdf",
            source_ref="/workspace/docs/paper.pdf",
            title="Paper",
            snippet="PDF text",
            status="extracted",
            confidence="medium",
            metadata={"page_number": 1},
        )
        locator = passage_locator(item, {})
        self.assertEqual(locator["kind"], "pdf")
        self.assertEqual(locator["page_number"], 1)


class PdfReaderSafetyTests(unittest.TestCase):
    """Tests for PDF reader safety boundaries."""

    def setUp(self):
        from app.tools.pdf_reader import _validate_url, _check_pdf_magic

        self._validate_url = _validate_url
        self._check_pdf_magic = _check_pdf_magic

    def test_validate_url_rejects_non_http(self):
        self.assertIsNone(self._validate_url("ftp://example.com/file.pdf"))
        self.assertIsNone(self._validate_url("file:///etc/passwd"))

    def test_validate_url_rejects_private_ips(self):
        for bad in ("http://127.0.0.1/admin", "http://10.1.2.3/test",
                    "http://192.168.1.1/test", "http://[::1]/test"):
            self.assertIsNone(self._validate_url(bad), f"Should reject {bad}")

    def test_validate_url_accepts_public(self):
        with patch("app.tools.ssrf.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            self.assertIsNotNone(self._validate_url("https://example.com/file.pdf"))
            self.assertIsNotNone(self._validate_url("https://arxiv.org/pdf/2301.00001.pdf"))

    def test_check_pdf_magic_valid(self):
        self.assertTrue(self._check_pdf_magic(b"%PDF-1.4\n..."))
        self.assertTrue(self._check_pdf_magic(b"%PDF-2.0\x00\x00..."))

    def test_check_pdf_magic_invalid(self):
        self.assertFalse(self._check_pdf_magic(b"Not a PDF"))
        self.assertFalse(self._check_pdf_magic(b"<html>"))
        self.assertFalse(self._check_pdf_magic(b""))
        self.assertFalse(self._check_pdf_magic(b"%PD"))  # too short

    def test_local_path_outside_allowed_roots(self):
        from app.tools.pdf_reader import _resolve_local_path

        path, err = _resolve_local_path("C:/Windows/System32/test.pdf")
        self.assertIsNone(path)
        self.assertIsNotNone(err)
        self.assertFalse(err.success)
        self.assertEqual(err.metadata.get("error_type"), "safety_rejected")


class PdfReaderIntegrationTests(unittest.TestCase):
    """End-to-end tests for the pdf_read function with valid PDF bytes."""

    def setUp(self):
        from app.tools.pdf_reader import pdf_read

        self.pdf_read = pdf_read

    @patch("app.tools.pdf_reader._download_pdf")
    def test_url_mode_extracts_pdf(self, mock_download):
        pdf_bytes = _make_minimal_pdf(pages=2)
        mock_download.return_value = (pdf_bytes, None)

        result = self.pdf_read({"paths": ["https://example.com/test.pdf"]})
        self.assertTrue(result.success)
        self.assertEqual(result.output["extracted_documents"], 1)
        self.assertEqual(result.output["failed_documents"], 0)
        doc = result.output["documents"][0]
        self.assertIsNone(doc.get("error"))
        self.assertGreater(doc["total_pages"], 0)

    @patch("app.tools.pdf_reader._download_pdf")
    def test_url_mode_download_failure(self, mock_download):
        from app.tools.base import ToolResult

        mock_download.return_value = (
            None,
            ToolResult(
                success=False,
                error_message="HTTP 404",
                metadata={"error_type": "http_error", "tool_name": "pdf_reader"},
            ),
        )

        result = self.pdf_read({"paths": ["https://example.com/missing.pdf"]})
        self.assertTrue(result.success)  # outer always succeeds
        self.assertEqual(result.output["extracted_documents"], 0)
        self.assertEqual(result.output["failed_documents"], 1)
        self.assertIn("HTTP 404", result.output["documents"][0]["error"])

    def test_local_mode_file_not_found(self):
        result = self.pdf_read({"paths": ["workspace/docs/nonexistent.pdf"]})
        self.assertTrue(result.success)
        self.assertEqual(result.output["extracted_documents"], 0)
        self.assertEqual(result.output["failed_documents"], 1)

    def test_mixed_valid_and_invalid_paths(self):
        import httpx
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200,
            request=request, content=_make_minimal_pdf(pages=1), headers={"content-type": "application/pdf"})))
        with patch("app.tools.pdf_reader.httpx.Client", return_value=client), patch(
            "app.tools.ssrf.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]
        ):
            result = self.pdf_read({"paths": [
                "https://example.com/valid.pdf",
                "http://127.0.0.1/bad.pdf",
                "",
            ]})
        self.assertTrue(result.success)
        self.assertEqual(result.output["total_documents"], 3)
        # At least the private IP and empty string should fail
        self.assertEqual(result.output["failed_documents"], 2)
        self.assertEqual(result.output["extracted_documents"], 1)


if __name__ == "__main__":
    unittest.main()
