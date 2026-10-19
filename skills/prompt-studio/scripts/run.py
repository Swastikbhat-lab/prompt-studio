#!/usr/bin/env python3
"""Skill entrypoint: locate the Prompt Studio engine and forward to its CLI.

Resolution order:
1. ``PROMPT_STUDIO_HOME`` (explicit override)
2. The same directory as this script (self-contained packaged skill)
3. The repository root, when running from a checkout
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
CANDIDATES = [
    Path(os.environ["PROMPT_STUDIO_HOME"]).expanduser() if os.environ.get("PROMPT_STUDIO_HOME") else None,
    HERE.parent,
    HERE.parents[3] if len(HERE.parents) > 3 else None,
]
CANDIDATES = [path for path in CANDIDATES if path is not None]

for candidate in CANDIDATES:
    if (candidate / "prompt_studio" / "__init__.py").is_file():
        sys.path.insert(0, str(candidate))
        break
else:
    print(json.dumps({
        "error": (
            "Could not find the prompt_studio package. Set PROMPT_STUDIO_HOME to the "
            "prompt-studio repository, or reinstall the skill with scripts/install_skill.py."
        )
    }))
    raise SystemExit(2)

from prompt_studio.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
