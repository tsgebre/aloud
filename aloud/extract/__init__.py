"""extract_text(path) -> Document, dispatched by file suffix."""

import os

from aloud.errors import ExtractionError, UnsupportedFormatError
from aloud.extract import docx as _docx
from aloud.extract import epub as _epub
from aloud.extract import html as _html
from aloud.extract import pdf as _pdf
from aloud.extract import tex as _tex
from aloud.extract import txt as _txt
from aloud.extract.base import Block, Document
from aloud.extract.txt import document_from_text

_DISPATCH = {
    ".pdf": _pdf.extract,
    ".txt": _txt.extract,
    ".docx": _docx.extract,
    ".epub": _epub.extract,
    ".html": _html.extract,
    ".htm": _html.extract,
    ".tex": _tex.extract,
    ".latex": _tex.extract,
}


def extract_text(path) -> Document:
    path = str(path)
    if not os.path.isfile(path):
        raise ExtractionError(f"File not found: {path}")

    suffix = os.path.splitext(path)[1].lower()
    handler = _DISPATCH.get(suffix)
    if handler is None:
        supported = ", ".join(sorted(_DISPATCH))
        raise UnsupportedFormatError(
            f"Unsupported file format '{suffix}'. Supported formats: {supported}"
        )
    return handler(path)


__all__ = ["Document", "Block", "extract_text", "document_from_text"]
