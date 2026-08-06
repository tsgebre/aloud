"""Tests for the PDF extraction cleanup (hyphenation, paragraphs, repeated
headers/footers) and the optional OCR fallback wiring."""

import pytest

from aloud.errors import EmptyDocumentError
from aloud.extract import extract_text
from aloud.extract.pdf import (
    _join_lines,
    _page_paragraphs,
    _strip_repeated_page_lines,
)

# --- dehyphenation ----------------------------------------------------------


def test_join_lines_repairs_soft_hyphenation():
    assert _join_lines(["This is an exam-", "ple of wrapped text."]) == (
        "This is an example of wrapped text."
    )


def test_join_lines_keeps_hyphen_before_uppercase():
    # "Micro-" + "Soft" style breaks are likely real hyphens/compounds.
    assert _join_lines(["The Merriam-", "Webster dictionary."]) == (
        "The Merriam- Webster dictionary."
    )


def test_join_lines_keeps_hyphen_after_digits():
    assert _join_lines(["It scored 7-", "3 overall."]) == "It scored 7- 3 overall."


# --- paragraph reconstruction ----------------------------------------------


def test_page_paragraphs_split_on_blank_lines():
    lines = ["First paragraph line one.", "", "Second paragraph here."]
    assert _page_paragraphs(lines) == [
        "First paragraph line one.",
        "Second paragraph here.",
    ]


def test_page_paragraphs_short_ending_line_splits():
    long_a = "This is a long line of body text that fills the column width fully."
    long_b = "Another long line of body text that also fills the column width ok."
    short_end = "It ends here."
    next_start = "A brand new paragraph starts with this rather long line of text."
    paragraphs = _page_paragraphs([long_a, long_b, short_end, next_start])
    assert len(paragraphs) == 2
    assert paragraphs[0].endswith("It ends here.")
    assert paragraphs[1].startswith("A brand new paragraph")


def test_page_paragraphs_no_false_split_mid_sentence():
    long_a = "This long line of body text does not end with punctuation at all"
    long_b = "so it must continue into this line and stay a single paragraph."
    assert _page_paragraphs([long_a, long_b]) == [f"{long_a} {long_b}"]


# --- repeated headers/footers ----------------------------------------------


def test_repeated_headers_and_footers_are_stripped():
    pages = [
        ["MY GREAT BOOK", f"Body text of page {i}.", f"Chapter 2 - {i}"]
        for i in range(1, 5)
    ]
    stripped = _strip_repeated_page_lines(pages)
    for lines in stripped:
        assert "MY GREAT BOOK" not in lines
        assert not any(line.startswith("Chapter 2 -") for line in lines)
        assert any(line.startswith("Body text") for line in lines)


def test_repeated_lines_kept_when_fewer_than_three_pages():
    pages = [["HEADER", "Body one."], ["HEADER", "Body two."]]
    assert _strip_repeated_page_lines(pages) == pages


def test_varying_first_lines_are_kept():
    pages = [
        ["An apple fell from the tree.", "More apple prose."],
        ["Bees hummed in the garden.", "More bee prose."],
        ["Cats slept on the windowsill.", "More cat prose."],
        ["Dogs barked at the postman.", "More dog prose."],
    ]
    assert _strip_repeated_page_lines(pages) == pages


def test_long_wrapped_prose_is_never_treated_as_footer():
    # Identical-modulo-digits *long* lines are body text, not footers.
    long_line = (
        "The committee reconvened in room {} to continue the very long "
        "discussion that had started the previous evening."
    )
    pages = [
        ["HEADER LINE", "Some body prose.", long_line.format(i)] for i in range(4)
    ]
    stripped = _strip_repeated_page_lines(pages)
    for i, lines in enumerate(stripped):
        assert long_line.format(i) in lines  # kept
        assert "HEADER LINE" not in lines  # short repeated header still goes


def test_full_pdf_with_repeated_header_is_cleaned(tmp_path):
    fpdf = pytest.importorskip("fpdf")

    bodies = [
        "Apples ripen slowly in the northern orchard climate.",
        "Bridges of that era were built from local granite blocks.",
        "Cartographers redrew the coastline after the great storm.",
    ]
    pdf = fpdf.FPDF()
    pdf.set_font("Helvetica", size=12)
    for i, body in enumerate(bodies, start=1):
        pdf.add_page()
        pdf.cell(0, 10, "CONFIDENTIAL DRAFT", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 10, body, new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 10, f"Page {i} of 3", new_x="LMARGIN", new_y="NEXT")
    out = tmp_path / "report.pdf"
    pdf.output(str(out))

    document = extract_text(out)
    assert "CONFIDENTIAL DRAFT" not in document.full_text
    assert "Page 1 of 3" not in document.full_text
    for body in bodies:
        assert body in document.full_text


# --- OCR fallback wiring ----------------------------------------------------


def _blank_pdf(tmp_path):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    out = tmp_path / "scan.pdf"
    with open(out, "wb") as f:
        writer.write(f)
    return out


def test_scanned_pdf_without_ocr_names_tesseract(tmp_path, monkeypatch):
    import aloud.extract.pdf as pdf_module

    monkeypatch.setattr(pdf_module, "_ocr_support_missing", lambda: True)
    with pytest.raises(EmptyDocumentError, match="tesseract"):
        extract_text(_blank_pdf(tmp_path))


def test_scanned_pdf_with_ocr_extracts_text(tmp_path, monkeypatch):
    import aloud.extract.pdf as pdf_module

    monkeypatch.setattr(pdf_module, "_ocr_support_missing", lambda: False)
    monkeypatch.setattr(
        pdf_module,
        "_ocr_page_images",
        lambda reader: "Recognized scanned text.\n\nSecond OCR paragraph.",
    )
    document = extract_text(_blank_pdf(tmp_path))
    texts = [b.text for b in document.blocks]
    assert texts == ["Recognized scanned text.", "Second OCR paragraph."]


def test_scanned_pdf_ocr_finds_nothing_gives_clean_error(tmp_path, monkeypatch):
    import aloud.extract.pdf as pdf_module

    monkeypatch.setattr(pdf_module, "_ocr_support_missing", lambda: False)
    monkeypatch.setattr(pdf_module, "_ocr_page_images", lambda reader: None)
    with pytest.raises(EmptyDocumentError, match="OCR ran"):
        extract_text(_blank_pdf(tmp_path))
