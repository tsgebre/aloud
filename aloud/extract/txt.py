"""Plain text extraction with charset-normalizer encoding detection."""

from charset_normalizer import from_path

from aloud.errors import EmptyDocumentError, ExtractionError
from aloud.extract.base import Block, Document


def document_from_text(text, source="<text>"):
    """Build a Document from raw plain text (stdin, pasted text, ...)."""
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        raise EmptyDocumentError(f"No text content in {source}")
    blocks = [Block(kind="paragraph", text=p) for p in paragraphs]
    return Document(title=None, blocks=blocks, source_path=source)


def extract(path):
    path = str(path)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        raise ExtractionError(f"Could not read file: {path}") from exc

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        result = from_path(path).best()
        if result is None:
            raise ExtractionError(f"Could not detect text encoding: {path}")
        text = str(result)

    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        raise EmptyDocumentError(f"No text content found in {path}")

    blocks = [Block(kind="paragraph", text=p) for p in paragraphs]
    return Document(title=None, blocks=blocks, source_path=path)


def _split_paragraphs(text):
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = []
    for raw_paragraph in normalized.split("\n\n"):
        collapsed = " ".join(line.strip() for line in raw_paragraph.split("\n") if line.strip())
        if collapsed:
            paragraphs.append(collapsed)
    return paragraphs
