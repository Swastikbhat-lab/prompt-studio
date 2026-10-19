"""Jev System-1 primitives: Choice, Score and Noul.

The decision layer follows Jev's contract shape: typed perception in, typed
decision out, no generation. It runs entirely on this machine.

What this is not: the numbers below are normalized decision scores produced by
deterministic feature counting, not calibrated probabilities from a trained
model. No latency or accuracy claim about a hosted System-1 service is made
here, and this module never opens a socket.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Mapping, Sequence

MAX_OPTIONS = 16
MAX_FIELDS = 16
MAX_VALUE_CHARS = 48
_WORD = re.compile(r"[a-z0-9][a-z0-9'+-]*")
_MONEY = re.compile(r"[$€£]\s?\d[\d,.]*")
_VALUE_AFTER_CUE = r"\s+([A-Za-z0-9$€£.,:/-]+(?:\s+[A-Za-z0-9$€£.,:/-]+){0,2})"
_TRAILING_STOPWORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}


@dataclass(frozen=True)
class ChoiceResult:
    """One option selected from a closed set, with the full score distribution."""

    value: str
    probabilities: dict[str, float]
    margin: float
    latency_ms: float
    backend: str = "embedded"


@dataclass(frozen=True)
class ScoreResult:
    """A bounded 0..1 reading for one named metric."""

    metric: str
    value: float
    signals: dict[str, float]
    latency_ms: float
    backend: str = "embedded"


@dataclass(frozen=True)
class FieldReading:
    """Presence reading for one typed field, plus the text that triggered it."""

    field: str
    probability: float
    value: str = ""
    cues: tuple[str, ...] = ()


@dataclass(frozen=True)
class NoulResult:
    """Typed field extraction; every field is independent and bounded."""

    readings: dict[str, FieldReading]
    latency_ms: float
    backend: str = "embedded"


def _clamp01(value: float) -> float:
    if value != value:  # NaN
        return 0.0
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


class EmbeddedEngine:
    """Deterministic, dependency-free implementation of the three primitives.

    ``choice`` takes per-option evidence computed by the caller and returns a
    softmax distribution, so the decision stays inspectable rather than hidden
    inside a black box. ``score`` clamps a supplied reading. ``noul`` reports
    per-field presence from explicit cue phrases.
    """

    backend = "embedded"

    def choice(self, options: Sequence[str], evidence: Mapping[str, float], temperature: float = 0.25) -> ChoiceResult:
        started = time.perf_counter()
        cleaned = tuple(dict.fromkeys(str(option) for option in options))
        if not cleaned:
            raise ValueError("Choice needs at least one option.")
        if len(cleaned) > MAX_OPTIONS:
            raise ValueError(f"Choice is limited to {MAX_OPTIONS} options.")
        if not temperature > 0:
            raise ValueError("Choice temperature must be positive.")
        raw = {option: float(evidence.get(option, 0.0)) for option in cleaned}
        if any(value != value or value in (float("inf"), float("-inf")) for value in raw.values()):
            raise ValueError("Choice evidence must be finite numbers.")
        peak = max(raw.values())
        weights = {option: math.exp((value - peak) / temperature) for option, value in raw.items()}
        total = sum(weights.values())
        probabilities = {option: round(weight / total, 6) for option, weight in weights.items()}
        ranked = sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
        margin = round(ranked[0][1] - (ranked[1][1] if len(ranked) > 1 else 0.0), 6)
        return ChoiceResult(ranked[0][0], probabilities, margin, _elapsed_ms(started), self.backend)

    def score(self, metric: str, value: float, signals: Mapping[str, float] | None = None) -> ScoreResult:
        started = time.perf_counter()
        if not metric.strip():
            raise ValueError("Score needs a metric name.")
        return ScoreResult(
            metric,
            round(_clamp01(float(value)), 6),
            {str(key): round(_clamp01(float(val)), 6) for key, val in (signals or {}).items()},
            _elapsed_ms(started),
            self.backend,
        )

    def noul(self, schema: Mapping[str, Sequence[str]], text: str) -> NoulResult:
        started = time.perf_counter()
        if len(schema) > MAX_FIELDS:
            raise ValueError(f"Noul is limited to {MAX_FIELDS} fields.")
        lowered = text.lower()
        readings: dict[str, FieldReading] = {}
        for name, cues in schema.items():
            hits = tuple(cue for cue in cues if cue.lower() in lowered)
            probability = round(1.0 - math.exp(-float(len(hits))), 6)
            value = _compact_value(text, hits) if hits else ""
            readings[str(name)] = FieldReading(str(name), probability, value, hits)
        return NoulResult(readings, _elapsed_ms(started), self.backend)


def _compact_value(text: str, hits: Sequence[str]) -> str:
    """Return a short evidence span for a field, not the whole sentence."""

    for cue in hits:
        if cue in ("$", "€", "£"):
            money = _MONEY.search(text)
            if money:
                return money.group(0)[:MAX_VALUE_CHARS]
        match = re.search(re.escape(cue) + _VALUE_AFTER_CUE, text, re.IGNORECASE)
        if match:
            words = (cue.strip() + " " + match.group(1)).split()
            while len(words) > 1 and words[-1].lower() in _TRAILING_STOPWORDS:
                words.pop()
            return " ".join(words).rstrip(".,;:")[:MAX_VALUE_CHARS]
    return hits[0].strip()[:MAX_VALUE_CHARS] if hits else ""


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 4)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, used by the gate's transparent feature counters."""

    return _WORD.findall(text.lower())
