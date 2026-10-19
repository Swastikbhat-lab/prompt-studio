"""Packaging tests: the skill must work when copied away from this repo."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "prompt-studio"
DETAILED = (
    "Build a three-day vegetarian meal plan for one person, $80 total, microwave and kettle only, "
    "as a markdown table with a shopping list and a verification checklist."
)


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    block = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    assert block, f"{path} has no frontmatter"
    fields = {}
    for line in block.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


class TestSkillFiles(unittest.TestCase):
    def test_frontmatter_respects_claude_ai_limits(self):
        fields = frontmatter(SKILL / "SKILL.md")
        self.assertEqual(fields["name"], SKILL.name)
        self.assertLessEqual(len(fields["name"]), 64)
        self.assertLessEqual(len(fields["description"]), 200)
        self.assertGreater(len(fields["description"]), 40)
        self.assertIn("Jev", fields["description"])

    def test_manifests_are_valid_and_consistent(self):
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["name"], "prompt-studio")
        self.assertEqual(market["owner"]["name"], "Swastik Bhat")
        entry = market["plugins"][0]
        self.assertEqual(entry["name"], plugin["name"])
        self.assertEqual(entry["source"], "./")
        self.assertTrue((ROOT / "skills" / "prompt-studio" / "SKILL.md").is_file())

    def test_mcp_config_points_at_the_launcher(self):
        config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        server = config["mcpServers"]["prompt-studio"]
        self.assertTrue(server["args"][0].endswith("mcp_server.py"))
        self.assertTrue((ROOT / "mcp_server.py").is_file())

    def test_skill_does_not_claim_quality_results(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8").lower()
        for phrase in ("guaranteed better", "improves every prompt", "state of the art"):
            self.assertNotIn(phrase, text)


class TestPackagedSkill(unittest.TestCase):
    def test_zip_is_self_contained_and_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "skill.zip"
            built = subprocess.run(
                [sys.executable, "scripts/package_skill.py", "--out", str(out)],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            with zipfile.ZipFile(out) as archive:
                names = set(archive.namelist())
                self.assertIn("prompt-studio/SKILL.md", names)
                self.assertIn("prompt-studio/scripts/run.py", names)
                self.assertIn("prompt-studio/scripts/prompt_studio/cli.py", names)
                self.assertIn("prompt-studio/references/gate.md", names)
                self.assertNotIn("prompt-studio/scripts/prompt_studio/__pycache__/", names)
                archive.extractall(tmp)

            run = Path(tmp) / "prompt-studio" / "scripts" / "run.py"
            completed = subprocess.run(
                [sys.executable, str(run), "gate", "--request", DETAILED],
                cwd=tmp, capture_output=True, text=True, encoding="utf-8", timeout=60,
                env={"PATH": "", "SYSTEMROOT": "C:\\Windows"} if sys.platform == "win32" else {"PATH": ""},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["decision"]["route"], "package")


if __name__ == "__main__":
    unittest.main()
