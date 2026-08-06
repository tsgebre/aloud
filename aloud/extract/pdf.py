"""PDF extraction via pypdf.

Beyond raw text extraction this cleans up the artifacts that make PDFs
unpleasant to *listen* to:

- standalone page numbers ("3", "Page 3 of 10") are dropped;
- headers/footers repeated across three or more pages are dropped;
- end-of-line hyphenation ("exam-\\nple") is repaired;
- paragraphs are reconstructed from blank lines plus a short-line
  heuristic (a clearly short line ending in terminal punctuation ends a
  paragraph), instead of flattening each page into one giant block.

If the PDF has no text layer at all (a scan), an optional OCR fallback
runs when pytesseract + Pillow + the system tesseract binary are present.
"""

import contextlib
import io
import re
import shutil
import statistics
from collections import Counter

from pypdf import PdfReader

from aloud.errors import EmptyDocumentError, EncryptedPdfError, ExtractionError
from aloud.extract.base import Block, Document
from aloud.extract.txt import document_from_text

# Matches, on its own line: "3", "Page 3", "3 of 10", "Page 3 of 10"
# (case-insensitive). A line must match this *entirely* to be dropped, so
# body text that merely mentions a number ("Chapter 3 begins here.") is
# never touched.
_PAGE_ARTIFACT_RE = re.compile(r"(?:page\s+)?\d+(?:\s+of\s+\d+)?", re.IGNORECASE)

# A line this much shorter than the page's typical line, ending in terminal
# punctuation, is treated as the end of a paragraph.
_SHORT_LINE_FACTOR = 0.7
_PARAGRAPH_END_CHARS = '.!?:"’”'

# Headers/footers must repeat on at least this many pages to be stripped,
# and must be short - real running headers/footers are a few words, while
# a full-width wrapped prose line must never be mistaken for one.
_REPEAT_THRESHOLD = 3
_MAX_ARTIFACT_LEN = 60

_OCR_HINT = (
    "scanned/image-only PDFs need OCR - install the system 'tesseract' "
    "package plus 'pip install pytesseract pillow' to enable it"
)


def _is_page_artifact(line):
    return bool(_PAGE_ARTIFACT_RE.fullmatch(line.strip()))


def _page_lines(raw_text):
    """Per-page cleaned lines; empty strings mark blank-line breaks."""
    lines = []
    for raw_line in raw_text.splitlines():
        line = " ".join(raw_line.split())
        if line and _is_page_artifact(line):
            continue
        if not line and (not lines or not lines[-1]):
            continue  # collapse runs of blanks
        lines.append(line)
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _normalize_repeated(line):
    """Header/footer fingerprint: digits vary per page ("Chapter 2 - 17"),
    everything else must match exactly (case-insensitively)."""
    return re.sub(r"\d+", "#", line).lower()


def _strip_repeated_page_lines(pages_lines):
    """Drop first/last lines that repeat across pages (running headers/footers)."""
    if len(pages_lines) < _REPEAT_THRESHOLD:
        return pages_lines

    first_counts = Counter()
    last_counts = Counter()
    for lines in pages_lines:
        nonblank = [line for line in lines if line]
        if not nonblank:
            continue
        if len(nonblank[0]) <= _MAX_ARTIFACT_LEN:
            first_counts[_normalize_repeated(nonblank[0])] += 1
        if len(nonblank) > 1 and len(nonblank[-1]) <= _MAX_ARTIFACT_LEN:
            last_counts[_normalize_repeated(nonblank[-1])] += 1

    headers = {n for n, c in first_counts.items() if c >= _REPEAT_THRESHOLD}
    footers = {n for n, c in last_counts.items() if c >= _REPEAT_THRESHOLD}
    if not headers and not footers:
        return pages_lines

    stripped = []
    for lines in pages_lines:
        nonblank_indices = [i for i, line in enumerate(lines) if line]
        drop = set()
        if nonblank_indices:
            first, last = nonblank_indices[0], nonblank_indices[-1]
            if (
                len(lines[first]) <= _MAX_ARTIFACT_LEN
                and _normalize_repeated(lines[first]) in headers
            ):
                drop.add(first)
            if (
                last != first
                and len(lines[last]) <= _MAX_ARTIFACT_LEN
                and _normalize_repeated(lines[last]) in footers
            ):
                drop.add(last)
        stripped.append([line for i, line in enumerate(lines) if i not in drop])
    return stripped


def _join_lines(lines):
    """Join wrapped lines into one string, repairing end-of-line hyphenation."""
    text = ""
    for line in lines:
        if not text:
            text = line
        elif text.endswith("-") and len(text) > 1 and text[-2].isalpha() and line[:1].islower():
            text = text[:-1] + line
        else:
            text = f"{text} {line}"
    return text


def _page_paragraphs(lines):
    """Reconstruct paragraphs from a page's cleaned lines."""
    lengths = [len(line) for line in lines if line]
    if not lengths:
        return []
    typical = statistics.median(lengths)

    paragraphs = []
    current = []

    def flush():
        if current:
            paragraphs.append(_join_lines(current))
            current.clear()

    for line in lines:
        if not line:
            flush()
            continue
        current.append(line)
        if len(line) < _SHORT_LINE_FACTOR * typical and line[-1] in _PARAGRAPH_END_CHARS:
            flush()
    flush()
    return [p for p in paragraphs if p]


# --- OCR fallback (optional) -------------------------------------------------


def _ocr_support_missing():
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return True
    return shutil.which("tesseract") is None


def _ocr_page_images(reader):
    """OCR every embedded page image; returns extracted text or None."""
    import pytesseract
    from PIL import Image

    texts = []
    for page in reader.pages:
        for image_file in page.images:
            try:
                with Image.open(io.BytesIO(image_file.data)) as image:
                    text = pytesseract.image_to_string(image)
            except Exception:
                continue
            if text.strip():
                texts.append(text.strip())
    return "\n\n".join(texts) if texts else None


def _decrypts_with_empty_password(reader):
    """Many PDFs are 'encrypted' with only an owner password (restricting
    printing/editing) but an empty user password - those are perfectly
    readable and should not be rejected."""
    try:
        return bool(reader.decrypt(""))
    except Exception:
        return False


def extract(path):
    path = str(path)
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            reader = PdfReader(path)
        except Exception as exc:
            raise ExtractionError(f"Could not read PDF file: {path}") from exc

        if reader.is_encrypted and not _decrypts_with_empty_password(reader):
            raise EncryptedPdfError(f"PDF is password-protected: {path}")

        try:
            pages_lines = [_page_lines(page.extract_text() or "") for page in reader.pages]
        except Exception as exc:
            raise ExtractionError(f"Could not extract text from PDF: {path}") from exc

        pages_lines = _strip_repeated_page_lines(pages_lines)
        blocks = [
            Block(kind="paragraph", text=paragraph)
            for lines in pages_lines
            for paragraph in _page_paragraphs(lines)
        ]

        if not blocks:
            # No text layer: a scan. Try OCR if the optional pieces exist.
            if _ocr_support_missing():
                raise EmptyDocumentError(
                    f"No extractable text found in {path} ({_OCR_HINT})"
                )
            try:
                ocr_text = _ocr_page_images(reader)
            except Exception as exc:
                raise ExtractionError(f"OCR failed for {path}") from exc
            if not ocr_text:
                raise EmptyDocumentError(
                    f"No extractable text found in {path} "
                    "(OCR ran but recognized no text)"
                )
            ocr_document = document_from_text(ocr_text, source=path)
            return Document(title=None, blocks=ocr_document.blocks, source_path=path)

    return Document(title=None, blocks=blocks, source_path=path)
