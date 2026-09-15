"""Tests for the LaTeX (.tex) extractor (pure-Python source stripping)."""

import pytest

from aloud.errors import EmptyDocumentError
from aloud.extract import extract_text

_PAPER = r"""
\documentclass[11pt]{article}
\usepackage{amsmath}
\title{A \emph{Tiny} Paper}
\author{Jo Researcher}
\date{\today}

\begin{document}
\maketitle

\begin{abstract}
We study nothing in particular.
\end{abstract}

\section{Introduction}
% a comment that must not be narrated
The mass--energy relation $E=mc^2$ is famous~\cite{einstein1905}.
It achieves 95\% accuracy (see Section~\ref{sec:results}).

Deeply \textbf{\emph{nested emphasis}} survives.

\begin{equation}
  \int_0^1 f(x)\,dx = 1
\end{equation}

\subsection{Method}
\label{sec:method}
We proceed as follows:
\begin{itemize}
  \item First step.
  \item Second step.
\end{itemize}

\begin{figure}
  \includegraphics[width=\textwidth]{plot.png}
  \caption{Accuracy over time.}
  \label{fig:acc}
\end{figure}

\begin{verbatim}
raw code that must not leak
\end{verbatim}

Cost was \$5, i.e.\ cheap\footnote{Prices from 2024.}.
Thanks to P\'erez and M\"uller.
\end{document}
"""


def _write(tmp_path, body, name="doc.tex"):
    path = tmp_path / name
    path.write_text(body)
    return path


def test_full_paper_extraction(tmp_path):
    document = extract_text(_write(tmp_path, _PAPER))

    assert document.title == "A Tiny Paper"
    full = document.full_text

    # Preamble and comments never leak.
    assert "documentclass" not in full
    assert "amsmath" not in full
    assert "comment that must not be narrated" not in full

    # Headings become heading blocks.
    headings = [b.text for b in document.blocks if b.kind == "heading"]
    assert headings == ["Introduction", "Method"]

    # Inline math is kept literally; display math is dropped.
    assert "E=mc^2" in full
    assert "int_0^1" not in full

    # Citations, labels, and refs are dropped; escapes are restored.
    assert "einstein1905" not in full
    assert "sec:results" not in full
    assert "95% accuracy" in full
    assert "$5" in full

    # Nested styling commands unwrap to their text.
    assert "nested emphasis" in full

    # List items become their own paragraphs, with no environment names.
    texts = [b.text for b in document.blocks]
    assert "First step." in texts
    assert "Second step." in texts
    assert "itemize" not in full

    # Figures reduce to their caption; graphics filenames never leak.
    assert "Accuracy over time." in full
    assert "plot.png" not in full

    # Verbatim blocks are dropped.
    assert "raw code" not in full

    # Footnotes are inlined in parentheses; accents are applied.
    assert "(Prices from 2024.)" in full
    assert "Pérez" in full
    assert "Müller" in full


def test_fragment_without_document_env(tmp_path):
    document = extract_text(_write(tmp_path, r"Just a \emph{fragment} of text."))
    assert document.title is None
    assert document.blocks[0].text == "Just a fragment of text."


def test_latex_suffix_also_dispatches(tmp_path):
    document = extract_text(_write(tmp_path, "Hello there.", name="doc.latex"))
    assert document.blocks[0].text == "Hello there."


def test_math_only_document_gives_clean_error(tmp_path):
    body = r"\begin{document}\begin{equation}x=1\end{equation}\end{document}"
    with pytest.raises(EmptyDocumentError):
        extract_text(_write(tmp_path, body))


def test_cross_references_resolve_to_numbers(tmp_path):
    body = r"""
\begin{document}
\section{Intro}\label{sec:intro}
\section{Methods}
\subsection{Data}\label{sec:data}
\begin{equation}\label{eq:main} x = 1 \end{equation}
\begin{figure}\caption{A plot.}\label{fig:plot}\end{figure}
\begin{table}\caption{Results.}\label{tab:res}\end{table}
As shown in Section~\ref{sec:intro} and Section~\ref{sec:data},
equation~\eqref{eq:main} holds; see \autoref{fig:plot},
\cref{tab:res}, and Section~\ref{sec:undefined}.
\end{document}
"""
    full = extract_text(_write(tmp_path, body)).full_text
    assert "Section 1 and Section 2.1" in full
    assert "equation (1) holds" in full
    assert "see Figure 1" in full
    assert "table 1" in full
    # Undefined labels still drop rather than leaking the label name.
    assert "sec:undefined" not in full


def test_starred_sections_do_not_increment_numbering(tmp_path):
    body = r"""
\begin{document}
\section{One}\label{sec:one}
\section*{Unnumbered}
\section{Two}\label{sec:two}
See \ref{sec:one} and \ref{sec:two}.
\end{document}
"""
    full = extract_text(_write(tmp_path, body)).full_text
    assert "See 1 and 2." in full


def test_multifile_project_inputs_are_spliced(tmp_path):
    (tmp_path / "sections").mkdir()
    (tmp_path / "sections" / "methods.tex").write_text(
        "\\section{Methods}\\label{sec:methods}\nWe describe the methods here."
    )
    (tmp_path / "intro.tex").write_text(
        "\\section{Introduction}\nSee Section~\\ref{sec:methods} for details."
    )
    main = _write(
        tmp_path,
        r"""
\documentclass{article}
\begin{document}
\input{intro}
\include{sections/methods}
Closing remarks.
\end{document}
""",
        name="main.tex",
    )
    document = extract_text(main)
    headings = [b.text for b in document.blocks if b.kind == "heading"]
    assert headings == ["Introduction", "Methods"]
    full = document.full_text
    # Cross-file refs resolve against the whole assembled document.
    assert "See Section 2 for details." in full
    assert "We describe the methods here." in full
    assert "Closing remarks." in full


def test_multifile_flat_layout_falls_back_to_basename(tmp_path):
    # Web uploads arrive without folder structure; sections/x.tex must still
    # be found as x.tex next to the main file.
    (tmp_path / "chapter.tex").write_text("Chapter text here.")
    main = _write(
        tmp_path,
        "\\begin{document}\\input{sections/chapter}\\end{document}",
        name="main.tex",
    )
    assert "Chapter text here." in extract_text(main).full_text


def test_missing_and_cyclic_inputs_are_dropped(tmp_path):
    (tmp_path / "a.tex").write_text("From a. \\input{main}")
    main = _write(
        tmp_path,
        "\\begin{document}Before. \\input{a} \\input{nope} After.\\end{document}",
        name="main.tex",
    )
    full = extract_text(main).full_text
    assert "Before." in full
    assert "From a." in full
    assert "After." in full
    assert "nope" not in full


def test_typography_and_special_letters(tmp_path):
    body = r"``Quoted'' text --- pages 3--5, one\ldots{} and Stra\ss{}e."
    document = extract_text(_write(tmp_path, body))
    text = document.full_text
    assert '"Quoted" text' in text
    assert "— pages 3–5" in text
    assert "one..." in text
    assert "Straße" in text
