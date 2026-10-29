#!/usr/bin/env python3
"""Check the published presentation PDF and rendered page previews."""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SLIDES = ROOT / "slides"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = json.loads((SLIDES / "manifest.json").read_text(encoding="utf-8"))
    files = manifest["sha256"]
    if len(files) != 6:
        raise ValueError("Expected one PDF and five preview images")
    for relative, expected in files.items():
        path = (SLIDES / relative).resolve()
        if not path.is_relative_to(SLIDES.resolve()) or not path.is_file():
            raise ValueError("Missing or invalid slide artifact: " + relative)
        if digest(path) != expected:
            raise ValueError("Artifact hash mismatch: " + relative)
    pdf = (SLIDES / "london-office-monitoring.pdf").read_bytes()
    if not pdf.startswith(b"%PDF-"):
        raise ValueError("Presentation PDF header is invalid")
    pages = len(re.findall(rb"/Type\s*/Page\b", pdf))
    if pages != 5 or manifest.get("page_count") != 5:
        raise ValueError(f"Expected five PDF pages, got {pages}")
    previews = [name for name in files if name.startswith("previews/")]
    if len(previews) != 5 or any(not (SLIDES / name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for name in previews):
        raise ValueError("Expected five valid PNG previews")
    print("PASS | presentation files | 5-page PDF; 5 previews; hashes match")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print("FAIL | presentation files | " + str(error), file=sys.stderr)
        raise SystemExit(1)
