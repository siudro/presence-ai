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

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_URL = "https://github.com/siudro/presence-ai.git"
KEY = HERE / "api_key.txt"
BACKUP = HERE.parent / ".api_key.backup"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def run(cmd: list[str]) -> tuple[int, str]:
    """
    Run a git command in this folder with a usable PATH.

    A restricted PATH (a launcher, an IDE terminal, `PATH=/usr/bin:/bin`) hides
    the `okal` shim that provides the GitHub credential. git then cannot
    authenticate and sits waiting for a username at a prompt nobody can see,
    so every fetch and pull fails with "could not read Username". Inheriting
    the parent PATH and appending the usual tool locations fixes that without
    requiring the student to change anything.
    """
    env = dict(os.environ)
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    for extra in ("/usr/local/bin", "/opt/homebrew/bin", str(Path.home() / ".local/bin")):
        if extra not in parts:
            parts.append(extra)
    env["PATH"] = os.pathsep.join(parts)
    # Never let git block on an interactive credential prompt.
    env.setdefault("GIT_TERMINAL_PROMPT", "0")

    proc = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, env=env)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def adopt_zip_folder() -> bool:
    """
    Turn a zip-extracted folder into a real git checkout, without moving it.

    Attempting `git init` inside the folder is layout-dependent: the published
    repository keeps its code in a `presence/` subfolder, so initialising at
    the wrong level nests the tree one deep. Cloning into a scratch directory
    and copying the result over this folder is layout-independent and keeps
    the key, which is gitignored on both sides.
    """
    scratch = HERE.parent / ".presence_update"
    try:
        if scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)
        env_path = os.environ.get("PATH", "")
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join(
            [p for p in env_path.split(os.pathsep) if p]
            + ["/usr/local/bin", "/opt/homebrew/bin", str(Path.home() / ".local/bin")]
        )
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        proc = subprocess.run(
            ["git", "clone", "--depth=1", "--branch", "main", REPO_URL, str(scratch)],
            capture_output=True, text=True, env=env,
        )
        if proc.returncode != 0:
            return False

        # Copy the published tree over this folder, leaving the key in place.
        keep = {}
        for name in ("api_key.txt", "llm_api_key.txt"):
            f = HERE / name
            if f.is_file():
                keep[name] = f.read_bytes()

        # The published tree nests its code under `presence/`. If that is the
        # only real folder in the clone, descend into it so the student's
        # folder holds the app directly instead of one `presence/` too deep —
        # the nesting that made earlier copies impossible to update.
        source = scratch
        inner = scratch / "presence"
        if inner.is_dir():
            source = inner

        for item in source.iterdir():
            if item.name == ".git":
                continue
            dst = HERE / item.name
            if item.is_dir():
                if dst.exists():
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(item, dst)
            else:
                shutil.copyfile(item, dst)
        # The .git directory makes this folder a checkout, so later updates
        # are a plain pull.
        git_src = scratch / ".git"
        git_dst = HERE / ".git"
        if git_dst.exists():
            shutil.rmtree(git_dst, ignore_errors=True)
        if git_src.exists():
            shutil.copytree(git_src, git_dst)

        for name, data in keep.items():
            (HERE / name).write_bytes(data)

        # The copied clone must track main, or the next update has no upstream.
        return True
    except Exception:
        return False
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    print()
    print(f"{BOLD}PRESENCE AI — update{RESET}")

    # 1. Is this a git checkout? A downloaded zip is not, and could never
    # update itself — which is why the same folder stayed stale turn after
    # turn. Rather than turn the student away, adopt the folder into the
    # repository in place. The key file is gitignored, so it survives.
    code, _ = run(["git", "rev-parse", "--is-inside-work-tree"])
    if code != 0:
        print(f"{YELLOW}This folder came from a zip, so it cannot update itself yet.{RESET}")
        print(f"{DIM}Converting it in place — your files and key stay where they are.{RESET}")
        print()
        adopted = adopt_zip_folder()
        if not adopted:
            print(f"{RED}Could not convert this folder automatically.{RESET}")
            print()
            print("Run these three lines instead:")
            print(f"  {BOLD}cd .. && git clone {REPO_URL}{RESET}")
            print(f"  {BOLD}cd presence/presence && python3 app.py{RESET}")
            print()
            return 1
        print(f"{GREEN}Converted. Continuing with the update.{RESET}")
        print()

    # 2. Back up the key BEFORE touching anything.
    had_key = KEY.exists()
    if had_key:
        shutil.copyfile(KEY, BACKUP)
        print(f"{DIM}Key backed up.{RESET}")

    # 3. Pull.
    print(f"{DIM}Pulling from GitHub…{RESET}")
    code, output = run(["git", "pull", "-q", "--ff-only"])
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
        # Read the file rather than importing app: after a pull the imported
        # module is the pre-pull one, and a stale API_KEY_FILE path made a
        # present key look missing.
        try:
            ok = bool(KEY.is_file() and KEY.read_text(encoding="utf-8").strip())
        except OSError:
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
