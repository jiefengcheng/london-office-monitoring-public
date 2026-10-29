#!/usr/bin/env python3
"""Revised Python CP6 offline checks; no live/provider/human acceptance."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    for command in (['-m', 'unittest', 'tests.test_python_cp6'], ['scripts/verify_cp5.py']):
        result = subprocess.run([sys.executable, *command], cwd=ROOT, text=True, capture_output=True)
        print(result.stdout + result.stderr, end='')
        if result.returncode: return result.returncode
    print('PASS | Python CP6 offline installation boundaries and CP1–CP5 regression')
    print('PENDING_USER | fresh installation, chosen-provider live interaction and independent review; API live acceptance separate')
    return 0


if __name__ == '__main__': raise SystemExit(main())
