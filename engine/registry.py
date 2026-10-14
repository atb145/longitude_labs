"""Cross-document entity registry and anonymization.

``build()`` is a pure function of (documents, detections, policy, settings, ops). Detection
(the slow NLP step) runs once; every toggle, decision, merge or split just re-runs ``build``,
which takes milliseconds.

Ops (user actions) are plain dicts so they can be serialized behind an API later:
    decisions: {item_key: {"action": "accept" | "keep" | "edit", "value": str}}
    merges:    [(src_entity_key, dst_entity_key), ...]
    splits:    [(entity_key, variant_text), ...]
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .detect import Detection
from .policy import Policy

HONORIFICS = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "hon"}
ORG_SUFFIXES = {"llc", "l.l.c.", "inc", "inc.", "incorporated", "corp", "corp.", "corporation", "company",
                "l.p.", "lp", "ltd", "ltd.", "limited", "co", "co.", "plc", "n.a.", "ag", "sa", "s.a.", "n.v.", "gmbh"}
GENERIC_ORG_WORDS = {"first", "national", "american", "capital", "global", "united", "general",
                     "international", "the", "new", "north", "south", "east", "west", "bank",
                     "securities", "partners", "group", "holdings", "trust"}
HONORIFIC_RX = r"(?:(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof)\.?\s+)?"
POSSESSIVE_RX = re.compile(r"[’']s$")

DECISION_LABEL = {"auto": "auto", "manual": "manual redaction", "accepted": "accepted", "kept": "overridden", "edited": "overridden",
                  "pending": "unreviewed", "public": "kept (public company)",
                  "setting": "kept (policy setting)"}


# ---------------------------------------------------------------- data classes
@dataclass
class Entity:
    key: str                      # stable "TYPE:normalized" id used by ops
    type: str
    variants: set[str]            # normalized (lowercase) variant strings
    canonical: str = ""
    score: float = 0.0
    sources: set[str] = field(default_factory=set)
    mentions: list[tuple[int, int, int, str]] = field(default_factory=list)  # (doc, start, end, text)
    form_variant: dict[str, str] = field(default_factory=dict)  # mention text -> variant that matched it
    label: str = ""               # registry id: "Person A"
    proposed: str = ""            # replacement the policy proposes
    status: str = "auto"          # auto | pending | accepted | kept | edited | public | setting
    tier: str = "auto"            # auto | review
    rule: str = ""
    reason: str = ""
    custom: str = ""
    nick_target: str = ""         # key of the person this may be a nickname of ("Bob" → Robert Vance)

    @property
    def tokens(self) -> list[str]:
        return self.key.split(":", 1)[1].split()

    def replacement_for(self, mention_text: str) -> str:
        if self.status in ("kept", "public", "setting"):
            return mention_text
        value = self.custom if self.status == "edited" else self.proposed
        if mention_text.isupper() and len(mention_text) > 3 and self.type in ("PERSON", "ORGANIZATION"):
            return value.upper()
        return value

    @property
    def is_redacted(self) -> bool:
        return self.status not in ("kept", "public", "setting")


@dataclass
class Flag:
    key: str
    rule: str
    text: str
    proposed: str
    reason: str
    mentions: list[tuple[int, int, int, str]] = field(default_factory=list)
    status: str = "pending"       # pending | accepted | kept | edited
    custom: str = ""
    scope: str = "match"          # "sentence" flags cover a whole sentence (combinations, privilege)

    def replacement_for(self, mention_text: str) -> str:
        if self.status == "accepted":
            return self.proposed
        if self.status == "edited":
            return self.custom
        return mention_text


@dataclass
class Segment:
    start: int
    end: int
    original: str
    replacement: str
    kind: str                     # entity | flag
    ref: str                      # entity / flag key
    type: str
    status: str


@dataclass
class Result:
    names: list[str]
    texts: list[str]
    entities: list[Entity]
    flags: list[Flag]
    segments: list[list[Segment]]
    outputs: list[str]
    queue: list[str]              # item keys: "E|<entity key>" / "F|<flag key>"
    deleted: list[tuple[str, str, str]] = field(default_factory=list)  # (key, type, sample text)

    def entity(self, key: str) -> Entity | None:
        return next((e for e in self.entities if e.key == key), None)

    def flag(self, key: str) -> Flag | None:
        return next((f for f in self.flags if f.key == key), None)

    def item(self, item_key: str):
        kind, key = item_key.split("|", 1)
        return self.entity(key) if kind == "E" else self.flag(key)


# ---------------------------------------------------------------- normalization
def _strip_possessive(text: str) -> str:
    return POSSESSIVE_RX.sub("", text.strip())


def person_tokens(text: str) -> list[str]:
    out = []
    for tok in re.findall(r"[A-Za-z][A-Za-z'\-]*\.?", _strip_possessive(text)):
        base = tok.rstrip(".").lower()
        if base in HONORIFICS or len(base) == 1:
            continue
        out.append(base)
    return out


def org_tokens(text: str) -> list[str]:
    toks = [t.lower() for t in _strip_possessive(text).replace(",", " ").split()]
    while toks and toks[0] == "the":
        toks = toks[1:]
    while toks and toks[-1] in ORG_SUFFIXES:
        toks = toks[:-1]
    return toks


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def normalize(etype: str, text: str) -> str:
    if etype == "PERSON":
        return " ".join(person_tokens(text))
    if etype == "ORGANIZATION":
        return " ".join(org_tokens(text))
    if etype in ("ACCOUNT_NUMBER", "CRD_NUMBER", "PHONE_NUMBER", "US_SSN"):
        return _digits(text)
    if etype == "DATE":
        d = parse_date(text)
        return d.strftime("%Y-%m-%d") if d else text.lower()
    return re.sub(r"\s+", " ", text.strip().lower())


def parse_date(text: str) -> datetime | None:
    t = re.sub(r"\s+", " ", text.strip()).replace("Sept.", "Sep.")
    for fmt in ("%B %d, %Y", "%b. %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            continue
    return None


ADDRESS_TAIL = re.compile(r",\s+([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?),\s+([A-Z]{2}|[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)"
                          r"(?:\s+\d{5}(?:-\d{4})?)?\s*$")


def _letters(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


_ABBR = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|No|Nos|Inc|Co|Corp|Ltd|Jr|Sr|St|v|vs|U\.S|Wash|[A-Z])$")
_CUT = re.compile(r"[.!?][\"”’)]*\s+|:[ \t]*\n|\n\s*\n|\n(?=\s*(?:\d{1,3}\.|[IVX]+\.)\s)")


def sentence_spans(text: str) -> list[tuple[int, int]]:
    cuts = [0]
    for m in _CUT.finditer(text):
        if m.group()[0] in ".!?" and (_ABBR.search(text[:m.start()])
                                       or re.search(r"(?:^|\n)\s*(?:\d{1,3}|[IVX]+)$", text[:m.start()])):
            continue
        cuts.append(m.end())
    cuts = sorted(set(cuts + [len(text)]))
    out = []
    for a, b in zip(cuts, cuts[1:]):
        seg = text[a:b]
        lead = len(seg) - len(seg.lstrip())
        trail = len(seg.rstrip())
        if trail > lead:
            out.append((a + lead, a + trail))
    return out


_UNITS = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mm": 1e6, "billion": 1e9, "b": 1e9, "bn": 1e9}


def round_figure(text: str) -> str:
    """$4.75M → ~$5M · $68.7 billion → ~$70 billion · 41,000 shares → ~40,000 shares. One significant
    figure, unit kept, never random noise (noise would break totals)."""
    m = re.match(r"(\$?)\s?([\d,]+(?:\.\d+)?)(?:\s?(million|billion|thousand|[MBKmbk]|bn|mm)\b)?(.*)$", text.strip())
    if not m:
        return text
    cur, num, unit, rest = m.groups()
    value = float(num.replace(",", ""))
    if value <= 0:
        return text
    exp = math.floor(math.log10(value))
    r = round(value / 10 ** exp) * 10 ** exp
    shown = f"{r:,.0f}" if r >= 1 else f"{r:g}"
    sep = " " if unit and len(unit) > 2 else ""
    return f"~{cur}{shown}{sep}{unit or ''}{rest}"


def _date_like(original: str, d: datetime) -> str:
    o = original.strip()
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", o):
        return d.strftime("%m/%d/%Y")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", o):
        return d.strftime("%Y-%m-%d")
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _label(template: str, n: int) -> str:
    return template.replace("{A}", _letters(n)).replace("{1}", str(n + 1))


def norm_form(etype: str, text: str) -> str:
    """How a written form is compared when splitting: honorifics, initials, possessives and case ignored."""
    t = text.removeprefix(LIT)
    if etype == "PERSON":
        return " ".join(person_tokens(t))
    if etype == "ORGANIZATION":
        return " ".join(org_tokens(t))
    return re.sub(r"\s+", " ", _strip_possessive(t)).lower()


def split_key(ent_key: str, src_type: str, form: str, new_type: str | None = None) -> str:
    """Registry key of the entity a split creates (the UI uses it to bring that entity into focus)."""
    key = f"{new_type or src_type}:{norm_form(src_type, form)}"
    return key if key != ent_key else f"{ent_key}#split"


def type_title(etype: str) -> str:
    """Display name for a type, including custom ones typed in by a reviewer ("OTHER:Project codename")."""
    return etype.split(":", 1)[1] if etype.startswith("OTHER:") else etype.replace("_", " ").title()


# ---------------------------------------------------------------- matching patterns
# Longest first ("Corporation" before "Corp", so "Microsoft Corporation" isn't cut to "Microsoft Corp" + "oration");
# common suffixes in any case ("INC.", "Inc."), two-letter ones only as written (", AG" vs ", against").
ORG_SUFFIX_RX = (r"(?:(?i:Incorporated|Inc\.?|Corporation|Corp\.?|Company|Co\.|Limited|Ltd\.?|L\.L\.C\.|LLC|L\.P\.|LP|"
                 r"N\.A\.|plc|Holdings)|(?-i:AG|SA|S\.A\.|N\.V\.|GmbH))(?!\w)")
ALIAS_RX = re.compile(r'\s*\((?:the\s+|collectively,?\s+)?[“"]([^”"\n]{2,60})[”"](?:\s+or\s+(?:the\s+)?[“"]([^”"\n]{2,60})[”"])?')
LIT = "lit:"  # variant prefix: an exact alias defined in the text, e.g. (“MGM”) or (“J. Reichman”)


def _variant_pattern(etype: str, variant: str) -> tuple[str, int]:
    """Regex for finding a variant anywhere in the documents."""
    if variant.startswith(LIT):
        words = variant[len(LIT):].split()
        body = r"\s+".join(map(re.escape, words))
        upper = r"\s+".join(map(re.escape, (w.upper() for w in words)))
        return r"(?<![\w])(?:" + body + "|" + upper + r")(?![\w])", 0
    if etype == "PERSON":
        toks = variant.split()
        if len(toks) >= 2:
            body = re.escape(toks[0]) + r"(?:\s+[A-Za-z]\.?)*\s+" + r"\s+".join(map(re.escape, toks[1:]))
            return HONORIFIC_RX + r"\b" + body + r"\b", re.IGNORECASE
        cap = toks[0].capitalize()
        # Surname alone: Title-case or ALL-CAPS only, with optional honorific / initial.
        return (r"(?:(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof)\.?\s+|\b[A-Z]\.\s+)?\b(?:" + re.escape(cap) + "|"
                + re.escape(cap.upper()) + r")\b"), 0
    if etype == "ORGANIZATION":
        toks = variant.split()
        if len(toks) >= 2:
            suffix = r"(?:,?\s+" + ORG_SUFFIX_RX + r")?"
            return r"\b" + r"\s+".join(map(re.escape, toks)) + r"\b" + suffix, re.IGNORECASE
        cap = toks[0].capitalize()
        suffix = r"(?:,?\s+" + ORG_SUFFIX_RX + r")?"
        return r"\b(?:" + re.escape(cap) + "|" + re.escape(cap.upper()) + r")\b" + suffix, 0
    if etype in ("ACCOUNT_NUMBER", "CRD_NUMBER", "PHONE_NUMBER", "US_SSN"):
        return None, 0  # literal variants handled via mention texts
    return None, 0


def _keep_spans(policy: Policy, text: str) -> list[tuple[int, int]]:
    spans = []
    for term in policy.keep_terms:
        flags = 0 if term.isupper() and len(term) <= 5 else re.IGNORECASE
        body = r"\s+".join(map(re.escape, term.split()))  # PDF text wraps lines mid-name
        spans += [m.span() for m in re.finditer(r"\b" + body + r"\b", text, flags)]
    for rx in policy.keep_patterns:
        spans += [m.span() for m in rx.finditer(text)]
    return spans


def _overlaps(a: tuple[int, int], spans) -> bool:
    return any(a[0] < e and s < a[1] for s, e in spans)


def _greedy(cands: list[tuple]) -> list[tuple]:
    """Pick non-overlapping candidates; input already sorted by priority."""
    chosen: list[tuple] = []
    for c in cands:
        if not _overlaps((c[0], c[1]), [(x[0], x[1]) for x in chosen]):
            chosen.append(c)
    return sorted(chosen)


def _clean_name(d: Detection, policy: Policy, lower_words: set[str]) -> Detection | None:
    """Trim words detectors glue onto names ("Defendants Meyer Global LLC", "Company A wired")."""
    if d.type == "URL":  # a sentence's full stop or comma isn't part of the link
        text = d.text.rstrip(".,;:!?)]”\"'")
        return Detection(d.doc, d.start, d.start + len(text), text, d.type, d.score, d.source) if text else None
    if d.type not in ("PERSON", "ORGANIZATION"):
        return d
    if re.sub(r"\s+", " ", _strip_possessive(d.text)).strip("“”\"").lower() in policy.not_entities:
        return None
    toks = list(re.finditer(r"\S+", d.text))

    def bare(t):
        return t.group().strip("“”\"(),;:").lower()

    def lead_noise(t):
        w = bare(t)
        return (w in policy.name_noise or t.group()[:1].islower() or re.fullmatch(r"[ivx]+\.?", w) is not None
                or w in {"defendant", "defendants", "plaintiff", "respondent"})

    i, j = 0, len(toks)
    while i < j and lead_noise(toks[i]):
        i += 1
    # keep only the first capitalized run (connectors allowed inside it)
    k = i
    while k < j and (toks[k].group()[:1].isupper() or toks[k].group()[:1].isdigit() or bare(toks[k]) in {"and", "of", "&", "de"}):
        if k > i and bare(toks[k]) in policy.name_noise:
            break  # "Microsoft Announces Partnership…" → "Microsoft"
        k += 1
    j = k
    while j > i and bare(toks[j - 1]) in {"and", "of", "&", "for", "de"}:
        j -= 1
    if i >= j:
        return None
    start, end = d.start + toks[i].start(), d.start + toks[j - 1].end()
    text = d.text[toks[i].start():toks[j - 1].end()].rstrip(",;:“”\"(")
    end = start + len(text)
    norm = re.sub(r"\s+", " ", _strip_possessive(text)).strip("“”\"").lower()
    if not norm or norm in policy.not_entities:
        return None
    # A single capitalized word that also appears in lowercase elsewhere is a common word, not a name.
    if d.source == "SpacyRecognizer" and " " not in norm and norm in lower_words:
        return None
    return Detection(d.doc, start, end, text, d.type, d.score, d.source)


def _propagate(texts, ents, keep, manual=frozenset()):
    for e in ents.values():
        e.mentions = []
        e.form_variant = {}
    for doc, text in enumerate(texts):
        cands = []
        for e in ents.values():
            for v in e.variants:
                pat, fl = _variant_pattern(e.type, v)
                if pat is None:
                    pat = (r"\b" if v[:1].isalnum() else "") + re.escape(v) + (r"\b" if v[-1:].isalnum() else "")
                    fl = re.IGNORECASE
                for m in re.finditer(pat, text, fl):
                    if e.key in manual or not _overlaps(m.span(), keep[doc]):  # a manual redaction beats the keep list
                        cands.append((m.start(), m.end(), e.key, v))
        cands.sort(key=lambda c: (-(c[1] - c[0]), c[2] not in manual, not c[3].startswith(LIT), c[0]))
        for s, end, key, v in _greedy(cands):
            ents[key].mentions.append((doc, s, end, text[s:end]))
            ents[key].form_variant[text[s:end]] = v


# ---------------------------------------------------------------- build
def build(names: list[str], texts: list[str], detections: list[Detection], policy: Policy,
          settings: dict, ops: dict) -> Result:
    decisions = ops.get("decisions", {})
    disabled = set(settings.get("disabled", []))
    keep = [[] if "keep_list" in disabled else _keep_spans(policy, t) for t in texts]
    detections = [d for d in detections if d.source not in disabled]  # edge-case recognizers switched off

    # 1. Resolve overlapping detections per document (policy recognizers beat spaCy, longer wins).
    # Words used in lowercase are ordinary words — except inside web addresses and emails, where
    # "news.microsoft.com" must not turn Microsoft into an ordinary word.
    addr = re.compile(r"\S+@\S+|\b(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|gov|edu|io|co|ai|us|uk)\b\S*")
    lower_words = {w for t in texts for w in re.findall(r"(?<![\w’'-])[a-z][a-z’'-]+", addr.sub(" ", t))}
    locations = {re.sub(r"\s+", " ", d.text).lower() for d in detections if d.type == "LOCATION"}
    cleaned = [_clean_name(d, policy, lower_words) for d in detections]
    usable = [d for d in cleaned
              if d and policy.spec(d.type).get("action") != "ignore" and not _overlaps((d.start, d.end), keep[d.doc])
              and re.sub(r"\s+", " ", _strip_possessive(d.text)).lower() not in policy.not_entities]
    # A word the language model calls a company but that another detector owns (a ticker, or a
    # person's surname elsewhere in the set) belongs to that other entity.
    owned = {d.text.lower() for d in usable if d.source != "SpacyRecognizer" and d.type != "ORGANIZATION"}
    person_words = {t for d in usable if d.type == "PERSON" and len(person_tokens(d.text)) >= 2
                    for t in person_tokens(d.text)}
    owned |= person_words | locations | {t for loc in locations for t in loc.split()}

    def generic(d):  # every word of the name is also used as an ordinary lowercase word
        toks = org_tokens(d.text)
        return d.type == "ORGANIZATION" and toks and all(t.rstrip("’'s") in lower_words for t in toks)
    usable = [d for d in usable if not generic(d)
              and not (d.type == "ORGANIZATION" and len(org_tokens(d.text)) == 1 and org_tokens(d.text)[0] in owned)]
    usable = [d for d in usable if not (d.type == "ORGANIZATION" and d.source == "SpacyRecognizer"
                                        and (" ".join(org_tokens(d.text)) in owned
                                             or org_tokens(d.text)[:1] and org_tokens(d.text)[0].rstrip("’'s") in person_words))]
    # Ticker lookup (SEC company_tickers list): a bare "MSFT" is a ticker unless it's an ordinary word here,
    # a common acronym, or part of an all-caps heading (exchange names next to it are fine: "MSFT NASDAQ").
    if policy.tickers:
        exchanges = {"NASDAQ", "NYSE", "AMEX", "OTC", "NYSEARCA", "CBOE", "ARCA", "LSE", "TSX"}
        keep_upper = {k.upper() for k in policy.keep_terms}
        for doc, text in enumerate(texts):
            for m in re.finditer(r"(?<![\w$.@/-])([A-Z]{2,5})(?![\w-])", text):
                t = m.group(1)
                if (t not in policy.tickers or t in policy.ticker_stopwords or t.lower() in lower_words
                        or re.fullmatch(r"[IVXLCDM]+", t)                                   # roman numerals
                        or re.search(r"(?i)\b(?:a|an|the)\s+$", text[max(0, m.start() - 5):m.start()])
                        and re.match(r"\s+[a-z]", text[m.end():m.end() + 3])                  # "a UPS box"
                        or t.lower() in policy.not_entities or t in keep_upper or _overlaps(m.span(), keep[doc])):
                    continue
                near = (re.findall(r"[A-Za-z]{2,}", text[max(0, m.start() - 40):m.start()])[-1:]
                        + re.findall(r"[A-Za-z]{2,}", text[m.end():m.end() + 40])[:1])
                if any(w.isupper() and w not in exchanges for w in near) and not any(w in exchanges for w in near):
                    continue  # inside a heading like "UNITED STATES DISTRICT COURT"
                usable.append(Detection(doc, m.start(), m.end(), t, "TICKER", 0.9, "ticker_list"))

    resolved: list[Detection] = []
    for doc in range(len(texts)):
        cands = sorted((d for d in usable if d.doc == doc),
                       key=lambda d: (d.source == "SpacyRecognizer", -(d.end - d.start), -d.score))
        resolved += [x[2] for x in _greedy([(d.start, d.end, d) for d in cands])]

    # 2. Group detections into registry entities.
    ents: dict[str, Entity] = {}

    def add(etype: str, norm: str, det: Detection, variant: str | None = None) -> Entity:
        key = f"{etype}:{norm}"
        e = ents.setdefault(key, Entity(key=key, type=etype, variants=set()))
        e.variants.add(variant or norm)
        e.score = max(e.score, det.score)
        e.sources.add(det.source)
        return e

    orgs = sorted((d for d in resolved if d.type == "ORGANIZATION"), key=lambda d: -len(org_tokens(d.text)))
    for d in orgs:
        toks = org_tokens(d.text)
        if not toks or all(t in GENERIC_ORG_WORDS or t in policy.not_entities for t in toks):
            continue
        hit = next((e for e in ents.values() if e.type == "ORGANIZATION"
                    and (e.tokens == toks or e.tokens[:len(toks)] == toks)), None)
        add("ORGANIZATION", " ".join(hit.tokens) if hit else " ".join(toks), d, " ".join(toks))

    org_words = {v for e in ents.values() for v in e.variants} | {
        e.tokens[0] for e in ents.values() if len(e.tokens) > 1 and e.tokens[0] not in GENERIC_ORG_WORDS}
    persons = sorted((d for d in resolved if d.type == "PERSON"), key=lambda d: -len(person_tokens(d.text)))
    for d in persons:
        toks = person_tokens(d.text)
        if not toks or " ".join(toks) in org_words:
            continue  # spaCy sometimes tags a company short-name as a person
        pe = [e for e in ents.values() if e.type == "PERSON"]
        hits = [e for e in pe if e.tokens == toks]
        if not hits and len(toks) >= 2:
            hits = [e for e in pe if e.tokens[0] == toks[0] and e.tokens[-1] == toks[-1]]
        if not hits and len(toks) == 1:
            hits = [e for e in pe if len(e.tokens) > 1 and e.tokens[-1] == toks[0]]
        target = " ".join(hits[0].tokens) if len(hits) == 1 else " ".join(toks)
        add("PERSON", target, d, " ".join(toks))

    for d in resolved:
        if d.type not in ("PERSON", "ORGANIZATION"):
            add(d.type, normalize(d.type, d.text), d, d.text.strip())

    # Context tool — known parties: names the reviewer lists up front (client, counterparties, codenames) are
    # always redacted, with every variant, whatever the detectors found.
    for party in ops.get("parties", []):
        name = re.sub(r"\s+", " ", str(party.get("name", "")).strip())
        etype = party.get("type") or "ORGANIZATION"
        if not name:
            continue
        norm = normalize(etype, name) or name.lower()
        hit = next((e for e in ents.values() if e.type == etype and
                    (e.key == f"{etype}:{norm}" or (etype == "ORGANIZATION" and e.tokens[:len(norm.split())] == norm.split()))), None)
        e = hit or ents.setdefault(f"{etype}:{norm}", Entity(key=f"{etype}:{norm}", type=etype, variants=set()))
        e.variants.update({norm, LIT + name})
        e.score, e.sources = 1.0, e.sources | {"known_party"}

    # Context tool — ticker linking: "MSFT" stands for MICROSOFT CORP, so that company is in the document too.
    for t_ent in [e for e in list(ents.values()) if e.type == "TICKER"]:
        title = policy.tickers.get(t_ent.key.split(":", 1)[1].upper(), "")
        toks = org_tokens(title.replace(".", " ").replace("/", " "))
        if not toks or (len(toks) == 1 and toks[0] in lower_words):
            continue
        hit = next((e for e in ents.values() if e.type == "ORGANIZATION" and e.tokens[:len(toks)] == toks), None)
        if hit:
            hit.sources.add("ticker_link")
            continue
        if any(re.search(r"(?<![\w-])" + r"\s+".join(re.escape(w.capitalize()) for w in toks) + r"(?![\w-])", t)
               or re.search(r"(?<![\w-])" + r"\s+".join(re.escape(w.upper()) for w in toks) + r"(?![\w-])", t)
               for t in texts):
            key = f"ORGANIZATION:{' '.join(toks)}"
            ents.setdefault(key, Entity(key=key, type="ORGANIZATION", variants={" ".join(toks)}, score=0.95,
                                        sources={"ticker_link"}))

    # Context tool — domain linking: "microsoft.com" / "press@microsoft.com" in a document means "Microsoft"
    # is a party name there, even if the language model missed it.
    domain_rx = re.compile(r"(?:@|\b(?:https?://)?(?:[\w-]+\.)*)([a-z][\w-]{2,})\.(?:com|org|net|io|co|ai|biz|us|uk|de|fr)\b",
                           re.IGNORECASE)
    labels = {m.group(1).lower() for t in texts for m in domain_rx.finditer(t)} - policy.generic_domains
    for lab in labels:
        if any(e.type == "ORGANIZATION" and lab in e.tokens for e in ents.values()):
            continue
        if any(re.search(r"(?<![\w.@/-])(?:" + re.escape(lab.capitalize()) + "|" + re.escape(lab.upper()) + r")(?![\w-])", t)
               for t in texts) and lab not in policy.not_entities:
            key = f"ORGANIZATION:{lab}"
            ents.setdefault(key, Entity(key=key, type="ORGANIZATION", variants={lab}, score=0.95,
                                        sources={"domain_link"}))

    # Derived variants so short forms are caught everywhere (surname, company short name).
    # A surname shared by two people (David and Justine Reichman) is ambiguous on its own, so it is
    # only linked through an explicit alias in the text, never derived.
    surnames = defaultdict(int)
    for e in ents.values():
        if e.type == "PERSON" and len(e.tokens) >= 2:
            surnames[e.tokens[-1]] += 1
    first_words = defaultdict(int)
    for e in ents.values():
        if e.type == "ORGANIZATION" and len(e.tokens) >= 2:
            first_words[e.tokens[0]] += 1
    for e in ents.values():
        if e.type == "PERSON" and len(e.tokens) >= 2:
            e.variants.add(f"{e.tokens[0]} {e.tokens[-1]}")
            if surnames[e.tokens[-1]] == 1 and len(e.tokens[-1]) >= 3 and not re.fullmatch(r"[ivx]+", e.tokens[-1]):
                e.variants.add(e.tokens[-1])
            else:
                e.variants.discard(e.tokens[-1])
        if e.type == "ORGANIZATION" and len(e.tokens) >= 2:
            e.variants.add(" ".join(e.tokens))
            w = e.tokens[0]
            if (w not in GENERIC_ORG_WORDS and w not in policy.not_entities and len(w) >= 4 and first_words[w] == 1 and w not in surnames
                    and w not in lower_words):
                e.variants.add(w)
    ambiguous = {n for n, c in surnames.items() if c > 1}
    for key in [k for k, e in ents.items() if e.type == "PERSON" and len(e.tokens) == 1 and e.tokens[0] in ambiguous]:
        del ents[key]  # resolved by aliases below

    # Manual redactions from the Documents page: a literal phrase, redacted everywhere it appears.
    manual: dict[str, dict] = {}
    for item in ops.get("manual", []):
        phrase = re.sub(r"\s+", " ", item["text"].strip())
        etype = item.get("type") or "MANUAL"
        key = f"{etype}:manual:{phrase.lower()}"
        e = ents.setdefault(key, Entity(key=key, type=etype, variants=set()))
        e.variants.add(LIT + phrase)
        e.score = 1.0
        e.sources.add("manual")
        manual[key] = item
    # A phrase redacted "as" an existing entity joins that entity (same pseudonym everywhere).
    for target, phrase in ops.get("aliases", []):
        if target in ents:
            ents[target].variants.add(LIT + re.sub(r"\s+", " ", phrase.strip()))
            manual.setdefault(target, None)

    # 3. User splits and merges. A split moves every variant that is the same written name as the
    #    chosen form (Okafor, Mr. Okafor, OKAFOR, Okafor's and the defined alias “Okafor” all go
    #    together), optionally into another entity type or into an existing entity.
    split_into: set[str] = set()
    for op in ops.get("splits", []):
        ent_key, form, new_type, into = (list(op) + [None, None])[:4]
        src = ents.get(ent_key)
        if not src:
            continue
        target = norm_form(src.type, form)
        moving = {v for v in src.variants if norm_form(src.type, v) == target}
        if not moving:
            continue
        etype = new_type or src.type
        if into and into in ents and into != ent_key:
            dst = ents[into]
        else:
            new_key = split_key(ent_key, src.type, form, new_type)
            dst = ents.setdefault(new_key, Entity(key=new_key, type=etype, variants=set()))
        src.variants -= moving
        dst.variants |= moving
        split_into.add(dst.key)
        dst.score, dst.sources = max(dst.score, src.score), dst.sources | src.sources
        if not src.variants:
            del ents[ent_key]
    for src_key, dst_key in ops.get("merges", []):
        src, dst = ents.get(src_key), ents.get(dst_key)
        if src and dst and src is not dst:
            dst.variants |= src.variants
            dst.score, dst.sources = max(dst.score, src.score), dst.sources | src.sources
            del ents[src_key]
    # Entities a reviewer deleted ("not an entity", e.g. a heading the model picked up) are never matched.
    deleted_items = []
    for key in ops.get("deleted", []):
        gone = ents.pop(key, None)
        if gone:
            sample = next(iter(sorted(gone.variants)), "").removeprefix(LIT)
            deleted_items.append((key, gone.type, sample))
    manual = {k: v for k, v in manual.items() if k in ents}
    manual_keys = frozenset(manual)

    # 4. Propagate: find every occurrence of every variant in every document. Then read defined-term
    #    aliases that follow a mention — Meyer Global Management LLC (“MGM”) — and propagate again.
    _propagate(texts, ents, keep, manual_keys)
    for e in list(ents.values()):
        for doc, _, end, _ in e.mentions:
            m = ALIAS_RX.match(texts[doc], end)
            for alias in (m.groups() if m else ()):
                if not alias or not alias[:1].isupper():
                    continue
                norm = re.sub(r"\s+", " ", alias).strip()
                words = norm.lower().split()
                if norm.lower() in policy.not_entities or all(w in lower_words for w in words):
                    continue  # generic defined term (“Defendant”, “Relevant Period”)
                full = e.tokens if e.type == "PERSON" else org_tokens(e.canonical or " ".join(e.tokens)) or e.tokens
                full = [t for t in re.sub(r"[^\w\s]", " ", " ".join(full)).lower().split()]
                bare = [w.rstrip(".") for w in words]
                related = (all(any(t == w or (len(w) == 1 and t.startswith(w)) for t in full) for w in bare)
                           or norm.replace(".", "").lower() == "".join(t[0] for t in full if t not in ORG_SUFFIXES)
                           or norm.lower().replace(" ", "").startswith(full[0]))
                if not related:
                    continue  # the alias names something else (“Backdated Letter”)
                owner = next((o for o in ents.values() if o is not e and o.key in split_into
                              and any(norm_form(o.type, v) == norm_form(e.type, norm) for v in o.variants)), None)
                if owner is not None:
                    continue  # that name already belongs to another entry (e.g. a reviewer split it off)
                e.variants.add(LIT + norm)
    # Quoted nicknames are explicit: Jonathan "Jack" Mercer → "Jack" is Person A.
    for e in list(ents.values()):
        if e.type == "PERSON" and len(e.tokens) >= 2:
            rx = re.compile(re.escape(e.tokens[0]) + r"\s+[“\"']([A-Z][a-z]{1,15})[”\"']\s+(?:[A-Z]\.\s+)?"
                            + re.escape(e.tokens[-1]), re.IGNORECASE)
            for t in texts:
                for m in rx.finditer(t):
                    e.variants.add(LIT + m.group(1))
    if any(v.startswith(LIT) for e in ents.values() for v in e.variants):
        _propagate(texts, ents, keep, manual_keys)

    entities = [e for e in ents.values() if e.mentions]

    # A first name used alone that is a common nickname of someone in the registry ("Jack" for Jonathan)
    # goes to review with that person's pseudonym proposed — unlinked it would leak or become a new person.
    taken = defaultdict(list)
    for e in entities:
        for doc, s, end, _ in e.mentions:
            taken[doc].append((s, end))
    nick_of: dict[str, Entity] = {}
    for e in entities:
        if e.type == "PERSON" and len(e.tokens) >= 2:
            for nick in policy.nicknames.get(e.tokens[0], []):
                if len(nick) >= 3 and nick not in lower_words and f"{e.key}" not in nick_of.get(nick, Entity("", "", set())).key:
                    nick_of.setdefault(nick, e)
    for nick, target in nick_of.items():
        key = f"PERSON:nick:{nick}>{target.key}"
        if key in set(ops.get("deleted", [])):
            continue
        cand = Entity(key=key, type="PERSON", variants={LIT + nick.capitalize()}, score=0.5, sources={"nickname"})
        for doc, text in enumerate(texts):
            for m in re.finditer(r"(?<![\w-])(?:" + nick.capitalize() + "|" + nick.upper() + r")(?![\w-])", text):
                if not _overlaps(m.span(), taken[doc]) and not _overlaps(m.span(), keep[doc]):
                    cand.mentions.append((doc, m.start(), m.end(), m.group()))
        if cand.mentions:
            cand.nick_target = target.key
            entities.append(cand)
    # A nickname the language model already picked up as a lone first name ("Bob") is linked the same way.
    for e in entities:
        if e.type == "PERSON" and len(e.tokens) == 1 and e.tokens[0] in nick_of and not e.nick_target:
            e.nick_target = nick_of[e.tokens[0]].key

    for e in entities:
        e.mentions.sort()
    entities.sort(key=lambda e: e.mentions[0][:2])

    # Context tool — an amount is a share/stock price when its sentence, or the one before or after, says so
    # ("The stock fell 9%. It closed at $312.45.").
    price_cue = re.compile(r"(?i)\b(?:share price|stock price|price per share|per share|closing price|closed at|"
                           r"trading at|traded at|par value|exercise price|strike price|offer price|IPO price|"
                           r"market price|stock (?:rose|fell|closed|traded|jumped|dropped))\b")
    spans_by_doc = {}
    for e in entities:
        if e.type != "MONEY" or re.search(r"(?i)million|billion|thousand|\b[mbk]\b|bn|mm", e.mentions[0][3]):
            continue
        try:
            value = float(re.sub(r"[^\d.]", "", e.mentions[0][3]) or 0)
        except ValueError:
            continue
        if value >= 10_000:
            continue
        for doc, ms, me, _ in e.mentions:
            sp = spans_by_doc.setdefault(doc, sentence_spans(texts[doc]))
            i = next((k for k, (a, b) in enumerate(sp) if a <= ms < b), None)
            if i is None:
                continue
            ctx = " ".join(texts[doc][a:b] for a, b in sp[max(0, i - 1):i + 2])
            if price_cue.search(ctx):
                e.type = "SHARE_PRICE"
                break

    # 5. Labels, proposed replacements, confidence tiers, decisions.
    counters: dict[str, int] = defaultdict(int)
    public = [p.lower() for p in policy.public_companies]
    # Detected entities are numbered first, manual redactions after them, so adding one by hand
    # never renumbers the pseudonyms a reviewer has already seen.
    hand = {k for k, v in manual.items() if v}  # manual entries proper (not aliases joined to an entity)
    public_names = {" ".join(org_tokens(p)) for p in policy.public_companies}

    pub_mode = settings.get("public_companies") or ("keep" if settings.get("keep_public_companies", True) else "redact")

    def looks_public(e: Entity) -> bool:
        if "ticker_link" in e.sources or any(" ".join(org_tokens(m[3])) in public_names for m in e.mentions):
            return True
        return any(re.match(r"\.?,?\s*\((?:NASDAQ|Nasdaq|NYSE|NYSE American|OTC\w*|AMEX|LSE|TSX)\s*:\s*[A-Z.]{1,6}\)",
                            texts[m[0]][m[2]:m[2] + 30]) for m in e.mentions)

    def caption_of(e: Entity) -> str:
        """SEC cover pages label each field in parentheses on the next line: '(Address of Principal Executive
        Offices)', '(Zip Code)'. Returns that caption in sentence case, or ''."""
        for doc, _, me, _ in e.mentions:
            window = texts[doc][me:me + 160]
            if e.type == "POSTAL_CODE":   # "(Address of principal executive offices) (Zip Code)" → the ZIP's caption
                m = re.search(r"\((Zip(?: Code)?|Postal Code)\)", window[:120], re.IGNORECASE)
                if m:
                    return "Zip code"
                continue
            m = re.match(r"[\s\d,-]{0,24}\(([A-Z][^()\n]{2,60})\)", window)
            if m:
                words = " ".join(m.group(1).split())
                return words[:1].upper() + words[1:].lower()
        return ""

    def party_cue(e: Entity) -> str:
        """Context tool — words around a mention that make it a party (registrant line, defined role,
        'agreement with', 'will acquire' …). Returns the cue found, or ''."""
        if "known_party" in e.sources:
            return "listed as a known party"
        for doc, ms, me, _ in e.mentions:
            after, before = texts[doc][me:me + 120], texts[doc][max(0, ms - 60):ms]
            for rx in policy.party_cues.get("after", []):
                m = rx.search(after)
                if m:
                    return " ".join(m.group().split())[:60]
            for rx in policy.party_cues.get("before", []):
                m = rx.search(before)
                if m:
                    return " ".join(m.group().split())[-60:]
        return ""

    def kept_by_policy(e: Entity) -> bool:  # kept entities don't use up a pseudonym ("Company C")
        if e.key in hand:
            return False
        if e.type in disabled or (e.type == "DATE" and settings.get("date_mode") == "exact"):
            return True
        if e.type == "ORGANIZATION":
            if not settings.get("redact_organizations", True):
                return True
            return pub_mode == "keep" and looks_public(e) and not party_cue(e)
        return False

    # Context tool — role labels ("Defendant", "Buyer", "Target") instead of "Company B", when switched on.
    def role_of(e: Entity) -> str:
        for rule in policy.role_rules:
            if e.type not in rule["types"]:
                continue
            for doc, ms, me, _ in e.mentions:
                window = texts[doc][me:me + 160] if rule["where"] == "after" else texts[doc][max(0, ms - 80):ms]
                m = rule["regex"].search(window)
                if m:
                    role = rule["role"] or m.groupdict().get("role") or ""
                    role = re.sub(r"\s+", " ", role).strip()
                    if role.lower().endswith("s") and role.lower()[:-1] in {"defendant", "respondent", "plaintiff",
                                                                           "relief defendant"}:
                        role = role[:-1]
                    return role[:1].upper() + role[1:]
        return ""

    roles: dict[str, str] = {}
    if settings.get("role_labels", False):
        roles = {e.key: r for e in entities if e.type in ("PERSON", "ORGANIZATION") and not e.nick_target
                 and e.key not in hand and not kept_by_policy(e) and (r := role_of(e))}

    for e in [x for x in entities if x.key not in hand] + [x for x in entities if x.key in hand]:
        spec = policy.spec(e.type)
        if e.key in roles:
            continue  # labelled by role below; doesn't use up a letter
        if e.nick_target:
            continue  # takes its target's pseudonym, below
        if kept_by_policy(e):
            e.label = "Kept"
            continue
        default = manual[e.key].get("label") if e.key in hand and manual[e.key].get("label") else (
            "Redacted {1}" if e.type == "MANUAL" else type_title(e.type) + " {1}")
        e.label = _label(spec.get("label", default), counters[e.type])
        counters[e.type] += 1
    by_role: dict[str, list[Entity]] = defaultdict(list)
    for e in entities:
        if e.key in roles:
            by_role[roles[e.key]].append(e)
    for role, group in by_role.items():
        for i, e in enumerate(group):
            e.label = role if len(group) == 1 else f"{role} {_letters(i)}"

    # Labels the reviewer typed (or that were frozen when documents were added) win over everything, and
    # every other entity is renumbered around them so no two entities ever share a pseudonym.
    fixed = {k: v.strip() for k, v in (ops.get("labels") or {}).items() if v and v.strip()}
    used = set()
    for e in entities:
        if e.key in fixed:
            e.label = fixed[e.key]
            used.add(e.label.lower())
    for e in entities:
        if e.key in fixed or e.label in ("", "Kept") or e.nick_target:
            continue
        if e.label.lower() in used:
            base = re.sub(r"\s+(?:[A-Z]{1,2}|\d+)$", "", e.label)
            numeric = bool(re.search(r"\d+$", e.label))
            i = 0
            while True:
                cand = f"{base} {i + 1 if numeric else _letters(i)}"
                if cand.lower() not in used:
                    e.label = cand
                    break
                i += 1
        used.add(e.label.lower())
    by_key = {e.key: e for e in entities}

    # Batch-wide date handling: one random-looking but fixed offset per document set keeps every interval
    # ("trades March 15–21, flagged March 23") while hiding the real dates.
    date_mode = settings.get("date_mode", "month_year")
    seed = int(hashlib.sha256("".join(texts).encode()).hexdigest()[:8], 16)
    shift = timedelta(days=(30 + seed % 335) * (1 if seed & 1 else -1))
    parsed = [parse_date(e.mentions[0][3]) for e in entities if e.type == "DATE"]
    anchor = min((d for d in parsed if d), default=None)
    for e in entities:
        if e.nick_target:
            target = by_key.get(e.nick_target)
            e.label = (target.label if target else "Person ?") + " (nickname?)"
            e.canonical = e.mentions[0][3]
            e.proposed = target.label if target else "Person ?"
            e.custom, e.tier, e.rule = "", "review", "always_redact.person_names"
            e.reason = (f"Possible nickname of {target.canonical if target else 'someone'} ({e.proposed}). "
                        "Accept to link; Keep original if it's someone else.")
            d = decisions.get("E|" + e.key)
            e.status = {"accept": "accepted", "keep": "kept", "edit": "edited"}[d["action"]] if d else "pending"
            e.custom = d.get("value", "") if d else ""
            continue
        spec = policy.spec(e.type)
        e.rule = spec.get("rule", "")
        e.reason = spec.get("reason", "")
        if e.key in hand:
            e.canonical = re.sub(r"\s+", " ", e.mentions[0][3])
            e.proposed = manual[e.key].get("replacement") or e.label
            e.rule, e.reason, e.tier = "manual", "Redacted by hand on the Documents page.", "auto"
            d = decisions.get("E|" + e.key)
            e.status = {"accept": "manual", "keep": "kept", "edit": "edited"}[d["action"]] if d else "manual"
            e.custom = d.get("value", "") if d else ""
            continue
        if e.type in disabled:
            e.proposed = e.label
            e.status, e.rule, e.reason = "setting", f"{e.rule} (off)", "This rule bucket is switched off."
            continue
        forms = [re.sub(r"\s+", " ", re.sub("^" + HONORIFIC_RX, "", _strip_possessive(m[3]))) for m in e.mentions]
        mixed = [f for f in forms if not f.isupper()] or (
            [f.title() for f in forms] if e.type in ("PERSON", "ORGANIZATION") else forms)
        e.canonical = max(mixed, key=len)
        e.proposed = e.label

        if e.type == "DATE":
            d = parse_date(e.mentions[0][3])
            e.proposed = d.strftime("%B %Y") if d else e.label
            if date_mode == "exact":
                e.status, e.rule, e.reason = "setting", "generalize.dates (off)", "Exact dates are kept by your policy setting."
                continue
            if date_mode == "shifted" and d:
                e.proposed, e.rule = _date_like(e.mentions[0][3], d + shift), "generalize.dates (shifted)"
                e.reason = "Same hidden offset for every date; gaps survive."
            elif date_mode == "relative" and d and anchor:
                e.proposed, e.rule = f"Day {(d - anchor).days}", "generalize.dates (relative)"
                e.reason = "Days from the earliest date; gaps survive."
        if e.type in ("MONEY", "SHARE_COUNT"):
            e.proposed = round_figure(e.mentions[0][3])
        caption = caption_of(e) if settings.get("role_labels", False) and e.type in ("STREET_ADDRESS",
                                                                                         "POSTAL_CODE") else ""
        if caption and not (ops.get("labels") or {}).get(e.key):
            # The document names the field: "(Address of Principal Executive Offices)" → use that, not the city.
            e.label = e.proposed = caption
            e.rule = "generalize.context_caption"
            e.reason = "Labelled by the document's own caption."
        elif e.type == "STREET_ADDRESS":
            m = ADDRESS_TAIL.search(e.mentions[0][3])
            if settings.get("generalize_addresses", True) and m:
                e.proposed = f"{m.group(1)}, {m.group(2)}"
            else:
                e.proposed, e.rule = f"[{e.label}]", "always_redact.addresses"
        if e.type == "ORGANIZATION":
            canon = " ".join(org_tokens(e.canonical))
            cue = party_cue(e)
            if pub_mode == "keep" and looks_public(e) and not cue:
                e.status, e.rule = "public", "keep_list.public_companies"
                e.reason = "Public companies are kept while this setting is on."
                continue
            if pub_mode == "review" and looks_public(e) and not cue:
                e.tier, e.rule = "review", "names.public_company_party_check"
                e.reason = ("Public company. A party (client, counterparty, target)? Accept. "
                            "Only cited for public facts? Keep original.")
                d = decisions.get("E|" + e.key)
                e.status = {"accept": "accepted", "keep": "kept", "edit": "edited"}[d["action"]] if d else "pending"
                e.custom = d.get("value", "") if d else ""
                continue
            if not settings.get("redact_organizations", True):
                e.status, e.rule, e.reason = "setting", "always_redact.private_organizations (off)", "Organization redaction is turned off."
                continue

        if (ops.get("labels") or {}).get(e.key):
            e.proposed = e.label  # a reviewer's rename beats city/state, month-year or rounding
        high = e.score >= policy.auto_apply
        if e.type == "ORGANIZATION" and (cue or {"known_party", "domain_link"} & e.sources):
            e.tier = "auto"  # context says it's a party: redact without asking
            e.rule = "names.party_by_context"
            e.reason = f"Party by context ({cue or 'its own domain appears'})."
        elif e.type == "URL":
            link = e.mentions[0][3]
            specific = re.search(r"\d{4,}|\.(?:s?html?|pdf|docx?|xlsx?|txt|xml|json|csv)\b|[?#=]|/(?:data|archives?|"
                                 r"files?|docs?|ix|cgi-bin)/", link, re.IGNORECASE)
            dom = re.search(r"(?:@|//|www\.|^)(?:[\w-]+\.)*?([\w-]+)\.[a-z]{2,}(?:[/:?#]|$)", link, re.IGNORECASE)
            org_words = {t for o in entities if o.type == "ORGANIZATION" for t in o.tokens}
            party = bool(dom) and dom.group(1).lower() in org_words
            e.tier = "auto" if specific or party else "review"
            if e.tier == "review":
                e.reason = "General link? Keep if it names no party or record."
        elif spec.get("auto"):
            e.tier = "auto"
        elif e.type == "PERSON":
            e.tier = "auto" if high and len(e.tokens) >= 2 else "review"
        elif e.type == "ORGANIZATION":
            e.tier = "auto" if any(s.startswith("org_suffix") for s in e.sources) else "review"
        else:
            e.tier = "auto" if high else "review"
        if e.tier == "review":
            why = ("a single name with no full name to link to" if e.type == "PERSON"
                   else "found by the language model only")
            e.reason = f"Unsure: {why}."

        d = decisions.get("E|" + e.key)
        if d:
            e.status = {"accept": "accepted", "keep": "kept", "edit": "edited"}[d["action"]]
            e.custom = d.get("value", "")
        else:
            e.status = "auto" if e.tier == "auto" else "pending"

    # 6. Flags: never auto-changed. "match" flags cover the matched words (relationship phrases);
    #    "sentence" flags cover the whole sentence (privilege, group inference, identifying combinations).
    flags: dict[str, Flag] = {}
    mention_spans = defaultdict(list)
    for e in entities:
        for doc, s_, end, _ in e.mentions:
            mention_spans[doc].append((s_, end))
    locations = defaultdict(list)
    for d in detections:
        if d.type in ("LOCATION", "STREET_ADDRESS"):
            locations[d.doc].append((d.start, d.end, d.text))

    def anonymized(doc: int, a: int, b: int) -> str:
        """The sentence as it currently reads in the output (entity replacements applied)."""
        out, pos = [], a
        for e in entities:
            for md, ms, me, mt in e.mentions:
                if md == doc and a <= ms and me <= b:
                    out.append((ms, me, e.replacement_for(mt)))
        res_, txt = [], texts[doc]
        for ms, me, r in sorted(out):
            res_ += [txt[pos:ms], r]
            pos = me
        res_.append(txt[pos:b])
        return re.sub(r"\s+", " ", "".join(res_)).strip()

    def add_flag(rule_id, doc, s_, end, replacement, reason, scope):
        phrase = texts[doc][s_:end]
        key = f"{rule_id}|{re.sub(r'\s+', ' ', phrase.lower())[:160]}"
        f = flags.setdefault(key, Flag(key=key, rule=rule_id, text=re.sub(r"\s+", " ", phrase), proposed=replacement,
                                       reason=reason, scope=scope))
        f.mentions.append((doc, s_, end, phrase))

    for doc, text in enumerate(texts):
        spans = sentence_spans(text)
        cands, sent_hits = [], {}
        for rule in (r for r in policy.flags if r.id not in disabled):
            for m in rule.regex.finditer(text):
                s_, end = m.span("v") if "v" in rule.regex.groupindex else m.span()
                if rule.scope == "sentence":
                    sp = next(((a, b) for a, b in spans if a <= s_ < b), (s_, end))
                    sent_hits.setdefault((rule.id, sp), rule)
                elif not _overlaps((s_, end), mention_spans[doc]):
                    cands.append((s_, end, rule))
        cands.sort(key=lambda c: (-(c[1] - c[0]), c[0]))
        for s_, end, rule in _greedy(cands):
            add_flag(rule.id, doc, s_, end, rule.replacement, rule.reason, "match")
        for (rid, (a, b)), rule in sent_hits.items():
            add_flag(rid, doc, a, b, rule.replacement, rule.reason, "sentence")

        cats = policy.quasi.get("categories") or {}
        if cats and "quasi.combination" not in disabled:
            for a, b in spans:
                sent = text[a:b]
                found = {}
                for cat, rxs in cats.items():
                    for rx in rxs:
                        m = rx.search(sent)
                        if m:
                            found[cat] = m.group().strip(" ,")
                            break
                loc = next((t for ls, le, t in locations[doc] if a <= ls and le <= b), None)
                if loc:
                    found["place"] = loc
                if len(found) >= policy.quasi.get("min_categories", 3):
                    attrs = ", ".join(f"{k} ({v})" for k, v in found.items())
                    reason = (f"{len(found)} identifying details together: {attrs}. Edit to generalize two or more, "
                              "or keep if common.")
                    add_flag("quasi.combination", doc, a, b, anonymized(doc, a, b), reason, "sentence")

    for f in flags.values():
        d = decisions.get("F|" + f.key)
        if d:
            f.status = {"accept": "accepted", "keep": "kept", "edit": "edited"}[d["action"]]
            f.custom = d.get("value", "")
    flag_list = sorted(flags.values(), key=lambda f: f.mentions[0][:2])

    # 7. Render.
    segments: list[list[Segment]] = [[] for _ in texts]
    # Placeholder style: labels (Person A, Acquirer…) keep the link between mentions; "[REDACTED]" or removing the
    # text gives nothing back — use it when a label itself could point to the source.
    style = settings.get("placeholder_style", "labels")
    rounded = {"DATE", "MONEY", "SHARE_COUNT"}  # generalized values stay readable in every style

    def styled(e: Entity, orig: str) -> str:
        rep_ = e.replacement_for(orig)
        if style == "labels" or not e.is_redacted or e.type in rounded:
            return rep_
        return "[REDACTED]" if style == "redacted" else ""

    for e in entities:
        for doc, s, end, orig in e.mentions:
            segments[doc].append(Segment(s, end, orig, styled(e, orig), "entity", e.key, e.type, e.status))
    covering = defaultdict(list)
    for f in flag_list:
        for doc, s, end, orig in f.mentions:
            if f.scope == "sentence":
                if f.status in ("accepted", "edited"):
                    covering[doc].append(Segment(s, end, orig, f.replacement_for(orig), "flag", f.key, "FLAG", f.status))
                continue  # an unchanged sentence flag doesn't alter the text
            segments[doc].append(Segment(s, end, orig, f.replacement_for(orig), "flag", f.key, "FLAG", f.status))
    outputs = []
    for doc, text in enumerate(texts):
        if covering[doc]:
            cov = _greedy([(g.start, g.end, g) for g in sorted(covering[doc], key=lambda g: -(g.end - g.start))])
            spans_ = [(a, b) for a, b, _ in cov]
            segments[doc] = [g for g in segments[doc] if not _overlaps((g.start, g.end), spans_)] + [g for _, _, g in cov]
        segments[doc].sort(key=lambda g: g.start)
        out, pos = [], 0
        for g in segments[doc]:
            out += [text[pos:g.start], g.replacement]
            pos = g.end
        out.append(text[pos:])
        joined = "".join(out)
        if style == "remove":  # tidy the gaps left behind
            joined = re.sub(r"\(\s*[\"“”'‘’]{0,2}\s*\)|[\"“]\s*[\"”]|[‘']\s*[’']", "", joined)  # empty ("") / “”
            joined = re.sub(r"[ \t]{2,}", " ", re.sub(r"[ \t]+([,.;:)])", r"\1", joined))
        outputs.append(joined)

    # 8. Review queue (stable order: first appearance).
    items = [("E|" + e.key, e.mentions[0][:2]) for e in entities if e.tier == "review" and e.status != "public"]
    items += [("F|" + f.key, f.mentions[0][:2]) for f in flag_list]
    queue = [k for k, _ in sorted(items, key=lambda x: x[1])]

    return Result(names, texts, entities, flag_list, segments, outputs, queue, deleted_items)


