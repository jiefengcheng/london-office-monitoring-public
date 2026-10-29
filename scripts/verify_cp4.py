#!/usr/bin/env python3
"""Offline CP4 gate. Does not call a model or grant human/live acceptance."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(arguments, expected=0):
    result = subprocess.run([sys.executable, *arguments], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != expected:
        raise RuntimeError(f"{arguments}: exit {result.returncode}, expected {expected}\n{result.stdout}{result.stderr}")
    return result.stdout + result.stderr


def main():
    tests = run(["-m", "unittest", "tests.test_agent_loop", "tests.test_apple_provider", "tests.test_cp4_live_review"])
    count = re.search(r"Ran (\d+) tests?", tests)
    if not count or int(count[1]) == 0 or "\nOK" not in tests: raise RuntimeError(tests)
    print(f"PASS | CP4 offline unit tests | {count[1]} tests; HTTP and Apple model calls mocked")
    with tempfile.TemporaryDirectory(prefix="london-cp4-") as temporary:
        trace = Path(temporary) / "scripted.jsonl"
        output = run(["agent.py", "--provider", "scripted", "--case", "market_change", "--trace", str(trace)])
        if "NOT A MODEL EVALUATION" not in output: raise RuntimeError("Scripted run is not labelled")
        report = json.loads(run(["scripts/check_cp4_trace.py", str(trace)]))
        if report["mode"] != "offline" or report["answered"] != 4 or report["tool_calls"] != 9:
            raise RuntimeError("Unexpected scripted trace: " + str(report))
        rejected = run(["scripts/check_cp4_trace.py", str(trace), "--require-live"], expected=1)
        if "not a supported live mode" not in rejected: raise RuntimeError("Scripted trace passed as live")
        print("PASS | labelled scripted scenario | 4 turns; 9 evidence tool calls; support trace valid")
        print("PASS | mode boundary | scripted trace rejected by --require-live")
    cp3 = run(["scripts/verify_cp3.py"])
    if "PASS | CP3 automated checks" not in cp3: raise RuntimeError(cp3)
    gateway_count = re.search(r"gateway tests \| (\d+) tests", cp3)
    if not gateway_count: raise RuntimeError(cp3)
    examples = len(json.loads((ROOT / "contracts/golden-cases.json").read_text())["cases"])
    print(f"PASS | gateway regression | {gateway_count[1]} tests; {examples} contract examples; evidence inputs unchanged")
    print("PASS | CP4 offline checks")
    print("PENDING_USER | CP4 live-model interaction and manual acceptance; no API call made by this verifier")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print("FAIL | CP4 offline checks | " + str(error), file=sys.stderr)
        sys.exit(1)
