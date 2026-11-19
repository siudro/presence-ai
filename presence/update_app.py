#!/usr/bin/env python3
"""
update_app.py — pull the latest version without losing your key.

    python3 update_app.py

Use this instead of typing git commands. It:

  1. Confirms you are in a git checkout (a downloaded zip is not one).
  2. Saves a copy of your api_key.txt outside the repo.
  3. Runs `git pull`.
  4. Restores api_key.txt if the pull somehow removed it.

The key file is gitignored, so a normal pull never touches it — this exists so
that even if something odd happens, the key is not lost.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEY = HERE / "api_key.txt"
BACKUP = HERE.parent / ".api_key.backup"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def main() -> int:
    print()
    print(f"{BOLD}PRESENCE AI — update{RESET}")

    # 1. Is this a git checkout?
    code, _ = run(["git", "rev-parse", "--is-inside-work-tree"])
    if code != 0:
        print(f"{RED}This folder is not a git checkout.{RESET}")
        print()
        print("You are running a downloaded zip, which cannot be updated this way.")
        print("Either:")
        print("  - keep using the zip the assistant sends, or")
        print("  - clone once with:  git clone https://github.com/siudro/presence-ai.git")
        print()
        return 1

    # 2. Back up the key BEFORE touching anything.
    had_key = KEY.exists()
    if had_key:
        shutil.copyfile(KEY, BACKUP)
        print(f"{DIM}Key backed up.{RESET}")

    # 3. Pull.
    print(f"{DIM}Pulling from GitHub…{RESET}")
    code, output = run(["git", "pull", "--ff-only"])
    print(output or f"{DIM}(already up to date){RESET}")

    # 4. Restore the key if it vanished.
    if had_key and not KEY.exists():
        shutil.copyfile(BACKUP, KEY)
        print(f"{YELLOW}Key restored after the pull.{RESET}")

    if BACKUP.exists():
        BACKUP.unlink()

    if code != 0:
        print()
        print(f"{RED}The pull did not complete.{RESET}")
        if "Authentication" in output or "could not read" in output:
            print()
            print("GitHub needs a token, not your account password.")
            print("Create one at https://github.com/settings/tokens (tick 'repo'),")
            print("then use it as the password when asked.")
        elif "diverged" in output or "not possible to fast-forward" in output:
            print()
            print("Your local copy has changes. Keep them by running:")
            print("  git stash && python3 update_app.py && git stash pop")
        return 1

    # Confirm the key survived.
    print()
    if KEY.exists():
        try:
            import app  # noqa: PLC0415 - import after the pull, so it is current
            ok = bool(app.read_api_key())
        except Exception:
            ok = False
        print(f"{GREEN}Updated.{RESET} Your key is still saved." if ok
              else f"{YELLOW}Updated, but no key found — paste it again in Settings.{RESET}")
    else:
        print(f"{YELLOW}Updated. No key file yet — paste your key in Settings.{RESET}")

    print()
    print(f"{DIM}Next: python3 run_all_tests.py   then   python3 app.py{RESET}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
