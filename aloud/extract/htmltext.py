"""Shared HTML -> Block conversion using the stdlib html.parser.

Used by both the .html/.htm extractor and the EPUB extractor (whose
chapters are XHTML). No external dependencies.
"""

from html.parser import HTMLParser

from aloud.extract.base import Block

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# Tags that delimit a block of prose: text on either side of them must not
# be merged into one block.
_BLOCK_TAGS = _HEADING_TAGS | {
    "p", "li", "blockquote", "figcaption", "caption", "dt", "dd",
    "td", "th", "tr", "div", "section", "article", "aside", "main",
    "header", "footer", "nav", "body", "ul", "ol", "table", "pre",
}
# Content that must never be read aloud.
_SKIP_TAGS = {"script", "style", "head", "noscript", "template", "svg", "math"}


class _BlockCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.title = None
        self._buffer = []
        self._skip_depth = 0
        self._heading_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag in _BLOCK_TAGS:
            self.flush()
            if tag in _HEADING_TAGS:
                self._heading_depth += 1
        elif tag == "br":
            self._buffer.append(" ")

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self._buffer.append(" ")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in _BLOCK_TAGS:
            self.flush()
            if tag in _HEADING_TAGS:
                self._heading_depth = max(0, self._heading_depth - 1)

    def handle_data(self, data):
        # Title first: <title> lives inside <head>, which is otherwise skipped.
        if self._in_title:
            stripped = data.strip()
            if stripped and self.title is None:
                self.title = stripped
            return
        if self._skip_depth:
            return
        self._buffer.append(data)

    def flush(self):
        text = " ".join("".join(self._buffer).split())
        self._buffer = []
        if text:
            kind = "heading" if self._heading_depth else "paragraph"
            self.blocks.append(Block(kind=kind, text=text))


def html_to_blocks(html_text):
    """Parse HTML/XHTML markup into (blocks, title).

    Never raises on malformed markup - html.parser is a forgiving,
    best-effort tokenizer, which is exactly right for real-world files.
    """
    parser = _BlockCollector()
    parser.feed(html_text)
    parser.close()
    parser.flush()
    return parser.blocks, parser.title
