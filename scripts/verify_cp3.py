#!/usr/bin/env python3
"""Run CP3 automated checks without filling in human acceptance."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(arguments, expected=0):
    result = subprocess.run([sys.executable, *arguments], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != expected:
        raise RuntimeError(f"{arguments}: exit {result.returncode}; expected {expected}\n{result.stdout}{result.stderr}")
    return result.stdout + result.stderr


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def tool(arguments, expected=0):
    response = json.loads(run(["agent_tools.py", *arguments], expected))
    check(response["ok"] == (expected == 0), "Envelope/exit-code mismatch")
    return response["data"] if expected == 0 else response["error"]


def main():
    tests = run(["-m", "unittest", "tests.test_agent_tools"])
    count = re.search(r"Ran (\d+) tests?", tests)
    check(count is not None and int(count[1]) > 0 and "\nOK" in tests, tests)
    print(f"PASS | gateway tests | {count[1]} tests; exit 0")
    result = tool(["search_facts", "--topic", "vacancy", "--geography", "City Core",
                   "--period-start", "2026-04-01", "--period-end", "2026-06-30", "--limit", "1"])
    check([f["fact_id"] for f in result["facts"]] == ["F04"] and not result["truncated"], "Unexpected scoped search")
    print("PASS | search_facts | City Core Q2 2026 -> F04; no geography expansion")
    result = tool(["get_fact", "--fact-id", "F01"])
    check(result["source_id"] == "S01" and result["locator"] and result["source"]["access_status"], "Missing provenance")
    print("PASS | get_fact | F01; S01; locator and complete source metadata")
    result = tool(["get_source", "--source-id", "S01"])
    check("F01" in result["fact_ids"] and result["url"], "Source link missing")
    print("PASS | get_source | S01 metadata and linked fact IDs")
    result = tool(["compare_facts", "--fact-id-a", "F01", "--fact-id-b", "F02"])
    check((result["difference"], result["basis_points"]) == ("-0.2", "-20"), "Wrong pp/bps result")
    print("PASS | time_series | -0.2 percentage points / -20 basis points")
    for a, b in [("F01", "F07"), ("F09", "F10")]:
        result = tool(["compare_facts", "--fact-id-a", a, "--fact-id-b", b], expected=2)
        check(result["code"] == "COMPARISON_REFUSED", "Expected comparison refusal")
    print("PASS | refusals | F01/F07 and F09/F10 default time_series; JSON errors; exit 2")
    result = tool(["compare_facts", "--fact-id-a", "F09", "--fact-id-b", "F10", "--mode", "cross_section"])
    check(result["difference"] == "29.96" and result["difference_unit"] == "GBP_per_sq_ft", "Wrong cross-section result")
    print("PASS | cross_section | West End minus City = 29.96 GBP_per_sq_ft")
    result = tool(["coverage_gap", "--request", "Canary Wharf vacancy in Q2 2026"])
    check(result["assessment"] == "no_matching_facts_in_fixture" and result["available_count"] == 0,
          "Expected fixture coverage gap")
    print("PASS | coverage_gap | no matching Canary Wharf vacancy fact; not zero market activity")
    foundation = run(["scripts/verify_foundation.py"])
    check("PASS | foundation" in foundation, foundation)
    print(foundation.rstrip())
    print("PASS | CP3 automated checks")
    print("PENDING_USER | CP3 human verification; no agent/native/slides acceptance")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"FAIL | CP3 automated checks | {error}", file=sys.stderr)
        sys.exit(1)
