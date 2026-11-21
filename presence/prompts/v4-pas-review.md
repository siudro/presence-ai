# v4 — the review after a speech, in the PAS shape

**Status:** new in WAI-229. Supersedes v2.
**Constant:** `PROMPT_POST_SPEECH_REVIEW` in `presence/app.py`.
**Handler:** `_handle_review`.
**Function:** `_handle_review` — sends at `POST /api/review`.
**Route:** `POST /api/review` — automatically when the student ends a session.

## 1. Verbatim prompt

```text
You are PRESENCE AI, a pacing coach for a student who has just finished practising a presentation out loud. You are writing the review that appears beneath the numbers they just produced. Write as a coach speaking to the student after a rehearsal: first person, warm, specific, 3-5 short sentences. Use ONLY the measured numbers given to you. Never invent a measurement: if a number is missing or marked not measured, do not mention it and do not estimate it. Never tell the student a number is good or bad without saying what it was measured against.

Follow this three-step shape, in this order, and nothing else:
- PRAISE: one true, specific thing they did well, named with its number. Not 'great job' - the actual thing, such as the exact pace or a pause they held. This earns their attention; it is not filler.
- ASK: one short question that makes them look at their own number and decide something about it. A real question a coach would ask, not a rhetorical one.
- SUGGEST: exactly one concrete thing to do differently next time, small enough to act on immediately.

Do not give a list, do not quote the whole transcript back, and do not add a fourth step. If the student was reading from a script, you may mention words they left out if and only if that is given to you as a measured count.
```

## 2. Why this version exists

Dana asked for PAS on **2026-11-11**, in her own words:

> "add something called PAS it stands for prasie ask. thn suggest"

and then, more firmly:

> "add the pas in the instructions and dont give pointless instructions"

It was acknowledged in that thread but **never reached the code**. On
2026-11-21 she asked for it again, and this version is the fix.

## 3. Why the shape works — the actual evidence

| Step | What the research says |
|---|---|
| **Praise first** | Kluger & DeNisi (1996) reviewed 607 feedback studies and found task-directed feedback reliably improves performance, while praise by itself often does not. Praise appears here only when tied to the student's own measured number. |
| **Ask** | Hattie & Timperley (2007) show feedback answering "where am I going?", "how am I going?", "where to next?" is what moves learning. The ASK step is the middle question, handed to the student. |
| **Suggest** | Rahayu (2021) tested praise, suggestion and criticism across three EFL classes; the suggestion group improved the most. |

**Honest limit:** Hattie & Timperley and Kluger & DeNisi are general feedback
research, not speaking-specific. Rahayu is writing, not speech. The shape is a
reasonable transfer, not a proven result for this exact app.

## 4. What changed from v2

| v2 | v4 |
|---|---|
| The right intent, but unlabelled | Made explicit as three named steps in order |
| 2-4 sentences | 3-5 sentences — room for one per step |
| "Arab school students" | "a student who has just finished practising" — the app supports 15 languages |
| No guard against a fourth step | "do not add a fourth step" |

## 5. How it is built at call time

The measured session data is appended as JSON, plus an optional
script-comparison count. The model never computes a number.

Request settings: `temperature 0.5`, `max_tokens 260`.

## 6. Test that guards it

`TestPasReviewShape` in `presence/test_features.py` asserts the constant
contains `PRAISE`, `ASK` and `SUGGEST` in that order, that praise must carry a
number, and that the prompt is saved verbatim here.

## 7. The failure that would motivate v5

If the ASK question comes back rhetorical ("wasn't that pace a bit fast?"), it
already contains the answer and teaches nothing. v5 would forbid yes/no
questions outright.
