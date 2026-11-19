#!/usr/bin/env python3
"""
run_all_tests.py — run every test and show the results in Terminal.

    python3 run_all_tests.py          # summary: which groups passed
    python3 run_all_tests.py -v       # every individual test name

The plain `python3 test_app.py` prints only "OK", which does not tell you what
was tested. This runs both suites and prints a per-group and per-test view, so
the terminal output is evidence you can screenshot for the PDF.
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


class GroupReporter(unittest.TextTestResult):
    """Records pass/fail per test class so the summary can group them."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.groups: dict[str, dict[str, int]] = {}
        self.failures_named: list[str] = []

    def _group(self, test) -> dict[str, int]:
        name = test.__class__.__name__
        return self.groups.setdefault(name, {"ok": 0, "bad": 0, "skip": 0})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._group(test)["ok"] += 1

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._group(test)["bad"] += 1
        self.failures_named.append(f"{test.__class__.__name__}.{test._testMethodName}")

    def addError(self, test, err):
        super().addError(test, err)
        self._group(test)["bad"] += 1
        self.failures_named.append(f"{test.__class__.__name__}.{test._testMethodName} (error)")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        # A skip is a real outcome, not a pass: it means an optional tool is
        # absent. Counting it as "ok" would overstate what was verified.
        self._group(test)["skip"] = self._group(test).get("skip", 0) + 1


def run_module(module_name: str, verbose: bool) -> tuple[GroupReporter, float]:
    started = time.time()
    loader = unittest.TestLoader()
    try:
        module = __import__(module_name)
    except Exception as exc:  # noqa: BLE001
        print(f"{RED}Could not import {module_name}: {exc}{RESET}")
        # Build a result object without running anything, so the caller can
        # still print a summary instead of crashing on a broken module.
        stream = open(os.devnull, "w")
        reporter = GroupReporter(stream, descriptions=False, verbosity=0)
        reporter.failures_named.append(f"{module_name} (import failed)")
        return reporter, 0.0

    suite = loader.loadTestsFromModule(module)
    runner = unittest.TextTestRunner(
        verbosity=2 if verbose else 1,
        resultclass=GroupReporter,
        stream=sys.stdout,
        buffer=False,
    )
    result = runner.run(suite)
    return result, time.time() - started


def main() -> int:
    verbose = "-v" in sys.argv or "--verbose" in sys.argv

    print()
    print(f"{BOLD}PRESENCE AI — full test run{RESET}")
    if not verbose:
        print(f"{DIM}Add -v to see every test name.{RESET}")
    print()

    all_groups: dict[str, dict[str, int]] = {}
    failures: list[str] = []
    total_ok = total_bad = total_skip = 0
    started = time.time()

    for module in ("test_app", "test_features"):
        print(f"{BOLD}── {module}.py ──{RESET}")
        result, _ = run_module(module, verbose)
        for group, counts in result.groups.items():
            agg = all_groups.setdefault(group, {"ok": 0, "bad": 0, "skip": 0})
            agg["ok"] += counts["ok"]
            agg["bad"] += counts["bad"]
            agg["skip"] += counts.get("skip", 0)
            total_ok += counts["ok"]
            total_bad += counts["bad"]
            total_skip += counts.get("skip", 0)
        failures.extend(result.failures_named)
        print()

    print(f"{BOLD}Results by group{RESET}")
    print("-" * 62)
    for group in sorted(all_groups):
        counts = all_groups[group]
        if counts["bad"]:
            mark = f"{RED}FAIL{RESET}"
        elif counts["skip"] and not counts["ok"]:
            mark = f"{YELLOW}skip{RESET}"
        else:
            mark = f"{GREEN}pass{RESET}"
        suffix = f"  {YELLOW}({counts['skip']} skipped){RESET}" if counts["skip"] else ""
        print(f"  {mark}  {group:<38} {counts['ok']:>3} tests{suffix}")

    print("-" * 62)
    duration = round(time.time() - started, 2)
    if total_bad:
        print(f"{RED}{BOLD}{total_ok} passed, {total_bad} FAILED{RESET} "
              f"{DIM}({duration}s){RESET}")
        print()
        print(f"{RED}Failed tests:{RESET}")
        for name in failures:
            print(f"  - {name}")
        return 1

    skip_note = f", {total_skip} skipped" if total_skip else ""
    print(f"{GREEN}{BOLD}All {total_ok} tests passed{RESET}{YELLOW}{skip_note}{RESET} "
          f"{DIM}({duration}s){RESET}")
    if total_skip:
        print()
        print(f"{DIM}Skipped tests need an optional tool that is not installed.")
        print(f"Nothing is broken — the app does not require it.{RESET}")
    print()
    print(f"{DIM}Next: python3 check_connections.py  — are the services reachable?{RESET}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
