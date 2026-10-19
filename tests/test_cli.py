"""End-to-end tests for the CLI (no model required)."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DETAILED = (
    "Build a three-day vegetarian meal plan for one person, $80 total, microwave and kettle only, "
    "as a markdown table with a shopping list and a verification checklist."
)


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "prompt_studio.cli", *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


class TestCli(unittest.TestCase):
    def test_gate_prints_json(self):
        completed = run("gate", "--request", DETAILED)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"]["route"], "package")
        self.assertFalse(payload["network_used"])
        self.assertIn("budget", {item["field"] for item in payload["decision"]["constraints"]})

    def test_build_packages_without_a_model(self):
        completed = run("build", "--request", DETAILED)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["engine"], "deterministic")
        self.assertIn("$80", payload["prompt"])
        self.assertIn("## Response guidance", payload["prompt"])
        self.assertTrue(payload["prompt"].startswith(DETAILED))

    def test_gate_rejects_an_empty_request(self):
        completed = run("gate", "--request", "   ")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("--request is required", json.loads(completed.stdout)["error"])

    def test_unknown_strategy_is_refused_by_the_parser(self):
        completed = run("build", "--request", DETAILED, "--strategy", "nope")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("invalid choice", completed.stderr)


if __name__ == "__main__":
    unittest.main()
