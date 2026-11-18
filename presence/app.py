#!/usr/bin/env python3
"""
PRESENCE AI — measuring tool for Arab school students presenting in English.

DESIGN RULE (the whole point of this file):
    Code measures. The LLM coaches.

    An LLM cannot count words, cannot hear volume, and cannot hear tone.
    Task 6 proved it: Advisor v1 invented "120-140 wpm" out of thin air.
    So every number on this page is computed in Python from real audio.

What this file does NOT do:
    - It does not coach. No advice, no feedback text, no LLM calls.
    - It does not judge. It reports measurements and one comparison line.
    - It does not claim clinical accuracy. Browser audio is approximate.

The ONE exception, and why it does not break the design rule:
    After 20 seconds of real silence the page says a short encouragement out
    loud. That is not coaching — it is a safety net. A frozen student cannot
    reach a chat window to be coached; she needs a voice in the room. The
    words are fixed and authored, not generated, so nothing is invented. The
    nudge is also logged as a measurement, so the advisor reads
    "four freezes, longest 34s" and coaches from that.

Scope (locked in WAI-134):
    Pacing + tone, for Arab school students presenting in English.
    Posture and camera analysis are OUT OF SCOPE on purpose.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Deque, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
SESSIONS_FILE = HERE / "sessions.jsonl"

# --- Cohere transcription (optional) ----------------------------------------
# The audio is uploaded to Cohere to be transcribed, so the page never claims
# it stays on this machine. Measurements are still computed locally; only the
# transcript comes from the cloud model.
#
# The key is read from one small file, api_key.txt, or from COHERE_API_KEY.
# Keeping it in a file means a student can paste one line and run, and the key
# never appears in their shell history.
API_KEY_FILE = HERE / "api_key.txt"
MAX_AUDIO_BYTES = 25 * 1024 * 1024  # Cohere's documented limit

# ---------------------------------------------------------------------------
# Measurement tables — every number has a published source.
# Do not "improve" these by guessing. They are cited in the WAICY PDF.
# ---------------------------------------------------------------------------

# Huang & Graf (2020), Taiwan J TESOL, ERIC EJ1275545.
# B2 English speakers: 118 wpm. C1: 142 wpm. Native speakers: 106-265 wpm.
# Our target band is a B2 school student presenting in English.
WPM_TARGET_LOW = 110
WPM_TARGET_HIGH = 135
WPM_B2_BASELINE = 118

# A pause longer than this is "not a natural breath" — it reads as searching
# for a word or translating from Arabic in the head (Zeng 2022, SLA).
LONG_PAUSE_SECONDS = 2.0

# Below this many seconds of actual speaking, words-per-minute is arithmetic
# noise rather than a measurement. Report "too short" instead of a number.
# 20s is roughly two sentences of a school presentation.
MIN_SPEECH_SECONDS = 20.0

# --- Silence nudge (pause safety net) ---------------------------------------
# A student who freezes on stage does not freeze for two seconds. She freezes
# for twenty and loses the room. The page watches for that freeze and helps
# her start again. This is the ONE place the page talks during a recording.
#
# Silent routes to help:
#   1. It speaks a short, calm, encouraging line out loud.
#   2. It offers a "way back in" — the next sentence to say.
#   3. It asks for the help and pauses itself for a bit, so it never talks
#      over her while she is recovering.
NUDGE_AFTER_SECONDS = 20.0     # silence that triggers the first nudge
NUDGE_REPEAT_SECONDS = 15.0    # wait between nudges if she stays silent
NUDGE_MIC_CHECK_AT = 2         # nudge #3 asks "can you hear yourself?" honestly
NUDGE_MIC_CHECK_HOLD = 45.0    # after the mic check, shut up for this long
NUDGE_QUIET_DB = -33.0         # below this for 0.6s counts as "not speaking"

# Filler tokens. Matched case-insensitively on word boundaries.
# Arabic-script fillers are included because the student's first language
# leaks into the second — that is the specific failure this project names.
FILLERS_EN = [
    "um", "umm", "uh", "uhh", "er", "erm", "ah", "eh",
    "like", "you know", "i mean", "kind of", "sort of", "actually", "basically",
]
FILLERS_AR_TRANSLITERATED = ["yaani", "ya3ni", "yani", "aani", "innu", "inna"]
FILLERS_AR_SCRIPT = ["يعني", "امم", "اه", "ايه", "كذا"]
FILLER_AR_SCRIPT_SET = set(FILLERS_AR_SCRIPT)

# ---------------------------------------------------------------------------
# Languages the transcription model can actually handle.
# Source: Cohere Transcribe supports exactly these 14, and does NOT detect the
# language automatically — the caller must name it. So the page has to ask,
# rather than guess, or Arabic audio gets transcribed as if it were English.
#
# `script` decides which character range counts as a word, and `fillers` lists
# the hesitation sounds of that language. A language with an empty filler list
# still gets correct word/pace counts; it just has no filler detection, and
# the UI says so rather than pretending.
# ---------------------------------------------------------------------------

LANGUAGES = {
    "en": {"label": "English", "script": "latin",
           "fillers": FILLERS_EN + FILLERS_AR_TRANSLITERATED},
    "ar": {"label": "Arabic", "script": "arabic",
           "fillers": FILLERS_AR_TRANSLITERATED + FILLERS_AR_SCRIPT},
    "fr": {"label": "French", "script": "latin",
           "fillers": ["euh", "heu", "ben", "bah", "genre", "voilà", "en fait", "tu vois", "du coup"]},
    "es": {"label": "Spanish", "script": "latin",
           "fillers": ["eh", "este", "bueno", "pues", "o sea", "es que", "vale", "mm"]},
    "de": {"label": "German", "script": "latin",
           "fillers": ["äh", "ähm", "halt", "also", "irgendwie", "sozusagen"]},
    "it": {"label": "Italian", "script": "latin",
           "fillers": ["eh", "ehm", "cioè", "allora", "insomma", "diciamo"]},
    "pt": {"label": "Portuguese", "script": "latin",
           "fillers": ["eh", "hum", "tipo", "então", "né", "assim"]},
    "nl": {"label": "Dutch", "script": "latin",
           "fillers": ["eh", "ehm", "nou", "zeg maar", "dus", "eigenlijk"]},
    "pl": {"label": "Polish", "script": "latin",
           "fillers": ["eee", "yyy", "no", "jakby", "wiesz", "generalnie"]},
    "el": {"label": "Greek", "script": "greek",
           "fillers": ["εε", "εμ", "λοιπόν", "δηλαδή", "σαν"]},
    "tr": {"label": "Turkish", "script": "latin",
           "fillers": ["ııı", "eee", "şey", "işte", "yani"]},
    "vi": {"label": "Vietnamese", "script": "latin",
           "fillers": ["ờ", "ừ", "thì", "kiểu", "đại loại"]},
    "zh": {"label": "Chinese", "script": "cjk",
           "fillers": ["嗯", "呃", "那个", "就是", "然后"]},
    "ja": {"label": "Japanese", "script": "cjk",
           "fillers": ["えー", "あの", "その", "まあ", "なんか"]},
    "ko": {"label": "Korean", "script": "hangul",
           "fillers": ["음", "어", "그", "저기", "약간"]},
}


# A spoken word, in any script the transcription model can produce.
#
# The first version was [A-Za-z']+ only, so every Arabic word was silently
# dropped: a student speaking fluent Arabic scored "no audio captured" and
# 0 wpm. Extending it once, properly, avoids repeating that bug for each new
# language the page offers.
#
#   Latin + apostrophe      A-Za-z'
#   Arabic                  \u0600-\u06FF \u0750-\u077F \u08A0-\u08FF
#   Greek                   \u0370-\u03FF
#   Cyrillic                \u0400-\u04FF
#   Vietnamese + Latin ext. \u00C0-\u024F \u1E00-\u1EFF
#   CJK                     \u3040-\u30FF (kana) \u4E00-\u9FFF \u3400-\u4DBF
#   Hangul                  \uAC00-\uD7AF \u1100-\u11FF
#
# CJK has no spaces, so a "word" here is a run of CJK characters. That makes
# the word count and therefore wpm incomparable with spaced languages; the
# report says so for CJK rather than implying the numbers mean the same thing.
WORD_PATTERN = re.compile(
    "["
    "A-Za-z'"
    "\u00c0-\u024f"      # Latin-1 Supplement + Extended-A/B (French, German, Polish...)
    "\u0370-\u03ff"      # Greek
    "\u0400-\u04ff"      # Cyrillic
    "\u0600-\u06ff"      # Arabic
    "\u0750-\u077f"      # Arabic Supplement
    "\u08a0-\u08ff"      # Arabic Extended-A
    "\u1e00-\u1eff"      # Latin Extended Additional (Vietnamese)
    "\u3040-\u30ff"      # Hiragana + Katakana
    "\u3400-\u4dbf"      # CJK Extension A
    "\u4e00-\u9fff"      # CJK Unified Ideographs
    "\uac00-\ud7af"      # Hangul syllables
    "\u1100-\u11ff"      # Hangul Jamo
    "]+"
)

# Scripts written without spaces between words. Word counts for these measure
# character runs, not words, so wpm is not comparable to spaced languages.
UNSPACED_SCRIPTS = {"cjk"}

# Rate-of-speech "tone" bands, in characters-per-second of transcript.
# This is a shape measure, not a prosody model. It distinguishes
# an even presentational delivery from a rushed or flat one.
RATE_EVEN = (11.0, 16.0)      # comfortable school-presentation pace
RATE_RUSHED = 16.0            # above this reads as rushing
RATE_FLAT = 11.0              # below this reads as slow / unsure

# Voice-level bands in dBFS (0 = loudest before clipping, -60 = near silence).
# Browser microphones are uncalibrated, so these are relative warnings only.
DB_TOO_QUIET = -40.0
DB_CLIPPING = -3.0


# ---------------------------------------------------------------------------
# Core measurement math. Pure functions so they are testable without a browser.
# ---------------------------------------------------------------------------

def count_words(text: str, language: str = "en") -> int:
    """
    Count real spoken words.

    Filler PHRASES ("you know", "kind of", "i mean") are removed before
    counting. Single-word fillers are removed by token.

    The phrase-level removal is why we do not maintain a set of component
    words: "kind" and "of" and "is" appear in ordinary speech, and a naive
    component set silently deletes real words and silently erodes the
    word count (and therefore the wpm). Found by test_counts_plain_words.
    """
    if not text:
        return 0

    fillers = language_fillers(language)
    working = text.lower()

    # Multi-word fillers first, longest first, so "you know" goes before "know".
    # Only phrases containing a space: CJK fillers have no spaces and are
    # handled by plain substring below.
    phrases = sorted(
        (f for f in fillers if " " in f),
        key=len,
        reverse=True,
    )
    for phrase in phrases:
        working = re.sub(r"\b" + re.escape(phrase) + r"\b", " ", working)

    words = WORD_PATTERN.findall(working)

    single_fillers = {f for f in fillers if " " not in f}
    kept = [
        w for w in words
        if w not in single_fillers and w not in FILLER_AR_SCRIPT_SET
    ]
    return len(kept)


def language_fillers(language: str) -> list:
    """The filler list for a language code, defaulting to English."""
    entry = LANGUAGES.get((language or "en")[:2].lower())
    if entry:
        return entry["fillers"]
    return FILLERS_EN


def language_script(language: str) -> str:
    """The script family for a language code ('latin', 'arabic', 'cjk'...)."""
    entry = LANGUAGES.get((language or "en")[:2].lower())
    return entry["script"] if entry else "latin"


def find_fillers(text: str, language: str = "en") -> Dict[str, int]:
    """
    Return every filler token found, with its count.

    Two matching strategies, because one is not correct for every script:
      * Spaced scripts use \b word boundaries, so "ah" does not match the
        "ah" inside "ahmed".
      * CJK has no spaces, so boundaries never match; those fillers are
        matched as plain substrings instead.
    """
    if not text:
        return {}
    lowered = text.lower()
    found: Dict[str, int] = {}
    for token in language_fillers(language):
        escaped = re.escape(token.lower())
        if " " in token or token.isascii():
            pattern = r"\b" + escaped + r"\b"
            n = len(re.findall(pattern, lowered))
        else:
            # Non-ASCII, single token (CJK, Arabic script): substring match.
            n = lowered.count(token.lower())
        if n:
            found[token] = n
    return dict(sorted(found.items(), key=lambda kv: -kv[1]))


def wpm(word_count: int, speaking_seconds: float) -> float:
    """
    Words per minute over actual speaking time (pauses excluded).

    Returns 0.0 rather than a guess when there is nothing to compute.
    This is the direct fix for Advisor v1 inventing "120-140 wpm":
    if no audio was captured, the honest answer is 0 / unavailable.

    A session shorter than MIN_SPEECH_SECONDS is also treated as
    unmeasurable. Wpm from a 5-second sample is arithmetic noise, and
    presenting it as a rate would be the same sin as inventing one.
    """
    if speaking_seconds < MIN_SPEECH_SECONDS or word_count <= 0:
        return 0.0
    return round(word_count / (speaking_seconds / 60.0), 1)


def pace_verdict(wpm_value: float, speaking_seconds: float = 0.0) -> str:
    if speaking_seconds and speaking_seconds < MIN_SPEECH_SECONDS:
        return f"too short to measure (under {MIN_SPEECH_SECONDS:.0f}s of speech)"
    if wpm_value <= 0:
        return "no audio captured"
    if wpm_value < WPM_TARGET_LOW:
        return "slower than the B2 target band"
    if wpm_value > WPM_TARGET_HIGH:
        return "faster than the B2 target band"
    return "inside the B2 target band"


def rate_profile(transcript: str, speaking_seconds: float) -> str:
    """
    Even / rushed / flat, from characters per second of real speech.

    This measure works the same way in every script, which is why it is the
    one pace signal used for languages where wpm is not comparable.
    """
    if not transcript or speaking_seconds <= 0:
        return "unknown"
    cps = len(transcript.strip()) / speaking_seconds
    if cps >= RATE_RUSHED:
        return "rushed"
    if cps <= RATE_FLAT:
        return "flat"
    return "even"


def wpm_comparable(language: str) -> bool:
    """
    Whether a words-per-minute figure means anything for this language.

    Chinese and Japanese are written without spaces, so a "word" is a whole
    run of characters and wpm would be small and meaningless. Those languages
    get the character-based delivery shape instead, and the report says wpm
    is not comparable rather than printing a number a judge could challenge.
    """
    return language_script(language) not in UNSPACED_SCRIPTS


def chars_per_minute(transcript: str, speaking_seconds: float) -> float:
    """Character rate, the pace measure that works in unspaced scripts."""
    if not transcript or speaking_seconds <= 0:
        return 0.0
    return round(len(transcript.strip()) / (speaking_seconds / 60.0), 1)


def level_verdict(avg_db: float, peak_db: float) -> str:
    if peak_db > DB_CLIPPING:
        return "clipping - too close to the microphone"
    if avg_db < DB_TOO_QUIET:
        return "very quiet - the room may not hear you"
    return "audible and steady"


def long_pauses(pauses: List[float]) -> List[float]:
    return [p for p in pauses if p >= LONG_PAUSE_SECONDS]


# ---------------------------------------------------------------------------
# Silence nudge. The page does not sit there watching a student freeze.
#
# Rules this engine must obey, and why:
#   - It speaks ONLY after NUDGE_AFTER_SECONDS of real silence. A 5-second
#     breath is normal in a presentation; interrupting it would teach the
#     student to fear the one pause an audience actually needs.
#   - It never gives up on the student but it DOES give her room: after the
#     microphone check it goes quiet for NUDGE_MIC_CHECK_HOLD seconds.
#   - It never claims to hear something it cannot hear. Nudge 3 exists
#     because "I can't hear you" might be true — the microphone may be the
#     problem, not her. Saying so is the honest move, and it is the same
#     rule the whole project runs on: no invented facts.
# ---------------------------------------------------------------------------

# Short, calm, and in the student's language. Not "CMON!!" — a student who
# has frozen is embarrassed, and shouting at an embarrassed student makes
# the freeze worse. Encouragement here means lowering the stakes.
NUDGES = [
    {
        "text": "Take your time — I'm still listening. Say the next sentence slowly.",
        "kind": "encourage",
    },
    {
        "text": "You haven't lost it. Start again from your last point — one sentence is enough.",
        "kind": "restart",
    },
    {
        "text": "I'm still here, but I can't hear you. Check your microphone, then keep going.",
        "kind": "mic_check",
    },
    {
        "text": "One breath, then the next sentence. I'll keep measuring.",
        "kind": "encourage",
    },
    {
        "text": "Still with you. If you're stuck on a word, say it in a simpler way — that is fine on stage.",
        "kind": "encourage",
    },
]


@dataclass
class Nudge:
    """One thing the page said out loud, and when."""
    at_seconds: float
    silence_seconds: float
    text: str
    kind: str


def nudge_for(silence_seconds: float, nudge_index: int) -> Optional[dict]:
    """
    Decide whether to speak, and what to say.

    Returns None when the student is still inside her allowance — a return
    of None is the common case and it is a decision, not a failure.
    """
    if silence_seconds < NUDGE_AFTER_SECONDS:
        return None
    if nudge_index == NUDGE_MIC_CHECK_AT:
        return NUDGES[NUDGE_MIC_CHECK_AT % len(NUDGES)]
    return NUDGES[nudge_index % len(NUDGES)]


def silence_after_nudge(nudge_count: int) -> float:
    """
    How much silence the student is allowed before the next nudge.

    Index 3 is the microphone check: after it, the page holds back for
    NUDGE_MIC_CHECK_HOLD seconds. It will not keep apologising over a
    student who is silently fixing her mic.
    """
    if nudge_count == NUDGE_MIC_CHECK_AT + 1:
        return NUDGE_MIC_CHECK_HOLD
    return NUDGE_REPEAT_SECONDS


def nudge_report(nudges: List[Nudge], duration_seconds: float) -> dict:
    """
    The nudge list as a measurement, not as praise.

    This is what a student sees afterwards and what the advisor reads:
    "you went silent four times, twice for over twenty seconds". It is
    evidence, and the PDF can use it as such.
    """
    silences = [n.silence_seconds for n in nudges]
    return {
        "count": len(nudges),
        "longest_silence": max(silences) if silences else 0.0,
        "per_minute": (
            round(len(nudges) / (duration_seconds / 60.0), 1)
            if duration_seconds >= 20 else None
        ),
        "verdict": (
            "no freezes over 20 seconds" if not nudges
            else f"{len(nudges)} freeze(s), longest {max(silences):.0f}s"
        ),
        "note": "a nudge spoken out loud during the recording, not advice",
    }


@dataclass
class Session:
    """One recorded attempt. This is what a student can look back at."""
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    profile: str = "unknown"
    language: str = "en"
    duration_seconds: float = 0.0
    speaking_seconds: float = 0.0
    pauses: List[float] = field(default_factory=list)
    avg_db: float = -60.0
    peak_db: float = -60.0
    transcript: str = ""
    words: int = 0
    wpm: float = 0.0
    fillers: Dict[str, int] = field(default_factory=dict)
    filler_total: int = 0
    rate: str = "unknown"
    nudges: List[dict] = field(default_factory=list)
    # One bucket per second of the recording. This is what makes the heatmap
    # and the before/after comparison possible: without a timeline the report
    # can only describe a whole session, never point at a moment inside it.
    timeline: List[dict] = field(default_factory=list)


def _as_seconds(value) -> float:
    """A non-negative float from whatever the client sent, or 0.0.

    The client is a browser that a student's flaky laptop runs. It must not
    be able to 500 this endpoint with a string where a number belongs.
    """
    try:
        return round(max(0.0, float(value)), 1)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Timeline — one bucket per second of the recording.
#
# The heatmap and the before/after comparison both need to point at a moment
# INSIDE a session, not just summarise it. The browser already collects
# per-frame volume and pause gaps; this folds them into second-sized buckets.
#
# Every flag is computed from a measurement. Nothing here infers emotion.
# ---------------------------------------------------------------------------

def _is_number(value) -> bool:
    """True for a finite number, False for anything else (strings, None, nan)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return f == f and abs(f) != float("inf")


