"""Long filings (a 230-page proxy statement crashed the hosted app): memory, speed and progress."""
import io
import random
import re

from engine import Session
from engine.detect import CHUNK, chunks
from engine.registry import Spans, _find, _greedy, _overlaps, _rx, _word_index, sentence_spans


def test_chunks_cover_the_text_and_cut_between_words():
    words = ["Jane", "Smith", "of", "Acme", "Corp", "agreed.", "\n\n", "Section", "4.1", "\n"]
    rng = random.Random(1)
    text = " ".join(rng.choice(words) for _ in range(30_000))
    pieces = list(chunks(text))
    assert "".join(p for _, p in pieces) == text
    assert all(len(p) <= CHUNK for _, p in pieces) and len(pieces) > 5
    for off, p in pieces[1:]:
        assert text[off - 1].isspace()  # never in the middle of a word


def test_names_near_a_chunk_boundary_are_found():
    filler = "The parties agreed to the terms described in this section. " * (CHUNK // 60)
    text = filler + "\n\nJonathan Mercer signed the agreement on behalf of Harborview Capital LLC.\n\n" + filler
    res = Session.from_texts([("long.txt", text)]).run()
    assert "Jonathan Mercer" not in res.outputs[0] and "Harborview" not in res.outputs[0]


def test_span_index_and_greedy_match_the_plain_versions():
    rng = random.Random(7)
    spans = [(a, a + rng.randint(1, 30)) for a in (rng.randint(0, 5000) for _ in range(400))]
    ix = Spans(spans)
    for _ in range(2000):
        a = rng.randint(0, 5000)
        q = (a, a + rng.randint(1, 40))
        assert ix.hit(q) == _overlaps(q, spans)
    cands = sorted(((s, e, i) for i, (s, e) in enumerate(spans)), key=lambda c: (-(c[1] - c[0]), c[0]))
    plain = []
    for c in cands:
        if not any(c[0] < e and s < c[1] for s, e, _ in plain):
            plain.append(c)
    assert _greedy(cands) == sorted(plain)


def test_variant_search_by_word_index_matches_a_full_scan():
    text = ("Mr. Jonathan R. Mercer met JONATHAN MERCER and Mercer's sister. Goldsmith and Smithson; "
            "Acme Holdings, Inc. and ACME HOLDINGS met in Acme Holdings LLC's office.\n") * 40
    idx = _word_index(text)
    for pat, flags, v in [(r"\bJonathan(?:\s+[A-Za-z]\.?)*\s+Mercer\b", re.I, "Jonathan Mercer"),
                          (r"\b(?:Mercer|MERCER)\b", 0, "Mercer"),
                          (r"\bSmith\b", re.I, "Smith"),
                          (r"\bAcme\s+Holdings\b(?:,?\s+(?:Inc\.?|LLC))?", re.I, "Acme Holdings")]:
        rx = _rx(pat, flags)
        assert sorted(m.span() for m in _find(rx, v, text, idx)) == [m.span() for m in rx.finditer(text)]


def test_sentence_spans_long_text_is_fast_and_handles_abbreviations():
    import time
    text = "Mr. Smith of Acme Inc. agreed.\n12. The next item follows! " * 20_000   # ~1.2M characters
    t = time.time()
    spans = sentence_spans(text)
    assert time.time() - t < 5
    first = [text[a:b] for a, b in spans[:3]]
    assert first[0] == "Mr. Smith of Acme Inc. agreed."
    assert first[1] == "12. The next item follows!"


def test_progress_is_reported_while_reading_and_detecting():
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    for i in range(3):
        c.drawString(72, 700, f"Page {i + 1}: Jane Smith of Acme Corp called Robert Vance.")
        c.showPage()
    c.save()
    seen = []
    Session.from_files([("three.pdf", buf.getvalue())], progress=lambda msg, f: seen.append((msg, f)))
    fractions = [f for _, f in seen]
    assert fractions == sorted(fractions) and 0 <= fractions[0] and fractions[-1] <= 1
    assert any("page 3 of 3" in m for m, _ in seen) and any("Finding names" in m for m, _ in seen)
