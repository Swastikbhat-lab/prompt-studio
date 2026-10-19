"""Focused tests for the prompt workflow and local HTTP surface; provider is mocked."""

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

from prompt_studio.models import Clarification, ModelInfo, PromptDraft
from prompt_studio.packaging import compile_prompt
from prompt_studio.provider import Generation, OllamaProvider
from prompt_studio.workflow import PromptWorkflow
import server

DETAILED = (
    "Build a three-day vegetarian meal plan for one person, $80 total, microwave and kettle only, "
    "as a markdown table with a shopping list and a verification checklist."
)


class FakeProvider:
    def __init__(self, *_args, **_kwargs):
        self.calls = 0

    def list_models(self):
        return [ModelInfo("test-local:1b", 1234)]

    def generate(self, request, answers, model, draft_now=False, context="", max_questions=3, strategy="brief", constraints=None):
        self.calls += 1
        self.draft_now = draft_now
        self.max_questions = max_questions
        self.request = request
        self.answers = answers
        self.constraints = constraints
        return Generation(
            PromptDraft(
                "Build the requested feature without changing its API.",
                ["Assume existing tests define compatibility unless clarified."],
                [Clarification("What failure do you observe?", "The reproduction affects the regression test.")],
                "## Objective\nFix the reported bug.\n\n## Constraints\nPreserve the public API.\n\n## Verification\nAdd and run a regression test.",
            ),
            0.25,
            model,
        )


class TestProviderAndWorkflow(unittest.TestCase):
    def test_rejects_non_loopback_model_endpoints(self):
        for url in ("https://example.com", "http://192.0.2.1:11434", "http://user@127.0.0.1:11434"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                OllamaProvider(url)

    def test_provider_uses_explicit_localhost_endpoint_by_default(self):
        self.assertEqual(OllamaProvider().base_url, "http://127.0.0.1:11434")

    def test_draft_schema_limits_questions_and_rejects_empty_prompt(self):
        draft = PromptDraft.from_value({
            "summary": "ok", "assumptions": [],
            "questions": [{"question": f"Q{i}"} for i in range(5)],
            "prompt": " Do this ",
        })
        self.assertEqual(len(draft.questions), 3)
        self.assertEqual(draft.prompt, "Do this")
        with self.assertRaises(ValueError):
            PromptDraft.from_value({"summary": "bad", "prompt": " "})

    def test_workflow_passes_only_request_and_explicit_answers(self):
        provider = FakeProvider()
        result = PromptWorkflow(provider).create_prompt(
            "Fix the API bug", [{"question": "Failure?", "answer": "Empty ID raises."}], "test-local:1b"
        )
        self.assertEqual(provider.request, "Fix the API bug")
        self.assertEqual(provider.answers, ["Question: Failure?\nAnswer: Empty ID raises."])
        self.assertTrue(provider.draft_now)
        self.assertEqual(provider.max_questions, 0)
        self.assertEqual(result.decision.route, "draft")
        self.assertEqual(result.model, "test-local:1b")
        self.assertFalse(result.network_used)
        self.assertEqual(result.answered_questions, 1)
        self.assertEqual(result.engine, "ollama")
        self.assertIn("regression test", result.draft.prompt)

    def test_draft_now_policy_reaches_local_model(self):
        provider = FakeProvider()
        result = PromptWorkflow(provider).create_prompt("Proceed with assumptions", [], "test-local:1b", draft_now=True)
        self.assertTrue(provider.draft_now)
        self.assertEqual(result.decision.route, "draft")
        self.assertEqual(provider.max_questions, 0)

    def test_gate_routes_detailed_requests_without_a_model_call(self):
        provider = FakeProvider()
        result = PromptWorkflow(provider).create_prompt(DETAILED, [], "test-local:1b")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(result.engine, "deterministic")
        self.assertEqual(result.decision.route, "package")
        self.assertEqual(result.draft.prompt, compile_prompt(DETAILED)["prompt"])

    def test_force_rewrite_overrides_the_package_route(self):
        provider = FakeProvider()
        result = PromptWorkflow(provider).create_prompt(DETAILED, [], "test-local:1b", force_rewrite=True)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.engine, "ollama")
        self.assertEqual(result.decision.route, "package")

    def test_closed_question_budget_drops_model_questions(self):
        provider = FakeProvider()
        result = PromptWorkflow(provider).create_prompt(DETAILED, [], "test-local:1b", force_rewrite=True)
        self.assertEqual(result.decision.question_budget, 0)
        self.assertEqual(result.draft.questions, [])

    def test_constraint_readings_reach_the_writer_as_evidence(self):
        provider = FakeProvider()
        PromptWorkflow(provider).create_prompt(DETAILED, [], "test-local:1b", force_rewrite=True)
        self.assertTrue(any(item.startswith("budget:") for item in provider.constraints))

    def test_workflow_accepts_answers_from_two_clarification_rounds(self):
        provider = FakeProvider()
        answers = [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(6)]
        result = PromptWorkflow(provider).create_prompt("Do it", answers, "test-local:1b")
        self.assertEqual(result.answered_questions, 6)
        self.assertIn("Question: Q0\nAnswer: A0", provider.answers)
        self.assertIn("Question: Q5\nAnswer: A5", provider.answers)

    def test_workflow_rejects_empty_or_overspecified_requests(self):
        workflow = PromptWorkflow(FakeProvider())
        with self.assertRaises(ValueError):
            workflow.create_prompt(" ", [], "test-local:1b")
        with self.assertRaises(ValueError):
            workflow.create_prompt("Do it", [{"question": str(i), "answer": "x"} for i in range(7)], "test-local:1b")
        with self.assertRaises(ValueError):
            workflow.create_prompt("Do it", [], "test-local:1b", strategy="unknown")


