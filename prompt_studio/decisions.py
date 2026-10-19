"""The Jev gate: decide whether a request needs the local writer at all.

Three primitives run before any generation:

* ``Score`` reads request specificity, ambiguity and selected-context bloat.
* ``Choice`` routes the request to ``package`` (deterministic, no model),
  ``clarify`` (model asks a bounded number of questions) or ``draft``
  (model writes now and states its assumptions).
* ``Noul`` reads typed constraints out of the request so the writer preserves
  them instead of inventing new ones.

Every number here is measured on this machine and returned to the caller. The
gate makes no accuracy claim about a hosted System-1 model.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Sequence

from .jev import EmbeddedEngine, FieldReading, tokenize

ACTION_CUES = (
    "build", "write", "fix", "design", "plan", "create", "implement", "refactor",
    "explain", "summarize", "review", "compare", "generate", "draft", "debug",
    "migrate", "analyze", "analyse", "test", "rewrite", "convert", "improve",
)
SCOPE_CUES = (
    "must", "only", "within", "at most", "no more than", "under", "limit",
    "between", "exactly", "without", "budget", "deadline", "before",
)
OUTPUT_CUES = (
    "json", "markdown", "table", "bullet", "csv", "email", "code", "script",
    "essay", "list", "diagram", "schema", "format", "output", "paragraph",
)
AUDIENCE_CUES = ("audience", "tone", "voice", "reader", "for beginners", "for my team", "for a recruiter")
VERIFICATION_CUES = ("verify", "test", "acceptance", "success", "so that", "must pass", "check", "validate")
AMBIGUITY_CUES = (
    "something", "stuff", "things", "a few", "some", "maybe", "kind of",
    "sort of", "better", "nice", "good", "help me", "etc", "whatever",
)
INJECTION_CUES = ("ignore previous", "ignore all previous", "you are now", "system:", "act as")
REPETITION_WINDOW = 40

CONSTRAINT_SCHEMA = {
    "deadline": ("by ", "deadline", "due", "before ", "within ", "this week", "tomorrow"),
    "budget": ("$", "budget", "cost", "price", "under ", "cheap", "free"),
    "platform": ("windows", "macos", "linux", "ios", "android", "web", "browser", "chrome", "python", "node"),
    "format": ("json", "markdown", "table", "csv", "bullet", "email", "essay", "pdf"),
    "audience": ("audience", "for beginners", "for my team", "for a recruiter", "for users", "for students"),
}


@dataclass(frozen=True)
class GateDecision:
    """What the gate decided, why, and how long it took."""

    route: str
    question_budget: int
    specificity: float
    ambiguity: float
    context_bloat: float
    probabilities: dict[str, float]
    constraints: tuple[FieldReading, ...]
    context_flags: tuple[str, ...]
    rationale: tuple[str, ...]
    latency_ms: float
    backend: str = "embedded"

    @property
    def force_draft(self) -> bool:
        return self.route == "draft"

    def to_dict(self) -> dict:
        return {
            "route": self.route,
            "question_budget": self.question_budget,
            "specificity": round(self.specificity, 3),
            "ambiguity": round(self.ambiguity, 3),
            "context_bloat": round(self.context_bloat, 3),
            "probabilities": {key: round(value, 3) for key, value in self.probabilities.items()},
            "constraints": [
                {"field": item.field, "probability": round(item.probability, 3), "value": item.value}
                for item in self.constraints
            ],
            "context_flags": list(self.context_flags),
            "rationale": list(self.rationale),
            "latency_ms": round(self.latency_ms, 3),
            "backend": self.backend,
        }


def _hits(text: str, cues: Sequence[str]) -> int:
    return sum(1 for cue in cues if cue in text)


class PromptGate:
    """Runs the System-1 primitives and returns a typed routing decision."""

    def __init__(self, engine: EmbeddedEngine | None = None):
        self.engine = engine or EmbeddedEngine()

    def evaluate(
        self,
        request: str,
        context: str = "",
        answers: Sequence[dict[str, str]] | None = None,
        draft_now: bool = False,
        rounds: int = 0,
    ) -> GateDecision:
        started = time.perf_counter()
        answers = answers or []
        lowered = request.lower()
        tokens = tokenize(request)
        rationale: list[str] = []

        deliverable = 1.0 if _hits(lowered, ACTION_CUES) else 0.0
        scope = 1.0 if (_hits(lowered, SCOPE_CUES) or any(char.isdigit() for char in request)) else 0.0
        output = 1.0 if _hits(lowered, OUTPUT_CUES) else 0.0
        audience = 1.0 if _hits(lowered, AUDIENCE_CUES) else 0.0
        verification = 1.0 if _hits(lowered, VERIFICATION_CUES) else 0.0
        length = 1.0 if len(tokens) >= 12 else 0.5 if len(tokens) >= 6 else 0.0

        specificity = (
            0.28 * deliverable + 0.20 * scope + 0.20 * output
            + 0.12 * audience + 0.10 * verification + 0.10 * length
        )
        ambiguity_hits = _hits(lowered, AMBIGUITY_CUES)
        ambiguity = min(1.0, 0.45 * (len(tokens) < 6) + 0.18 * min(ambiguity_hits, 3) + 0.25 * (1.0 - deliverable))

        context_tokens = tokenize(context)
        unique = len(set(context_tokens)) or 1
        repetition = 0.0
        if len(context_tokens) > REPETITION_WINDOW:
            repetition = 1.0 - (unique / len(context_tokens))
        bloat = min(1.0, 0.65 * (min(len(context), 8000) / 8000) + 0.35 * repetition)

        specificity_score = self.engine.score("request_specificity", specificity, {
            "deliverable": deliverable, "scope": scope, "output": output,
            "audience": audience, "verification": verification, "length": length,
        })
        ambiguity_score = self.engine.score("request_ambiguity", ambiguity, {"ambiguous_cues": min(ambiguity_hits, 3) / 3})
        bloat_score = self.engine.score("context_bloat", bloat, {"length": min(len(context), 8000) / 8000, "repetition": repetition})

        context_flags: list[str] = []
        if _hits(context.lower(), INJECTION_CUES):
            context_flags.append("instruction-like text in context")
        if repetition > 0.15:
            context_flags.append("repetitive context")
        if len(context) > 6000:
            context_flags.append("large context")
        if bloat_score.value > 0.6:
            context_flags.append("trim context before sending")

        noul = self.engine.noul(CONSTRAINT_SCHEMA, request)
        constraints = tuple(reading for reading in noul.readings.values() if reading.probability >= 0.4)
        if constraints:
            rationale.append("gate read constraints: " + ", ".join(item.field for item in constraints))

        evidence = {
            "package": 2.4 * specificity_score.value - 1.7 * ambiguity_score.value - 1.8 * bloat_score.value
                       + (1.2 if len(tokens) >= 8 else -0.9) + (-3.0 if answers else 0.0),
            "clarify": 1.9 * ambiguity_score.value + 1.4 * (1.0 - specificity_score.value)
                       + (-2.5 if answers else 0.5) + (0.6 if rounds < 2 else -3.0),
            "draft": 1.2 * specificity_score.value + (2.0 if draft_now else 0.0) + (1.3 if answers else 0.0),
        }
        choice = self.engine.choice(("package", "clarify", "draft"), evidence)
        route = choice.value

        # Hard safety rules outrank the score distribution.
        if draft_now:
            route = "draft"
            rationale.append("draft-now was chosen explicitly")
        elif route == "package" and (answers or len(tokens) < 8):
            route = "clarify" if rounds < 2 else "draft"
            rationale.append("package route blocked: request or clarification state is too thin")
        if route == "clarify" and rounds >= 2:
            route = "draft"
            rationale.append("clarification rounds exhausted")
        if route == "package":
            rationale.append("gate selected the package route: deterministic, no model call needed")
        elif route == "clarify":
            rationale.append(f"gate reserved {3} questions for material unknowns")
        else:
            rationale.append("gate chose a single draft with visible assumptions")

        question_budget = 3 if route == "clarify" else 0
        return GateDecision(
            route=route,
            question_budget=question_budget,
            specificity=specificity_score.value,
            ambiguity=ambiguity_score.value,
            context_bloat=bloat_score.value,
            probabilities=choice.probabilities,
            constraints=constraints,
            context_flags=tuple(context_flags),
            rationale=tuple(rationale),
            latency_ms=(time.perf_counter() - started) * 1000.0,
            backend=self.engine.backend,
        )
