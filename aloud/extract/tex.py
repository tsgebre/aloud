"""LaTeX (.tex) extraction: pure-Python source stripping, no new dependencies.

LaTeX is a typesetting *source* format, so the goal here is listenable
prose rather than typographic fidelity:

- sectioning commands (\\section{...} etc.) become heading blocks;
- text-wrapping commands (\\emph{...}, \\textbf{...}, unknown macros)
  keep their text; footnotes are inlined in parentheses;
- inline math keeps its literal content ("E=mc^2" reads acceptably),
  while display math, tables, verbatim/code listings, and pictures are
  dropped entirely (there is no useful way to narrate them);
- figures and tables are replaced by their \\caption text;
- comments, the preamble, labels, refs, and citations are removed.
"""

import os
import re
import unicodedata

from charset_normalizer import from_bytes

from aloud.errors import EmptyDocumentError, ExtractionError
from aloud.extract.base import Block, Document

_HEADING_MARK = "\x00"

# LaTeX escapes for characters that would otherwise confuse the stripping
# passes below; hidden behind sentinels early, restored literally at the end.
_PROTECTED = {
    r"\%": "\x01pct\x02",
    r"\&": "\x01amp\x02",
    r"\#": "\x01hash\x02",
    r"\$": "\x01dollar\x02",
    r"\_": "\x01underscore\x02",
    r"\{": "\x01lbrace\x02",
    r"\}": "\x01rbrace\x02",
}
_RESTORED = {
    "\x01pct\x02": "%",
    "\x01amp\x02": "&",
    "\x01hash\x02": "#",
    "\x01dollar\x02": "$",
    "\x01underscore\x02": "_",
    "\x01lbrace\x02": "{",
    "\x01rbrace\x02": "}",
}

# Environments whose entire content has no spoken form.
_DROP_ENV_NAMES = (
    "equation",
    "align",
    "alignat",
    "flalign",
    "gather",
    "multline",
    "eqnarray",
    "displaymath",
    "math",
    "verbatim",
    "Verbatim",
    "lstlisting",
    "minted",
    "tikzpicture",
    "tabular",
    "tabularx",
    "longtable",
    "array",
    "thebibliography",
    "filecontents",
)
_DROP_ENV_RES = [
    re.compile(r"\\begin\{" + name + r"\*?\}.*?\\end\{" + name + r"\*?\}", re.S)
    for name in _DROP_ENV_NAMES
]

# Environments replaced by their \caption text (the graphic itself is silent).
_CAPTION_ENV_RES = [
    re.compile(r"\\begin\{" + name + r"\*?\}.*?\\end\{" + name + r"\*?\}", re.S)
    for name in ("figure", "table", "wrapfigure", "sidewaysfigure", "sidewaystable")
]

_HEADING_RE = re.compile(
    r"\\(?:part|chapter|section|subsection|subsubsection|paragraph|subparagraph)"
    r"\*?\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}"
)
_FOOTNOTE_RE = re.compile(r"\\footnote\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")
_HREF_RE = re.compile(r"\\href\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}\s*\{([^{}]*)\}")
_BEGIN_RE = re.compile(r"\\begin\s*\{[^{}]*\}(?:\[[^\]]*\])?(?:\{[^{}]*\})?")
_END_RE = re.compile(r"\\end\s*\{[^{}]*\}")
_DROP_CMD_RE = re.compile(
    r"\\(?:label|pageref|cite[a-zA-Z]*|nocite"
    r"|includegraphics|usepackage|RequirePackage|documentclass"
    r"|bibliographystyle|bibliography|addbibresource|pagestyle|thispagestyle"
    r"|vspace|hspace|setlength|addtolength|setcounter|stepcounter|numberwithin"
    r"|hypersetup|graphicspath|captionsetup|geometry|newtheorem|theoremstyle"
    r"|definecolor)\*?\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}"
)
# Any other \command{...}: assume it wraps text worth keeping (\emph, \textbf,
# custom macros, ...). Innermost-first via the resolution loop handles nesting.
_KEEP_CMD_RE = re.compile(r"\\[a-zA-Z@]+\*?\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")

