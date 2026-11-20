# PRESENCE AI — system prompts, saved verbatim

Every prompt the app sends to the model is recorded here, word for word, with the
function it serves. This is the evidence for the WAICY *Iterative Development*
row: a prompt that is not saved cannot be shown to have been designed, tested,
broken, or improved.

## Why this folder exists

The first prompt lived only inside a chat thread. A prompt that is not saved
somewhere the student controls disappears, and with it the record the rubric
asks about.

## The rule

An LLM does not run without a prompt. If a new function in the app calls a
model, its prompt belongs here before it ships — not after.

## Files

| File | Function it serves | Status |
| --- | --- | --- |
| `v1-advisor.md` | `POST /api/llm` — the single coach reply (`app.py`, handler `_llm`, variable `system`) | current |
