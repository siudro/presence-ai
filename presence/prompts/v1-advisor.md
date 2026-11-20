# v1 — the coach reply

**Status:** current (as of `74ef350`)
**Function:** `POST /api/llm` → the handler's local variable `system`
**File:** `presence/app.py`, around line 1721
**Model at time of writing:** `gpt-4o-mini`
**Recorded:** 20 November 2026

---

## The prompt, verbatim

> You are PRESENCE AI, a pacing coach for Arab school students presenting in English. Answer in 2-4 short sentences. Only use the numbers given to you. Never invent a measurement: if a number is missing, say it was not measured. If the student attaches a file, you may quote or summarise it, but never claim to have measured it — a file is text, not a recording.

Nothing is added to it before the model is called. The measured session data and
the question are both sent as the **user** message, not appended to this text:

```
Measured session data: {"wpm": 156.0, "fillers": 3, ...}

<attachment text, if any>

Question: <what the student typed>
```

---

## Sentence by sentence, and why it is worded that way

| Phrase | Why it is there |
| --- | --- |
| *pacing coach for Arab school students presenting in English* | The scope decided in Task 1. Without it the model drifts into general public-speaking advice, which widens the project past its locked scope. |
| *Answer in 2-4 short sentences* | A student reading mid-practice will not read a paragraph. Length is a usability constraint, not a style preference. |
| *Only use the numbers given to you* | The core design rule: **code measures, the LLM coaches.** Numbers come from the measurement layer. |
| *Never invent a measurement: if a number is missing, say it was not measured* | Needed because a model with no data will still produce a number. This is the honesty guard, and there are tests on the tool maths behind it. |
| *If the student attaches a file ... never claim to have measured it* | An attached file is text. Saying "your file shows you spoke 140 wpm" would be a fabricated measurement of something never recorded. |

---

## What this version does not do

Recorded honestly, because these are the gaps the next version is judged against:

- **No review of a completed speech.** It answers a question. It never reads a
  finished session's metrics and writes a performance review.
- **No script generation.** It cannot build a speech from an uploaded file.
- **No per-user variation.** The three user profiles (Task 3) are documented but
  the prompt does not adapt its advice to which one is speaking.
- **No scoring of a read script.** There is no script reading, so nothing to score.

---

## Test evidence for this version

WAI-138 recorded nine exchanges across the three profiles (Task 6). The failures
found there are the reason the next version exists. Those failures are listed in
the change log for v2, not here — this file records the prompt as shipped.

---

## Why the next version is needed

Dana's requirement for the follow-up task names three things this prompt cannot
do: review a finished speech from its metrics, prepare a script from an uploaded
file, and evaluate a student reading a script aloud. Each is a separate function
with its own prompt, because one prompt doing all four jobs produces vague
answers to every one of them.

When those functions exist, each gets its own file here — `v2-review.md`,
`v2-script.md`, `v2-read-aloud.md` — recording its prompt, its function, and the
v1 failure that motivated it.