class TestLocalHttpSurface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider_patch = patch.object(server, "OllamaProvider", FakeProvider)
        cls.provider_patch.start()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.PromptHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)
        cls.provider_patch.stop()

    def post(self, path, payload, origin=None):
        request = Request(
            self.base + path, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", "Origin": origin or self.base},
        )
        return urlopen(request, timeout=3)

    def test_serves_ui_and_explicit_local_model_discovery(self):
        with urlopen(self.base + "/", timeout=2) as response:
            html = response.read().decode("utf-8")
        self.assertIn("What do you want to do?", html)
        self.assertIn("No personal sources connected", html)
        with urlopen(self.base + "/api/models", timeout=2) as response:
            self.assertEqual(json.load(response)["models"][0]["name"], "test-local:1b")

    def test_same_origin_generation_returns_editable_export_text(self):
        with self.post("/api/generate", {
            "request": "Fix the API bug", "model": "test-local:1b",
            "answers": [{"question": "Failure?", "answer": "Empty ID raises."}],
        }) as response:
            result = json.load(response)
        self.assertIn("regression test", result["prompt"])
        self.assertEqual(result["answered_questions"], 1)
        self.assertFalse(result["network_used"])
        self.assertEqual(result["decision"]["route"], "draft")
        self.assertIn("latency_ms", result["decision"])

    def test_detailed_request_is_packaged_without_a_model(self):
        with self.post("/api/generate", {"request": DETAILED, "model": "test-local:1b", "answers": []}) as response:
            result = json.load(response)
        self.assertEqual(result["engine"], "deterministic")
        self.assertEqual(result["decision"]["route"], "package")
        self.assertIn("$80", result["prompt"])

    def test_decision_endpoint_runs_the_gate_only(self):
        with self.post("/api/decision", {"request": "help me with something"}) as response:
            result = json.load(response)
        self.assertEqual(result["decision"]["route"], "clarify")
        self.assertFalse(result["network_used"])
        self.assertLess(result["decision"]["latency_ms"], 50)

    def test_cross_origin_request_is_rejected_before_generation(self):
        with self.assertRaises(HTTPError) as error:
            self.post("/api/generate", {"request": "do it", "model": "test-local:1b", "answers": []}, origin="http://192.0.2.1")
        self.assertEqual(error.exception.code, 403)
        self.assertIn("loopback", error.exception.read().decode())

    def test_missing_origin_and_unknown_routes_fail_closed(self):
        request = Request(
            self.base + "/api/generate", data=b'{"request":"do it","model":"test-local:1b","answers":[]}',
            method="POST", headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=3)
        self.assertEqual(error.exception.code, 403)

        with self.assertRaises(HTTPError) as too_many:
            self.post("/api/generate", {
                "request": "do it", "model": "test-local:1b",
                "answers": [{"question": str(i), "answer": "yes"} for i in range(7)],
            })
        self.assertEqual(too_many.exception.code, 400)

        with self.assertRaises(HTTPError) as bad_rounds:
            self.post("/api/generate", {"request": "do it", "model": "test-local:1b", "rounds": 5})
        self.assertEqual(bad_rounds.exception.code, 400)

        with self.post("/api/generate", {
            "request": "do it", "model": "test-local:1b",
            "answers": [{"question": str(i), "answer": "yes"} for i in range(6)],
        }) as response:
            self.assertEqual(json.load(response)["answered_questions"], 6)

        with self.assertRaises(HTTPError) as missing:
            urlopen(self.base + "/api/not-a-route", timeout=3)
        self.assertEqual(missing.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