# One scan of the body in source order recovers LaTeX's deterministic
# numbering: sectioning commands and numbered environments increment
# counters, and each \label attaches to the most recently numbered thing
# (matching \refstepcounter semantics). Starred forms are unnumbered.
_COUNTER_TOKEN_RE = re.compile(
    r"\\(chapter|section|subsection|subsubsection)(\*?)\s*\{"
    r"|\\begin\{(figure|table|equation|align|alignat|flalign|gather|multline|eqnarray)(\*?)\}"
    r"|\\label\s*\{([^{}]*)\}"
)
_SECTION_LEVELS = ("chapter", "section", "subsection", "subsubsection")

# Multi-file projects: \input{...}/\include{...}/\subfile{...} splice in a
# sibling file; \import{dir}{file} and \subimport{dir}{file} name it in two
# parts. Unresolvable files are dropped, as all unknown commands are.
_INPUT_RE = re.compile(
    r"\\(?:input|include|subfile)\s*\{([^{}]*)\}"
    r"|\\(?:import|subimport)\s*\{([^{}]*)\}\s*\{([^{}]*)\}"
)
_MAX_INPUT_DEPTH = 10

_ACCENTS = {
    "'": "\u0301",
    "`": "\u0300",
    "^": "\u0302",
    '"': "\u0308",
    "~": "\u0303",
    "=": "\u0304",
    ".": "\u0307",
}
_LETTER_ACCENTS = {"c": "\u0327", "v": "\u030c", "H": "\u030b", "k": "\u0328"}
_SPECIAL_LETTERS = {
    "ss": "ß",
    "ae": "æ",
    "AE": "Æ",
    "oe": "œ",
    "OE": "Œ",
    "aa": "å",
    "AA": "Å",
    "o": "ø",
    "O": "Ø",
    "l": "ł",
    "L": "Ł",
}


def extract(path):
    path = str(path)
    source = _preprocess(_read_source(path))
    base_dir = os.path.dirname(os.path.abspath(path))
    source = _resolve_inputs(source, base_dir, {os.path.abspath(path)}, depth=0)
    title_raw = _balanced_arg(source, "title")
    body = _document_body(source)
    labels = _build_label_map(body)
    blocks = _split_blocks(_fragment_to_text(body, labels))
    if not blocks:
        raise EmptyDocumentError(f"No text content found in {path}")

    title = None
    if title_raw is not None:
        title = " ".join(_fragment_to_text(title_raw, labels).split()) or None
    return Document(title=title, blocks=blocks, source_path=path)


