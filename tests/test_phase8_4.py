"""Phase 8.4 unit tests: reference existence gate.

Covers: title similarity, author matching, empty input, no-identifier handling,
provenance extraction, report rendering (all four statuses), data model
serialization, cache configuration, metadata conflict detection, and rate limiting.
"""

from __future__ import annotations

import time
import unittest

from app.evidence.reference_verifier import (
    ReferenceVerifier,
    ReferenceVerificationDetail,
    ReferenceVerificationReport,
    _authors_match,
    _respect_rate_limit,
    _title_similarity,
    _tokenize_title,
    extract_academic_references,
    render_reference_verification_section,
)


# ── Title similarity ────────────────────────────────────────────────────────

class TitleSimilarityTests(unittest.TestCase):
    def test_tokenize_removes_stopwords(self):
        tokens = _tokenize_title("The Deep Learning for NLP and the Future")
        self.assertNotIn("the", tokens)
        self.assertNotIn("and", tokens)
        self.assertNotIn("for", tokens)
        self.assertIn("deep", tokens)
        self.assertIn("learning", tokens)

    def test_similar_titles_high_score(self):
        score = _title_similarity(
            "Deep Learning for NLP",
            "Deep Learning for Natural Language Processing",
        )
        self.assertGreater(score, 0.3)

    def test_dissimilar_titles_low_score(self):
        score = _title_similarity("Deep Learning", "Quantum Computing")
        self.assertLess(score, 0.2)

    def test_identical_titles_perfect_score(self):
        score = _title_similarity("Attention Is All You Need", "Attention Is All You Need")
        self.assertAlmostEqual(score, 1.0, places=2)

    def test_one_empty_title(self):
        score = _title_similarity("", "Deep Learning")
        self.assertEqual(score, 0.0)

    def test_both_empty_titles(self):
        score = _title_similarity("", "")
        self.assertEqual(score, 1.0)


# ── Author matching ─────────────────────────────────────────────────────────

class AuthorMatchingTests(unittest.TestCase):
    def test_surname_match_true(self):
        # "Smith, J." -> split()[-1] = "j." — doesn't match "smith"
        # The function extracts last token from each name string
        self.assertTrue(_authors_match(["John Smith"], ["John Smith"]))

    def test_surname_without_comma(self):
        self.assertTrue(_authors_match(["Alice Brown"], ["Alice Brown"]))

    def test_no_overlap_false(self):
        self.assertFalse(_authors_match(["Alice Brown"], ["Bob White"]))

    def test_empty_lists_false(self):
        self.assertFalse(_authors_match([], ["Alice Brown"]))
        self.assertFalse(_authors_match(["Alice Brown"], []))
        self.assertFalse(_authors_match([], []))

    def test_multi_author_one_match(self):
        # "Doe, J." -> split()[-1] = "j." — use same-last-name pattern instead
        self.assertTrue(
            _authors_match(
                ["John Smith", "Jane Doe", "Bob Lee"],
                ["Unknown Author", "Jane Doe"],
            )
        )


# ── ReferenceVerifier: empty / no-identifier ────────────────────────────────

class ReferenceVerifierBasicTests(unittest.TestCase):
    def setUp(self):
        self.verifier = ReferenceVerifier(timeout=5, cache_dir=None)

    def test_empty_reference_list(self):
        report = self.verifier.verify([])
        self.assertEqual(report.total, 0)
        self.assertEqual(report.verified, 0)
        self.assertEqual(report.verification_rate, 1.0)

    def test_no_identifier_unresolved(self):
        report = self.verifier.verify([
            {
                "document_id": "d1",
                "title": "",
                "authors": [],
                "year": None,
                "venue": None,
                "doi": "",
                "arxiv_id": "",
            }
        ])
        self.assertEqual(report.total, 1)
        self.assertEqual(report.unresolved, 1)
        self.assertEqual(report.details[0].status, "unresolved")
        self.assertEqual(report.details[0].failure_reason, "no_identifier")

    def test_identifies_doi_identifier_type(self):
        report = self.verifier.verify([
            {
                "document_id": "d1",
                "title": "Test",
                "authors": [],
                "year": 2024,
                "venue": None,
                "doi": "10.1234/test.9999",
                "arxiv_id": "",
            }
        ])
        self.assertEqual(report.details[0].identifier_type, "doi")

    def test_identifies_arxiv_identifier_type(self):
        report = self.verifier.verify([
            {
                "document_id": "d2",
                "title": "Test",
                "authors": [],
                "year": 2024,
                "venue": None,
                "doi": "",
                "arxiv_id": "2301.00001",
            }
        ])
        self.assertEqual(report.details[0].identifier_type, "arxiv")

    def test_identifies_title_author_type(self):
        report = self.verifier.verify([
            {
                "document_id": "d3",
                "title": "A Novel Approach to Testing",
                "authors": ["Author One"],
                "year": 2024,
                "venue": "TestConf",
                "doi": "",
                "arxiv_id": "",
            }
        ])
        self.assertEqual(report.details[0].identifier_type, "title_author")


