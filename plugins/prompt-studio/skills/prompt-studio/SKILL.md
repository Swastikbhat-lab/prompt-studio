---
name: prompt-studio
description: Turn a rough ask into a reviewable prompt without a cloud call. A Jev System-1 gate routes to deterministic packaging, bounded questions, or a draft with assumptions.
license: MIT
---

# Prompt Studio

Compile the user's task into a portable prompt, locally. The gate runs first and
decides how much work is actually needed:

| Route | Meaning | What you do |
|---|---|---|
| `package` | The request is specific enough | Use the returned prompt. No model was called. |
| `clarify` | Material unknowns remain | Ask the user only the returned questions, then rebuild. |
| `draft` | Proceed now | Show the prompt and its visible assumptions. |

Never invent a route, a model, or a timing number. Every value comes from the
command output.

## 1. Gate first (no model, costs nothing)

```bash
python scripts/run.py gate --request "the user's task" --context "optional notes"
```

Read `decision.route`, `decision.specificity`, `decision.ambiguity`,
`decision.context_bloat`, and `decision.constraints`. The command prints JSON and
never touches the network.

## 2. Build the prompt

```bash
python scripts/run.py build --request "the user's task" \
  --strategy brief \
  --context "optional task-only notes"
```

- `--strategy` is one of `original`, `brief`, `coding`, `research`, `writing`,
  `decision`. Default to `original` when the user already writes precisely;
  otherwise `brief`.
- Add `--rewrite` only when the user explicitly wants the local writer, or the
  gate returned `clarify` and the unknowns are now answered.
- The returned `prompt` starts with the user's own words. Do not paraphrase it;
  copy it verbatim into your reply, inside a code block.

If `engine` is `deterministic`, the prompt was assembled locally with no model
call. If it is `ollama`, a local model wrote the suggestion block.

## 3. Clarification policy

If `route` is `clarify`, the output contains `questions` (at most three). Ask the
user those questions — nothing else. If they would rather not answer, rebuild
with `--draft-now` so unresolved unknowns become labeled assumptions.

Never ask a question the gate did not return, and never assume a privacy, safety,
or irreversible-action constraint.

## What the numbers mean

- `specificity` — 0..1, how much of a complete brief is present (deliverable,
  scope, output format, audience, verification, length).
- `ambiguity` — 0..1, vague nouns, hedges, or a very short request.
- `context_bloat` — 0..1, context length plus repetition. If above ~0.6, suggest
  trimming the pasted context before sending it anywhere.
- `latency_ms` — measured gate time on this machine, typically a few
  milliseconds. Quote it only if the user asks about speed.

The gate is deterministic feature scoring, not a trained model. Do not describe
it as an LLM, and do not promise prompt-quality improvements it has not measured.

## Failure modes

- **`No local Ollama model is available`** — the gate and deterministic packaging
  still work. Use `gate`, or `build` on a request that routes to `package`.
- **Ollama is not running** — same as above; tell the user the writer is optional.
- **`error` in the JSON** — report it verbatim and stop. Do not retry with a
  different model or provider.

## Example

Request: "help me make a study app"

1. `run.py gate ...` → `route: clarify`, `question_budget: 3`.
2. Ask the returned questions (platform, learning outcome, offline constraint).
3. `run.py build --request "..." --context "answers: iOS; spaced repetition; offline"`.
4. Return the `prompt` field in a code block, plus any assumptions.

See [references/gate.md](references/gate.md) for the full signal list and the
hard rules that override the score distribution.
