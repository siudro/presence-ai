#!/usr/bin/env python3
"""
check_connections.py — does each service actually connect?

Run this in Terminal:

    python3 check_connections.py

It makes REAL requests to each service and prints PASS / FAIL for every one,
so the answer is a measurement rather than a guess. It never prints a key —
only the last four characters, so you can confirm the right key is loaded.

WHAT IT CHECKS
  1. Server files present and importable
  2. Cohere key present, and accepted by Cohere
  3. Cohere Arabic model reachable
  4. Gemini key present, and accepted by Google
  5. Optional: LLM config file valid

Every check is independent: one failure does not stop the others, so a single
run shows the whole picture.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

results = []


def record(name: str, ok: bool | None, detail: str = "") -> None:
    results.append((name, ok, detail))
    if ok is True:
        print(f"  {GREEN}PASS{RESET}  {name}")
    elif ok is False:
        print(f"  {RED}FAIL{RESET}  {name}")
    else:
        print(f"  {YELLOW}SKIP{RESET}  {name}")
    if detail:
        print(f"        {DIM}{detail}{RESET}")


def hint(key: str) -> str:
    """Last four characters only — never the whole key."""
    return ("…" + key[-4:]) if len(key) >= 8 else "(too short)"


# ---------------------------------------------------------------- 1. files

def check_files() -> None:
    needed = ["app.py", "cohere_transcribe.py", "static/index.html",
              "static/app.css", "static/app.js"]
    missing = [f for f in needed if not (HERE / f).exists()]
    record("Project files present", not missing,
           "" if not missing else "missing: " + ", ".join(missing))

    try:
        import app  # noqa: F401
        record("app.py imports (measurement core loads)", True)
    except Exception as exc:  # noqa: BLE001
        record("app.py imports (measurement core loads)", False, str(exc)[:160])


# ---------------------------------------------------------------- 2. Cohere

def cohere_key() -> str:
    try:
        import app
        return app.read_api_key()
    except Exception:
        return (os.environ.get("COHERE_API_KEY") or "").strip()


def check_cohere() -> None:
    key = cohere_key()
    if not key:
        record("Cohere key present", False,
               "no key. Paste one in the app's Settings, or put it in api_key.txt")
        record("Cohere API accepts the key", None, "skipped — no key")
        return

    record("Cohere key present", True, f"loaded {hint(key)}")

    req = urllib.request.Request(
        "https://api.cohere.com/v1/models",
        headers={"Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        models = [m.get("name", "") for m in data.get("models", [])]
        arabic = any("arabic" in m for m in models)
        record("Cohere API accepts the key", True, f"HTTP {resp.status}, {len(models)} models")
        record("Arabic transcription model available", arabic,
               "cohere-transcribe-arabic found" if arabic
               else "not listed for this key — Arabic audio will use the general model")
    except urllib.error.HTTPError as exc:
        body = exc.read()[:200].decode(errors="replace")
        record("Cohere API accepts the key", False, f"HTTP {exc.code}: {body}")
    except Exception as exc:  # noqa: BLE001
        record("Cohere API accepts the key", False, f"network error: {exc}")


# ---------------------------------------------------------------- 3. Gemini

def gemini_key() -> str:
    return (os.environ.get("GEMINI_API_KEY") or "").strip()


def check_gemini() -> None:
    key = gemini_key()
    if not key:
        record("Gemini key present", None,
               "not set — optional. export GEMINI_API_KEY=\"...\" to enable AI chat answers")
        return

    record("Gemini key present", True, f"loaded {hint(key)}")

    body = json.dumps({
        "model": "gemini-4-hydrogen",
        "messages": [{"role": "user", "content": "Reply with the single word: connected"}],
        # Thinking tokens count against max_tokens on Gemini: a budget of 12
        # was always spent thinking and came back empty.
        "max_tokens": 2048 + 12,
        "reasoning_effort": "low",
    }).encode()
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        if text.strip():
            record("Gemini answers a real question", True,
                   f"reply: {text.strip()[:60]!r}")
        else:
            record("Gemini answers a real question", False,
                   "the key works but the reply was EMPTY (finish_reason="
                   f"{choice.get('finish_reason')!r}) - the model spent its token budget thinking")
    except urllib.error.HTTPError as exc:
        body_text = exc.read()[:220].decode(errors="replace")
        record("Gemini answers a real question", False, f"HTTP {exc.code}: {body_text}")
    except Exception as exc:  # noqa: BLE001
        record("Gemini answers a real question", False, f"network error: {exc}")


# ---------------------------------------------------------------- 4. LLM config

def check_llm_config() -> None:
    path = HERE / "llm_config.json"
    if not path.is_file():
        record("llm_config.json present", None,
               "not created — the chat falls back to built-in answers, which is fine")
        return
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        ok = bool(cfg.get("base_url") and cfg.get("model"))
        record("llm_config.json valid", ok,
               f"provider={cfg.get('provider')} model={cfg.get('model')}")
        env_name = cfg.get("api_key_env") or "LLM_API_KEY"
        present = bool((os.environ.get(env_name) or "").strip())
        record(f"Key for llm_config ({env_name}) present", present,
               "set" if present else f"export {env_name}=\"...\" in this Terminal")
    except (json.JSONDecodeError, OSError) as exc:
        record("llm_config.json valid", False, str(exc)[:160])


# ---------------------------------------------------------------- 5. server

def check_server() -> None:
    """Is the app itself already running on 8080?"""
    try:
        with urllib.request.urlopen("http://localhost:8080/api/health", timeout=4) as resp:
            data = json.loads(resp.read().decode())
        record("App server running on :8080", True,
               f"build {data.get('build', 'unknown')}")
    except Exception:
        record("App server running on :8080", None,
               "not running — start it with: python3 app.py")


# ---------------------------------------------------------------- main

def main() -> int:
    print()
    print(f"{BOLD}PRESENCE AI — connection check{RESET}")
    print(f"{DIM}Real requests to each service. No keys are printed.{RESET}")
    print()

    print(f"{BOLD}1. Project files{RESET}")
    check_files()
    print()

    print(f"{BOLD}2. Cohere — speech to text{RESET}")
    check_cohere()
    print()

    print(f"{BOLD}3. Gemini — AI chat (optional){RESET}")
    check_gemini()
    print()

    print(f"{BOLD}4. Chat configuration{RESET}")
    check_llm_config()
    print()

    print(f"{BOLD}5. Local server{RESET}")
    check_server()
    print()

    passed = sum(1 for _, ok, _ in results if ok is True)
    failed = sum(1 for _, ok, _ in results if ok is False)
    skipped = sum(1 for _, ok, _ in results if ok is None)

    print("-" * 62)
    print(f"{BOLD}Summary:{RESET} {GREEN}{passed} passed{RESET}, "
          f"{RED}{failed} failed{RESET}, {YELLOW}{skipped} not configured{RESET}")
    print()

    if failed:
        print(f"{RED}Something needs attention — see the FAIL lines above.{RESET}")
        return 1

    if passed:
        print(f"{GREEN}Every configured service connected.{RESET}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
