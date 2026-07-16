"""Offline contract tests for safety-critical helper logic."""

from __future__ import annotations

import unittest

from app.config import Settings
from app.mcp.schemas import MCPJsonRpcRequest, MCPTraceOptions, MCPToolCallRequest
from app.rag.chunker import chunk_documents
from app.rag.embedding_backends import DeterministicEmbeddingBackend, create_embedding_backend
from app.tools.sql_safety import validate_read_only_sql


class SqlSafetyTests(unittest.TestCase):
    def test_select_and_with_are_allowed(self) -> None:
        for query in (
            "SELECT id, title FROM documents LIMIT 3",
            "WITH rows AS (SELECT id FROM documents) SELECT id FROM rows",
        ):
            allowed, reason, metadata = validate_read_only_sql(query)
            self.assertTrue(allowed)
            self.assertIsNone(reason)
            self.assertTrue(metadata["read_only"])

    def test_write_and_multiple_statements_are_rejected(self) -> None:
        for query in (
            "DELETE FROM documents",
            "SELECT 1; SELECT 2",
            "PRAGMA database_list",
        ):
            allowed, reason, metadata = validate_read_only_sql(query)
            self.assertFalse(allowed)
            self.assertIsNotNone(reason)
            self.assertIn(metadata["error_type"], {"invalid_sql", "safety_rejected"})


class RagChunkerTests(unittest.TestCase):
    def test_chunk_metadata_preserves_source_and_offsets(self) -> None:
        chunks = chunk_documents(
            [{"source": "demo.md", "text": "abcdefghijklmnopqrstuvwxyz"}],
            chunk_size=10,
            chunk_overlap=2,
        )

        self.assertEqual(chunks[0]["chunk_id"], "demo.md#0")
        self.assertEqual(chunks[0]["metadata"]["start"], 0)
        self.assertEqual(chunks[1]["metadata"]["start"], 8)
        self.assertTrue(all(chunk["source"] == "demo.md" for chunk in chunks))

    def test_invalid_overlap_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            chunk_documents(
                [{"source": "demo.md", "text": "abc"}],
                chunk_size=10,
                chunk_overlap=10,
            )


class McpSchemaTests(unittest.TestCase):
    def test_json_rpc_request_defaults_to_version_2(self) -> None:
        request = MCPJsonRpcRequest(method="tools/list")
        self.assertEqual(request.jsonrpc, "2.0")
        self.assertEqual(request.method, "tools/list")

    def test_tool_call_accepts_trace_options(self) -> None:
        request = MCPToolCallRequest(
            name="trace_reader",
            arguments={"run_id": "run-1"},
            trace=MCPTraceOptions(run_id="run-1", step_no=2),
        )
        self.assertEqual(request.trace.step_no, 2)


class SettingsContractTests(unittest.TestCase):
    def test_real_embedding_without_enable_flag_falls_back_to_deterministic(self) -> None:
        backend = create_embedding_backend(
            Settings(
                rag_embedding_backend="sentence_transformers",
                rag_real_backend_enabled=False,
            )
        )
        self.assertIsInstance(backend, DeterministicEmbeddingBackend)


if __name__ == "__main__":
    unittest.main()