TIMELINE_FAST_WPM = 160.0
TIMELINE_SLOW_WPM = 95.0
TIMELINE_QUIET_DB = -40.0


def build_timeline(
    duration: float,
    pauses: List[float],
    db_samples: List[float],
    word_times: List[float],
    filler_times: List[float],
) -> List[dict]:
    """
    Fold raw per-frame data into one bucket per second.

    Args:
      duration:    total recording length in seconds
      pauses:      each pause length, in order
      db_samples:  volume readings, roughly one per animation frame
      word_times:  the second at which each spoken word landed
      filler_times: the second at which each filler landed

    Returns a list of buckets: {t, wpm, fillers, paused, db, flags}
    Buckets are always the same length in time, so the heatmap can draw them
    side by side without rescaling.
    """
    seconds = int(max(0.0, duration))
    if seconds <= 0:
        return []

    buckets = [
        {"t": i, "wpm": None, "fillers": 0, "paused": False, "db": None, "flags": []}
        for i in range(seconds)
    ]

    # Volume: the browser samples far faster than 1 Hz, so average per bucket.
    if db_samples:
        per_bucket = max(1, len(db_samples) // seconds)
        for i in range(seconds):
            chunk = db_samples[i * per_bucket:(i + 1) * per_bucket]
            if chunk:
                buckets[i]["db"] = round(sum(chunk) / len(chunk), 1)

    # Pauses occupy a contiguous run of seconds starting where they began.
    # We do not store exact pause positions, so they are laid down in order
    # from the start of the following word — approximate, and labelled so.
    t = 0.0
    for length in pauses:
        start = int(t)
        end = min(seconds, start + max(1, int(round(length))))
        for i in range(start, end):
            buckets[i]["paused"] = True
        t += length + 1

    # Words and fillers, bucketed by the second they were spoken.
    for wt in word_times:
        i = int(wt)
        if 0 <= i < seconds:
            buckets[i]["wpm"] = (buckets[i]["wpm"] or 0) + 60
    for ft in filler_times:
        i = int(ft)
        if 0 <= i < seconds:
            buckets[i]["fillers"] += 1

    # Flags drive the heatmap colours. Only measured conditions qualify.
    for b in buckets:
        if b["paused"]:
            b["flags"].append("pause")
        if b["wpm"] is not None and b["wpm"] > TIMELINE_FAST_WPM:
            b["flags"].append("fast")
        elif b["wpm"] is not None and b["wpm"] < TIMELINE_SLOW_WPM:
            b["flags"].append("slow")
        if b["fillers"] >= 2:
            b["flags"].append("fillers")
        if b["db"] is not None and b["db"] < TIMELINE_QUIET_DB:
            b["flags"].append("quiet")
        # Filler density per second is the most actionable single signal.
        if b["fillers"] and "fillers" not in b["flags"]:
            b["flags"].append("fillers")

    return buckets


def build_session(payload: dict) -> Session:
    """
    Turn raw browser measurements into a measured session.

    The browser sends only what it can actually observe: duration, pauses,
    dB samples, and an optional transcript it heard. Everything derived here
    is computed, never accepted from the client as a claimed number.
    """
    transcript = str(payload.get("transcript") or "").strip()
    duration = max(0.0, float(payload.get("duration_seconds") or 0.0))
    pauses = [float(p) for p in (payload.get("pauses") or []) if float(p) > 0]
    pause_total = sum(pauses)
    speaking = max(0.0, duration - pause_total)

    # The chosen language decides which fillers to look for and whether wpm
    # is a meaningful number. Unknown codes fall back to English.
    language = str(payload.get("language") or "en")[:2].lower()
    if language not in LANGUAGES:
        language = "en"

    words = count_words(transcript, language)
    fillers = find_fillers(transcript, language)

    # Nudges are reported by the browser, which is the only thing that knows
    # when it actually spoke. They are bounded and shaped here so a malformed
    # client cannot stuff arbitrary text into the report.
    raw_nudges = payload.get("nudges")
    if not isinstance(raw_nudges, list):
        raw_nudges = []
    nudges: List[dict] = []
    for raw in raw_nudges[:40]:
        if not isinstance(raw, dict):
            continue
        nudges.append({
            "at_seconds": _as_seconds(raw.get("at_seconds")),
            "silence_seconds": _as_seconds(raw.get("silence_seconds")),
            "text": str(raw.get("text") or "")[:200],
            "kind": str(raw.get("kind") or "encourage")[:20],
        })

    return Session(
        profile=str(payload.get("profile") or "unknown")[:40],
        language=language,
        duration_seconds=round(duration, 2),
        speaking_seconds=round(speaking, 2),
        pauses=[round(p, 2) for p in pauses],
        avg_db=round(float(payload.get("avg_db") or -60.0), 1),
        peak_db=round(float(payload.get("peak_db") or -60.0), 1),
        # 4000 characters truncates at roughly five minutes of dense speech,
        # which is shorter than a school presentation. 40 000 is about an
        # hour and still far below any request-size limit.
        transcript=transcript[:40000],
        words=words,
        wpm=wpm(words, speaking),
        fillers=fillers,
        filler_total=sum(fillers.values()),
        rate=rate_profile(transcript, speaking),
        nudges=nudges,
        timeline=build_timeline(
            duration,
            pauses,
            [float(x) for x in (payload.get("db_samples") or []) if _is_number(x)],
            [float(x) for x in (payload.get("word_times") or []) if _is_number(x)],
            [float(x) for x in (payload.get("filler_times") or []) if _is_number(x)],
        ),
    )


# ---------------------------------------------------------------------------
# Progress analysis — achievements and a training roadmap.
#
# Both read only sessions the student actually recorded. With too few sessions
# they say so rather than inventing a milestone, because a fake achievement is
# worthless to the student and obvious to a judge.
# ---------------------------------------------------------------------------

MILESTONES = [
    ("first_session", "First session recorded", lambda ss: len(ss) >= 1),
    ("five_sessions", "Five sessions completed", lambda ss: len(ss) >= 5),
    ("ten_sessions", "Ten sessions completed", lambda ss: len(ss) >= 10),
    ("in_band", "Pace inside the target band",
     lambda ss: any(WPM_TARGET_LOW <= (s.get("wpm") or 0) <= WPM_TARGET_HIGH for s in ss)),
    ("low_fillers", "Under 3 fillers in a session",
     lambda ss: any((s.get("filler_total") or 99) <= 2 for s in ss)),
    ("no_freeze", "A session with no long freezes",
     lambda ss: any(len([n for n in (s.get("nudges") or [])]) == 0 for s in ss)),
    ("three_in_band", "Three sessions inside the band",
     lambda ss: sum(1 for s in ss if WPM_TARGET_LOW <= (s.get("wpm") or 0) <= WPM_TARGET_HIGH) >= 3),
]


def personal_records(sessions: List[dict]) -> dict:
    """
    Best-ever figures across the student's own sessions.

    'Best' means closest to target for pace, and lowest for fillers — never
    'most' anything, which would reward speaking fast or long.
    """
    rates = [s.get("wpm") for s in sessions if (s.get("wpm") or 0) > 0]
    fillers = [s.get("filler_total") for s in sessions if s.get("filler_total") is not None]
    durations = [s.get("duration_seconds") for s in sessions if s.get("duration_seconds")]

    best_pace = None
    if rates:
        target = (WPM_TARGET_LOW + WPM_TARGET_HIGH) / 2
        best_pace = min(rates, key=lambda r: abs(r - target))

    return {
        "sessions": len(sessions),
        "best_pace": round(best_pace, 1) if best_pace is not None else None,
        "fewest_fillers": min(fillers) if fillers else None,
        "longest_speaking": round(max(durations), 1) if durations else None,
    }


def achievements(sessions: List[dict]) -> dict:
    """Unlocked and pending milestones, plus the records to beat."""
    unlocked = []
    locked = []
    for key, label, test in MILESTONES:
        try:
            reached = bool(test(sessions))
        except Exception:
            reached = False
        (unlocked if reached else locked).append({"key": key, "label": label})

    return {
        "unlocked": unlocked,
        "locked": locked,
        "records": personal_records(sessions),
        # Nothing is claimed before there is something to claim.
        "note": (
            "No sessions yet — record one to start tracking progress."
            if not sessions else ""
        ),
    }


# Weakness -> drill. Each maps a MEASURED condition to an action, so the plan
# is derived from the student's own numbers rather than a generic template.
TRAINING_DRILLS = [
    {
        "key": "fillers",
        "when": lambda r: (r.get("fillers_per_min") or 0) > 6,
        "title": "Cut fillers by pausing",
        "why": "More than 6 fillers per minute last session.",
        "drill": "Read one paragraph aloud. Every time you feel an 'um' coming, close your mouth and count one second instead.",
        "minutes": 5,
    },
    {
        "key": "fast",
        "when": lambda r: (r.get("wpm") or 0) > WPM_TARGET_HIGH + 15,
        "title": "Slow the ends of sentences",
        "why": "Pace above the target band.",
        "drill": "Pick three sentences. Say each one, then stop completely for two seconds before the next.",
        "minutes": 6,
    },
    {
        "key": "slow",
        "when": lambda r: 0 < (r.get("wpm") or 0) < WPM_TARGET_LOW - 15,
        "title": "Build forward momentum",
        "why": "Pace below the target band.",
        "drill": "Read the same paragraph twice; the second time, aim to finish ten seconds sooner without rushing any single word.",
        "minutes": 6,
    },
    {
        "key": "freezes",
        "when": lambda r: (r.get("freezes") or 0) >= 2,
        "title": "Rehearse the first sentence",
        "why": "Repeated long silences during the session.",
        "drill": "Say only your opening sentence, out loud, until you can start it without looking. The freeze usually begins there.",
        "minutes": 4,
    },
    {
        "key": "quiet",
        "when": lambda r: (r.get("avg_db") or 0) < -42,
        "title": "Project to the back row",
        "why": "Average volume was low for this microphone.",
        "drill": "Stand up and read to a point on the far wall. Volume is measured relative to your own recording, so aim for a higher reading than last time.",
        "minutes": 5,
    },
    {
        "key": "steady",
        "when": lambda r: (r.get("pitch_cv") or 0) > 9,
        "title": "Steady your voice",
        "why": "Pitch moved a lot — an unsteady delivery.",
        "drill": "Hum one steady note for five seconds, then say a sentence keeping that same pitch. Aim for a lower variation than last session.",
        "minutes": 4,
    },
]


# ---------------------------------------------------------------------------
# AI audience simulator.
#
# A judge-style question bank. The questions are AUTHORED, not generated, for
# two reasons: a school competition should not depend on an API being awake,
# and a generated question could ask something inappropriate. The student
# answers out loud, and the tool measures how the answer was DELIVERED —
# pace, fillers, pauses — which is what this project can honestly assess.
#
# It does not grade whether the answer was correct. That would need to know
# the content of the talk, and a browser microphone cannot supply that.
# ---------------------------------------------------------------------------

AUDIENCE_QUESTIONS = {
    "en": [
        "Can you tell us in one sentence what your project does?",
        "Why did you choose this problem?",
        "What would you do differently if you started again?",
        "Who is the person this helps, and how do you know?",
        "What is the weakest part of your work?",
        "How does your project compare with what already exists?",
        "What did you learn that surprised you?",
        "Can you explain it to someone who knows nothing about it?",
    ],
    "ar": [
        "\u0645\u0627 \u0647\u0648 \u0645\u0634\u0631\u0648\u0639\u0643 \u0641\u064a \u062c\u0645\u0644\u0629 \u0648\u0627\u062d\u062f\u0629\u061f",
        "\u0644\u0645\u0627\u0630\u0627 \u0627\u062e\u062a\u0631\u062a \u0647\u0630\u0647 \u0627\u0644\u0645\u0634\u0643\u0644\u0629\u061f",
        "\u0645\u0627 \u0623\u0636\u0639\u0641 \u062c\u0627\u0646\u0628 \u0641\u064a \u0639\u0645\u0644\u0643\u061f",
        "\u0645\u0646 \u0647\u0648 \u0627\u0644\u0634\u062e\u0635 \u0627\u0644\u0630\u064a \u064a\u0633\u062a\u0641\u064a\u062f \u0645\u0646 \u0647\u0630\u0627\u061f",
    ],
}


def audience_questions(language: str = "en", count: int = 3) -> dict:
    """
    Pick judge-style questions for a practice round.

    English is the default because the project's scope is Arab students
    presenting IN ENGLISH; Arabic questions are offered for rehearsal in the
    first language.
    """
    bank = AUDIENCE_QUESTIONS.get((language or "en")[:2], AUDIENCE_QUESTIONS["en"])
    picked = bank[:max(1, min(count, len(bank)))]
    return {
        "questions": [{"id": i, "text": q} for i, q in enumerate(picked)],
        "note": (
            "Answer each one out loud. The tool measures HOW you answered — "
            "pace, fillers and pauses — not whether the answer was correct."
        ),
    }


def compare_sessions(before: dict, after: dict) -> dict:
    """
    Measure what changed between two recordings.

    Only differences in measured values are reported. "Better" is defined
    against the target band, not against raw magnitude, so speaking faster is
    not automatically an improvement.
    """
    target = (WPM_TARGET_LOW + WPM_TARGET_HIGH) / 2

    def pace_delta(a, b):
        """
        Distance from target before, minus distance after.

        Positive means the student moved CLOSER to the band, which is an
        improvement regardless of whether the raw number rose or fell.
        """
        if not a or not b:
            return None
        return round(abs(a - target) - abs(b - target), 1)

    def plain_delta(a, b):
        if a is None or b is None:
            return None
        return round(b - a, 1)

    before_wpm = before.get("wpm") or None
    after_wpm = after.get("wpm") or None

    rows = [
        {
            "key": "pace",
            "label": "Speaking pace",
            "before": round(before_wpm, 1) if before_wpm else None,
            "after": round(after_wpm, 1) if after_wpm else None,
            "delta": plain_delta(before_wpm, after_wpm),
            # Closer to target is better, even if the raw number went up.
            "improved": (pace_delta(before_wpm, after_wpm) or 0) > 0,
        },
        {
            "key": "fillers",
            "label": "Fillers",
            "before": before.get("filler_total"),
            "after": after.get("filler_total"),
            "delta": plain_delta(before.get("filler_total"), after.get("filler_total")),
            "improved": (after.get("filler_total") or 0) < (before.get("filler_total") or 0),
        },
        {
            "key": "pauses",
            "label": "Long pauses",
            "before": len([p for p in (before.get("pauses") or []) if p > 2]),
            "after": len([p for p in (after.get("pauses") or []) if p > 2]),
            "delta": plain_delta(
                len([p for p in (before.get("pauses") or []) if p > 2]),
                len([p for p in (after.get("pauses") or []) if p > 2]),
            ),
            "improved": (
                len([p for p in (after.get("pauses") or []) if p > 2])
                < len([p for p in (before.get("pauses") or []) if p > 2])
            ),
        },
    ]

    improved = sum(1 for r in rows if r["improved"])
    return {
        "rows": rows,
        "summary": f"{improved} of {len(rows)} measures improved",
        "before_at": before.get("created_at"),
        "after_at": after.get("created_at"),
    }


def training_roadmap(sessions: List[dict], deadline: Optional[str] = None) -> dict:
    """
    Build a plan from the most recent session's measured weaknesses.

    Ordering is by impact: fillers and freezes first because they are what an
    audience notices and what this tool measures most reliably.
    """
    if not sessions:
        return {
            "items": [],
            "note": "Record one session and a plan will be built from your own numbers.",
            "days_left": None,
        }

    latest = sessions[-1]

    # Derive the figures the drills read.
    duration = latest.get("duration_seconds") or 0
    fillers = latest.get("filler_total") or 0
    rating = {
        "wpm": latest.get("wpm") or 0,
        "fillers_per_min": (fillers / (duration / 60.0)) if duration >= 20 else 0,
        "freezes": len(latest.get("nudges") or []),
        "avg_db": latest.get("avg_db") or -60,
        "pitch_cv": latest.get("pitch_cv"),
    }

    items = []
    for drill in TRAINING_DRILLS:
        try:
            if drill["when"](rating):
                items.append({
                    "key": drill["key"],
                    "title": drill["title"],
                    "why": drill["why"],
                    "drill": drill["drill"],
                    "minutes": drill["minutes"],
                })
        except Exception:
            continue

    days_left = None
    if deadline:
        try:
            when = datetime.fromisoformat(deadline).date()
            days_left = (when - datetime.now(timezone.utc).date()).days
        except (ValueError, TypeError):
            days_left = None

    if not items:
        items = [{
            "key": "maintain",
            "title": "Hold this level",
            "why": "Nothing was badly out of range last session.",
            "drill": "Record the same passage again and try to keep every figure within a few percent.",
            "minutes": 5,
        }]

    return {
        "items": items,
        "note": "",
        "days_left": days_left,
        "based_on": latest.get("created_at"),
    }


def report(session: Session) -> dict:
    """
    Measurements plus one comparison line per tool.

    Deliberately NOT coaching. The advisor (Advisor_v2) does the coaching.
    This page answers "what did I actually do", never "what should I do".
    """
    long_p = long_pauses(session.pauses)
    per_min_fillers = (
        round(session.filler_total / (session.duration_seconds / 60.0), 1)
        if session.duration_seconds >= 20
        else None
    )
    return {
        "session_id": session.session_id,
        "created_at": session.created_at,
        "measurements": asdict(session),
        "tools": {
            "words_per_minute": {
                "value": session.wpm if wpm_comparable(session.language) else None,
                "unit": "wpm",
                "target": f"{WPM_TARGET_LOW}-{WPM_TARGET_HIGH}",
                "baseline_b2": WPM_B2_BASELINE,
                # Chinese/Japanese have no spaces between words, so a wpm
                # figure would be meaningless. Say that instead of printing it.
                "comparable": wpm_comparable(session.language),
                "verdict": (
                    pace_verdict(session.wpm, session.speaking_seconds)
                    if wpm_comparable(session.language)
                    else "not comparable in this language; use delivery shape"
                ),
                "source": "Huang & Graf (2020), Taiwan J TESOL, ERIC EJ1275545",
            },
            "language": {
                "code": session.language,
                "label": LANGUAGES.get(session.language, {}).get("label", "English"),
                "chars_per_minute": chars_per_minute(
                    session.transcript, session.speaking_seconds
                ),
            },
            "word_counter": {
                "value": session.words,
                "note": "filled words only; fillers excluded",
            },
            "timer": {
                "value": session.duration_seconds,
                "speaking": session.speaking_seconds,
                "long_pauses": len(long_p),
                "longest_pause": max(session.pauses) if session.pauses else 0.0,
                "verdict": (
                    "no long pauses" if not long_p
                    else f"{len(long_p)} pause(s) over {LONG_PAUSE_SECONDS:.0f}s"
                ),
                "source": "Zeng et al. (2025), Studies in Second Language Acquisition",
            },
            "voice_level": {
                "value_db": session.avg_db,
                "peak_db": session.peak_db,
                "verdict": level_verdict(session.avg_db, session.peak_db),
                "note": "browser microphone, uncalibrated - relative only",
            },
            "tone": {
                "rate": session.rate,
                "note": "shape of delivery from speech rate, not a prosody model",
                "verdict": {
                    "even": "even presentational delivery",
                    "rushed": "rushing - allow the ends of sentences to land",
                    "flat": "slow and flat - may read as unsure",
                    "unknown": "not enough speech to judge",
                }[session.rate],
            },
            "fillers": {
                "total": session.filler_total,
                "per_minute": per_min_fillers,
                "breakdown": session.fillers,
                "verdict": (
                    "none detected" if session.filler_total == 0
                    else f"{session.filler_total} filler word(s)"
                ),
            },
            "silence": nudge_report(
                [Nudge(**n) for n in session.nudges], session.duration_seconds
            ),
        },
    }


# Key entered from the web page. Memory only — deliberately never written to
# disk, so it is gone the moment the server stops. That is the right default
# for a key someone types into a form in a browser.
_RUNTIME_KEY = {"value": ""}


def set_runtime_key(value: str) -> str:
    """
    Store a key typed into the page. Returns the normalised value stored.

    Rejects anything that clearly is not a key so a paste accident (an empty
    string, a whole curl command) does not silently look like "configured".
    """
    cleaned = normalise_key(value or "")
    _RUNTIME_KEY["value"] = cleaned
    return cleaned


def clear_runtime_key() -> None:
    _RUNTIME_KEY["value"] = ""


def key_status() -> dict:
    """
    What the page is allowed to know. Never returns the key itself.
    """
    source = ""
    if _RUNTIME_KEY["value"]:
        source = "page"
    elif _file_key():
        source = "file"
    elif (os.environ.get("COHERE_API_KEY") or "").strip():
        source = "environment"
    key = read_api_key()
    return {
        "configured": bool(key),
        "source": source,
        # Only ever the last 4 characters: enough to confirm the right key was
        # pasted, useless to anyone who reads the response.
        "hint": ("…" + key[-4:]) if len(key) >= 8 else "",
    }


def normalise_key(raw: str) -> str:
    """
    Accept the several ways a key gets pasted, return just the key.

    Handles: plain key, quoted key, `COHERE_API_KEY=...`, and a whole
    `export COHERE_API_KEY=...` line. Anything else is returned stripped.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    # Only the first non-comment line matters.
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" in line:
            name, _, value = line.partition("=")
            if "KEY" in name.upper():
                line = value.strip()
        return strip_quotes(line.strip())
    return ""


def strip_quotes(value: str) -> str:
    """
    Remove surrounding quotes, including the "smart" ones.

    macOS TextEdit replaces " with curly quotes as you type. A key pasted
    inside those still looks correct on screen but fails auth with a 401,
    because the quote characters travel with it. Straight quotes are not the
    only thing a student will paste.
    """
    pairs = [
        ('"', '"'), ("'", "'"),
        ("\u201c", "\u201d"),   # “ ”
        ("\u2018", "\u2019"),   # ‘ ’
        ("\u00ab", "\u00bb"),   # « »
    ]
    changed = True
    while changed and value:
        changed = False
        for left, right in pairs:
            if len(value) >= 2 and value.startswith(left) and value.endswith(right):
                value = value[len(left):-len(right)].strip()
                changed = True
    return value



def _file_key() -> str:
    """The key from api_key.txt, or ""."""
    try:
        if API_KEY_FILE.is_file():
            return normalise_key(API_KEY_FILE.read_text(encoding="utf-8"))
    except OSError:
        pass
    return ""


def read_api_key() -> str:
    """
    The one place the Cohere key is read.

    Order: the key typed into the page (memory), then api_key.txt, then
    COHERE_API_KEY. The page wins so someone testing can paste a key without
    editing a file or restarting. Returns "" when unset — callers must handle
    that, not crash.
    """
    return (
        _RUNTIME_KEY["value"]
        or _file_key()
        or (os.environ.get("COHERE_API_KEY") or "").strip()
    )



def sniff_audio_ext(data: bytes) -> str:
    """
    Work out the real container from the bytes, not from what the browser said.

    Browsers record WebM or MP4 depending on the platform, and Cohere accepts
    neither: its documented list is flac, mp3, mpeg, mpga, ogg, wav. Renaming
    whatever arrived to ".wav" produced a file Cohere could not decode, which
    surfaced as a confusing failure. Detect, then either convert or say so.
    """
    head = data[:16]
    if head[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return ".wav"
    if head[:4] == b"OggS":
        return ".ogg"
    if head[:4] == b"fLaC":
        return ".flac"
    if head[:4] == b"\x1aE\xdf\xa3":
        return ".webm"          # Chrome / Firefox MediaRecorder default
    if data[4:8] == b"ftyp":
        return ".mp4"           # Safari MediaRecorder default
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return ".mp3"
    return ""


# Cohere's documented accepted list.
COHERE_OK_EXT = (".wav", ".ogg", ".flac", ".mp3", ".mpeg", ".mpga")


def transcribe_audio(data: bytes, filename: str, language: str) -> dict:
    """
    Upload one clip to Cohere and return {"text": ...} or {"error": ...}.

    Never raises: the HTTP handler turns this into a JSON response so the page
    can show a clear message instead of a stack trace.
    """
    if not read_api_key():
        return {
            "error": "no_key",
            "message": (
                "No Cohere API key yet. Paste it in the box at the top of "
                "the page (it is kept in memory only), or put it in "
                "api_key.txt."
            ),
        }
    if not data:
        return {"error": "empty", "message": "No audio received."}
    if len(data) > MAX_AUDIO_BYTES:
        return {
            "error": "too_large",
            "message": f"Clip is {len(data) / 1e6:.1f}MB; the limit is 25MB.",
        }

    ext = sniff_audio_ext(data)
    if not ext:
        return {
            "error": "bad_audio",
            "message": "That did not arrive as a readable audio file.",
        }
    if ext not in COHERE_OK_EXT:
        # WebM/MP4 from MediaRecorder. Converting needs ffmpeg, which this
        # project deliberately does not require, so say exactly what to do.
        return {
            "error": "needs_conversion",
            "message": (
                f"Your browser recorded {ext}, which Cohere does not accept. "
                "Use the key panel route in Chrome/Edge, or convert the clip "
                "to .wav first."
            ),
        }

    tmp = HERE / f".upload_{uuid.uuid4().hex[:8]}{ext}"
    try:
        tmp.write_bytes(data)
        import cohere_transcribe as ct

        # ct.transcribe reads COHERE_API_KEY from the environment, so hand it
        # the value we resolved (file or env) without rewriting that module.
        previous = os.environ.get("COHERE_API_KEY")
        os.environ["COHERE_API_KEY"] = read_api_key()
        try:
            text = ct.transcribe(str(tmp), language)
        finally:
            if previous is None:
                os.environ.pop("COHERE_API_KEY", None)
            else:
                os.environ["COHERE_API_KEY"] = previous
        return {"text": text}
    except Exception as exc:  # noqa: BLE001 - report, never crash the page
        return {"error": "transcribe_failed", "message": str(exc)[:300]}
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def store_session(session: Session) -> None:
    """Append-only history. Append-only is the honest choice: never overwrite."""
    with SESSIONS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(session), ensure_ascii=False) + "\n")


def load_sessions(limit: int = 20) -> List[dict]:
    if not SESSIONS_FILE.exists():
        return []
    rows: Deque[dict] = deque(maxlen=limit)
    for line in SESSIONS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a corrupt line must not take the page down
    return list(rows)


# ---------------------------------------------------------------------------
# HTTP server. No framework: one file, no install step, runs on a MacBook Air.
# ---------------------------------------------------------------------------

def load_llm_config() -> Optional[dict]:
    """
    Read llm_config.json. Returns None when the file is absent or unusable.

    Absent is the normal case: the page then answers from its own
    measurements, and says so. The API key is never read from this file —
    only the NAME of the environment variable that holds it.
    """
    path = HERE / "llm_config.json"
    if not path.is_file():
        return None
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(cfg, dict):
        return None
    if not cfg.get("base_url") or not cfg.get("model"):
        return None
    return cfg


class Handler(BaseHTTPRequestHandler):
    server_version = "PresenceAI/1.0"

    def log_message(self, fmt: str, *args) -> None:  # quieter logs
        print(f"[presence] {self.address_string()} {fmt % args}", flush=True)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._serve_static("index.html", "text/html; charset=utf-8")
        if path.startswith("/static/"):
            name = path.removeprefix("/static/")
            return self._serve_static(name)
        # The page links its assets relatively ("style.css"), so it also works
        # when index.html is opened straight from disk with no server. That
        # means the server must answer those bare names too, not just
        # "/static/...". Without this the page loads unstyled when served.
        if path in ("/style.css", "/app.js", "/app.css"):
            return self._serve_static(path.lstrip("/"))
        if path == "/api/health":
            return self._json(200, {"ok": True, "service": "presence-ai", "version": "1.0"})
        if path == "/api/key":
            return self._json(200, key_status())
        if path == "/api/history":
            return self._json(200, {"sessions": load_sessions()})
        if path == "/api/achievements":
            return self._json(200, achievements(load_sessions(100)))
        if path == "/api/audience":
            params = parse_qs(urlparse(self.path).query)
            lang = params.get("language", ["en"])[0]
            n = params.get("count", ["3"])[0]
            try:
                n_int = int(n)
            except ValueError:
                n_int = 3
            return self._json(200, audience_questions(lang, n_int))
        if path == "/api/compare":
            sessions = load_sessions(200)
            params = parse_qs(urlparse(self.path).query)
            a = params.get("before", [""])[0]
            b = params.get("after", [""])[0]
            if a and b:
                by_id = {s.get("session_id"): s for s in sessions}
                first, second = by_id.get(a), by_id.get(b)
            else:
                first = sessions[-2] if len(sessions) >= 2 else None
                second = sessions[-1] if sessions else None
            if not first or not second:
                return self._json(200, {
                    "rows": [],
                    "summary": "",
                    "note": "Record two sessions and the comparison appears here.",
                })
            return self._json(200, compare_sessions(first, second))
        if path == "/api/roadmap":
            parsed = urlparse(self.path)
            deadline = (parse_qs(parsed.query).get("deadline", [""])[0] or None)
            return self._json(200, training_roadmap(load_sessions(100), deadline))
        if path == "/api/spec":
            return self._json(200, {
                "wpm_target": [WPM_TARGET_LOW, WPM_TARGET_HIGH],
                "b2_baseline": WPM_B2_BASELINE,
                "long_pause_seconds": LONG_PAUSE_SECONDS,
                "nudge_after_seconds": NUDGE_AFTER_SECONDS,
                "nudge_repeat_seconds": NUDGE_REPEAT_SECONDS,
                "nudges": [n["text"] for n in NUDGES],
                "fillers": FILLERS_EN + FILLERS_AR_TRANSLITERATED + FILLERS_AR_SCRIPT,
                "languages": [
                    {
                        "code": code,
                        "label": entry["label"],
                        "script": entry["script"],
                        "filler_count": len(entry["fillers"]),
                    }
                    for code, entry in LANGUAGES.items()
                ],
                "note": "Measurements only. Coaching is done by the advisor prompt.",
            })
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/llm":
            return self._handle_llm()
        if path == "/api/chat":
            return self._handle_chat()
        if path == "/api/key":
            return self._handle_set_key()
        if path == "/api/transcribe":
            return self._handle_transcribe()
        if path != "/api/session":
            return self._json(404, {"error": "not found"})

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            return self._json(413, {"error": "payload too large or empty"})
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._json(400, {"error": "invalid json"})

        session = build_session(payload)
        store_session(session)
        return self._json(200, report(session))

    def _handle_llm(self) -> None:
        """
        Optional LLM chat endpoint.

        This is the "API place for the LLM" — one place to plug in any provider
        so the whole application can be exercised, not just transcription.

        Configuration lives in llm_config.json next to this file:
            {
              "provider": "openai",          // openai | ollama | custom
              "base_url": "https://api.openai.com/v1",
              "model": "gpt-4o-mini",
              "api_key_env": "LLM_API_KEY"   // env var to read, never a literal
            }

        The key is read from the environment, never from the JSON file and
        never from the browser, so a shared config cannot leak it.

        Shape follows the OpenAI chat-completions schema, which Ollama and most
        local servers also speak — so "custom" needs no extra code.
        """
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 50_000:
            return self._json(400, {"error": "empty"})
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._json(400, {"error": "invalid json"})

        question = str(payload.get("question") or "").strip()[:2000]
        if not question:
            return self._json(400, {"error": "empty question"})

        cfg = load_llm_config()
        if not cfg:
            # Not configured is a normal state, not an error: the page answers
            # locally instead. 503 lets it do that cleanly.
            return self._json(503, {
                "error": "llm_not_configured",
                "message": "Add llm_config.json to enable AI answers.",
            })

        system = (
            "You are PRESENCE AI, a pacing coach for Arab school students "
            "presenting in English. Answer in 2-4 short sentences. Only use "
            "the numbers given to you. Never invent a measurement: if a number "
            "is missing, say it was not measured."
        )
        report = payload.get("report")
        context = ""
        if isinstance(report, dict) and isinstance(report.get("tools"), dict):
            context = "Measured session data: " + json.dumps(report["tools"])[:2000]

        body = {
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": (context + "\n\nQuestion: " + question).strip()},
            ],
            "temperature": 0.4,
            "max_tokens": 300,
        }

        url = cfg["base_url"].rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(cfg.get("api_key_env") or "LLM_API_KEY", "").strip()
        if key:
            headers["Authorization"] = "Bearer " + key

        try:
            import urllib.request as _ur
            req = _ur.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                method="POST",
                headers=headers,
            )
            with _ur.urlopen(req, timeout=cfg.get("timeout", 25)) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            answer = ""
            choices = data.get("choices") or []
            if choices:
                answer = (choices[0].get("message") or {}).get("content", "")
            answer = (answer or "").strip()
            if not answer:
                return self._json(502, {"error": "empty_completion"})
            return self._json(200, {"answer": answer, "provider": cfg.get("provider", "custom")})
        except Exception as exc:  # noqa: BLE001 - report, never crash the page
            return self._json(502, {"error": "llm_request_failed", "message": str(exc)[:200]})

    def _handle_chat(self) -> None:
        """
        One coaching question, answered by the Okal agent if it is awake.

        This never waits: the agent answers out of band, and a browser cannot
        block on a task being scheduled. So the honest contract is
          * answer present  -> card answered by the advisor
          * no answer yet   -> 503, and the page answers from its own
                              measurements instead
        The page must not be left spinning because an agent run is asleep.
        """
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 20_000:
            return self._json(400, {"error": "empty"})
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._json(400, {"error": "invalid json"})

        question = str(payload.get("question") or "").strip()[:1000]
        if not question:
            return self._json(400, {"error": "empty question"})

        try:
            import coach_bridge
        except ImportError:
            return self._json(503, {"error": "bridge_unavailable"})

        # A session token, so the browser gets exactly its own answer back.
        token = str(payload.get("token") or "").strip()
        if token:
            try:
                result = coach_bridge.fetch(token)
            except Exception:
                result = None
            if result and result.get("status") == "answered":
                return self._json(200, {"answer": result.get("advice", "")})
            return self._json(503, {"error": "no_answer_yet"})

        # No token yet: queue the question and hand back the token to poll.
        try:
            measurement = str(payload.get("measurement") or question)
            new_token = coach_bridge.enqueue(measurement, question)
        except Exception:
            return self._json(503, {"error": "queue_failed"})
        return self._json(202, {"token": new_token, "status": "pending"})

    def _handle_set_key(self) -> None:
        """
        Accept a key typed into the page. Memory only, never written to disk.

        A DELETE-style empty body clears it, so the page can offer "forget".
        """
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 10_000:
            clear_runtime_key()
            return self._json(200, key_status())
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._json(400, {"error": "invalid json"})

        raw = str(payload.get("key") or "")
        if not raw.strip():
            clear_runtime_key()
            return self._json(200, key_status())

        cleaned = set_runtime_key(raw)
        if len(cleaned) < 8:
            clear_runtime_key()
            return self._json(400, {
                "error": "bad_key",
                "message": "That does not look like a key. Paste the whole key.",
            })
        return self._json(200, key_status())

    def _handle_transcribe(self) -> None:
        """
        Raw audio in, transcript out.

        The browser sends audio/wav bytes directly rather than multipart, so
        this stays stdlib-only and there is exactly one place (transcribe_audio)
        that talks to Cohere.

        Audio NEVER stays on disk: transcribe_audio writes a temp file, sends
        it, and deletes it in a finally block.
        """
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_AUDIO_BYTES:
            return self._json(413, {
                "error": "too_large",
                "message": "Empty clip, or larger than the 25MB limit.",
            })
        try:
            data = self.rfile.read(length)
        except OSError:
            return self._json(400, {"error": "read_failed"})

        language = (
            urlparse(self.path).query
            and parse_qs(urlparse(self.path).query).get("language", ["en"])[0]
        ) or "en"

        result = transcribe_audio(data, "recording.wav", language)
        return self._json(200 if "text" in result else 422, result)

    def _serve_static(self, name: str, ctype: Optional[str] = None) -> None:
        # Reject traversal explicitly; a school project is still a server.
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return self._json(404, {"error": "not found"})
        target = (STATIC / name).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            return self._json(404, {"error": "not found"})
        types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }
        ctype = ctype or types.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)


def main() -> None:
    port = int(os.environ.get("PORT") or 8080)
    host = os.environ.get("HOST") or "0.0.0.0"
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[presence] PRESENCE AI measuring tool on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
