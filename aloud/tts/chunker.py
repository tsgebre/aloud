"""Split extracted text into paragraph/sentence chunks for streaming synthesis."""

import re

_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")

# A split after one of these is almost never a real sentence boundary
# ("Dr. Smith", "e.g. this", "et al. found"), so the pieces get re-merged.
# Erring toward merging is safe: a missed split just makes one chunk a bit
# longer, while a wrong split puts an audible full-stop pause mid-sentence.
_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "rev.", "hon.", "st.", "sr.", "jr.",
    "vs.", "etc.", "cf.", "al.", "inc.", "ltd.", "co.", "corp.", "dept.",
    "fig.", "figs.", "no.", "nos.", "vol.", "vols.", "pp.", "p.", "ed.", "eds.",
    "approx.", "apt.", "ave.", "blvd.", "rd.", "mt.", "ft.",
}
# Dotted-letter runs: initials ("J."), "e.g.", "i.e.", "a.m.", "U.S." ...
_DOTTED_LETTERS_RE = re.compile(r"^(?:[a-z]\.)+$")


def _ends_with_abbreviation(piece):
    last_word = piece.rsplit(None, 1)[-1].lower()
    return last_word in _ABBREVIATIONS or bool(_DOTTED_LETTERS_RE.match(last_word))


def chunk_text(text, max_chars=400):
    """Split text into chunks, never dropping, merging, or deduplicating any."""
    if not text or not text.strip():
        return []

    chunks = []
    for paragraph in _split_paragraphs(text):
        for sentence in _split_sentences(paragraph):
            chunks.extend(_hard_wrap(sentence, max_chars))
    return chunks


def _split_paragraphs(text):
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [p.strip() for p in normalized.split("\n\n")]
    return [p for p in paragraphs if p]


def _split_sentences(paragraph):
    collapsed = " ".join(paragraph.split())
    pieces = [s.strip() for s in _SENTENCE_BOUNDARY_RE.split(collapsed)]
    sentences = []
    for piece in pieces:
        if not piece:
            continue
        if sentences and _ends_with_abbreviation(sentences[-1]):
            sentences[-1] = f"{sentences[-1]} {piece}"
        else:
            sentences.append(piece)
    return sentences


def _hard_wrap(sentence, max_chars):
    if len(sentence) <= max_chars:
        return [sentence]

    words = sentence.split(" ")
    pieces = []
    current = []
    current_len = 0
    for word in words:
        added_len = len(word) + (1 if current else 0)
        if current and current_len + added_len > max_chars:
            pieces.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += added_len
    if current:
        pieces.append(" ".join(current))
    return pieces
