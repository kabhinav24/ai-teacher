"""Chunking, retrieval and the grounding check."""
import pytest

from backend.ingestion.chunker import chunk_blocks, estimate_tokens
from backend.ingestion.loaders import Block, _attach_headings, looks_like_heading
from backend.rag.retriever import verify_grounding
from backend.rag.vector_store import BM25, tokenize


@pytest.mark.parametrize("line,expected", [
    ("Chapter 4: Electricity", True),
    ("4.3 Ohm's Law", True),
    ("अध्याय 2 विद्युत धारा", True),
    ("The current through a conductor is proportional to the voltage across it.", False),
])
def test_heading_detection(line, expected):
    assert looks_like_heading(line)[0] is expected


def test_chapter_stays_a_parent_of_its_sections():
    blocks = _attach_headings([
        Block(text="Chapter 4: Electricity", kind="heading"),
        Block(text="4.1 Current", kind="heading"),
        Block(text="Current is the rate of flow of charge."),
    ])
    assert blocks[-1].heading_path == ["Chapter 4: Electricity", "4.1 Current"]


def test_chunks_never_span_a_heading_boundary():
    blocks = _attach_headings([
        Block(text="4.1 Current", kind="heading"),
        Block(text="Current is the rate of flow of charge. " * 4),
        Block(text="4.2 Voltage", kind="heading"),
        Block(text="Voltage is work done per unit charge. " * 4),
    ])
    chunks = chunk_blocks(blocks, target_tokens=500)
    assert len(chunks) == 2
    assert "Voltage" not in chunks[0].text
    assert chunks[0].heading_path[-1] == "4.1 Current"


def test_tables_are_not_split():
    blocks = _attach_headings([Block(text="A | B\n1 | 2\n3 | 4", kind="table")])
    chunks = chunk_blocks(blocks, target_tokens=5)
    assert len(chunks) == 1 and chunks[0].kind == "table"


def test_devanagari_token_estimate_is_denser_than_latin():
    assert estimate_tokens("विद्युत धारा आवेश") > estimate_tokens("electric current charge") / 2


def test_bm25_ranks_the_matching_document_first():
    corpus = [tokenize(t) for t in [
        "Ohm's law relates voltage current and resistance",
        "Photosynthesis happens in the chloroplast",
        "Newton's second law relates force mass and acceleration",
    ]]
    scores = BM25(corpus).scores(tokenize("ohm law resistance"))
    assert scores.argmax() == 0


class _Hit:
    def __init__(self, text):
        self.chunk = type("C", (), {"text": text, "id": "c0", "label": "x", "page": 1})()
        self.score = 1.0


def test_grounded_text_passes_and_invented_text_fails():
    hits = [_Hit("Ohm's law states that current is proportional to potential difference "
                 "provided temperature remains constant.")]
    ok = verify_grounding("Current is proportional to potential difference when temperature is constant.", hits)
    bad = verify_grounding("Ohm was a Norwegian chemist who discovered helium in 1868 while sailing.", hits)
    assert ok.passed and ok.score > bad.score
    assert not bad.passed


def test_questions_and_asides_are_not_treated_as_claims():
    hits = [_Hit("Resistance opposes the flow of current in a conductor.")]
    report = verify_grounding("Now look at the screen for a moment. What do you think happens next?", hits)
    assert report.score == 1.0


def test_no_source_material_means_nothing_to_verify():
    assert verify_grounding("Anything at all goes here.", []).passed


def test_unstyled_headings_are_promoted():
    """Most real documents bold a line instead of using Heading 1, and anything
    converted from a legacy format loses styles entirely."""
    blocks = _attach_headings([
        Block(text="Chapter 4: Electricity"),          # kind defaults to "text"
        Block(text="4.3 Ohm's Law"),
        Block(text="Current is proportional to potential difference."),
    ])
    assert blocks[0].kind == "heading"
    assert blocks[-1].heading_path == ["Chapter 4: Electricity", "4.3 Ohm's Law"]


def test_body_text_is_not_mistaken_for_a_heading():
    blocks = _attach_headings([
        Block(text="4.1 Current"),
        Block(text="Current is the rate of flow of charge through a conductor, "
                   "measured in amperes, and it is the same at every point in a series circuit."),
    ])
    assert blocks[1].kind == "text"


def test_legacy_office_formats_are_declared():
    from backend.ingestion.loaders import LEGACY_OFFICE, SUPPORTED

    # Advertising a format the loader cannot actually read is worse than
    # rejecting it, so every legacy extension needs a conversion target.
    assert ".doc" in SUPPORTED and ".ppt" in SUPPORTED
    assert LEGACY_OFFICE[".doc"] == "docx"
    assert LEGACY_OFFICE[".ppt"] == "pptx"
