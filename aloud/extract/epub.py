"""EPUB extraction: stdlib zipfile + ElementTree + the shared HTML parser.

An EPUB is a zip: META-INF/container.xml points at an OPF package file,
whose <manifest> lists the content documents and whose <spine> gives their
reading order. Each spine document is XHTML, parsed with html_to_blocks.
No new dependencies.
"""

import posixpath
import xml.etree.ElementTree as ET
import zipfile

from aloud.errors import EmptyDocumentError, ExtractionError
from aloud.extract.base import Document
from aloud.extract.htmltext import html_to_blocks

_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"
_OPF_NS = "{http://www.idpf.org/2007/opf}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"


def extract(path):
    path = str(path)
    try:
        epub_zip = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExtractionError(f"Could not read EPUB file: {path}") from exc

    with epub_zip:
        names = set(epub_zip.namelist())
        if "META-INF/encryption.xml" in names:
            raise ExtractionError(
                f"EPUB appears to be DRM-protected/encrypted: {path} "
                "(Aloud does not decrypt DRM-protected books)"
            )

        opf_path = _find_opf_path(epub_zip, names, path)
        try:
            opf = ET.fromstring(epub_zip.read(opf_path))
        except ET.ParseError as exc:
            raise ExtractionError(f"Could not parse EPUB package data: {path}") from exc

        title_el = opf.find(f".//{_DC_NS}title")
        title = (title_el.text or "").strip() or None if title_el is not None else None

        manifest = {}
        for item in opf.findall(f".//{_OPF_NS}manifest/{_OPF_NS}item"):
            manifest[item.get("id")] = (item.get("href", ""), item.get("media-type", ""))

        opf_dir = posixpath.dirname(opf_path)
        blocks = []
        for itemref in opf.findall(f".//{_OPF_NS}spine/{_OPF_NS}itemref"):
            entry = manifest.get(itemref.get("idref"))
            if entry is None:
                continue
            href, media_type = entry
            if "html" not in media_type.lower():
                continue
            doc_path = posixpath.normpath(posixpath.join(opf_dir, href))
            if doc_path not in names:
                continue
            markup = epub_zip.read(doc_path).decode("utf-8", errors="replace")
            chapter_blocks, _ = html_to_blocks(markup)
            blocks.extend(chapter_blocks)

    if not blocks:
        raise EmptyDocumentError(f"No text content found in {path}")
    return Document(title=title, blocks=blocks, source_path=path)


def _find_opf_path(epub_zip, names, path):
    try:
        container = ET.fromstring(epub_zip.read("META-INF/container.xml"))
    except (KeyError, ET.ParseError) as exc:
        raise ExtractionError(
            f"Not a valid EPUB (missing or broken META-INF/container.xml): {path}"
        ) from exc
    rootfile = container.find(f".//{_CONTAINER_NS}rootfile")
    opf_path = rootfile.get("full-path") if rootfile is not None else None
    if not opf_path or opf_path not in names:
        raise ExtractionError(f"Not a valid EPUB (no package file): {path}")
    return opf_path