# ---------------------------------------------------------------- reports
def redaction_log(res: Result) -> list[dict]:
    rows = []
    for e in res.entities:
        reps_by_case: dict[str, str] = {}
        for m in e.mentions:  # "PERSON A" (ALL-CAPS caption) and "Person A" are one replacement
            r = e.replacement_for(m[3])
            if r.casefold() not in reps_by_case or reps_by_case[r.casefold()].isupper():
                reps_by_case[r.casefold()] = r
        reps = sorted(reps_by_case.values())
        rows.append({
            "original": " | ".join(sorted({m[3] for m in e.mentions})),
            "replacement": " | ".join(reps),
            "entity_type": e.type,
            "registry_id": e.label,
            "documents": "; ".join(res.names[d] for d in sorted({m[0] for m in e.mentions})),
            "occurrences": len(e.mentions),
            "rule": e.rule,
            "decision": DECISION_LABEL.get(e.status, e.status),
            "confidence": round(e.score, 2),
        })
    for key, etype, sample in res.deleted:
        rows.append({"original": sample, "replacement": sample, "entity_type": etype, "registry_id": "",
                     "documents": "", "occurrences": 0, "rule": "deleted by reviewer (not an entity)",
                     "decision": "deleted", "confidence": None})
    for f in res.flags:
        rows.append({
            "original": f.text,
            "replacement": f.replacement_for(f.text),
            "entity_type": "RELATIONSHIP_PHRASE",
            "registry_id": "",
            "documents": "; ".join(res.names[d] for d in sorted({m[0] for m in f.mentions})),
            "occurrences": len(f.mentions),
            "rule": f.rule,
            "decision": DECISION_LABEL.get(f.status, f.status),
            "confidence": None,
        })
    return rows


