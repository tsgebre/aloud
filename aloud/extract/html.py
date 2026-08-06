"""HTML extraction (saved web pages etc.) via the shared stdlib parser."""

from charset_normalizer import from_bytes

from aloud.errors import EmptyDocumentError, ExtractionError
from aloud.extract.base import Document
from aloud.extract.htmltext import html_to_blocks


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
        result = from_bytes(raw).best()
        if result is None:
            raise ExtractionError(f"Could not detect text encoding: {path}")
        text = str(result)

    blocks, title = html_to_blocks(text)
    if not blocks:
        raise EmptyDocumentError(f"No text content found in {path}")
    return Document(title=title, blocks=blocks, source_path=path)
