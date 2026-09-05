#!/usr/bin/env python3
"""Add a text layer to a scanned PDF so it can be indexed.

    python scripts/ocr.py textbook.pdf --lang eng+hin

Needs: sudo apt install ocrmypdf tesseract-ocr tesseract-ocr-hin tesseract-ocr-tam
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Tesseract language packs for the languages this project targets.
HINTS = {
    "hi": "tesseract-ocr-hin", "bn": "tesseract-ocr-ben", "ta": "tesseract-ocr-tam",
    "te": "tesseract-ocr-tel", "mr": "tesseract-ocr-mar", "gu": "tesseract-ocr-guj",
    "kn": "tesseract-ocr-kan", "ml": "tesseract-ocr-mal", "pa": "tesseract-ocr-pan",
    "ur": "tesseract-ocr-urd",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--lang", default="eng", help="tesseract codes joined by '+', e.g. eng+hin")
    ap.add_argument("--out", help="output path (default: <name>.ocr.pdf)")
    ap.add_argument("--force", action="store_true", help="re-OCR pages that already have text")
    args = ap.parse_args()

    src = Path(args.pdf)
    if not src.exists():
        print(f"No such file: {src}", file=sys.stderr)
        return 1
    if not shutil.which("ocrmypdf"):
        print("ocrmypdf is not installed.\n"
              "  sudo apt install ocrmypdf tesseract-ocr\n"
              f"  language packs: {' '.join(sorted(set(HINTS.values())))}", file=sys.stderr)
        return 1

    dest = Path(args.out) if args.out else src.with_suffix(".ocr.pdf")
    cmd = ["ocrmypdf", "-l", args.lang, "--rotate-pages", "--deskew", "--optimize", "1"]
    cmd += ["--force-ocr"] if args.force else ["--skip-text"]
    cmd += [str(src), str(dest)]

    print(f"Running OCR ({args.lang}) — this takes a while on a full textbook…")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print("OCR failed. If it reports a missing language, install the matching "
              "tesseract-ocr-<lang> package.", file=sys.stderr)
        return proc.returncode

    print(f"Wrote {dest}\nNow upload that file instead of the original.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
