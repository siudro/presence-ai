# v3 — turning an uploaded file into a script to read

**Status:** new in WAI-229 (R3, feeding R4 and R6).
**Constant:** `PROMPT_SCRIPT_FROM_FILE` in `presence/app.py` (a template).
**Handler:** `_handle_script`.
**Route:** `POST /api/script` — when the student chooses **Prepare a script**.

## 1. Verbatim prompt

```text
You are PRESENCE AI, a speech-writing assistant for Arab school students presenting in English. The student has uploaded the text of a presentation they must give, or notes for one. Turn it into a script they can read aloud at a pace of about 118 words per minute, which is the normal pace for a school presentation in English. Write it in the first person, as the student would say it out loud: short sentences, no bullet points, no headings, no speaker labels, no stage directions, no markdown. Keep every fact, name and number from the source and do not add facts that are not in it. If the source is only a list of headings with no content, turn each heading into one or two spoken sentences and nothing more; say nothing you cannot derive from the source. Write between {minimum_words} and {maximum_words} words. Output only the script text, with no preamble and no closing remark.
```

`{minimum_words}` and `{maximum_words}` are filled per request — see section 3.

## 2. Function it serves

The **prepare a script** path. The student supplies a file, the app produces a
script, and that script becomes the text they read from — in the same reading
area the transcript uses (R4), and the text word-level alignment scores them
against (R6).

## 3. How it is built at call time

```text
PRESENTATION TEXT:
{extracted text, capped at 12000 characters}
```

Word range, computed before formatting:

```text
minimum_words = max(60, round(target_seconds * 1.6))
maximum_words = round(target_seconds * 2.4)
```

At 118 wpm a 120-second target is ~236 words, so the range is 192–288 — wide
enough to write naturally, narrow enough to fit the slot.

Request settings: `temperature 0.6`, `max_tokens 1200`.

## 4. Why it is worded that way

| Clause | The requirement it satisfies |
|---|---|
| *"first person, as the student would say it out loud"* | A deck is written in note form; read aloud that sounds like bullets. The script must be *speakable*. |
| *"no bullet points, no headings, no speaker labels, no stage directions, no markdown"* | Each would become a word the student is asked to read and never says — a false "missed word" in the R6 alignment. |
| *"short sentences"* | Matched to the pacing measure; long compound sentences are where students freeze. |
| *"Keep every fact, name and number... do not add facts that are not in it."* | Asked for 236 words from a 60-word outline, a model invents statistics. The wide range means it never needs to. |
| *"say nothing you cannot derive from the source"* | Handles a slide deck exported as a heading list — the most dangerous input, where the model would otherwise fabricate a whole speech. |
| *"about 118 words per minute"* | Lets the model size its output instead of guessing. |
| *"Output only the script text, with no preamble"* | "Here is your script:" would enter the reading view and be scored as missed text. |

## 5. What motivated it

Before v3 there was no script path. The only text input was an optional
`<details>` passage, treated as a glance-card and *"never compared
automatically"*. Nothing produced a script and nothing could score against one.

**The honest limitation.** The app is Python stdlib only — no `python-pptx`,
no PDF parser. A `.pptx` is a ZIP of XML and a `.pdf` a binary container;
neither can be read with the standard library. The upload path accepts `.txt`,
`.md` and pasted text, and the UI says so plainly. When a `.pptx` or `.pdf` is
chosen, the app tells the student to export as text first — it never silently
does something worse.

## Known weaknesses in v3 (candidates for v4 — not fixed)

- **No PPT/PDF ingestion.** Needs a dependency, trading away the "no install
  step" property the project keeps.
- **No source-integrity check.** The prompt asks the model not to add facts;
  nothing verifies it.
- **No personalisation by profile.**
