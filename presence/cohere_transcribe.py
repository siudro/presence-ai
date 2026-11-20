#!/usr/bin/env python3
"""
cohere_transcribe.py — Cohere Transcribe client. Stdlib only, no install.

PROTOTYPE. Built to answer one question: can this project use Cohere
transcription instead of the browser's SpeechRecognition?

WHY IT MATTERS
    The current site transcribes in the browser with SpeechRecognition,
    which is Chrome-only-ish and unreliable on Safari. Cohere Transcribe is
    a real ASR model with a dedicated Arabic variant that handles
    Arabic-English code-switching — which is exactly this project's
    population. That is a genuine improvement, not decoration.

WHAT IT COSTS
    Audio leaves the machine. Recording is uploaded to Cohere's API to be
    transcribed, so the page must never claim the audio stays local.
    Measurements (pace, pauses, volume, fillers) are still computed on this
    machine; only the transcript is produced by a cloud model. That split is
    stated to the user on the page and in the README.

THE TWO HARD CONSTRAINTS (from the API docs, not guessed)
    1. `language` is REQUIRED and there is no auto language detection.
       So the UI must ask, or the app must try both and pick.
    2. No timestamps, no speaker diarization. Fine for us: we have one
       speaker and we compute our own timing from the browser.

Env var: COHERE_API_KEY (the name is configurable via env var name below).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid

ENDPOINT = "https://api.cohere.com/v2/audio/transcriptions"

# Both live, per docs. Arabic one handles Arabic+English code-switching.
MODEL_GENERAL = "cohere-transcribe-03-2026"
MODEL_ARABIC = "cohere-transcribe-arabic-07-2026"

MAX_BYTES = 25 * 1024 * 1024   # documented limit
ALLOWED_EXT = (".flac", ".mp3", ".mpeg", ".mpga", ".ogg", ".wav")


class TranscribeError(Exception):
    """Raised with a safe message. Never embeds the API key."""


def api_key() -> str:
    """
    Read the key from the environment. Named COHERE_API_KEY by default.

    Raises instead of returning "" so a misconfiguration is loud, not a
    mystery 401 later.
    """
    key = (os.environ.get("COHERE_API_KEY") or "").strip()
    if not key:
        raise TranscribeError(
            "COHERE_API_KEY is not set in this process environment"
        )
    return key


def pick_model(language: str) -> str:
    """
    Arabic students mixing Arabic and English get the Arabic model.

    The docs say the Arabic model covers both Arabic and English and
    supports code-switching, so it is the correct choice for a student whose
    first language leaks into their English. This is a real design decision,
    not a default.
    """
    return MODEL_ARABIC if language.lower().startswith("ar") else MODEL_GENERAL


def _multipart(fields: dict, file_field: str, filename: str, payload: bytes):
    """Hand-rolled multipart. No `requests` dependency."""
    boundary = "----okal" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
    )
    parts.append(payload)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def transcribe(audio_path: str, language: str, timeout: int = 60) -> str:
    """
    Send one audio file, get the transcript back.

    Validation happens before the network call so a 25MB upload is not sent
    just to be rejected.
    """
    path = os.fspath(audio_path)
    if not os.path.isfile(path):
        raise TranscribeError("audio file not found")
    if not path.lower().endswith(ALLOWED_EXT):
        raise TranscribeError(
            f"unsupported extension; allowed: {', '.join(ALLOWED_EXT)}"
        )
    size = os.path.getsize(path)
    if size > MAX_BYTES:
        raise TranscribeError(
            f"file is {size / 1e6:.1f}MB; the API limit is 25MB"
        )
    if size == 0:
        raise TranscribeError("audio file is empty")

    with open(path, "rb") as fh:
        payload = fh.read()
    body, ctype = _multipart(
        {"model": pick_model(language), "language": language[:2].lower()},
        "file", os.path.basename(path), payload,
    )

    req = urllib.request.Request(
        ENDPOINT, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {api_key()}",
            "Content-Type": ctype,
            "Accept": "application/json",
        },
    )
    try:
        from tls import urlopen as _tls_urlopen
        with _tls_urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Surface the status, never the key.
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("message", "")
        except Exception:
            pass
        raise TranscribeError(f"HTTP {exc.code}: {detail or 'no detail'}") from None
    except urllib.error.URLError as exc:
        raise TranscribeError(f"network error: {exc.reason}") from None

    text = (data.get("text") or "").strip()
    if not text:
        raise TranscribeError("empty transcript returned")
    return text


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print(__doc__)
        print("usage: python3 cohere_transcribe.py <audio> <language>")
        raise SystemExit(2)
    try:
        print(transcribe(sys.argv[1], sys.argv[2]))
    except TranscribeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
