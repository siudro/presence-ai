# PRESENCE AI — system prompts, saved verbatim

Every prompt the app sends to the model is recorded here, word for word, with the
function it serves and the route that calls it. This is the evidence for the
WAICY *Iterative Development* row (25 points).

## Why this folder exists

The first prompt lived only inside a chat thread. A prompt that is not saved
somewhere the student controls disappears, and with it the record the rubric
asks about.

## The rule

An LLM does not run without a prompt. If a new function in the app calls a
model, its prompt belongs here before it ships — not after.

## The other rule, which every prompt states

> **Code measures. The LLM coaches.**

Numbers — wpm, fillers, pauses, freezes, volume — are computed in `app.py` and
handed to the model inside the input. The model may interpret them, never
produce one that was not measured.

## Files

| File | Function it serves | Route | Status |
| --- | --- | --- | --- |
| `v1-advisor.md` | `_handle_llm` — the single coach reply | `POST /api/llm` | current, unchanged |
| `v2-post-speech-review.md` | `_handle_review` — review after a speech | `POST /api/review` | new (WAI-229 R2) |
| `v3-script-from-file.md` | `_handle_script` — script from an uploaded file | `POST /api/script` | new (WAI-229 R3) |

Prompts live in `app.py` as `PROMPT_CHAT_COACH`, `PROMPT_POST_SPEECH_REVIEW`
and `PROMPT_SCRIPT_FROM_FILE`. Tests assert each saved copy matches the code.
