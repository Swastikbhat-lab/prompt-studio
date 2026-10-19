"""The browser compiler and the gate's Python compiler must emit identical text."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prompt_studio.packaging import compile_prompt

COMPILER = ROOT / "web" / "compiler.js"
CASES = [
    {"request": "Build a three-day meal plan.", "context": "", "strategy": "brief", "target": "portable"},
    {
        "request": "Summarise this incident for the on-call channel.",
        "context": "Disk filled at 02:14 on node-7. Page fired at 02:16.",
        "strategy": "writing",
        "target": "portable",
    },
    {"request": "Keep my words exactly.", "context": "note", "strategy": "original", "target": "portable"},
    {"request": "Compare three hosting options.", "context": "prices here", "strategy": "decision", "target": "reasoning"},
    {"request": "Explain backpressure.", "context": "café latency 12 ms", "strategy": "research", "target": "reasoning"},
]


@unittest.skipUnless(shutil.which("node"), "node is required for the parity check")
class TestCompilerParity(unittest.TestCase):
    def test_python_and_javascript_outputs_match(self):
        for case in CASES:
            with self.subTest(request=case["request"]):
                completed = subprocess.run(
                    ["node", "-e",
                     "const c=require(process.argv[1]);const i=JSON.parse(process.argv[2]);"
                     "process.stdout.write(JSON.stringify(c.compile(i)));",
                     str(COMPILER), json.dumps(case)],
                    capture_output=True, text=True, encoding="utf-8", check=True, timeout=20,
                )
                from_js = json.loads(completed.stdout)
                from_py = compile_prompt(case["request"], case["context"], case["strategy"], case["target"])
                self.assertEqual(from_js, from_py)


if __name__ == "__main__":
    unittest.main()
