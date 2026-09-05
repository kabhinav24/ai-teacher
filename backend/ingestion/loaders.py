"""Turn uploaded files into structured blocks.

Structure matters more than raw text here: a chunk that knows it belongs to
"Chapter 4 > 4.2 Ohm's Law" can be retrieved when the student says "teach me
chapter 4", which plain text chunks cannot do.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

SUPPORTED = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".txt", ".md", ".rtf", ".html", ".htm", ".epub"}

#: Legacy binary Office formats and what they convert to. These are OLE
#: compound files, not zipped XML, so the modern parsers cannot read them.
LEGACY_OFFICE = {".doc": "docx", ".ppt": "pptx"}


@dataclass
class Block:
    """A contiguous piece of the document with its position and heading trail."""

    text: str
    page: int | None = None
    heading_path: list[str] = field(default_factory=list)
    kind: str = "text"  # text | heading | caption | code | table


CHAPTER_RE = re.compile(
    r"^\s*(chapter|unit|lesson|module|section|अध्याय|पाठ|इकाई)\s+([0-9IVXivx]+)\s*[:.\-–]?\s*(.{0,80})$",
    re.IGNORECASE,
)
NUMBERED_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\s+([A-Z\u0900-\u097F][^\n]{2,80})$")


def looks_like_heading(line: str) -> tuple[bool, int]:
    """Return (is_heading, depth). Cheap heuristics that work on real textbooks."""
    s = line.strip()
    if not s or len(s) > 110:
        return False, 0
    if CHAPTER_RE.match(s):
        return True, 1
    m = NUMBERED_RE.match(s)
    if m:
        return True, 1 + m.group(1).count(".")
    words = s.split()
    if 1 < len(words) <= 12 and not s.endswith((".", ",", ";", ":")):
        letters = [c for c in s if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
            return True, 2
        if s.istitle():
            return True, 3
    return False, 0


def load(path: str | Path) -> list[Block]:
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"Unsupported file type '{ext}'. Supported: {sorted(SUPPORTED)}")

    # Legacy binary Office formats are a different container entirely —
    # python-docx and python-pptx read the XML-based ones only and fail with a
    # confusing zip error on these. Convert first.
    if ext in LEGACY_OFFICE:
        path = _convert_legacy(path)
        ext = path.suffix.lower()

    loader = {
        ".pdf": _load_pdf,
        ".docx": _load_docx,
        ".pptx": _load_pptx,
        ".epub": _load_epub,
        ".html": _load_html,
        ".htm": _load_html,
        ".rtf": _load_rtf,
    }.get(ext, _load_text)
    blocks = loader(path)
    return _attach_headings(blocks)


def _convert_legacy(path: Path) -> Path:
    """Convert .doc/.ppt to their modern equivalents with LibreOffice."""
    target = LEGACY_OFFICE[path.suffix.lower()]
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise ValueError(
            f"'{path.suffix}' is a legacy binary Office format and needs converting first.\n"
            f"Either install LibreOffice (sudo apt install libreoffice-core libreoffice-writer "
            f"libreoffice-impress) so this happens automatically, or re-save the file as "
            f"'.{target}' and upload that."
        )

    out_dir = path.parent / "_converted"
    out_dir.mkdir(exist_ok=True)
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", target, "--outdir", str(out_dir), str(path)],
            check=True, capture_output=True, timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"Converting '{path.name}' timed out.") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            f"LibreOffice could not convert '{path.name}': "
            f"{exc.stderr.decode(errors='ignore')[-300:]}"
        ) from exc

    converted = out_dir / f"{path.stem}.{target}"
    if not converted.exists():
        raise ValueError(f"Conversion of '{path.name}' produced no output.")
    log.info("Converted legacy %s to %s", path.suffix, target)
    return converted


def _load_rtf(path: Path) -> list[Block]:
    """RTF via striprtf, falling back to a crude control-word strip."""
    raw = path.read_text(encoding="utf-8", errors="ignore")
    try:
        from striprtf.striprtf import rtf_to_text

        text = rtf_to_text(raw, errors="ignore")
    except ImportError:
        log.warning("striprtf not installed; using a basic RTF strip. `pip install striprtf` for better results.")
        text = re.sub(r"\\[a-z]+-?\d*\s?|[{}]", "", raw)

    blocks: list[Block] = []
    for para in re.split(r"\n\s*\n", text):
        blocks.extend(_split_leading_heading(_clean(para)))
    return blocks


def _load_pdf(path: Path) -> list[Block]:
    try:
        import pypdf
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pip install pypdf") from exc

    blocks: list[Block] = []
    reader = pypdf.PdfReader(str(path))
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        for para in re.split(r"\n\s*\n", text):
            blocks.extend(_split_leading_heading(_clean(para), page=i))
    if not blocks:
        log.warning("No extractable text in %s — it is likely a scan. Run scripts/ocr.py first.", path.name)
    return blocks


def _load_docx(path: Path) -> list[Block]:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pip install python-docx") from exc

    doc = docx.Document(str(path))
    blocks: list[Block] = []
    for para in doc.paragraphs:
        text = _clean(para.text)
        if not text:
            continue
        style = (para.style.name or "").lower()
        kind = "heading" if style.startswith("heading") or style == "title" else "text"
        blocks.append(Block(text=text, kind=kind))
    for table in doc.tables:
        rows = [" | ".join(_clean(c.text) for c in r.cells) for r in table.rows]
        rows = [r for r in rows if r.strip(" |")]
        if rows:
            blocks.append(Block(text="\n".join(rows), kind="table"))
    return blocks


def _load_pptx(path: Path) -> list[Block]:
    try:
        from pptx import Presentation
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pip install python-pptx") from exc

    prs = Presentation(str(path))
    blocks: list[Block] = []
    for i, slide in enumerate(prs.slides, start=1):
        title = ""
        body: list[str] = []
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            text = _clean(shape.text_frame.text)
            if not text:
                continue
            if shape == slide.shapes.title and not title:
                title = text
            else:
                body.append(text)
        if title:
            blocks.append(Block(text=title, page=i, kind="heading"))
        if body:
            blocks.append(Block(text="\n".join(body), page=i))
        notes = getattr(slide, "notes_slide", None)
        if notes and _clean(notes.notes_text_frame.text):
            blocks.append(Block(text=_clean(notes.notes_text_frame.text), page=i, kind="caption"))
    return blocks


def _load_epub(path: Path) -> list[Block]:
    try:
        from ebooklib import ITEM_DOCUMENT, epub
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pip install EbookLib") from exc

    book = epub.read_epub(str(path))
    blocks: list[Block] = []
    for item in book.get_items():
        if item.get_type() == ITEM_DOCUMENT:
            blocks.extend(_html_to_blocks(item.get_content().decode("utf-8", "ignore")))
    return blocks


def _load_html(path: Path) -> list[Block]:
    return _html_to_blocks(path.read_text(encoding="utf-8", errors="ignore"))


def _html_to_blocks(html: str) -> list[Block]:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pip install beautifulsoup4") from exc

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    blocks: list[Block] = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "table"]):
        text = _clean(el.get_text(" "))
        if not text:
            continue
        kind = "heading" if el.name.startswith("h") else "code" if el.name == "pre" else "table" if el.name == "table" else "text"
        blocks.append(Block(text=text, kind=kind))
    return blocks


def _load_text(path: Path) -> list[Block]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    blocks: list[Block] = []
    for para in re.split(r"\n\s*\n", raw):
        blocks.extend(_split_leading_heading(_clean(para)))
    return blocks


def _split_leading_heading(para: str, page: int | None = None) -> list[Block]:
    """Emit a heading block when a paragraph opens with one.

    Textbooks rarely leave a blank line after "4.3 Ohm's Law" — the section
    title and its first sentence arrive as one paragraph. Without this split,
    every subsection collapses into its parent and the chapter becomes a single
    undifferentiated chunk.
    """
    if not para:
        return []
    lines = para.split("\n")
    first = lines[0].strip()
    is_head, _ = looks_like_heading(first)
    if not is_head:
        return [Block(text=para, page=page)]
    rest = "\n".join(lines[1:]).strip()
    out = [Block(text=first, page=page, kind="heading")]
    if rest:
        out.append(Block(text=rest, page=page))
    return out


def _attach_headings(blocks: list[Block]) -> list[Block]:
    """Walk the document once, maintaining the current heading stack."""
    stack: list[tuple[int, str]] = []
    for b in blocks:
        first_line = b.text.split("\n", 1)[0]
        detected, detected_depth = looks_like_heading(first_line)
        if b.kind == "heading":
            # Trust the block's own claim that it is a heading, but take the
            # depth from the text so "Chapter 4" stays a parent of "4.3".
            is_head, depth = True, (detected_depth if detected else 1)
        elif detected and "\n" not in b.text.strip() and len(b.text.strip()) <= 110:
            # Promote it. Plenty of documents carry no style metadata — authors
            # bold a line instead of using Heading 1, and anything converted
            # from a legacy format loses styles entirely. Without this, those
            # files collapse into one structureless chunk.
            is_head, depth = True, detected_depth
            b.kind = "heading"
        else:
            is_head, depth = detected, detected_depth
        if is_head and b.kind == "heading":
            depth = depth or 1
            stack = [(d, t) for d, t in stack if d < depth]
            stack.append((depth, first_line.strip()))
            b.heading_path = [t for _, t in stack]
            b.kind = "heading"
        else:
            b.heading_path = [t for _, t in stack]
    return blocks


def _clean(text: str) -> str:
    text = text.replace("\u00ad", "").replace("\ufeff", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # de-hyphenate across line breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
