import pathlib

import pytest

from aloud.errors import (
    EmptyDocumentError,
    ExtractionError,
    UnsupportedFormatError,
)
from aloud.extract import extract_text
from aloud.extract.pdf import _is_page_artifact

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"

TXT_MARKER = "The quick brown fox jumps over the lazy dog."
PDF_MARKER = "Aloud reads PDF documents aloud."
DOCX_MARKER = "Aloud reads Word documents aloud."


def test_txt_extraction_contains_marker():
    doc = extract_text(SAMPLES / "sample.txt")
    assert TXT_MARKER in doc.full_text


def test_pdf_extraction_contains_marker():
    doc = extract_text(SAMPLES / "sample.pdf")
    assert PDF_MARKER in doc.full_text


def test_docx_extraction_contains_marker():
    doc = extract_text(SAMPLES / "sample.docx")
    assert DOCX_MARKER in doc.full_text


def test_pdf_two_pages_no_footer_artifacts():
    doc = extract_text(SAMPLES / "sample.pdf")
    assert len(doc.blocks) == 2
    assert "Page 1 of 2" not in doc.full_text
    assert "Page 2 of 2" not in doc.full_text


@pytest.mark.parametrize(
    "line,expected_drop",
    [
        ("3", True),
        ("Page 3", True),
        ("Page 3 of 10", True),
        ("3 of 10", True),
        ("  Page 3 of 10  ", True),
        ("PAGE 3 OF 10", True),
        ("Chapter 3 begins here.", False),
        ("Aloud reads PDF documents aloud.", False),
        ("Section 3 of the report.", False),
    ],
)
def test_page_artifact_stripping_is_conservative(line, expected_drop):
    assert _is_page_artifact(line) is expected_drop


def test_docx_has_heading_block():
    doc = extract_text(SAMPLES / "sample.docx")
    assert any(block.kind == "heading" for block in doc.blocks)


def test_corrupt_pdf_raises_extraction_error_with_clean_message():
    with pytest.raises(ExtractionError) as exc_info:
        extract_text(SAMPLES / "corrupt.pdf")
    assert "\n" not in exc_info.value.user_message


def test_unsupported_extension_raises(tmp_path):
    bogus = tmp_path / "sample.xyz"
    bogus.write_text("hello")
    with pytest.raises(UnsupportedFormatError):
        extract_text(bogus)


def test_nonexistent_path_raises_extraction_error():
    with pytest.raises(ExtractionError):
        extract_text("/nonexistent/path/does_not_exist.txt")


def test_whitespace_only_txt_raises_empty_document_error(tmp_path):
    blank = tmp_path / "blank.txt"
    blank.write_text("   \n\n   \n\t\n")
    with pytest.raises(EmptyDocumentError):
        extract_text(blank)


def test_non_utf8_txt_preserves_accented_characters(tmp_path):
    # Short/ambiguous snippets can fool encoding detection into picking a
    # different single-byte codec that still decodes without error but
    # garbles the text, so this uses a longer, natural-language passage
    # to give charset-normalizer enough signal to identify cp1252 correctly.
    text = (
        "Le café était très amer, mais délicieux à la fin de la journée. "
        "Les étoiles brillaient dans le ciel étoilé pendant que nous marchions "
        "vers la vieille maison abandonnée près de la forêt enchantée. "
        "Après plusieurs heures, nous avons enfin trouvé le trésor caché sous "
        "un vieux chêne, entouré de fleurs sauvages magnifiques."
    )
    latin1_path = tmp_path / "latin1.txt"
    latin1_path.write_bytes(text.encode("latin-1"))
    doc = extract_text(latin1_path)
    assert "café" in doc.full_text
    assert "délicieux" in doc.full_text
