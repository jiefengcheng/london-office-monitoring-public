#!/usr/bin/env python3
"""Isolated core install, with an explicit optional Apple SDK profile."""
from __future__ import annotations

from pathlib import Path
import argparse
from datetime import datetime, timezone
import os
import platform
import subprocess
import sys
import venv
import uuid

ROOT = Path(__file__).resolve().parents[1]


def environment_healthy(destination):
    python = destination / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")
    activation = destination / ("Scripts/activate.bat" if platform.system() == "Windows" else "bin/activate")
    if not all(path.is_file() for path in (python, activation, destination / "pyvenv.cfg")):
        return False
    try:
        result = subprocess.run([str(python), "-c", "import pip, sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 3)"],
                                capture_output=True, text=True, timeout=15)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_environment(destination):
    if not environment_healthy(destination):
        if destination.is_symlink():
            raise RuntimeError("Incomplete .venv is a symlink; preserve its target and supply a complete local venv")
        if destination.exists():
            backup = ROOT / "output/venv-backups" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
            backup.parent.mkdir(parents=True, exist_ok=True)
            destination.rename(backup)
            print("PRESERVED | incomplete or incompatible .venv | " + str(backup), flush=True)
        # Never clear or overwrite an existing failed environment.
        venv.EnvBuilder(with_pip=True).create(destination)
        if not environment_healthy(destination):
            raise RuntimeError("New .venv is incomplete: pip, activation script and pyvenv.cfg are required. Check this Python installation's venv/ensurepip support; preserved backups remain under output/venv-backups/.")
    return destination / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")


def install(profile="core"):
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10+ is required; Xcode's Python 3.9 is unsupported. Install/select Python 3.12, then run python3.12 scripts/install.py. Existing .venv has not been changed.")
    if profile not in ("core", "apple"):
        raise RuntimeError("Unknown installation profile; choose core or apple")
    if profile == "apple":
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise RuntimeError("Apple profile requires native Apple silicon macOS; use --profile core for API/offline mode")
        if tuple(map(int, platform.mac_ver()[0].split(".")[:2])) < (26, 4):
            raise RuntimeError("Apple adapter requires macOS 26.4+; install does not upgrade macOS")
        # Fail on missing tools before creating/changing the environment.
        subprocess.run(["xcrun", "--show-sdk-version"], check=True)
    destination = ROOT / ".venv"
    print(f"Execution environment: {platform.system()} {platform.machine()}", flush=True)
    python = ensure_environment(destination)
    environment = {**os.environ, "PATH": str(python.parent) + os.pathsep + os.environ.get("PATH", "")}
    if profile == "apple":
        subprocess.run([str(python), "-m", "pip", "install", "--require-hashes", "-r", str(ROOT / "requirements-apple-build.lock")], check=True, env=environment)
        subprocess.run([str(python), "-m", "pip", "install", "--no-deps", "--no-build-isolation", "--require-hashes",
                        "-r", str(ROOT / "requirements-apple.lock")], check=True, env=environment)
    # Core uses only the standard library: no registry access or Apple probe.
    subprocess.run([str(python), "-m", "pip", "check"], check=True, env=environment)
    print("PASS | installation | profile=" + profile + " | " + str(python), flush=True)
    if profile == "apple":
        print("Next: .venv/bin/python apple_provider.py (real availability check; no model generation)")
    else:
        print("Core has no third-party runtime dependencies. Existing packages are preserved; no automatic fallback.")
        print("Next: configure API model/key locally and run agent.py --provider openai, or use explicit scripted offline mode.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("core", "apple"), default="core")
    options = parser.parse_args()
    try:
        install(options.profile)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print("FAIL | installation | " + str(error), file=sys.stderr)
        sys.exit(2)