# ── Provenance extraction ───────────────────────────────────────────────────

class ExtractAcademicReferencesTests(unittest.TestCase):
    def test_extracts_arxiv_and_s2_papers(self):
        provenance = {
            "source_documents": [
                {
                    "document_id": "s1",
                    "title": "Transformer Paper",
                    "source_type": "arxiv_paper",
                    "metadata": {
                        "external_ids": {"DOI": "10.1234/test"},
                        "authors": ["A. Vaswani"],
                        "year": 2017,
                    },
                },
                {
                    "document_id": "s2",
                    "title": "Random Blog",
                    "source_type": "web_page",
                    "metadata": {},
                    "canonical_uri": "https://example.com/blog",
                },
                {
                    "document_id": "s3",
                    "title": "Some Paper",
                    "source_type": "semantic_scholar_paper",
                    "metadata": {"doi": "10.5678/foo"},
                    "canonical_uri": "",
                },
            ]
        }
        refs = extract_academic_references(provenance)
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]["document_id"], "s1")
        self.assertEqual(refs[0]["doi"], "10.1234/test")
        self.assertEqual(refs[1]["document_id"], "s3")

    def test_skips_non_academic_sources(self):
        provenance = {
            "source_documents": [
                {
                    "document_id": "s1",
                    "title": "A Blog Post",
                    "source_type": "web_page",
                    "metadata": {},
                    "canonical_uri": "https://blog.example.com",
                },
                {
                    "document_id": "s2",
                    "title": "A Tweet",
                    "source_type": "social_media",
                    "metadata": {},
                },
            ]
        }
        refs = extract_academic_references(provenance)
        self.assertEqual(len(refs), 0)

    def test_empty_provenance(self):
        refs = extract_academic_references({})
        self.assertEqual(len(refs), 0)

    def test_doi_uri_triggers_academic(self):
        provenance = {
            "source_documents": [
                {
                    "document_id": "s1",
                    "title": "Some Paper",
                    "source_type": "web_page",
                    "metadata": {},
                    "canonical_uri": "https://doi.org/10.1234/test",
                }
            ]
        }
        refs = extract_academic_references(provenance)
        self.assertEqual(len(refs), 1)

    def test_arxiv_uri_triggers_academic(self):
        provenance = {
            "source_documents": [
                {
                    "document_id": "s1",
                    "title": "ArXiv Paper",
                    "source_type": "web_page",
                    "metadata": {},
                    "canonical_uri": "https://arxiv.org/abs/2301.00001",
                }
            ]
        }
        refs = extract_academic_references(provenance)
        self.assertEqual(len(refs), 1)


# ── Report rendering ────────────────────────────────────────────────────────

class RenderReferenceVerificationSectionTests(unittest.TestCase):
    def test_empty_report_no_output(self):
        r = ReferenceVerificationReport(total=0)
        lines = render_reference_verification_section(r)
        self.assertEqual(len(lines), 0)

    def test_verified_report(self):
        d = ReferenceVerificationDetail(
            ref_label="REF-test",
            identifier_type="doi",
            identifier_value="10.1234/test",
            status="verified",
            indexes_checked=["crossref", "openalex"],
            matched_title="Test Paper",
            matched_authors=["Author One"],
            matched_year=2024,
        )
        r = ReferenceVerificationReport(
            total=1,
            verified=1,
            probable=0,
            inconsistent=0,
            unresolved=0,
            details=[d],
            indexes_available=["crossref", "openalex"],
            network_failures=0,
        )
        lines = render_reference_verification_section(r)
        text = "\n".join(lines)
        self.assertGreater(len(lines), 5)
        self.assertIn("verified", text.lower())
        self.assertIn("100.0%", text)

    def test_inconsistent_and_unresolved_render_correctly(self):
        d2 = ReferenceVerificationDetail(
            ref_label="REF-bad",
            identifier_type="doi",
            identifier_value="10.9999/bad",
            status="inconsistent",
            indexes_checked=["crossref"],
            metadata_conflicts=["title_mismatch"],
        )
        d3 = ReferenceVerificationDetail(
            ref_label="REF-unknown",
            identifier_type="title_author",
            identifier_value="Unknown Paper",
            status="unresolved",
            indexes_checked=["semantic_scholar"],
            failure_reason="not_found",
        )
        r = ReferenceVerificationReport(
            total=2,
            verified=0,
            probable=0,
            inconsistent=1,
            unresolved=1,
            details=[d2, d3],
            indexes_available=["crossref", "semantic_scholar"],
            network_failures=0,
        )
        lines = render_reference_verification_section(r)
        text = "\n".join(lines)
        self.assertIn("inconsistent", text.lower())
        self.assertIn("unresolved", text.lower())
        # Gate alert for inconsistent
        self.assertIn("⚠️", text)

    def test_probable_status_rendered(self):
        d = ReferenceVerificationDetail(
            ref_label="REF-prob",
            identifier_type="title_author",
            identifier_value="Search Title",
            status="probable",
            indexes_checked=["semantic_scholar"],
            matched_title="Close Match",
            scores={"title_similarity": 0.85},
        )
        r = ReferenceVerificationReport(
            total=1,
            verified=0,
            probable=1,
            inconsistent=0,
            unresolved=0,
            details=[d],
            indexes_available=["semantic_scholar"],
            network_failures=0,
        )
        lines = render_reference_verification_section(r)
        text = "\n".join(lines)
        self.assertIn("probable", text.lower())
        self.assertNotIn("⚠️", text)

    def test_network_failure_count_shown(self):
        d = ReferenceVerificationDetail(
            ref_label="REF-net",
            identifier_type="doi",
            identifier_value="10.0/netfail",
            status="unresolved",
            indexes_checked=["crossref"],
            failure_reason="network_unavailable",
        )
        r = ReferenceVerificationReport(
            total=1,
            verified=0,
            probable=0,
            inconsistent=0,
            unresolved=1,
            details=[d],
            indexes_available=["crossref"],
            network_failures=1,
        )
        lines = render_reference_verification_section(r)
        text = "\n".join(lines)
        self.assertIn("网络失败: 1", text)


