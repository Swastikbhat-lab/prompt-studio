# Gate reference

The gate runs three deterministic primitives before any generation. It never
opens a socket.

## Score

| Metric | Inputs | Notes |
|---|---|---|
| `request_specificity` | deliverable verb, scope/number cues, output-format cue, audience cue, verification cue, request length | 0..1, weighted sum |
| `request_ambiguity` | request shorter than 6 tokens, vague nouns and hedges, missing deliverable verb | 0..1, capped |
| `context_bloat` | context length / 8,000, plus token repetition when context exceeds 40 tokens | 0..1 |

Signals are returned alongside each score so a caller can explain the result.

## Choice

Evidence per route (before softmax at temperature 0.25):

```
package = 2.4*specificity - 1.7*ambiguity - 1.8*bloat
          + (1.2 if tokens >= 8 else -0.9)
          - (3.0 if answers were supplied)

clarify = 1.9*ambiguity + 1.4*(1 - specificity)
          + (0.5 if no answers else -2.5)
          + (0.6 if rounds < 2 else -3.0)

draft   = 1.2*specificity
          + (2.0 if draft_now)
          + (1.3 if answers were supplied)
```

## Hard rules (override the distribution)

1. `draft_now` always wins.
2. A supplied answer moves the request out of `package` — answered work is
   finished, not repackaged.
3. `package` is refused for requests under 8 tokens or when answers exist.
4. `clarify` becomes `draft` after two rounds.
5. A closed question budget (`0`) drops any questions the writer returned.

## Noul

Fields: `deadline`, `budget`, `platform`, `format`, `audience`.

Presence probability is `1 - e^(-hits)`, where `hits` is the number of cue
phrases found. Readings at `>= 0.4` are surfaced as constraints, each with a
compact evidence span (for example `$80`, `markdown table`) rather than a whole
sentence. Constraints are evidence for the writer, never new requirements.

## What the gate does not do

- It does not judge prompt quality or downstream model output.
- It is not a trained classifier; probabilities are normalized decision scores.
- It does not call a hosted System-1 service. The embedded backend is local and
  deterministic.