def _read_source(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        raise ExtractionError(f"Could not read file: {path}") from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        result = from_bytes(raw).best()
        if result is None:
            raise ExtractionError(f"Could not detect text encoding: {path}")
        return str(result)


def _document_body(source):
    """The part between \\begin{document} and \\end{document}, if present."""
    begin = re.search(r"\\begin\{document\}", source)
    if not begin:
        return source
    body = source[begin.end():]
    end = re.search(r"\\end\{document\}", body)
    return body[: end.start()] if end else body


def _resolve_inputs(text, base_dir, seen, depth):
    """Splice \\input/\\include/\\subfile files into the source, recursively.

    LaTeX resolves these relative to the main file's directory, so base_dir
    stays constant down the recursion. Files uploaded without their folder
    structure are found by basename as a fallback. Missing files, cycles,
    and over-deep nesting resolve to nothing, like other dropped commands.
    """
    if depth >= _MAX_INPUT_DEPTH:
        return text

    def splice(match):
        name = match.group(1)
        if name is None:  # \import{dir}{file} form
            name = os.path.join(match.group(2), match.group(3))
        name = name.strip()
        if not name:
            return " "
        candidates = [os.path.join(base_dir, name)]
        if not os.path.splitext(name)[1]:
            candidates.insert(0, os.path.join(base_dir, name + ".tex"))
        basename = os.path.basename(name)
        if basename != name:
            candidates.append(os.path.join(base_dir, basename))
            if not os.path.splitext(basename)[1]:
                candidates.append(os.path.join(base_dir, basename + ".tex"))
        for candidate in candidates:
            resolved = os.path.abspath(candidate)
            if resolved in seen or not os.path.isfile(resolved):
                continue
            seen.add(resolved)
            try:
                child = _preprocess(_read_source(resolved))
            except ExtractionError:
                return " "
            # subfiles carry their own preamble/document wrapper: keep only
            # the body so the parent's document slicing stays intact.
            child = _document_body(child)
            child = _resolve_inputs(child, base_dir, seen, depth + 1)
            return f"\n\n{child}\n\n"
        return " "

    return _INPUT_RE.sub(splice, text)


def _preprocess(source):
    """Normalize newlines, strip comments, protect escapes: run once per file."""
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    source = re.sub(r"(?<!\\)%[^\n]*", "", source)
    source = re.sub(r"\\\\\*?\s*(?:\[[^\]]*\])?", "\n", source)  # \\ line breaks
    for escape, sentinel in _PROTECTED.items():
        source = source.replace(escape, sentinel)
    return source


def _build_label_map(body):
    """Map \\label names to ("Section", "2.1")-style (kind, number) pairs."""
    counters = dict.fromkeys(_SECTION_LEVELS, 0)
    counters.update(figure=0, table=0, equation=0)
    labels = {}
    current = None
    for match in _COUNTER_TOKEN_RE.finditer(body):
        level, level_star, env, env_star, label = match.groups()
        if level:
            if level_star:
                continue
            counters[level] += 1
            for child in _SECTION_LEVELS[_SECTION_LEVELS.index(level) + 1 :]:
                counters[child] = 0
            parts = [
                str(counters[name])
                for name in _SECTION_LEVELS[: _SECTION_LEVELS.index(level) + 1]
                if counters[name] > 0 or name == level
            ]
            current = (level.capitalize(), ".".join(parts))
        elif env:
            if env_star:
                continue
            # align/gather/... can number several lines; one number per
            # environment is close enough for narration.
            kind = env if env in ("figure", "table") else "equation"
            counters[kind] += 1
            current = (kind.capitalize(), str(counters[kind]))
        elif label.strip() and current:
            labels.setdefault(label.strip(), current)
    return labels


def _resolve_refs(text, labels):
    """Replace \\ref-family commands with the number the label resolves to.

    Unresolvable labels are dropped, matching the old behavior.
    """

    def lookup(match, style):
        entry = labels.get(match.group(1).strip())
        if entry is None:
            return ""
        kind, number = entry
        if style == "eq":
            return f"({number})"
        if style == "lower":
            return f"{kind.lower()} {number}"
        if style == "cap":
            return f"{kind} {number}"
        return number

    text = re.sub(r"\\eqref\s*\{([^{}]*)\}", lambda m: lookup(m, "eq"), text)
    text = re.sub(r"\\(?:autoref|Cref)\s*\{([^{}]*)\}", lambda m: lookup(m, "cap"), text)
    text = re.sub(r"\\cref\s*\{([^{}]*)\}", lambda m: lookup(m, "lower"), text)
    text = re.sub(r"\\(?:ref|vref)\s*\{([^{}]*)\}", lambda m: lookup(m, "plain"), text)
    return text


def _fragment_to_text(text, labels=None):
    """Turn a preprocessed LaTeX fragment into plain narration text."""
    text = _resolve_refs(text, labels or {})
    for pattern in _CAPTION_ENV_RES:
        text = pattern.sub(_caption_of_env, text)
    for pattern in _DROP_ENV_RES:
        text = pattern.sub(" ", text)
    text = re.sub(r"\$\$.*?\$\$", " ", text, flags=re.S)
    text = re.sub(r"\\\[.*?\\\]", " ", text, flags=re.S)
    text = re.sub(r"\$(.+?)\$", _inline_math, text, flags=re.S)
    text = re.sub(r"\\\((.+?)\\\)", _inline_math, text, flags=re.S)
    text = re.sub(r"\\item\b\s*(?:\[[^\]]*\])?", "\n\n", text)
    text = _apply_accents(text)
    text = _replace_words(text)
    text = _resolve_commands(text)
    return _finish(text)


def _caption_of_env(match):
    caption = _balanced_arg(match.group(0), "caption")
    return f"\n\n{caption}\n\n" if caption else "\n\n"


def _inline_math(match):
    # "E=mc^2" or "\alpha > 0" read acceptably once the TeX markup is gone.
    return match.group(1).replace("\\", " ").replace("{", "").replace("}", "")


def _balanced_arg(text, command):
    """Return the {...} argument of \\command, honoring nested braces."""
    match = re.search(r"\\" + command + r"\s*(?:\[[^\]]*\])?\s*\{", text)
    if not match:
        return None
    depth, i = 1, match.end()
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[match.end() : i - 1] if depth == 0 else None


def _apply_accents(text):
    def combine(letter, mark):
        return unicodedata.normalize("NFC", letter + mark)

    text = re.sub(
        r"\\(['`^\"~=.])\s*\{?([a-zA-Z])\}?",
        lambda m: combine(m.group(2), _ACCENTS[m.group(1)]),
        text,
    )
    text = re.sub(
        r"\\([cvHk])\s*\{([a-zA-Z])\}",
        lambda m: combine(m.group(2), _LETTER_ACCENTS[m.group(1)]),
        text,
    )
    return text


def _resolve_commands(text):
    """Repeatedly rewrite innermost commands until the text stops changing.

    Each pass handles the special forms before the generic keep-the-argument
    fallback, so nested arguments resolve inside-out across passes.
    """
    for _ in range(64):
        new = _HEADING_RE.sub(lambda m: f"\n\n{_HEADING_MARK}{m.group(1)}\n\n", text)
        new = _FOOTNOTE_RE.sub(r" (\1)", new)
        new = _HREF_RE.sub(r"\1", new)
        new = _BEGIN_RE.sub(" ", new)
        new = _END_RE.sub(" ", new)
        new = _DROP_CMD_RE.sub(" ", new)
        new = _KEEP_CMD_RE.sub(r"\1", new)
        if new == text:
            break
        text = new
    return text


def _replace_words(text):
    """Commands that stand for a literal word or character."""
    text = re.sub(r"\\(?:LaTeX|LaTeXe)\b", "LaTeX", text)
    text = re.sub(r"\\TeX\b", "TeX", text)
    text = re.sub(r"\\(?:ldots|dots|dotsc|dotso|textellipsis)\b", "...", text)
    for name, char in sorted(_SPECIAL_LETTERS.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(r"\\" + name + r"(?![a-zA-Z])", char, text)
    return text


def _finish(text):
    text = re.sub(r"\\[,;:!/ ]", " ", text)
    text = re.sub(r"\\-", "", text)
    text = re.sub(r"\\[a-zA-Z@]+\*?", " ", text)  # leftover bare commands
    text = text.replace("\\", "").replace("{", "").replace("}", "")
    text = text.replace("``", '"').replace("''", '"').replace("`", "'")
    text = text.replace("---", "\u2014").replace("--", "\u2013")
    text = text.replace("~", " ")
    for sentinel, char in _RESTORED.items():
        text = text.replace(sentinel, char)
    return text


def _split_blocks(text):
    blocks = []
    for raw_paragraph in re.split(r"\n\s*\n", text):
        collapsed = " ".join(raw_paragraph.split())
        if not collapsed:
            continue
        kind = "heading" if collapsed.startswith(_HEADING_MARK) else "paragraph"
        cleaned = collapsed.replace(_HEADING_MARK, " ")
        cleaned = " ".join(cleaned.split())
        if cleaned:
            blocks.append(Block(kind=kind, text=cleaned))
    return blocks
