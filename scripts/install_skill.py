"""Install the skill as a self-contained personal Claude Code skill.

    python scripts/install_skill.py            # ~/.claude/skills/prompt-studio
    python scripts/install_skill.py --dest DIR

The installed folder does not depend on this repository: the engine package is
copied into ``scripts/prompt_studio`` so the skill keeps working if the repo
moves. Re-run after changing the engine.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "plugins" / "prompt-studio" / "skills" / "prompt-studio"
PACKAGE = ROOT / "prompt_studio"


def install(dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SKILL, dest)
    shutil.copytree(
        PACKAGE,
        dest / "scripts" / "prompt_studio",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install prompt-studio as a Claude Code skill.")
    parser.add_argument(
        "--dest", type=Path,
        default=Path.home() / ".claude" / "skills" / "prompt-studio",
        help="Destination directory (defaults to ~/.claude/skills/prompt-studio).",
    )
    args = parser.parse_args(argv)
    dest = install(args.dest.expanduser())
    print(f"Installed skill at {dest}")
    print("Claude Code discovers it automatically; start a new session to load it.")
    print(f"Try: python \"{dest / 'scripts' / 'run.py'}\" gate --request \"build a study app\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
