#!/usr/bin/env python3
"""Verify the preserved inputs and supplied explorer, never an agent or native host."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "evidence" / "london_office_evidence_pack"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(script, *args, expected=0):
    result = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)
    require(result.returncode == expected,
            f"{script.name} {args}: exit {result.returncode}, expected {expected}\n"
            f"{result.stdout}{result.stderr}")
    return result.stdout + result.stderr


def read_rows(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        return {table: sorted(connection.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr)
                for table in ("sources", "facts", "coverage", "evidence")}


def main():
    manifest = json.loads((ROOT / "inputs" / "manifest.json").read_text())
    require(bool(manifest.get("sha256")), "Evidence manifest is empty")
    for relative, expected in manifest["sha256"].items():
        require(digest(ROOT / relative) == expected, f"Hash mismatch: {relative}")
    require(all(relative.startswith("evidence/london_office_evidence_pack/")
                for relative in manifest["sha256"]), "Manifest includes an unexpected input")
    print(f"PASS | evidence input hashes | {len(manifest['sha256'])} files")
    counts = [len(json.loads((PACK / name).read_text()))
              for name in ("sources.json", "facts.json", "coverage.json")]
    cases = json.loads((PACK / "chat_cases.json").read_text())["cases"]
    require(counts == [20, 39, 10], f"Unexpected fixture counts: {counts}")
    require(len(cases) == 6 and sum(len(case["prompts"]) for case in cases) == 18,
            "Expected six cases and 18 prompts")
    print("PASS | inventory | 20 sources; 39 facts; 10 coverage; 6 cases; 18 prompts")
    check = run(PACK / "query.py", "check")
    require("FAIL" not in check, check)
    print(check.rstrip())
    comparison = run(PACK / "query.py", "compare", "F01", "F02")
    require("-0.2 percentage points" in comparison and "-20 basis points" in comparison,
            "Missing documented pp/bps result")
    refusal = run(PACK / "query.py", "compare", "F01", "F07", expected=2)
    require("Comparison refused:" in refusal, refusal)
    print("PASS | comparison refusal | F01/F07; exit 2")
    with closing(sqlite3.connect((PACK / "evidence.sqlite").as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        try:
            connection.execute("DELETE FROM facts WHERE fact_id = ?", ("F01",))
        except sqlite3.OperationalError as error:
            require("readonly" in str(error).lower(), str(error))
        else:
            raise RuntimeError("Read-only connection allowed a write")
    print("PASS | runtime database mode | write refused")
    with tempfile.TemporaryDirectory(prefix="london-fixture-") as temporary:
        rebuilt = Path(temporary) / "pack"
        shutil.copytree(PACK, rebuilt)
        run(rebuilt / "query.py", "build")
        run(rebuilt / "query.py", "check")
        require(read_rows(PACK / "evidence.sqlite") == read_rows(rebuilt / "evidence.sqlite"),
                "Rebuilt logical evidence differs")
    print("PASS | temporary rebuild | query.py build; same logical rows")
    for relative, expected in manifest["sha256"].items():
        require(digest(ROOT / relative) == expected, f"Verification changed input: {relative}")
    print("PASS | preserved evidence inputs | unchanged after verification")
    print("PASS | foundation")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(f"FAIL | foundation | {error}", file=sys.stderr)
        sys.exit(1)