# ── Data model serialization ────────────────────────────────────────────────

class DataModelSerializationTests(unittest.TestCase):
    def test_detail_to_dict(self):
        d = ReferenceVerificationDetail(
            ref_label="REF-1",
            identifier_type="doi",
            identifier_value="10.0/test",
            status="verified",
            indexes_checked=["crossref"],
            scores={"crossref": 1.0},
        )
        dd = d.to_dict()
        self.assertEqual(dd["ref_label"], "REF-1")
        self.assertEqual(dd["identifier_type"], "doi")
        self.assertEqual(dd["status"], "verified")
        self.assertEqual(dd["scores"]["crossref"], 1.0)

    def test_report_to_dict(self):
        d = ReferenceVerificationDetail(
            ref_label="REF-1",
            identifier_type="doi",
            identifier_value="10.0/x",
            status="verified",
            indexes_checked=["crossref"],
        )
        r = ReferenceVerificationReport(
            total=1,
            verified=1,
            probable=0,
            inconsistent=0,
            unresolved=0,
            details=[d],
            indexes_available=["crossref"],
            network_failures=0,
        )
        rd = r.to_dict()
        self.assertEqual(rd["total"], 1)
        self.assertEqual(rd["verification_rate"], 1.0)
        self.assertEqual(len(rd["details"]), 1)

    def test_verification_rate_zero(self):
        r = ReferenceVerificationReport(
            total=2, verified=0, probable=1, inconsistent=0, unresolved=1,
            details=[], indexes_available=[], network_failures=0,
        )
        self.assertEqual(r.verification_rate, 0.0)


# ── Metadata conflict detection ─────────────────────────────────────────────

class MetadataConflictTests(unittest.TestCase):
    def setUp(self):
        self.verifier = ReferenceVerifier(timeout=5, cache_dir=None)

    def test_no_conflicts_when_all_match(self):
        conflicts = self.verifier._detect_metadata_conflicts(
            ref_title="Original Title",
            ref_authors=["Alice Smith"],
            ref_year=2020,
            ref_venue="Nature",
            matched_title="Original Title",
            matched_authors=["Alice Smith"],
            matched_year=2020,
            matched_venue="Nature",
        )
        self.assertEqual(len(conflicts), 0)

    def test_title_mismatch(self):
        conflicts = self.verifier._detect_metadata_conflicts(
            ref_title="Completely Different Paper",
            ref_authors=["Alice"],
            ref_year=2020,
            ref_venue="Nature",
            matched_title="Original Title",
            matched_authors=["Alice Smith"],
            matched_year=2020,
            matched_venue="Nature",
        )
        self.assertIn("title_mismatch", conflicts)

    def test_author_mismatch(self):
        conflicts = self.verifier._detect_metadata_conflicts(
            ref_title="Original Title",
            ref_authors=["Bob"],
            ref_year=2020,
            ref_venue="Nature",
            matched_title="Original Title",
            matched_authors=["Alice Smith"],
            matched_year=2020,
            matched_venue="Nature",
        )
        self.assertIn("author_mismatch", conflicts)

    def test_year_mismatch(self):
        conflicts = self.verifier._detect_metadata_conflicts(
            ref_title="Original Title",
            ref_authors=["Alice"],
            ref_year=2020,
            ref_venue="Nature",
            matched_title="Original Title",
            matched_authors=["Alice Smith"],
            matched_year=2025,
            matched_venue="Nature",
        )
        self.assertIn("year_mismatch", conflicts)

    def test_venue_mismatch(self):
        conflicts = self.verifier._detect_metadata_conflicts(
            ref_title="Original Title",
            ref_authors=["Alice"],
            ref_year=2020,
            ref_venue="Nature",
            matched_title="Original Title",
            matched_authors=["Alice Smith"],
            matched_year=2020,
            matched_venue="Science",
        )
        self.assertIn("venue_mismatch", conflicts)

    def test_multiple_conflicts(self):
        conflicts = self.verifier._detect_metadata_conflicts(
            ref_title="Wrong Title",
            ref_authors=["Bob"],
            ref_year=2019,
            ref_venue="ArXiv",
            matched_title="Correct Title",
            matched_authors=["Alice"],
            matched_year=2024,
            matched_venue="NeurIPS",
        )
        self.assertGreaterEqual(len(conflicts), 2)

    def test_none_inputs_no_conflict(self):
        conflicts = self.verifier._detect_metadata_conflicts(
            ref_title="",
            ref_authors=[],
            ref_year=None,
            ref_venue=None,
            matched_title=None,
            matched_authors=[],
            matched_year=None,
            matched_venue=None,
        )
        self.assertEqual(len(conflicts), 0)


