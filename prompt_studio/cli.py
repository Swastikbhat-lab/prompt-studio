"""Command-line surface for Prompt Studio: gate, build, models.

The CLI is the bridge the agent skill calls. It prints JSON by default so a
caller can parse the gate decision instead of guessing from prose.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # imported as a script by a skill
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from .decisions import PromptGate
from .provider import OllamaProvider, ProviderError
from .workflow import PromptWorkflow


def _gate(args: argparse.Namespace) -> dict:
    if not args.request.strip():
        raise ValueError("--request is required.")
    decision = PromptGate().evaluate(
        args.request,
        context=args.context,
        answers=[{"question": "cli", "answer": "yes"}] if args.answered else [],
        draft_now=args.draft_now,
        rounds=args.rounds,
    )
    return {"decision": decision.to_dict(), "network_used": False}


def _build(args: argparse.Namespace) -> dict:
    if not args.request.strip():
        raise ValueError("--request is required.")
    provider = OllamaProvider()
    workflow = PromptWorkflow(provider)
    model = args.model
    if not model:
        try:
            installed = provider.list_models()
        except ProviderError:
            installed = []
        model = installed[0].name if installed else ""
    decision = workflow.gate.evaluate(
        args.request, context=args.context, draft_now=args.draft_now, rounds=args.rounds
    )
    needs_writer = args.rewrite or decision.route != "package"
    if needs_writer and not model:
        raise SystemExit(
            "No local Ollama model is available. Install one with `ollama pull gemma2:9b`, "
            "or run the gate and deterministic compiler without a writer."
        )
    result = workflow.create_prompt(
        args.request,
        [],
        model,
        draft_now=args.draft_now,
        context=args.context,
        rounds=args.rounds,
        strategy=args.strategy,
        force_rewrite=args.rewrite,
    )
    return {
        "prompt": result.draft.prompt,
        "summary": result.draft.summary,
        "assumptions": result.draft.assumptions,
        "questions": [item.question for item in result.draft.questions],
        "engine": result.engine,
        "model": result.model,
        "elapsed_seconds": round(result.elapsed_seconds, 2),
        "decision": result.decision.to_dict() if result.decision else None,
        "network_used": result.network_used,
    }


def _models(_args: argparse.Namespace) -> dict:
    try:
        models = OllamaProvider().list_models()
    except ProviderError as exc:
        return {"models": [], "error": str(exc)}
    return {"models": [{"name": model.name, "size_bytes": model.size_bytes} for model in models]}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prompt-studio",
        description="Local prompt compiler with a Jev System-1 gate.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--request", "-r", required=True, help="The task in plain words.")
        target.add_argument("--context", "-c", default="", help="Optional task-only context.")
        target.add_argument("--draft-now", action="store_true", help="Skip questions; state assumptions.")
        target.add_argument("--rounds", type=int, default=0, choices=(0, 1, 2))

    gate = sub.add_parser("gate", help="Run only the decision gate (no model call).")
    add_common(gate)
    gate.add_argument("--answered", action="store_true", help="Treat the request as already clarified.")
    gate.set_defaults(handler=_gate)

    build = sub.add_parser("build", help="Gate, then package or write the prompt.")
    add_common(build)
    build.add_argument("--model", default="", help="Installed Ollama model; defaults to the first one.")
    build.add_argument(
        "--strategy", default="brief",
        choices=("original", "brief", "coding", "research", "writing", "decision"),
    )
    build.add_argument("--rewrite", action="store_true", help="Force the local writer.")
    build.set_defaults(handler=_build)

    models = sub.add_parser("models", help="List installed local Ollama models.")
    models.set_defaults(handler=_models)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = args.handler(args)
    except (ValueError, ProviderError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
