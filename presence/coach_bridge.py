#!/usr/bin/env python3
"""
coach_bridge.py — a PROTOTYPE of the agentic backend, stdlib only.

PROTOTYPE STATUS. This file exists to answer one question from Dana:
can the Presence AI agent do the LLM work for the website without an API key?
It is a proof of concept, not a production service.

THE SHAPE
    browser  ->  POST /api/coach  ->  job queued on disk      (this file)
                                      |
                          outbound poll (agent side)          |
                                      v
                              agent writes an answer          (this file)
                                      ^
    browser  <-  GET /api/coach/<token>  <-  answer delivered

Nothing on the web server holds an LLM credential. The credential lives with
the agent. That is the whole reason the pattern is worth having.

THE THREE SAFETY PROPERTIES THAT MAKE IT SAFE TO EXPOSE
    1. NO INBOUND COMMAND PATH. The agent polls out. The internet cannot
       post a prompt into the agent; it can only queue a job, and the job
       body is data, never instructions.
    2. TOKEN-GATED. A job has an unguessable id. Knowing the id is the only
       way to read the answer. No listing endpoint, no enumeration.
    3. FIXED CONTRACT. The model may only return advice text plus a small
       enum of next-actions. It cannot call tools or reach the network from
       here, so a prompt injection cannot escalate into an action.

Run the demo:
    python3 coach_bridge.py demo
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
QUEUE_DIR = HERE / "coach_queue"

# --- Limits. Every one of these is a guardrail, not a preference. -----------
MAX_PROMPT_CHARS = 4000          # what a browser may submit
JOB_TTL_SECONDS = 30 * 60        # an unanswered job dies after 30 min
MAX_ANSWER_CHARS = 1200          # what the agent may return
MAX_JOBS_PER_HOUR = 20           # per-session abuse ceiling

# The only next-actions the agent may choose. Free text is impossible here,
# so a model that hallucinates "next_action": "refund" cannot invent an action.
ALLOWED_NEXT_ACTIONS = ("retry", "slow_down", "add_pauses", "cut_fillers", "stop")

# Words the coach may never emit. This mirrors the README honesty rule:
# browser audio measures signal, not emotion.
FORBIDDEN_WORDS = (
    "confident", "confidence level", "anxious", "nervous", "happy", "sad",
    "you are depressed", "diagnosed", "disorder",
)


class BridgeError(Exception):
    """Raised for a rejected request. Never contains user content."""


# ---------------------------------------------------------------------------
# Job storage. A file per job, named by its token. No index, by design:
# without the token there is nothing to look up.
# ---------------------------------------------------------------------------

@dataclass
class Job:
    token: str
    created_at: float
    status: str = "queued"          # queued | answered | expired
    measurement: str = ""           # the numbers, already computed by app.py
    context: str = ""              # optional student profile / question
    answer: str = ""
    next_action: str = ""
    answered_at: float = 0.0


def _job_path(token: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token or ""):
        raise BridgeError("bad token")
    return QUEUE_DIR / f"{token}.json"


def _write_job(job: Job) -> None:
    QUEUE_DIR.mkdir(exist_ok=True)
    path = _job_path(job.token)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(job), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a poller never sees a half-written job


def _read_job(token: str) -> Optional[Job]:
    try:
        path = _job_path(token)
    except BridgeError:
        return None
    if not path.is_file():
        return None
    try:
        return Job(**json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError):
        return None


def enqueue(measurement: str, context: str = "") -> str:
    """
    Queue one coaching request. Returns the token the browser keeps.

    This is the ONLY function the public web server calls.
    """
    measurement = str(measurement or "").strip()
    if not measurement:
        raise BridgeError("empty measurement")
    if len(measurement) > MAX_PROMPT_CHARS:
        raise BridgeError("measurement too long")

    cleanup()

    token = secrets.token_urlsafe(32)
    _write_job(Job(
        token=token,
        created_at=time.time(),
        measurement=measurement[:MAX_PROMPT_CHARS],
        context=str(context or "")[:500],
    ))
    return token


def fetch(token: str) -> Optional[dict]:
    """
    What the browser is allowed to see.

    Returns None for an unknown or expired token — the same answer for both,
    so the endpoint cannot be used to tell "wrong token" from "gone token".
    """
    job = _read_job(token)
    if job is None:
        return None
    if job.status == "queued" and time.time() - job.created_at > JOB_TTL_SECONDS:
        return None
    if job.status == "queued":
        return {"status": "pending"}
    return {"status": "answered", "advice": job.answer,
            "next_action": job.next_action}


def pending(limit: int = 5) -> List[Job]:
    """What the AGENT polls. Never exposed by the web server."""
    cleanup()
    if not QUEUE_DIR.is_dir():
        return []
    jobs: List[Job] = []
    for path in sorted(QUEUE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            job = Job(**json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            continue
        if job.status == "queued":
            jobs.append(job)
        if len(jobs) >= limit:
            break
    return jobs


def answer(token: str, advice: str, next_action: str = "retry") -> None:
    """
    Complete a job. The text is validated here, not trusted.

    If the agent returns something outside the contract, the write is refused
    rather than delivered — a bad answer is worse than a pending one.
    """
    job = _read_job(token)
    if job is None:
        raise BridgeError("unknown job")
    if job.status != "queued":
        raise BridgeError("job already answered")

    advice = str(advice or "").strip()
    if not advice:
        raise BridgeError("empty advice")
    if len(advice) > MAX_ANSWER_CHARS:
        advice = advice[:MAX_ANSWER_CHARS].rsplit(" ", 1)[0] + "…"

    lowered = advice.lower()
    for word in FORBIDDEN_WORDS:
        if word in lowered:
            raise BridgeError(f"refused: forbidden wording '{word}'")
    if re.search(r"https?://", lowered):
        raise BridgeError("refused: links are not allowed in advice")

    if next_action not in ALLOWED_NEXT_ACTIONS:
        raise BridgeError(f"refused: next_action '{next_action}' not allowed")

    job.status = "answered"
    job.answer = advice
    job.next_action = next_action
    job.answered_at = time.time()
    _write_job(job)


def cleanup() -> int:
    """Expire old jobs. Called on every enqueue, so the queue cannot grow."""
    if not QUEUE_DIR.is_dir():
        return 0
    now = time.time()
    expired = 0
    for path in QUEUE_DIR.glob("*.json"):
        try:
            if now - path.stat().st_mtime > JOB_TTL_SECONDS * 2:
                path.unlink()
                expired += 1
        except OSError:
            continue
    return expired


def rate_limited(session_key: str, window: List[float]) -> bool:
    """
    Keep the last MAX_JOBS_PER_HOUR timestamps for a session. Caller owns the
    list so this stays pure and testable.
    """
    now = time.time()
    window[:] = [t for t in window if now - t < 3600]
    if len(window) >= MAX_JOBS_PER_HOUR:
        return True
    window.append(now)
    return False


# ---------------------------------------------------------------------------
# Demo — end to end, no network, no model. Proves the plumbing only.
# ---------------------------------------------------------------------------

def demo() -> None:
    print("1. browser queues a job")
    token = enqueue(
        measurement="142 wpm, 6 fillers in 60s, 2 freezes (longest 24s), "
                    "avg volume -31 dBFS, pitch variance low",
        context="profile B: translates from Arabic before speaking",
    )
    print(f"   token: {token[:12]}…")
    print(f"   browser sees: {fetch(token)}")

    print("2. agent polls (this is the only reader of pending())")
    jobs = pending()
    print(f"   pending jobs: {len(jobs)}")
    print(f"   agent reads numbers: {jobs[0].measurement[:48]}…")

    print("3. agent tries to return an emotion label -> refused")
    try:
        answer(token, "You sounded anxious and nervous.", "retry")
    except BridgeError as exc:
        print(f"   refused: {exc}")

    print("4. agent tries to invent an action -> refused")
    try:
        answer(token, "Pause after each sentence.", "call_the_teacher")
    except BridgeError as exc:
        print(f"   refused: {exc}")

    print("5. agent returns a valid answer")
    answer(
        token,
        "Your fillers cluster where you translate from Arabic. Say the "
        "sentence once in Arabic, then once in English before you start — "
        "the filler is the sound of the translation happening.",
        "cut_fillers",
    )
    print(f"   browser sees: {fetch(token)}")

    print("6. wrong token -> same answer as expired")
    print(f"   fetch('deadbeef'*4) = {fetch('deadbeefdeadbeefdeadbeefdeadbeef')}")
    print(f"   fetch('_../etc/passwd_') = {fetch('../../etc/passwd')}")

    cleanup()
    for path in QUEUE_DIR.glob("*.json"):
        path.unlink()
    QUEUE_DIR.rmdir() if QUEUE_DIR.is_dir() and not any(QUEUE_DIR.iterdir()) else None
    print("demo complete — queue cleaned")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        demo()
    else:
        print(__doc__)