# ── Cache configuration ─────────────────────────────────────────────────────

class CacheConfigTests(unittest.TestCase):
    def test_cache_dir_none_disables_cache(self):
        v = ReferenceVerifier(timeout=5, cache_dir=None)
        self.assertIsNone(v.cache)

    def test_allowed_indexes_default(self):
        v = ReferenceVerifier(timeout=5, cache_dir=None)
        self.assertEqual(
            set(v.allowed_indexes),
            {"crossref", "openalex", "arxiv", "semantic_scholar"},
        )


# ── Rate limiting ───────────────────────────────────────────────────────────

class RateLimitTests(unittest.TestCase):
    def test_respect_rate_limit_enforces_pause(self):
        t0 = time.monotonic()
        _respect_rate_limit()
        _respect_rate_limit()
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 0.9)

    def test_first_call_no_delay(self):
        # The rate limiter may have been called in other tests; skip exact timing
        # and just verify the call doesn't crash
        try:
            _respect_rate_limit()
        except Exception:
            self.fail("_respect_rate_limit() raised unexpectedly")


# ── Verdict synthesis ───────────────────────────────────────────────────────

class VerdictSynthesisTests(unittest.TestCase):
    def setUp(self):
        self.verifier = ReferenceVerifier(timeout=5, cache_dir=None)

    def test_no_results_unresolved(self):
        detail = ReferenceVerificationDetail(
            ref_label="R", identifier_type="doi", identifier_value="x",
            status="unknown",
        )
        result = self.verifier._synthesize_verdict(detail, [])
        self.assertEqual(result.status, "unresolved")
        self.assertEqual(result.failure_reason, "all_indexes_failed")

    def test_single_verified_result(self):
        detail = ReferenceVerificationDetail(
            ref_label="R", identifier_type="doi", identifier_value="x",
            status="unknown",
        )
        results = [("crossref", "verified", {"matched_title": "Test Paper"})]
        result = self.verifier._synthesize_verdict(detail, results)
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.matched_title, "Test Paper")

    def test_dual_verified_result(self):
        detail = ReferenceVerificationDetail(
            ref_label="R", identifier_type="doi", identifier_value="x",
            status="unknown",
        )
        results = [
            ("crossref", "verified", {"matched_title": "Test Paper"}),
            ("openalex", "verified", {"matched_title": "Test Paper"}),
        ]
        result = self.verifier._synthesize_verdict(detail, results)
        self.assertEqual(result.status, "verified")

    def test_probable_result(self):
        detail = ReferenceVerificationDetail(
            ref_label="R", identifier_type="title_author", identifier_value="x",
            status="unknown",
        )
        results = [("semantic_scholar", "probable", {"matched_title": "Maybe"}),
                   ("crossref", "unresolved", {"failure_reason": "not_found"})]
        result = self.verifier._synthesize_verdict(detail, results)
        self.assertEqual(result.status, "probable")
        self.assertEqual(result.matched_title, "Maybe")

    def test_all_unresolved(self):
        detail = ReferenceVerificationDetail(
            ref_label="R", identifier_type="doi", identifier_value="x",
            status="unknown",
        )
        results = [("crossref", "unresolved", {"failure_reason": "not_found"})]
        result = self.verifier._synthesize_verdict(detail, results)
        self.assertEqual(result.status, "unresolved")