def release_check(res: Result, warnings: list[list[dict]] | None = None, rights_confirmed: bool = False) -> list[str]:
    """Everything that must be true before anonymized documents leave: returns the blockers (empty = OK)."""
    cc = consistency_check(res)
    out = []
    if not rights_confirmed:
        out.append("Confirm you have the right to share these files.")
    if cc["unreviewed"]:
        out.append(f"Finish review ({len(cc['unreviewed'])} open).")
    if not cc["leak_free"]:
        n = len(cc["leak_items"])
        out.append(f"Original text still in the output ({n} place{'s' if n != 1 else ''} — listed below).")
    if not cc["all_consistent"]:
        out.append("Some entities have more than one replacement.")
    for name, ws in zip(res.names, warnings or []):
        for w in ws:
            if w.get("level") == "block":
                out.append(f"{name}: {w['message']}")
    return out


def consistency_check(res: Result) -> dict:
    """Same replacement for each entity in every document + scan outputs for leaked originals."""
    rows, leak_items = [], []
    for e in res.entities:
        per_doc: dict[int, set[str]] = defaultdict(set)
        for doc, _, _, orig in e.mentions:
            per_doc[doc].add(e.replacement_for(orig).casefold() if e.is_redacted else "(kept)")
        all_reps = set().union(*per_doc.values())
        leaks = []
        if e.is_redacted:
            originals = {_strip_possessive(m[3]) for m in e.mentions}
            originals = {re.sub(HONORIFIC_RX, "", o) for o in originals}
            for doc, out in enumerate(res.outputs):
                for o in originals:
                    if o.lower() in e.replacement_for(o).lower():
                        continue  # already coarse ("$2B" → "~$2B") — the replacement contains it on purpose
                    hit = len(o) >= 3 and re.search(r"(?<![\w-])" + re.escape(o) + r"(?![\w-])", out, re.IGNORECASE)
                    if hit:
                        leaks.append(f"{hit.group()!r} in {res.names[doc]}")
                        # the exact wording found, so "Redact it" can target it
                        leak_items.append({"text": hit.group(), "doc": doc, "key": e.key, "label": e.label})
        rows.append({
            "registry_id": e.label,
            "entity_type": e.type,
            "original": e.canonical,
            "replacement": e.replacement_for(e.canonical) if e.is_redacted else "(kept)",
            "documents": len(per_doc),
            "consistent": len(all_reps) == 1,
            "leaks": "; ".join(sorted(set(leaks))),
        })
    unreviewed = [k for k in res.queue if res.item(k).status == "pending"]
    return {
        "rows": rows,
        "all_consistent": all(r["consistent"] for r in rows),
        "leak_free": not any(r["leaks"] for r in rows),
        "leak_items": leak_items,   # [{"text", "doc", "key", "label"}] — where each leak is, for the UI
        "unreviewed": unreviewed,
    }
