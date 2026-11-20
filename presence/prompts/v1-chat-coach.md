# v1 — the chat coach

**Status:** current. Not changed by this task.
**Constant:** `PROMPT_CHAT_COACH` in `presence/app.py`.
**Handler:** `_handle_llm`.
**Route:** `POST /api/llm` — every question the student types into the coach.

## 1. Verbatim prompt

```text
You are PRESENCE AI, a pacing coach for Arab school students presenting in English. Answer in 2-4 short sentences. Only use the numbers given to you. Never invent a measurement: if a number is missing, say it was not measured. If the student attaches a file, you may quote or summarise it, but never claim to have measured it — a file is text, not a recording.
```

## 2. Function it serves

`_handle_llm` — the free-text coaching chat. The student asks anything
("why do I pause so much?"); the answer appears as a chat bubble labelled with
the model id that produced it, so a reader can tell a model answer from a
rule-based one.

## 3. How it is built at call time

The prompt is the system message. The user message is, in order:

| Part | Source | Bound |
|---|---|---|
| `Measured session data: {json}` | `report["tools"]` from the last `/api/session` call | 2000 chars |
| `ATTACHED FILE: {name}` blocks | text files attached in the chat | 12000 chars total |
| `Question: {question}` | what the student typed | 2000 chars |

Request settings: `temperature 0.4`, `max_tokens 300`, `model` from
`llm_config.json`. With no session yet, `report` is `null` and the measured
block is absent — which is what the "if a number is missing" clause covers.

## 4. Why it is worded that way

| Clause | The requirement it satisfies |
|---|---|
| *"a pacing coach for Arab school students presenting in English"* | Scope lock from WAI-134. Not a general tutor, a therapist, or an essay helper. Naming the population stops the model drifting into generic public-speaking advice written for native speakers. |
| *"Answer in 2-4 short sentences."* | The bubble sits above the record button on a phone; long answers push it off-screen. |
| *"Only use the numbers given to you."* + *"Never invent a measurement"* | The **code measures, the LLM coaches** rule. A convincing false number destroys trust in every other number on the page. |
| *"if a number is missing, say it was not measured"* | Forces an honest empty state, rather than filling silence with a made-up baseline. |
| *"a file is text, not a recording"* | The student attaches a script; the model would otherwise praise "delivery" it never heard. |

## 5. What motivated it

1. **The chat looked "rule based and dumb" whatever key was pasted** — the
   browser never called `/api/llm`, so every message fell through to offline
   rules. The prompt was fine; nothing sent it. Fixed by wiring the call.
2. **The advisor invented pace figures with no session measured.** Asking
   "how was my pace?" before recording produced a wpm number. The "never
   invent a measurement" sentence was added, together with the `report`
   payload so the model has real numbers instead of guesses.

## Known weaknesses in v1 (candidates for v4 — not fixed)

- It never asks a follow-up question; the rubric's *"ask before advising"* tip
  is not addressed.
- It does not adapt to the three user profiles differently.
- Arabic input is accepted but the prompt never says which language to answer
  in.
