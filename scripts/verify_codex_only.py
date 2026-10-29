#!/usr/bin/env python3
"""Run the unchanged Python offline gates plus a local simulated-OpenAI HTTP round trip."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="New output directory under output/ for the simulation trace and receipt")
    args = parser.parse_args(argv)
    directory = args.output_dir.resolve()
    if not directory.is_relative_to((ROOT / "output").resolve()) or directory.exists():
        parser.error("Choose a new, not-yet-created directory under this checkout's output/")
    commands = [
        [sys.executable, str(ROOT / "scripts/verify_python_cp6.py")],
        [sys.executable, str(ROOT / "scripts/verify_simulated_openai.py"), "--output-dir", str(directory)],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode:
            print("FAIL | Codex-only suite stopped | " + " ".join(command), file=sys.stderr)
            return result.returncode
    print("PASS | Codex-only local suite | unchanged Python CP6 offline gates plus simulated OpenAI HTTP integration")
    print("PENDING | real OpenAI account/model live run, Apple on-device run and manual answer review are separate profiles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
