"""Offline contract tests for safety-critical helper logic."""

from __future__ import annotations

import unittest

from app.mcp.schemas import MCPJsonRpcRequest, MCPTraceOptions, MCPToolCallRequest
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


if __name__ == "__main__":
    unittest.main()
