"""Text extraction from uploaded bytes. Everything stays in memory.

Anything in a file that could carry an identifier is pulled into the text so detection sees it:
Word headers/footers, footnotes, comments, tracked changes, text boxes and author metadata;
PDF pages with no text layer and embedded images are reported as warnings (no OCR here, so
an image-only page blocks release instead of slipping through).
"""
from __future__ import annotations

import html as _html
import io
import re
import zipfile
from dataclasses import dataclass, field

SUPPORTED = (".txt", ".pdf", ".docx", ".htm", ".html")
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass
class Extracted:
    text: str
    warnings: list[dict] = field(default_factory=list)  # {"level": "block"|"info", "message": str}


def extract_text(filename: str, data: bytes) -> str:
    return extract_document(filename, data).text


def extract_document(filename: str, data: bytes) -> Extracted:
    name = filename.lower()
    if name.endswith((".txt", ".md")):
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                return Extracted(data.decode(enc))
            except UnicodeDecodeError:
                continue
    if name.endswith(".pdf"):
        return _pdf(data)
    if name.endswith(".docx"):
        return _docx(data)
    if name.endswith((".htm", ".html")):
        return _html_doc(data)
    raise ValueError(f"Unsupported file type: {filename} (use {', '.join(SUPPORTED)})")


def _pdf(data: bytes) -> Extracted:
    import pdfplumber

    pages, warnings = [], []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if page.images:
                text += f"\n[Image removed: page {i}, {len(page.images)} image(s)]"
                warnings.append({"level": "info", "message": f"Page {i}: {len(page.images)} image(s) removed, not checked."})
            if not text.strip() or (page.images and len((page.extract_text() or "").strip()) < 20):
                warnings.append({"level": "block", "message": f"Page {i} has no text (scan?). Remove it or use a text PDF."})
            pages.append(text)
        meta = {k: v for k, v in (pdf.metadata or {}).items() if k in ("Author", "Title", "Subject")  # Creator/Producer name software
                and isinstance(v, str) and v.strip()}
    text = "\n\n".join(pages)
    if meta:
        text += "\n\n[Document properties] " + "; ".join(f"{k}: {v}" for k, v in meta.items())
    return Extracted(text, warnings)


def _docx(data: bytes) -> Extracted:
    import docx
    from lxml import etree

    document = docx.Document(io.BytesIO(data))
    parts, warnings = [], []

    def para_text(p) -> str:
        # Tracked changes: show insertions as normal text and deletions explicitly, so both are scanned.
        out = []
        for node in p._p.iter():
            if node.tag == W + "t":
                out.append(node.text or "")
            elif node.tag == W + "delText":
                out.append(f"[deleted: {node.text or ''}]")
        return "".join(out)

    for section in document.sections:
        for label, part in (("Header", section.header), ("Footer", section.footer)):
            if part is not None and not part.is_linked_to_previous:
                txt = "\n".join(para_text(p) for p in part.paragraphs if para_text(p).strip())
                if txt:
                    parts.append(f"[{label}] {txt}")
    body = document.element.body
    for block in body.iterchildren():
        if block.tag == W + "p":
            parts.append("".join(n.text or "" if n.tag == W + "t" else f"[deleted: {n.text or ''}]"
                                 for n in block.iter() if n.tag in (W + "t", W + "delText")))
        elif block.tag == W + "tbl":
            for row in block.iter(W + "tr"):
                parts.append(" | ".join("".join(t.text or "" for t in cell.iter(W + "t")) for cell in row.iter(W + "tc")))

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        for xml_name, label in (("word/footnotes.xml", "Footnote"), ("word/endnotes.xml", "Endnote"),
                                ("word/comments.xml", "Comment")):
            if xml_name not in names:
                continue
            root = etree.fromstring(zf.read(xml_name))
            for note in root:
                txt = "".join(t.text or "" for t in note.iter(W + "t")).strip()
                if not txt:
                    continue
                author = note.get(W + "author")
                parts.append(f"[{label}{' by ' + author if author else ''}] {txt}")
        media = [n for n in names if n.startswith("word/media/")]
        if media:
            parts.append(f"[Images removed: {len(media)}]")
            warnings.append({"level": "info", "message": f"{len(media)} image(s) removed, not checked."})
        if any(n.startswith("word/embeddings/") for n in names):
            warnings.append({"level": "info", "message": "Embedded objects (e.g. chart data) removed."})

    props = document.core_properties
    meta = {k: getattr(props, k) for k in ("author", "last_modified_by", "title", "subject", "comments")
            if isinstance(getattr(props, k, None), str) and getattr(props, k).strip()}
    if meta:
        parts.append("[Document properties] " + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in meta.items()))
    return Extracted("\n".join(parts), warnings)


def _html_doc(data: bytes) -> Extracted:
    t = data.decode("utf-8", errors="replace")
    t = re.sub(r"(?is)<(script|style|ix:header)\b.*?</\1>", "", t)           # code + hidden XBRL facts
    t = re.sub(r'(?is)<div[^>]*display:\s*none[^>]*>.*?</div>', "", t)
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|table)>", "\n", t)
    t = re.sub(r"(?i)</t[dh]>", "  ", t)
    t = _html.unescape(re.sub(r"<[^>]+>", "", t))
    t = re.sub(r"[ \t ]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    warnings = []
    if re.search(rb"(?i)<img\b", data):
        warnings.append({"level": "info", "message": "Images in the page were removed from the output."})
    return Extracted(t, warnings)
