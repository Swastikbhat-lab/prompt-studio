"""Protocol and tool tests for the MCP server (provider mocked)."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prompt_studio import mcp
from prompt_studio.models import ModelInfo, PromptDraft
from prompt_studio.provider import Generation

DETAILED = (
    "Build a three-day vegetarian meal plan for one person, $80 total, microwave and kettle only, "
    "as a markdown table with a shopping list and a verification checklist."
)


class FakeProvider:
    def __init__(self, *_args, **_kwargs):
        self.calls = 0

    def list_models(self):
        return [ModelInfo("fake:1b", 1234)]

    def generate(self, request, answers, model, draft_now=False, context="", max_questions=3, strategy="brief", constraints=None):
        FakeProvider.last_calls += 1
        return Generation(PromptDraft("ok", ["stated assumption"], [], "## Task\n" + request), 0.1, model)

    last_calls = 0


def call(method, params=None, request_id=1):
    return mcp.handle_message({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})


class TestProtocol(unittest.TestCase):
    def test_initialize_echoes_a_known_version(self):
        for version in ("2024-11-05", "2025-06-18", "2026-07-28"):
            with self.subTest(version=version):
                response = call("initialize", {"protocolVersion": version})
                self.assertEqual(response["result"]["protocolVersion"], version)
                self.assertEqual(response["result"]["serverInfo"]["name"], "prompt-studio")
                self.assertIn("tools", response["result"]["capabilities"])

    def test_initialize_falls_back_on_unknown_version(self):
        response = call("initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")

    def test_notifications_get_no_response(self):
        self.assertIsNone(call("notifications/initialized"))
        self.assertIsNone(mcp.handle_message({"jsonrpc": "2.0", "method": "notifications/cancelled"}))

    def test_ping_and_tools_list(self):
        self.assertEqual(call("ping")["result"], {})
        tools = call("tools/list")["result"]["tools"]
        self.assertEqual({tool["name"] for tool in tools}, {"prompt_gate", "prompt_build", "prompt_models"})
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
        required = {tool["name"]: tool["inputSchema"].get("required", []) for tool in tools}
        self.assertEqual(required["prompt_gate"], ["request"])
        self.assertEqual(required["prompt_build"], ["request"])
        self.assertEqual(required["prompt_models"], [])

    def test_unknown_method_and_bad_envelope(self):
        self.assertEqual(call("tools/unknown")["error"]["code"], -32601)
        self.assertEqual(mcp.handle_message({"id": 1})["error"]["code"], -32600)
        self.assertEqual(mcp.handle_message("nope")["error"]["code"], -32600)

    def test_gate_tool_returns_the_decision(self):
        response = call("tools/call", {"name": "prompt_gate", "arguments": {"request": DETAILED}})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(payload["decision"]["route"], "package")
        self.assertLess(payload["decision"]["latency_ms"], 50)
        self.assertEqual(payload["decision"]["constraints"][0]["value"], "$80")

    def test_gate_rejects_missing_request(self):
        response = call("tools/call", {"name": "prompt_gate", "arguments": {}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("required", response["result"]["content"][0]["text"])

    def test_unknown_tool_is_a_tool_error(self):
        response = call("tools/call", {"name": "nope", "arguments": {}})
        self.assertTrue(response["result"]["isError"])

    def test_build_packages_without_a_model(self):
        with patch.object(mcp, "OllamaProvider", FakeProvider):
            response = call("tools/call", {"name": "prompt_build", "arguments": {"request": DETAILED}})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["engine"], "deterministic")
        self.assertIn("$80", payload["prompt"])

    def test_build_forced_rewrite_uses_the_writer(self):
        FakeProvider.last_calls = 0
        with patch.object(mcp, "OllamaProvider", FakeProvider):
            response = call("tools/call", {
                "name": "prompt_build",
                "arguments": {"request": DETAILED, "force_rewrite": True, "model": "fake:1b"},
            })
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["engine"], "ollama")
        self.assertEqual(FakeProvider.last_calls, 1)

    def test_build_without_writer_or_model_reports_an_error(self):
        class NoModels(FakeProvider):
            def list_models(self):
                return []

        args = {"request": "help me with something", "force_rewrite": True}
        with patch.object(mcp, "OllamaProvider", NoModels):
            response = call("tools/call", {"name": "prompt_build", "arguments": args})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("ollama pull", response["result"]["content"][0]["text"])

    def test_models_tool(self):
        with patch.object(mcp, "OllamaProvider", FakeProvider):
            response = call("tools/call", {"name": "prompt_models", "arguments": {}})
        self.assertEqual(json.loads(response["result"]["content"][0]["text"])["models"][0]["name"], "fake:1b")


class TestHttpTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), mcp.MCPHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def post(self, payload, token=None, path="/mcp"):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(self.base + path, data=json.dumps(payload).encode(), method="POST", headers=headers)
        return urlopen(request, timeout=5)

    def test_tools_list_over_http(self):
        with self.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}) as response:
            self.assertEqual(json.load(response)["result"]["tools"][0]["name"], "prompt_gate")

    def test_notification_returns_accepted(self):
        with self.post({"jsonrpc": "2.0", "method": "notifications/initialized"}) as response:
            self.assertEqual(response.status, 202)
            self.assertEqual(response.read(), b"")

    def test_batch_request(self):
        batch = [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        with self.post(batch) as response:
            payload = json.load(response)
        self.assertEqual([item["id"] for item in payload], [1, 2])

    def test_health_and_wrong_methods(self):
        with urlopen(self.base + "/health", timeout=5) as response:
            self.assertEqual(json.load(response)["status"], "ok")
        with self.assertRaises(HTTPError) as wrong:
            urlopen(self.base + "/mcp", timeout=5)
        self.assertEqual(wrong.exception.code, 405)
        with self.assertRaises(HTTPError) as missing:
            self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, path="/nope")
        self.assertEqual(missing.exception.code, 404)

    def test_bearer_token_is_enforced_when_configured(self):
        with patch.dict("os.environ", {"PROMPT_STUDIO_MCP_TOKEN": "secret-token"}):
            with self.assertRaises(HTTPError) as denied:
                self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"})
            self.assertEqual(denied.exception.code, 401)
            with self.assertRaises(HTTPError) as wrong:
                self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="wrong")
            self.assertEqual(wrong.exception.code, 401)
            with self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="secret-token") as allowed:
                self.assertEqual(allowed.status, 200)


class TestStdioLoop(unittest.TestCase):
    def test_newline_delimited_messages(self):
        import io
        messages = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            "{not json",
        ]) + "\n"
        with patch.object(mcp.sys, "stdin", io.StringIO(messages)), patch.object(mcp.sys, "stdout", io.StringIO()) as out:
            mcp.serve_stdio()
            lines = [json.loads(line) for line in out.getvalue().strip().splitlines()]
        self.assertEqual([item["id"] for item in lines], [1, 2, None])
        self.assertEqual(lines[2]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
