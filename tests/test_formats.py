"""The same rules must hold whatever the file format (HTML, PDF, Word) and for more than one kind of
finance document: an SEC 8-K (public filing) and a confidential M&A advisory memo (private engagement)."""
import io
import pathlib

import pytest

from engine import Session, consistency_check
from engine.extract import extract_document

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"


def to_docx(text: str, header: str = "", comment: str = "", author: str = "") -> bytes:
    import docx
    d = docx.Document()
    for para in text.split("\n"):
        d.add_paragraph(para)
    if header:
        d.sections[0].header.paragraphs[0].text = header
    if author:
        d.core_properties.author = author
    if comment and hasattr(d, "add_comment"):
        d.add_comment(d.paragraphs[0].runs, text=comment, author=author or "Reviewer")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def to_pdf(text: str) -> bytes:
    # A text-layer PDF built with pdfplumber's dependency (pypdfium2 can't write text), so use reportlab-free
    # minimal PDF writer: one page per ~60 lines, Helvetica, plain ASCII.
    lines = text.encode("ascii", "replace").decode().split("\n")
    pages, objs = [lines[i:i + 60] for i in range(0, len(lines), 60)] or [[""]], []
    kids = []
    for i, pg in enumerate(pages):
        stream = "BT /F1 9 Tf 40 780 Td 11 TL " + " ".join(
            "(" + ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") '" for ln in pg) + " ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        kids.append(len(objs) + 2 * len(pages) + 2)
    # Build: 1 catalog, 2 pages, 3 font, then page objects, then content streams
    out = ["%PDF-1.4"]
    body = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [" + " ".join(f"{4 + i} 0 R" for i in range(len(pages))) + f"] /Count {len(pages)} >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i in range(len(pages)):
        body.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {4 + len(pages) + i} 0 R >>")
    body += objs
    offsets, pdf = [], "%PDF-1.4\n"
    for n, ob in enumerate(body, 1):
        offsets.append(len(pdf.encode("latin-1")))
        pdf += f"{n} 0 obj\n{ob}\nendobj\n"
    xref = len(pdf.encode("latin-1"))
    pdf += f"xref\n0 {len(body) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    pdf += f"trailer\n<< /Size {len(body) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
    return pdf.encode("latin-1")


MSFT_HTML = (FIX / "msft_8k_2022-01-18.htm").read_bytes()
MSFT_TEXT = extract_document("8k.htm", MSFT_HTML).text
MEMO_TEXT = (FIX / "advisory_memo.txt").read_text()

MSFT_FORMATS = {
    "html": ("8-K.htm", MSFT_HTML),
    "pdf (browser print)": ("8-K.pdf", (FIX / "msft_8k_2022-01-18.pdf").read_bytes()),
    "pdf (text layer)": ("8-K.pdf", to_pdf(MSFT_TEXT)),
    "docx": ("8-K.docx", to_docx(MSFT_TEXT)),
    "txt": ("8-K.txt", MSFT_TEXT.encode()),
}
MEMO_FORMATS = {
    "txt": ("memo.txt", MEMO_TEXT.encode()),
    "docx (header, comment, author)": ("memo.docx", to_docx(
        MEMO_TEXT, header="Harrowgate Partners LLC — Project Falcon",
        comment="Per Dana (Northwind BD): don't share with the Fabrikam team", author="rliu@harrowgatepartners.com")),
    "pdf": ("memo.pdf", to_pdf(MEMO_TEXT)),
}


def outputs(name, data):
    s = Session.from_files([(name, data)])
    res = s.run()
    return s, res, res.outputs[0]


@pytest.mark.parametrize("fmt", list(MSFT_FORMATS))
def test_8k_every_format(fmt):
    s, res, out = outputs(*MSFT_FORMATS[fmt])
    for leak in ("Microsoft", "MICROSOFT", "Activision", "Dolliver", "91-1144442", "001-37845", "882-8080",
                 "microsoft.com", "$68.7 billion"):
        assert leak not in out, f"{fmt}: {leak} leaked"
    for keep in ("Securities Exchange Act of 1934", "Rule 425"):
        assert keep in " ".join(out.split()), f"{fmt}: {keep} was removed"
    junk = {"mozilla", "gecko", "macintosh", "press release", "state", "irs"}
    assert not any(e.canonical.lower() in junk for e in res.entities), fmt
    assert consistency_check(res)["all_consistent"], fmt


@pytest.mark.parametrize("fmt", list(MEMO_FORMATS))
def test_advisory_memo_every_format(fmt):
    s, res, out = outputs(*MEMO_FORMATS[fmt])
    flat = " ".join(out.split())
    # parties: client (named + domain), counterparty, target, the adviser itself
    for leak in ("Northwind", "Fabrikam", "Tailspin", "Harrowgate", "harrowgatepartners", "Rachel Liu", "Marcus Bell",
                 "Bell's", "4471-993012", "(206) 555-0142", "2200 Harbor Drive", "$412.5 million", "10,800,000"):
        assert leak not in flat, f"{fmt}: {leak} leaked"
    # a public company cited only for facts is left for a reviewer to decide, not silently kept or dropped
    contoso = next((e for e in res.entities if "Contoso" in e.canonical), None)
    assert contoso is not None and contoso.status == "pending", fmt
    rules = {f.rule for f in res.flags}
    assert "rights.privileged" in rules, f"{fmt}: privilege not flagged"
    assert "flag.family_relationship" in rules, f"{fmt}: brother-in-law not flagged"
    assert consistency_check(res)["all_consistent"], fmt


def test_memo_known_parties_join_existing_entities():
    s = Session.from_files([MEMO_FORMATS["txt"]])
    before = {e.key: e.label for e in s.run().entities}
    s.set_parties([{"name": "Project Falcon", "type": "ORGANIZATION"}, {"name": "Tailspin", "type": "ORGANIZATION"}])
    res = s.run()
    assert "Falcon" not in res.outputs[0] and "Tailspin" not in res.outputs[0]
    # listing a party the detector already found doesn't create a second pseudonym for it
    assert len([e for e in res.entities if "tailspin" in e.key]) == 1
    assert {k: v for k, v in before.items() if k in {e.key for e in res.entities}} == \
           {e.key: e.label for e in res.entities if e.key in before}


def test_memo_privilege_marker_is_flagged_not_redacted():
    s, res, out = outputs(*MEMO_FORMATS["txt"])
    assert out.startswith("PRIVILEGED & CONFIDENTIAL")
    assert any(f.rule == "rights.privileged" and "PRIVILEGED" in f.text for f in res.flags)


def test_docx_hidden_parts_are_redacted_too():
    s, res, out = outputs(*MEMO_FORMATS["docx (header, comment, author)"])
    assert "rliu@harrowgatepartners.com" not in out
    if "[Comment" in res.texts[0]:
        assert "Dana" not in out.split("[Comment", 1)[1].split("\n", 1)[0] or "Person" in out
