"""Typed application state shared by the prompt workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Clarification:
    question: str
    reason: str = ""


@dataclass(frozen=True)
class PromptDraft:
    summary: str
    assumptions: list[str] = field(default_factory=list)
    questions: list[Clarification] = field(default_factory=list)
    prompt: str = ""

    @classmethod
    def from_value(cls, value: Any) -> "PromptDraft":
        if not isinstance(value, dict):
            raise ValueError("The local model response must be a JSON object.")
        summary = value.get("summary", "")
        prompt = value.get("prompt", "")
        assumptions = value.get("assumptions", [])
        raw_questions = value.get("questions", [])
        if not isinstance(summary, str) or not isinstance(prompt, str):
            raise ValueError("The model response must contain string summary and prompt fields.")
        if not isinstance(assumptions, list) or not all(isinstance(x, str) for x in assumptions):
            raise ValueError("The model response assumptions field must be a list of strings.")
        if not isinstance(raw_questions, list):
            raise ValueError("The model response questions field must be a list.")
        questions: list[Clarification] = []
        for item in raw_questions[:3]:
            if not isinstance(item, dict) or not isinstance(item.get("question"), str):
                raise ValueError("Each clarification must contain a question string.")
            reason = item.get("reason", "")
            questions.append(Clarification(item["question"].strip(), reason.strip() if isinstance(reason, str) else ""))
        if not prompt.strip():
            raise ValueError("The local model returned an empty prompt.")
        return cls(summary.strip(), [x.strip() for x in assumptions if x.strip()], questions, prompt.strip())


@dataclass(frozen=True)
class ModelInfo:
    name: str
    size_bytes: int | None = None


@dataclass
class WorkflowState:
    request: str = ""
    draft: PromptDraft | None = None
    answers: list[str] = field(default_factory=list)
    error: str = ""


@dataclass(frozen=True)
class WorkflowResult:
    draft: PromptDraft
    model: str
    elapsed_seconds: float
    answered_questions: int = 0
    network_used: bool = False
    engine: str = "ollama"
    decision: Any = None
