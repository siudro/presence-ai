# PRESENCE AI — prompt archive

Every system prompt this app sends to a language model, saved verbatim, with
the function that sends it, why it is worded that way, and what failure
produced the next version.

This folder is documentation, not code. The live strings are in
`presence/app.py` as the constants `PROMPT_CHAT_COACH`,
`PROMPT_POST_SPEECH_REVIEW` and `PROMPT_SCRIPT_FROM_FILE`. Each file below
quotes its prompt **verbatim** — if the file and the code disagree, the code
is right and this file is stale. That is guarded: `test_features.py` contains
`TestPromptArchive`, which asserts each constant appears word-for-word in its
file, so the documentation cannot drift away from what is actually sent.

## Why the archive exists

The WAICY LLM rubric places 25 of 100 points on **Iterative Development**.
A prompt that changed five times and nobody wrote down why scores like a
prompt that never changed.

## The rule every prompt here obeys

> **Code measures. The LLM coaches.**

Numbers — wpm, filler counts, pause counts, freezes, volume — are computed in
`app.py` and handed to the model inside the input. The model may interpret
them. It may never produce one that was not measured. Every prompt in this
folder states that rule, because when it was only true by accident the advisor
invented wpm figures that no measurement produced.

## Index

| Version | File | Function it serves | Route |
|---|---|---|---|
| v1 (current, unchanged) | `v1-chat-coach.md` | `_handle_llm` — every chat question | `POST /api/llm` |
| v2 | `v2-post-speech-review.md` | `_handle_review` — review after a speech | `POST /api/review` |
| v3 | `v3-script-from-file.md` | `_handle_script` — script prepared from an uploaded file | `POST /api/script` |

## What was NOT changed, and why that matters

`v1` is the prompt that was already live. It is preserved verbatim rather than
improved, so the archive starts from a real baseline instead of a tidy
recollection. v2 and v3 are **new functions with new prompts**, not edits to
v1 — the chat coach still behaves exactly as it did before.

## How to read a prompt file

Each file has the same five sections:

1. **Verbatim prompt** — copy-pasteable, nothing summarised.
2. **Function it serves** — the handler and the HTTP route.
3. **How it is built** — what the code appends to it at call time and in what
   order.
4. **Why it is worded that way** — the requirement each clause satisfies.
5. **What motivated it** — the failure that produced this version, and what
   was true before it existed.
