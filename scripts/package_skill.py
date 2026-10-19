"""Build a self-contained skill zip for claude.ai upload.

    python scripts/package_skill.py [--out dist/prompt-studio-skill.zip]

The zip contains one top-level folder named after the skill, as the claude.ai
upload flow expects, with the engine copied in so the skill runs without this
repository being present.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "prompt-studio"
PACKAGE = ROOT / "prompt_studio"
NAME_LIMIT = 64
DESCRIPTION_LIMIT = 200


def read_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if not match:
        raise SystemExit(f"{path} has no YAML frontmatter block.")
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip("\"'")
    return fields


def validate() -> dict[str, str]:
    fields = read_frontmatter(SKILL / "SKILL.md")
    name = fields.get("name", "")
    description = fields.get("description", "")
    if name != SKILL.name:
        raise SystemExit(f"Skill name '{name}' must match its folder '{SKILL.name}'.")
    if len(name) > NAME_LIMIT:
        raise SystemExit(f"Skill name exceeds {NAME_LIMIT} characters.")
    if not description or len(description) > DESCRIPTION_LIMIT:
        raise SystemExit(
            f"Description must be 1..{DESCRIPTION_LIMIT} characters "
            f"(currently {len(description)}); claude.ai enforces this limit."
        )
    missing = [
        str(path.relative_to(ROOT))
        for path in (SKILL / "scripts" / "run.py", PACKAGE / "__init__.py", PACKAGE / "cli.py")
        if not path.is_file()
    ]
    if missing:
        raise SystemExit("Missing required files: " + ", ".join(missing))
    return fields


def build(out: Path) -> Path:
    fields = validate()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / SKILL.name
        shutil.copytree(SKILL, staging)
        shutil.copytree(PACKAGE, staging / "scripts" / "prompt_studio", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(staging.parent).as_posix())
    print(f"{out} ({out.stat().st_size / 1024:.0f} KB)")
    print(f"name: {fields['name']}")
    print(f"description: {len(fields['description'])} chars")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package the prompt-studio skill for claude.ai.")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "prompt-studio-skill.zip")
    args = parser.parse_args(argv)
    build(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
