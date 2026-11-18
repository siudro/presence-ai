# PRESENCE AI

A pacing coach for Arab school students who present in English.

> My AI is a pacing coach that helps Arab school students presenting in English stay clear and
> confident, and it adapts to how much they translate from Arabic in their head, how fast they
> speak, and how many filler words appear under pressure.

Built for **WAICY 2026 — LLM & AI Agent track**.

---

## The design rule

**Code measures. The LLM coaches.**

An LLM cannot count words, cannot hear volume, and cannot time a pause. It will confidently
invent "you're speaking at about 120–140 words per minute" — which is exactly the failure this
project was built to prevent. So:

- **Every number on the screen is computed** from real audio, in Python or in the browser.
- **When there is not enough speech to measure**, the page says so instead of showing a guess.
- **The advisor is given the numbers** and asked to interpret them, never to produce them.

That single rule decides most of the architecture below.

---

## What it does

| Tool | What it measures | Source of the number |
|---|---|---|
| Words per minute | Spoken words ÷ actual speaking time (pauses excluded) | Huang & Graf (2020) |
| Fillers | um, uh, like, "you know", يعني, yaani… | per-language lists |
| Pauses | Gaps over 2 seconds, counted once per gap | Zeng et al. (2025) |
| Voice level | Live dBFS from the microphone | browser audio, relative only |
| Transcript | Speech → text, typed live while speaking | Cohere Transcribe |
| Freezes | Silences of 20 s or more | live, during the recording |

### It adapts to three kinds of student

The advisor behaves differently for a student who freezes on stage, one who translates from
Arabic in her head, and one who rushes. Same measurements, different advice.

### 15 languages

English · العربية · Français · Español · Deutsch · Italiano · Português · Nederlands · Polski ·
Ελληνικά · Türkçe · Tiếng Việt · 中文 · 日本語 · 한국어

Word counting works in every script. Fillers are per-language.

**Chinese and Japanese report no words-per-minute.** Those languages are written without spaces,
so a word count (and therefore a rate) is not comparable. The page says "not comparable" and
gives characters per minute instead, rather than printing a precise-looking number that means
nothing.

---

## Running it

You need Python 3.9 or newer. No packages to install — standard library only.

```bash
cd presence
python3 app.py
```

Then open **http://localhost:8080**.

> **Do not open `index.html` directly from Finder.** That gives a `file://` address with no
> server behind it: the page renders but recording, transcription and saving cannot work.

### Keys

There are two different keys, and they are not interchangeable.

**1. Cohere key — transcription.** From [dashboard.cohere.com](https://dashboard.cohere.com/api-keys).
Paste it in Settings, or in `api_key.txt`. Turns speech into text; without it the page still
measures, but nothing is typed on screen.

**2. AI chat key — optional.** Any OpenAI-compatible provider. Copy
`llm_config.example.json` to `llm_config.json` and set your provider. The key is read from an
environment variable named in that file — **never put a real key in the file itself**.

---

## Layout

```
presence/
├── app.py                    measurement core + HTTP server
├── cohere_transcribe.py      Cohere Transcribe client
├── coach_bridge.py           queue for an out-of-band Okal agent
├── static/
│   ├── index.html            the four screens
│   ├── app.css               Concept C — warm paper, serif, terracotta
│   └── app.js                microphone, measurement, transcript, chat
├── test_app.py               41 tests — the measurement maths
├── test_features.py          86 tests — every feature, end to end
├── llm_config.example.json   chat provider config (generic)
└── RUN-ON-YOUR-MACHINE.txt   step-by-step instructions
```

Four screens: **home · speaking · report · progress**.

The progress screen reads `/api/history`. The server has always appended every session to
`sessions.jsonl`; nothing displayed it until now.

---

## Tests

```bash
python3 test_app.py        # 41 — measurement maths
python3 test_features.py   # 86 — key handling, transcription, routes, UI, languages
```

`test_features.py` covers the HTTP surface against a real server, every offered language's word
and filler detection, transcript escaping, and each bug found during development — so a fixed
bug cannot quietly return.

---

## Honest limitations

- **Browser microphones are uncalibrated.** Voice level is relative to this speaker's own
  recording, never comparable between people or devices.
- **Audio leaves the device** when Cohere transcription is on. Measurements are local; the
  transcript is produced by a cloud model. The page states this.
- **The cloud transcription path is only partly verified.** The request is well-formed and
  returns a correct authentication error without a key, but a full successful transcription has
  not been confirmed in this repository's own test run.
- **Chinese and Japanese** have no meaningful words-per-minute — see above.
- **Filler lists for some languages are conservative.** English and Arabic are tuned against real
  use; several others are reasonable but not validated against a corpus.
- **Free-tier AI models may be refused** by their provider when called from a third-party app.

---

## Why a page and not a chat window

A chat forgets. This keeps every session in `sessions.jsonl`, which is what makes progress
visible and what lets the advisor talk about change over time rather than a single attempt.

It is also why the numbers are computed in code: the page can be held to a standard a
conversation cannot.
