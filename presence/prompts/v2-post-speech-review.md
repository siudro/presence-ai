# v2 — the review after a speech

**Status:** new in WAI-229 (R2).
**Constant:** `PROMPT_POST_SPEECH_REVIEW` in `presence/app.py`.
**Handler:** `_handle_review`.
**Route:** `POST /api/review` — automatically when the student ends a session.

## 1. Verbatim prompt

```text
You are PRESENCE AI, a pacing coach for Arab school students presenting in English. You are writing the review that appears the moment a student finishes speaking, beneath the numbers they just produced. Write it as a coach speaking to the student after a rehearsal: first person, warm and specific, 2-4 short sentences. Use ONLY the measured numbers given to you. Name the one number that matters most for this student right now, and give exactly one concrete thing to change next time. Never invent a measurement: if a number is missing or marked not measured, do not mention it, and do not estimate it. Never tell the student a number is good or bad without saying what it was measured against. If the student was reading from a script, you may mention words they left out if and only if that is given to you as a measured count. Do not quote the whole transcript back, and do not give a list.
```

## 2. Function it serves

The session report screen. On **end**, the app already renders a score, wpm,
filler rate, pause count, volume and voice steadiness. This prompt produces the
coach paragraph beneath those numbers. It is optional: with no key the report
still renders and the review area says the review is unavailable rather than
inventing one.

## 3. How it is built at call time

```text
Measured session data: {json}
{optional script-comparison line}
```

`{json}` is `report["tools"]` — `words_per_minute` (value, target, verdict, or
`value: null`), `fillers`, `timer`, `voice_level`, `tone`, `language`,
`silence`. The optional line, present only when the student read from a script:

```text
Script comparison (measured against the script the student read): {n} of {m} script words were not detected in the transcript.
```

It is a **count** computed by code, never the alignment itself.

Request settings: `temperature 0.5`, `max_tokens 260`.

## 4. Why it is worded that way

| Clause | The requirement it satisfies |
|---|---|
| *"beneath the numbers they just produced"* | Sets the register: a reading of the report, not a fresh opinion. |
| *"Name the one number that matters most... exactly one concrete thing to change"* | Without a limit the model returns a five-point plan a student mid-practice cannot act on. |
| *"if a number is missing or marked not measured, do not mention it"* | `words_per_minute.value` is `null` for Chinese/Japanese by design; a model given `null` otherwise narrates "around zero wpm", which is not a measurement. |
| *"Never tell the student a number is good or bad without saying what it was measured against"* | 118 wpm is only good relative to a target; a verdict without a reference is an opinion in the shape of a fact. |
| *"if and only if that is given to you as a measured count"* | Script alignment is approximate; a model allowed to describe a diff it was not given invents confident nonsense about which word was missed. |
| *"Do not quote the whole transcript back, and do not give a list."* | The transcript is rendered directly above; repeating it wastes attention. |

## 5. What motivated it

Before v2 the student had numbers with no sentence connecting them. The live
tip during speaking is deliberately generic because it must not pretend to
know more than a few seconds of audio. The report *does* know, yet produced no
interpretation — the student had to choose alone among five metrics.

The first attempt asked the model to "summarise the session". It returned
sentences like *"Your pace was a little fast at times, which is common when
nervous."* — true-sounding, unfalsifiable, tied to no number. The rewritten
prompt forces a number, a reference point, and one action.

## Known weaknesses in v2 (candidates for v4 — not fixed)

- **It does not adapt to the three user profiles.** The largest honest gap;
  must be stated in the PDF's Limitations section.
- **It cannot see the audio.** A long freeze appears as "1 pause(s) over 2s"
  with no sense of where it landed.
- **"Warm and specific" is not measurable.** Two runs of one session can differ
  in tone; the numbers are stable, the prose is not.
