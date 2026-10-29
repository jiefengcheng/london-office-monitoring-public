#!/usr/bin/env python3
"""Check the published PDF, editable deck, notes and rendered page previews."""
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
SLIDES = ROOT / "slides"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = json.loads((SLIDES / "manifest.json").read_text(encoding="utf-8"))
    files = manifest["sha256"]
    required = {
        "london-office-monitoring.pdf",
        "london-office-monitoring.pptx",
        "CONTENT.md",
        *(f"previews/slide-{number}.png" for number in range(1, 6)),
    }
    if set(files) != required:
        raise ValueError("Expected PDF, PPTX, content/notes and five preview images")
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
    ns = {
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
        "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    }
    with zipfile.ZipFile(SLIDES / "london-office-monitoring.pptx") as deck:
        slide_parts = sorted(name for name in deck.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name))
        if len(slide_parts) != 5:
            raise ValueError("Expected five editable PPTX slides")
        for number in range(1, 6):
            notes = ElementTree.fromstring(deck.read(f"ppt/notesSlides/notesSlide{number}.xml"))
            bodies = [
                shape for shape in notes.findall(".//p:sp", ns)
                if (placeholder := shape.find(".//p:ph", ns)) is not None
                and placeholder.get("type") == "body"
            ]
            if not any(text.text and text.text.strip() for body in bodies for text in body.findall(".//a:t", ns)):
                raise ValueError(f"Missing presenter notes for slide {number}")
        architecture = ElementTree.fromstring(deck.read("ppt/slides/slide2.xml"))
        modules = ElementTree.fromstring(deck.read("ppt/slides/slide3.xml"))
        if not architecture.findall(".//p:cxnSp", ns) or modules.find(".//a:tbl", ns) is None:
            raise ValueError("Expected a native loop diagram and repository table")
    print("PASS | presentation files | 5-page PDF; 5-slide PPTX with notes; content; 5 previews; hashes match")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        print("FAIL | presentation files | " + str(error), file=sys.stderr)
        raise SystemExit(1)
