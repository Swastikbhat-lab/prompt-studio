"""Single-run workflow orchestration; browser state remains transient."""

from __future__ import annotations

import time
from dataclasses import replace

from .decisions import GateDecision, PromptGate
from .models import PromptDraft, WorkflowResult
from .packaging import compile_prompt
from .provider import OllamaProvider

PACKAGE_SUMMARY = (
    "Deterministic package: the gate found no material unknown worth a local model "
    "round-trip. Review the visible text before copying it."
)


class PromptWorkflow:
    def __init__(self, provider: OllamaProvider, gate: PromptGate | None = None):
        self.provider = provider
        self.gate = gate or PromptGate()

    def create_prompt(
        self,
        request: str,
        answers: list[dict[str, str]],
        model: str,
        draft_now: bool = False,
        context: str = "",
        rounds: int = 0,
        strategy: str = "brief",
        force_rewrite: bool = False,
    ) -> WorkflowResult:
        if not isinstance(request, str):
            raise ValueError("Request must be text.")
        request = request.strip()
        if not request:
            raise ValueError("Enter a request before generating a prompt.")
        if not isinstance(answers, list):
            raise ValueError("Clarification answers must be a list.")
        if len(answers) > 6:
            raise ValueError("Answer at most six clarifications across two rounds.")
        normalized: list[str] = []
        for answer in answers:
            if not isinstance(answer, dict):
                raise ValueError("Each clarification answer must be an object.")
            question = answer.get("question", "")
            response = answer.get("answer", "")
            if not isinstance(question, str) or not isinstance(response, str):
                raise ValueError("Clarification question and answer must be text.")
            question, response = question.strip(), response.strip()
            if response:
                normalized.append(f"Question: {question}\nAnswer: {response}")
        if not isinstance(context, str) or len(context) > 8000:
            raise ValueError("Selected context must be text of at most 8,000 characters.")
        if not isinstance(rounds, int) or not 0 <= rounds <= 2:
            raise ValueError("Clarification rounds must be an integer between 0 and 2.")
        if strategy != "original" and strategy not in ("brief", "coding", "research", "writing", "decision"):
            raise ValueError("Choose a supported fast pattern.")

        started = time.perf_counter()
        decision: GateDecision = self.gate.evaluate(
            request, context=context, answers=normalized, draft_now=draft_now, rounds=rounds
        )
        if decision.route == "package" and not force_rewrite:
            packaged = compile_prompt(request, context, strategy)
            return WorkflowResult(
                draft=PromptDraft(PACKAGE_SUMMARY, [], [], packaged["prompt"]),
                model="deterministic compiler",
                elapsed_seconds=time.perf_counter() - started,
                answered_questions=len(normalized),
                network_used=False,
                engine="deterministic",
                decision=decision,
            )

        generation = self.provider.generate(
            request,
            normalized,
            model,
            draft_now=draft_now or decision.force_draft,
            context=context,
            max_questions=decision.question_budget,
            strategy=strategy,
            constraints=[f"{item.field}: {item.value}" if item.value else item.field for item in decision.constraints],
        )
        draft = generation.draft
        if decision.question_budget == 0 and draft.questions:
            # The gate closed the question budget; do not surface questions the
            # workflow will not act on.
            draft = replace(draft, questions=[])
        return WorkflowResult(
            draft=draft,
            model=generation.model,
            elapsed_seconds=generation.elapsed_seconds,
            answered_questions=len(normalized),
            network_used=False,
            engine="ollama",
            decision=decision,
        )
