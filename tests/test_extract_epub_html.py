"""Tests for the EPUB and HTML extractors (both stdlib-based)."""

import zipfile

import pytest

from aloud.errors import EmptyDocumentError, ExtractionError
from aloud.extract import extract_text

_CONTAINER_XML = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_CONTENT_OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>A Tiny Test Book</dc:title>
    <dc:identifier id="uid">test-book</dc:identifier>
  </metadata>
  <manifest>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
  </spine>
</package>
"""

_CH1 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter One</title><style>p { color: red; }</style></head>
<body>
  <h1>Chapter One</h1>
  <p>The story begins here.</p>
  <p>It continues with a second paragraph.</p>
</body>
</html>
"""

_CH2 = """<html xmlns="http://www.w3.org/1999/xhtml">
<body>
  <h1>Chapter Two</h1>
  <p>The story ends here.</p>
  <script>ignore.me();</script>
</body>
</html>
"""


def _write_epub(path, encrypted=False):
    with zipfile.ZipFile(path, "w") as epub_zip:
        epub_zip.writestr("mimetype", "application/epub+zip")
        epub_zip.writestr("META-INF/container.xml", _CONTAINER_XML)
        if encrypted:
            epub_zip.writestr("META-INF/encryption.xml", "<encryption/>")
        epub_zip.writestr("OEBPS/content.opf", _CONTENT_OPF)
        epub_zip.writestr("OEBPS/ch1.xhtml", _CH1)
        epub_zip.writestr("OEBPS/ch2.xhtml", _CH2)
        epub_zip.writestr("OEBPS/style.css", "p { color: red; }")


# --- EPUB -------------------------------------------------------------------


def test_epub_extracts_chapters_in_spine_order(tmp_path):
    book = tmp_path / "book.epub"
    _write_epub(book)
    document = extract_text(book)

    assert document.title == "A Tiny Test Book"
    texts = [b.text for b in document.blocks]
    assert texts == [
        "Chapter One",
        "The story begins here.",
        "It continues with a second paragraph.",
        "Chapter Two",
        "The story ends here.",
    ]
    kinds = [b.kind for b in document.blocks]
    assert kinds[0] == "heading"
    assert kinds[1] == "paragraph"
    # <script> and <style> content must never leak into the narration.
    assert "ignore.me();" not in document.full_text
    assert "color: red" not in document.full_text


def test_epub_drm_gives_clean_error(tmp_path):
    book = tmp_path / "locked.epub"
    _write_epub(book, encrypted=True)
    with pytest.raises(ExtractionError, match="DRM"):
        extract_text(book)


def test_epub_not_a_zip_gives_clean_error(tmp_path):
    book = tmp_path / "broken.epub"
    book.write_bytes(b"this is not a zip file")
    with pytest.raises(ExtractionError, match="Could not read EPUB"):
        extract_text(book)


def test_epub_missing_container_gives_clean_error(tmp_path):
    book = tmp_path / "empty.epub"
    with zipfile.ZipFile(book, "w") as epub_zip:
        epub_zip.writestr("mimetype", "application/epub+zip")
    with pytest.raises(ExtractionError, match="container.xml"):
        extract_text(book)


# --- HTML -------------------------------------------------------------------


def test_html_extracts_blocks_and_title(tmp_path):
    page = tmp_path / "article.html"
    page.write_text(
        "<html><head><title>An Article</title><style>body{}</style></head>"
        "<body><h1>An Article</h1><p>First paragraph.</p>"
        "<p>Second<br>paragraph.</p>"
        "<script>var x = 1;</script></body></html>"
    )
    document = extract_text(page)

    assert document.title == "An Article"
    texts = [b.text for b in document.blocks]
    assert texts == ["An Article", "First paragraph.", "Second paragraph."]
    assert document.blocks[0].kind == "heading"
    assert "var x" not in document.full_text


def test_htm_suffix_also_dispatches(tmp_path):
    page = tmp_path / "page.htm"
    page.write_text("<p>Hello there.</p>")
    document = extract_text(page)
    assert document.blocks[0].text == "Hello there."


def test_html_with_no_text_gives_clean_error(tmp_path):
    page = tmp_path / "empty.html"
    page.write_text("<html><body><script>only.code();</script></body></html>")
    with pytest.raises(EmptyDocumentError):
        extract_text(page)
