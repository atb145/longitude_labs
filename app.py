"""Longitude Anonymizer — Streamlit UI. All logic lives in /engine; this file is presentation only."""
from __future__ import annotations

import csv
import json
import html
import io
import re
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from engine import Session, available_policies, consistency_check, load_policy, load_policy_text, redaction_log
from engine.extract import SUPPORTED
from engine.registry import norm_form, split_key, type_title

SAMPLES = Path(__file__).parent / "samples"
TYPE_NAMES = {
    "PERSON": "Person", "ORGANIZATION": "Organization", "ACCOUNT_NUMBER": "Account number",
    "CRD_NUMBER": "CRD number", "CUSIP": "CUSIP", "TICKER": "Ticker", "CASE_NUMBER": "Case / docket number",
    "EMAIL_ADDRESS": "Email", "PHONE_NUMBER": "Phone", "US_SSN": "SSN", "STREET_ADDRESS": "Street address",
    "DATE": "Date", "FLAG": "Relationship phrase", "MANUAL": "Manual redaction",
}
TYPE_COLORS = {  # restrained hues; used at low alpha so they read on cream and on charcoal
    "PERSON": "120,195,228", "ORGANIZATION": "205,170,120", "ACCOUNT_NUMBER": "29,181,218",
    "CRD_NUMBER": "29,181,218", "CUSIP": "150,140,200", "TICKER": "150,140,200",
    "CASE_NUMBER": "110,150,200", "EMAIL_ADDRESS": "120,180,150", "PHONE_NUMBER": "120,180,150",
    "US_SSN": "210,120,110", "STREET_ADDRESS": "190,175,130", "DATE": "150,150,150", "FLAG": "29,181,218",
    "MANUAL": "39,39,39",
}
VIEWS = ["Review", "Documents", "Approved", "Log & consistency"]

st.set_page_config(page_title="Longitude Anonymizer", page_icon="◐", layout="wide",
                   initial_sidebar_state="expanded")


# --------------------------------------------------------------------------- style
def inject_css() -> None:
    # Colors derive from the theme's own text color, so the same CSS is right in light and dark
    # (and the instant a viewer switches) without asking the server which theme is active.
    css = """
    :root { --fg: currentColor; --dim: color-mix(in srgb, currentColor 62%, transparent);
            --rule: color-mix(in srgb, currentColor 14%, transparent);
            --accent: color-mix(in srgb, #1DB5DA 55%, currentColor);
            --soft: #A7DAEF; --mark: color-mix(in srgb, #A7DAEF 45%, transparent);
            --serif: "Newsreader", Georgia, serif; --sans: "Libre Franklin", system-ui, sans-serif; }
    .block-container { max-width: 1120px; padding-top: 2.2rem; padding-bottom: 5rem; }
    header[data-testid="stHeader"] { background: transparent; }
    h1, h2, h3 { font-family: var(--serif); font-weight: 500 !important; letter-spacing: -.01em; }

    .ll-head { display:flex; align-items:center; justify-content:space-between; gap:1rem; margin-bottom:.4rem; }
    .ll-word { font-family:var(--serif); font-size:1.35rem; font-weight:500; }
    .ll-word .dim { color:var(--dim); font-family:var(--sans); font-size:.85rem; font-weight:400; margin-left:.3rem; }
    .ll-coord { font-size:.8rem; color:var(--dim); padding:.25rem .8rem; border:1px solid var(--rule); border-radius:999px; }
    .wave { display:block; width:100%; height:18px; color:var(--soft); margin:.2rem 0 2rem; }
    .ll-label { font-family:var(--sans); text-transform:uppercase; letter-spacing:.12em; font-size:.72rem;
                font-weight:600; color:var(--dim); margin:0 0 .5rem 0; }
    .ll-label b { color:var(--fg); font-weight:600; }
    .hero { text-align:center; max-width:760px; margin:2.2rem auto 2.6rem; }
    .hero h1 { font-size:clamp(2.4rem, 5.5vw, 3.6rem) !important; line-height:1.08; margin:0 0 1rem; padding:0; }
    .hero p { font-size:1.15rem; line-height:1.6; color:var(--dim); margin:0 auto; max-width:56ch; }
    .steps { display:grid; grid-template-columns:repeat(3, 1fr); gap:1rem; margin:0 0 2.4rem; }
    .step { border:1px solid var(--rule); border-radius:1rem; padding:1.1rem 1.2rem; }
    .step b { display:block; font-family:var(--serif); font-size:1.15rem; font-weight:500; margin-bottom:.35rem; }
    .step span { color:var(--dim); font-size:.93rem; line-height:1.55; }
    .step i { font-style:normal; display:inline-block; width:1.6rem; height:1.6rem; line-height:1.6rem; text-align:center;
              border-radius:999px; background:var(--soft); color:#272727; font-size:.8rem; font-weight:600; margin-bottom:.6rem; }
    @media (max-width: 760px) { .steps { grid-template-columns:1fr; } }
    .ll-note { color:var(--dim); font-size:.95rem; line-height:1.65; max-width:62ch; }
    .ll-rule { border-top:1px solid var(--rule); margin:1.8rem 0; }
    .dim { color:var(--dim); }

    /* progress */
    .fix { margin:.4rem 0 2rem; }
    .fix-row { display:flex; justify-content:space-between; font-size:.85rem; color:var(--dim); margin-bottom:.5rem; }
    .fix-row .now { color:var(--fg); font-weight:500; }
    .track { position:relative; height:8px; border-radius:999px; background:var(--rule); overflow:hidden; }
    .track .fill { position:absolute; inset:0 auto 0 0; border-radius:999px; background:var(--soft); }
    .track .mark { display:none; }

    /* review card */
    .card { border:1px solid var(--rule); border-radius:1.2rem; padding:1.6rem 1.8rem 1.2rem; margin-bottom:1rem; }
    .card-type { display:inline-block; font-size:.75rem; font-weight:600; letter-spacing:.08em; text-transform:uppercase;
                 padding:.2rem .7rem; border-radius:999px; background:var(--mark); }
    .card-ctx { font-family:var(--serif); font-size:1.3rem; line-height:1.6; margin:1rem 0 1.3rem; max-width:62ch; }
    .card-ctx mark { background:var(--soft); color:#272727; padding:.02em .3em; border-radius:.35rem; }
    .card-top { display:flex; justify-content:space-between; align-items:center; gap:1rem; font-size:.85rem; flex-wrap:wrap; }
    .ctx-dim { color:var(--dim); }
    .form { font-weight:500; padding:.05em .45em; border-radius:.4rem; background:var(--mark); }
    .form-row { padding:.55rem 0; border-bottom:1px solid var(--rule); }
    .form-ctx { font-size:.92rem; line-height:1.6; margin-top:.3rem; }
    .form-ctx mark { background:var(--soft); color:#272727; padding:0 .2em; border-radius:.3rem; }
    .ll-sub { font-size:.78rem; font-weight:600; color:var(--dim); margin:.8rem 0 .3rem; }
    .kv { display:grid; grid-template-columns: 9rem 1fr; gap:.55rem 1rem; font-size:1rem; margin:0; }
    .kv dt { font-size:.72rem; font-weight:600; letter-spacing:.1em; text-transform:uppercase; color:var(--dim); padding-top:.25rem; }
    .kv dd { margin:0; line-height:1.55; }
    .kv dd.big { font-weight:500; font-size:1.05rem; }
    .kv .rule-id { color:var(--dim); font-size:.85rem; }

    /* documents */
    .panes { display:grid; gap:1rem; }
    .panes.n2 { grid-template-columns: 1fr 1fr; }
    @media (max-width: 860px) { .panes.n2 { grid-template-columns: 1fr; } }
    .pane-wrap { border:1px solid var(--rule); border-radius:1rem; overflow:hidden; }
    .pane-head { padding:.6rem 1rem .55rem; border-bottom:1px solid var(--rule); }
    .pane-top { display:flex; justify-content:space-between; align-items:center; gap:.5rem; }
    .pane-name { font-size:.85rem; font-weight:500; margin-top:.35rem; overflow-wrap:anywhere; line-height:1.35; }
    .pane-name .ok { margin-left:.35rem; }
    .pane-edit { font:inherit; font-size:.78rem; font-weight:500; cursor:pointer; border-radius:999px; padding:.18rem .75rem;
                 border:1px solid var(--rule); background:transparent; color:inherit; }
    .pane-edit:hover, .pane-edit.on { background:var(--soft); color:#272727; border-color:var(--soft); }
    .chip-edit { display:inline-block; font-size:.78em; margin-left:.12em; padding:0 .2em; cursor:pointer; color:var(--dim);
                 border-radius:.3rem; vertical-align:baseline; user-select:none; }
    .chip-edit:hover { background:var(--soft); color:#272727; }
    .dp-root { position:relative; }
    .redact-fab { position:absolute; z-index:20; font:inherit; font-size:.8rem; font-weight:600; cursor:pointer; border-radius:999px;
                  padding:.3rem .8rem; border:1px solid #272727; background:#272727; color:#F7F3EA; box-shadow:0 2px 8px rgba(0,0,0,.18); }
    .edit-hint { font-size:.85rem; color:var(--dim); margin:-.2rem 0 .6rem; }
    .st-key-prev_doc button, .st-key-next_doc button { padding:.45rem 0 !important; min-width:0; font-size:1rem; }
    .count-top { font-size:.85rem; color:var(--dim); margin:0 0 .15rem; }
    .count-top b { color:var(--fg); font-weight:600; }
    .doc-count b { color:var(--fg); }
    .pane-tag { font-size:.7rem; font-weight:600; letter-spacing:.08em; text-transform:uppercase; padding:.12rem .6rem; border-radius:999px; }
    .pane-tag.original { border:1px solid var(--rule); color:var(--dim); }
    .pane-tag.anonymized { background:var(--soft); color:#272727; }
    .pane { height:68vh; overflow-y:auto; padding:1rem 1.2rem; }
    .panes.n1 .pane { height:auto; max-height:none; }
    .doc { white-space:pre-wrap; font-size:.97rem; line-height:1.8; }
    a.ln { display:inline; }
    .chip { font-size:.86em; font-weight:500; padding:.04em .45em; border:1px solid; border-radius:.4rem; cursor:help; white-space:nowrap; }
    .chip.pending { border-style:dashed; }
    .chip.kept { border-color:transparent !important; background:transparent !important; font-weight:inherit;
                 font-size:inherit; padding:0; border-bottom:1px dotted var(--dim) !important; border-radius:0; }
    .orig { border-radius:.3rem; padding:0 .1em; cursor:help; }
    .orig.kept { background:transparent !important; border-bottom:1px dotted var(--dim); }
    .flag { border-bottom:2px dashed #1DB5DA; cursor:help; }
    .legend { display:flex; flex-wrap:wrap; gap:.4rem 1rem; font-size:.8rem; color:var(--dim); margin:.4rem 0 1rem; align-items:center; }

    .ok { color:#2f8f62; } .warn { color:#c27a1a; }
    .check { font-size:.98rem; line-height:2; }

    /* widgets */
    .stButton button, .stDownloadButton button, .stFormSubmitButton button {
        font-weight:500; letter-spacing:0; border-radius:999px; box-shadow:none; padding:.5rem .9rem; white-space:nowrap; }
    .stButton button[kind="primary"], .stDownloadButton button[kind="primary"], .stFormSubmitButton button {
        background:#A7DAEF !important; color:#272727 !important; border:1px solid #A7DAEF !important; }
    .stButton button[kind="primary"]:hover, .stDownloadButton button[kind="primary"]:hover,
    .stFormSubmitButton button:hover { background:#8fcbe4 !important; border-color:#8fcbe4 !important; }
    [data-testid="stFileUploaderDropzone"] { border:1px dashed var(--rule); background:transparent; border-radius:1rem; }
    [data-testid="stSidebar"] .ll-label { margin-top:1.2rem; }
    """
    for t, rgb in TYPE_COLORS.items():
        css += f".chip.t-{t} {{ border-color: rgba({rgb},.85); background: rgba({rgb},.16); }}\n"
        css += f".orig.t-{t} {{ background: rgba({rgb},.22); box-shadow: inset 0 -1px rgba({rgb},.9); }}\n"
    st.html(f"<style>{css}</style>")


WAVE = ('<svg class="wave" viewBox="0 0 1200 18" preserveAspectRatio="none" aria-hidden="true">'
        '<path d="M0 9 C 100 1, 200 17, 300 9 S 500 1, 600 9 S 800 17, 900 9 S 1100 1, 1200 9" '
        'fill="none" stroke="currentColor" stroke-width="1.5" opacity=".9"/></svg>')


def label(text: str, accent: str = "") -> None:
    st.markdown(f'<p class="ll-label">{text}{f" · <b>{accent}</b>" if accent else ""}</p>', unsafe_allow_html=True)


def header() -> None:
    sess = st.session_state.get("session")
    coord = (f"{len(sess.names)} documents · {sess.policy.name} policy" if sess else "Prototype")
    st.markdown(f'<div class="ll-head"><span class="ll-word">Longitude Anonymizer <span class="dim">— prototype</span></span>'
                f'<span class="ll-coord">{coord}</span></div>{WAVE}', unsafe_allow_html=True)


# --------------------------------------------------------------------------- state helpers
def start_session(files: list[tuple[str, bytes]], policy) -> None:
    with st.spinner("Reading documents and detecting entities…"):
        st.session_state.session = Session.from_files(files, policy)
    st.session_state.view = "Review"
    st.session_state.editing = False


def reset() -> None:
    for k in [k for k in st.session_state if k.startswith(("b_", "t_"))] + ["session", "view", "editing", "focus", "edit_mode", "doc_pick", "last_action"]:
        st.session_state.pop(k, None)


def decide(item_key: str, action: str, value: str = "") -> None:
    st.session_state.session.decide(item_key, action, value)
    st.session_state.editing = False


def undo() -> None:
    st.session_state.session.undo()
    st.session_state.editing = False


def type_name(t: str) -> str:
    return TYPE_NAMES.get(t) or type_title(t)


def color_type(t: str) -> str:
    return t if t in TYPE_COLORS else "MANUAL"


def examples(e, k: int = 3) -> str:
    seen = []
    for m in e.mentions:
        f = " ".join(m[3].split())
        if f not in seen:
            seen.append(f)
    return ", ".join(seen[:k]) + ("…" if len(seen) > k else "")


def merge_into(sess: Session, src, dst) -> None:
    sess.merge(src.key, dst.key)
    st.session_state.focus = dst.key
    st.session_state.focus_note = f"{src.canonical} now joins {dst.label}"


def do_split(sess: Session, ent, form: str, dest: str) -> None:
    if dest.startswith("new:"):
        new_type = None if dest[4:] == ent.type else dest[4:]
        sess.split(ent.key, form, new_type)
        st.session_state.focus = split_key(ent.key, ent.type, form, new_type)
        where = f"a new {type_name(new_type or ent.type)} entry"
    else:
        sess.split(ent.key, form, None, dest)
        st.session_state.focus = dest
        where = "the entity you picked"
    st.session_state.focus_note = f"Moved every “{form}” form out of {ent.label} into {where}"


def split_controls(sess: Session, res, ent, key: str) -> None:
    """Pick a written form, then where all its variants go: a new entry (any type) or an existing entity."""
    groups: dict[str, list] = {}
    for f, _, cnt, _, _ in forms_of(ent):
        g = groups.setdefault(norm_form(ent.type, f), [[], 0])
        g[0].append(" ".join(f.split()))
        g[1] += cnt
    form = st.selectbox("Form to split off", list(groups), key=f"{key}_form",
                        format_func=lambda n: f"{', '.join(groups[n][0])}  (×{groups[n][1]})")
    types = list(dict.fromkeys([ent.type, "PERSON", "ORGANIZATION"]
                               + [t for t, sp in sess.policy.entities.items() if sp.get("action") == "redact"] + ["MANUAL"]))
    others = [e for e in res.entities if e.key != ent.key and e.is_redacted]
    dests = [f"new:{t}" for t in types] + [e.key for e in others]
    by = {e.key: e for e in others}
    st.selectbox("Move to", dests, key=f"{key}_dest",
                 format_func=lambda d: (f"New {type_name(d[4:])} entry" + (" (same type)" if d[4:] == ent.type else ""))
                 if d.startswith("new:") else f"Existing: {by[d].label} — e.g. {examples(by[d])}")
    st.button("Split off", key=f"{key}_go", width="stretch",
              on_click=lambda: do_split(sess, ent, groups[st.session_state[f"{key}_form"]][0][0],
                                        st.session_state[f"{key}_dest"]))


def replacement_picker(res, item, key: str, default: str) -> tuple[str, str]:
    """Dropdown of existing replacements (with examples) plus "Add new". Returns (kind, value):
    ("new", text) | ("existing", entity key) | ("text", flag replacement)."""
    is_flag = item is not None and not hasattr(item, "variants")
    if is_flag:
        pool = sorted({f.replacement_for(f.text) for f in res.flags if f.status in ("accepted", "edited")}
                      | {f.proposed for f in res.flags})
        choice = st.selectbox("Replacement", ["__new__"] + pool, key=key, index=1 + pool.index(default) if default in pool else 0,
                              format_func=lambda k: "➕ Add new replacement" if k == "__new__" else k)
        if choice == "__new__":
            return "text", st.text_input("New replacement", default, key=f"{key}_txt")
        return "text", choice
    own = getattr(item, "key", None) if item is not None else None
    t = getattr(item, "type", None)
    pool = sorted((e for e in res.entities if e.is_redacted and e.key != own),
                  key=lambda e: (e.type != t, e.type, e.label))
    by = {e.key: e for e in pool}
    choice = st.selectbox("Replacement", ["__new__"] + list(by), key=key,
                          format_func=lambda k: "➕ Add new replacement" if k == "__new__"
                          else f"{by[k].label} — e.g. {examples(by[k])}")
    if choice == "__new__":
        return "new", st.text_input("New replacement", default, key=f"{key}_txt",
                                    placeholder="Leave empty for the next free label")
    st.caption(f"This becomes the same entity as {by[choice].label}, so every occurrence reads “{by[choice].label}”.")
    return "existing", choice


def apply_replacement(sess: Session, res, item_key: str, item, kind: str, value: str) -> None:
    if kind == "existing":
        merge_into(sess, item, res.entity(value))
    else:
        sess.decide(item_key, "edit", value.strip() or item.proposed)
    st.session_state.editing = False


_ABBR = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|No|Nos|Inc|Co|Corp|Ltd|Jr|Sr|St|v|vs|U\.S|[A-Z])$")
_CUT = re.compile(r"[.!?][\"”’)]*\s+|:[ \t]*\n|\n\s*\n|\n(?=\s*(?:\d{1,3}\.|[IVX]+\.)\s)|\n(?=[A-Z][A-Z .,&'-]{3,}\n)")


def _flat(text: str, limit: int = 420, keep: str = "end") -> str:
    t = re.sub(r"\s+", " ", text)
    if len(t) <= limit:
        return t
    return "…" + t[-limit:] if keep == "end" else t[:limit] + "…"


def sentence_context(text: str, start: int, end: int) -> tuple[str, str, str, str, str]:
    """(previous sentence, text before span, span, text after span, next sentence)."""
    cuts = [0]
    for m in _CUT.finditer(text):
        if m.group()[0] in ".!?" and (_ABBR.search(text[:m.start()])
                                       or re.search(r"(?:^|\n)\s*(?:\d{1,3}|[IVX]+)$", text[:m.start()])):
            continue  # "Mr." / "No." / initials / paragraph numbers ("1.") are not sentence ends
        cuts.append(m.end())
    cuts = sorted(set(cuts + [len(text)]))
    i = max(k for k in range(len(cuts) - 1) if cuts[k] <= start)
    j = min(k for k in range(1, len(cuts)) if cuts[k] >= end)
    prev = text[cuts[i - 1]:cuts[i]] if i > 0 else ""
    nxt = text[cuts[j]:cuts[j + 1]] if j + 1 < len(cuts) else ""
    return (_flat(prev, 320), _flat(text[cuts[i]:start], 420), _flat(text[start:end]),
            _flat(text[end:cuts[j]], 420, "start"), _flat(nxt, 320, "start"))


def context_html(res, doc: int, start: int, end: int, neighbours: bool = True) -> str:
    prev, before, mid, after, nxt = sentence_context(res.texts[doc], start, end)
    core = f'{html.escape(before)}<mark>{html.escape(mid)}</mark>{html.escape(after)}'
    if not neighbours:
        return core
    return (f'<span class="ctx-dim">{html.escape(prev)}</span> {core} '
            f'<span class="ctx-dim">{html.escape(nxt)}</span>')


# --------------------------------------------------------------------------- home
def home() -> None:
    st.markdown(
        '<div class="hero"><h1>Test our anonymization on your own documents</h1>'
        '<p>Upload a set of documents and get them back anonymized the same way in every file: one person, '
        'one pseudonym, everywhere.</p></div>'
        '<div class="steps">'
        '<div class="step"><i>1</i><b>You upload a set</b><span>PDF, Word or text. Files are read in memory '
        'for this session; this app doesn\'t save them to disk.</span></div>'
        '<div class="step"><i>2</i><b>The tool proposes changes</b><span>It finds names, companies, account '
        'numbers and dates, and applies the clear-cut ones using the policy pack you choose.</span></div>'
        '<div class="step"><i>3</i><b>You review the rest</b><span>Anything uncertain comes to you one at a time. '
        'Then download the documents and a log of every change.</span></div></div>',
        unsafe_allow_html=True)

    policies = available_policies()
    names = list(policies)
    label("Policy pack")
    c1, c2 = st.columns([1, 1])
    policy_name = c1.selectbox("Built-in pack", names, index=names.index("Finance") if "Finance" in names else 0)
    custom = c2.file_uploader("Or upload your own (.yaml)", type=["yaml", "yml"], key="policy_upload")
    pol = load_policy(policies[policy_name])
    if custom is not None:
        try:
            pol = load_policy_text(custom.getvalue().decode("utf-8"), Path(custom.name).stem)
            st.success(f"Using uploaded policy “{pol.name}” — {len(pol.buckets())} rule buckets.")
        except (ValueError, UnicodeDecodeError) as exc:
            st.error(str(exc))
            pol = load_policy(policies[policy_name])
    st.caption(pol.raw.get("description", ""))

    label("Documents")
    uploads = st.file_uploader("Documents", type=[s.strip(".") for s in SUPPORTED], accept_multiple_files=True,
                               label_visibility="collapsed")
    c1, c2, _ = st.columns([1.2, 1.3, 2])
    if c1.button("Anonymize my files", type="primary", disabled=not uploads, width="stretch"):
        try:
            start_session([(u.name, u.getvalue()) for u in uploads], pol)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if c2.button("Try with sample case", width="stretch"):
        start_session([(p.name, p.read_bytes()) for p in sorted(SAMPLES.glob("*")) if p.suffix in SUPPORTED], pol)
        st.rerun()
    st.markdown('<p class="ll-note" style="margin-top:1rem">The sample case is three short documents from one '
                'enforcement matter: a complaint, a staff memo and a settlement order.</p>', unsafe_allow_html=True)


# --------------------------------------------------------------------------- sidebar
def swap_policy(sess: Session, pol) -> None:
    with st.spinner(f"Re-detecting under “{pol.name}”…"):
        st.session_state.session = sess.with_policy(pol)
    for k in [k for k in st.session_state if k.startswith(("b_", "t_"))]:
        st.session_state.pop(k, None)


def forms_of(ent) -> list[tuple[str, str, int, list[int], tuple]]:
    """Each distinct written form of an entity: (form, variant, count, docs, first mention)."""
    out: dict[str, list] = {}
    for m in ent.mentions:
        f = out.setdefault(m[3], [m[3], getattr(ent, "form_variant", {}).get(m[3], ""), 0, set(), m])
        f[2] += 1
        f[3].add(m[0])
    return [(f, v, n, sorted(d), first) for f, v, n, d, first in out.values()]


def sidebar(sess: Session, res) -> None:
    focus = st.session_state.get("focus")
    with st.sidebar:
        label("Policy", sess.policy.name)
        with st.expander("Change policy pack"):
            policies = available_policies()
            pick = st.selectbox("Built-in pack", list(policies), key="swap_pick")
            up = st.file_uploader("Or upload a policy (.yaml)", type=["yaml", "yml"], key="swap_upload")
            if st.button("Apply policy", width="stretch"):
                try:
                    pol = (load_policy_text(up.getvalue().decode("utf-8"), Path(up.name).stem) if up
                           else load_policy(policies[pick]))
                    swap_policy(sess, pol)
                    st.rerun()
                except (ValueError, UnicodeDecodeError) as exc:
                    st.error(str(exc))
            st.caption("Your review decisions are kept; detection re-runs under the new rules.")

        s = sess.settings
        label("Rule buckets")
        disabled = []
        groups: dict[str, list] = {}
        for group, bid, title, help_ in sess.policy.buckets():
            groups.setdefault(group, []).append((bid, title, help_))
        for group, items in groups.items():
            for bid, _, _ in items:
                st.session_state.setdefault(f"b_{bid}", bid not in s.get("disabled", []))
            with st.expander(f"{group} ({len(items)})", expanded=False):  # static label keeps it open while toggling
                for bid, title, help_ in items:
                    st.toggle(title, key=f"b_{bid}", help=help_ or None)
            disabled += [bid for bid, _, _ in items if not st.session_state[f"b_{bid}"]]
        s["disabled"] = disabled

        label("Options")
        st.session_state.setdefault("t_public", s.get("keep_public_companies", True))
        st.session_state.setdefault("t_addr", s.get("generalize_addresses", True))
        st.toggle("Keep public companies", key="t_public",
                  help="Large listed firms on the pack's public_companies list stay readable.")
        st.toggle("Addresses → city, state", key="t_addr", help="Off: the whole address becomes a placeholder.")
        s["keep_public_companies"] = st.session_state.t_public
        s["generalize_addresses"] = st.session_state.t_addr

        label("Entities", f"{len(res.entities)}")
        by_type: dict[str, list] = {}
        for e in res.entities:
            by_type.setdefault(e.type, []).append(e)
        for etype, ents in by_type.items():
            review_n = sum(e.status == "pending" for e in ents)
            open_ = any(e.key == focus for e in ents)
            with st.expander(f"{type_name(etype)} ({len(ents)}{f' · {review_n} to review' if review_n else ''})",
                             expanded=open_):
                for e in ents:
                    status = {"pending": " · review", "setting": " · off", "manual": " · manual"}.get(e.status, "")
                    mark = "● " if e.key == focus else ""
                    with st.popover(f"{mark}{e.label} — {e.canonical[:28]} · {len(e.mentions)}×{status}",
                                    width="stretch"):
                        st.markdown("<br>".join(
                            f"<span class='form'>{html.escape(f)}</span> <span class='dim'>×{n}</span>"
                            for f, _, n, _, _ in forms_of(e)), unsafe_allow_html=True)
                        docs = sorted({m[0] for m in e.mentions})
                        st.caption(f"In: {', '.join(sess.names[d] for d in docs)} · {e.rule}")
                        st.markdown('<p class="ll-sub">Split a form off</p>', unsafe_allow_html=True)
                        split_controls(sess, res, e, f"sb-{e.key}")
                        others = [o for o in res.entities if o.key != e.key and o.is_redacted]
                        if others:
                            st.markdown('<p class="ll-sub">Or merge the whole entry</p>', unsafe_allow_html=True)
                            names = {o.key: f"{o.label} — e.g. {examples(o)}" for o in others}
                            tgt = st.selectbox("Merge into", list(names), key=f"mv-{e.key}", format_func=names.get)
                            st.button("Merge", key=f"mb-{e.key}", on_click=merge_into, width="stretch",
                                      args=(sess, e, next(o for o in others if o.key == tgt)))
        st.markdown('<div class="ll-rule"></div>', unsafe_allow_html=True)
        st.button("Start over", on_click=reset, width="stretch")
        st.caption("Light / dark: ⋮ menu → Settings → Theme.")


# --------------------------------------------------------------------------- review
def bearing(done: int, total: int) -> None:
    frac = done / total if total else 1.0
    st.markdown(
        f'<div class="fix"><div class="fix-row"><span>Review queue</span>'
        f'<span class="now">{done} of {total} reviewed</span></div>'
        f'<div class="track"><div class="fill" style="width:{frac * 100:.1f}%"></div></div></div>',
        unsafe_allow_html=True)


def review(sess: Session, res) -> None:
    queue = res.queue
    pending = [k for k in queue if res.item(k).status == "pending"]
    bearing(len(queue) - len(pending), len(queue))

    if not pending:
        st.markdown('<h2>Everything is reviewed</h2>', unsafe_allow_html=True)
        st.markdown(f'<p class="ll-note">{sum(e.status == "auto" for e in res.entities)} high-confidence detections '
                    'were applied automatically. Open Documents to read the result.</p>', unsafe_allow_html=True)
        c1, c2, _ = st.columns([1, 1, 2])
        c1.button("Back", on_click=undo, disabled=not sess.ops["history"], width="stretch")
        c2.button("Open documents", type="primary", width="stretch",
                  on_click=lambda: st.session_state.update(view="Documents"))
        return

    key = pending[0]
    item = res.item(key)
    is_flag = key.startswith("F|")
    doc, s, e, _ = item.mentions[0]
    docs = sorted({m[0] for m in item.mentions})
    etype = "Relationship phrase · flagged" if is_flag else f"{type_name(item.type)} · low confidence"
    n = len(item.mentions)
    st.markdown(
        f'<div class="card"><div class="card-top"><span class="card-type">{etype}</span>'
        f'<span class="dim">{html.escape(res.names[doc])} · first of {n} occurrence{"s" if n != 1 else ""}</span></div>'
        f'<div class="card-ctx">{context_html(res, doc, s, e)}</div>'
        '<dl class="kv">'
        f'<dt>Found</dt><dd class="big">{html.escape(item.text if is_flag else item.canonical)}</dd>'
        f'<dt>Proposed</dt><dd class="big">{html.escape(item.proposed)}</dd>'
        f'<dt>Why</dt><dd>{html.escape(item.reason)} <span class="rule-id">Rule: {html.escape(item.rule)}</span></dd>'
        f'<dt>Applies to</dt><dd>{n} occurrence{"s" if n != 1 else ""} in '
        f'{html.escape(", ".join(res.names[d] for d in docs))}</dd>'
        '</dl></div>', unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns([1, 1.2, 1.35, .75])
    c1.button("Accept", type="primary", on_click=decide, args=(key, "accept"), width="stretch")
    c2.button("Keep original", on_click=decide, args=(key, "keep"), width="stretch")
    if c3.button("Edit replacement", width="stretch"):
        st.session_state.editing = True
    c4.button("Back", on_click=undo, disabled=not sess.ops["history"], width="stretch")

    if st.session_state.get("editing"):
        forms = forms_of(item)
        st.markdown(f'<p class="ll-label" style="margin-top:1.2rem">Your replacement will cover '
                    f'{len(forms)} form{"s" if len(forms) != 1 else ""} of this name</p>', unsafe_allow_html=True)
        for f, _, cnt, fdocs, (md, ms, me, _) in forms:
            st.markdown(
                f'<div class="form-row"><span class="form">{html.escape(f)}</span> '
                f'<span class="dim">×{cnt} · {html.escape(", ".join(res.names[d] for d in fdocs))}</span>'
                f'<div class="form-ctx">{context_html(res, md, ms, me, neighbours=False)}</div></div>',
                unsafe_allow_html=True)
        kind, value = replacement_picker(res, item, f"rp-{key}", item.proposed)
        b1, b2, _ = st.columns([1.2, 1, 3], vertical_alignment="bottom")
        if b1.button("Apply to all forms", type="primary", width="stretch"):
            apply_replacement(sess, res, key, item, kind, value)
            st.rerun()
        if b2.button("Cancel", width="stretch"):
            st.session_state.editing = False
            st.rerun()
        if not is_flag:
            with st.expander("Split a form off into another entry"):
                split_controls(sess, res, item, f"rv-{key}")

    if n > 1:
        with st.expander(f"See all {n} occurrences"):
            for md, ms, me, _ in item.mentions:
                st.markdown(f'<div class="form-row"><span class="dim">{html.escape(res.names[md])}</span>'
                            f'<div class="form-ctx">{context_html(res, md, ms, me, neighbours=False)}</div></div>',
                            unsafe_allow_html=True)


# --------------------------------------------------------------------------- documents
def _plain(text: str, line: list[int]) -> str:
    """Escape text and drop a line anchor after each newline so two panes can scroll in step."""
    parts = html.escape(text).split("\n")
    out = parts[0]
    for p in parts[1:]:
        line[0] += 1
        out += f'\n<a class="ln" data-l="{line[0]}"></a>' + p
    return out


def _anchors(text: str, line: list[int]) -> str:
    out = ""
    for _ in range(text.count("\n")):
        line[0] += 1
        out += f'<a class="ln" data-l="{line[0]}"></a>'
    return out


def _pencil(ref: str) -> str:
    return f'<span class="chip-edit" data-ref="{html.escape(ref, quote=True)}" title="Change or remove this redaction">✎</span>'


def render_doc(res, doc: int, version: str = "anonymized", edit: bool = False) -> str:
    """HTML for one document. ``version`` is "anonymized" (placeholder chips, hover = original) or
    "original" (source text with every detected span highlighted, hover = its replacement)."""
    text, out, pos, line = res.texts[doc], [], 0, [0]
    by_key = {e.key: e for e in res.entities}
    for g in res.segments[doc]:
        out.append(_plain(text[pos:g.start], line))
        if g.kind == "flag":
            changed = g.status in ("accepted", "edited")
            if version == "original":
                tip = f"Becomes: {g.replacement}" if changed else f"Flagged relationship phrase ({g.status})"
                out.append(f'<span class="flag" title="{html.escape(tip)}">{_plain(g.original, line)}</span>')
            elif changed:
                out.append(f'<span class="chip t-FLAG" title="Original: {html.escape(g.original)}">'
                           f'{html.escape(g.replacement)}</span>{_anchors(g.original, line)}')
            else:
                out.append(f'<span class="flag" title="Flagged relationship phrase ({g.status})">{_plain(g.original, line)}</span>')
            if edit and version == "anonymized":
                out.append(_pencil("F|" + g.ref))
        else:
            e = by_key[g.ref]
            cls = "kept" if not e.is_redacted else ("pending" if e.status == "pending" else "")
            if version == "original":
                tip = f"{e.label} · becomes “{g.replacement}” · {e.status}" if e.is_redacted else f"Kept · {e.rule}"
                out.append(f'<span class="orig t-{color_type(g.type)} {cls}" title="{html.escape(tip)}">{_plain(g.original, line)}</span>')
            elif e.is_redacted:
                tip = f"Original: {g.original} · {e.label} · {e.status}"
                out.append(f'<span class="chip t-{color_type(g.type)} {cls}" title="{html.escape(tip)}">'
                           f'{html.escape(g.replacement)}</span>{_anchors(g.original, line)}')
            else:
                out.append(f'<span class="chip kept" title="Kept · {html.escape(e.rule)}">{_plain(g.original, line)}</span>')
            if edit and version == "anonymized":
                out.append(_pencil("E|" + g.ref))
        pos = g.end
    out.append(_plain(text[pos:], line))
    return "".join(out)


DOC_JS = """
export default function (component) {
  const { data, setTriggerValue, parentElement } = component;
  let root = parentElement.querySelector('.dp-root');
  if (!root) { root = document.createElement('div'); root.className = 'dp-root'; parentElement.appendChild(root); }
  root.innerHTML = data.html;
  const send = (o) => setTriggerValue('action', JSON.stringify(Object.assign({ t: Date.now() }, o)));

  root.querySelectorAll('[data-act="toggle-edit"]').forEach((b) => { b.onclick = () => send({ kind: 'toggle_edit' }); });
  root.querySelectorAll('.chip-edit').forEach((b) => {
    b.onclick = (ev) => { ev.stopPropagation(); send({ kind: 'edit', ref: b.dataset.ref }); };
  });

  // Select text -> floating "Redact" button (edit mode only).
  const fab = document.createElement('button');
  fab.className = 'redact-fab'; fab.textContent = '✎ Redact'; fab.style.display = 'none';
  root.appendChild(fab);
  root.onmouseup = () => setTimeout(() => {
    if (!data.edit) return;
    const sel = window.getSelection();
    const text = sel ? sel.toString().replace(/✎/g, '').trim() : '';
    const node = sel && sel.anchorNode ? (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement) : null;
    const pane = node ? node.closest('.pane') : null;
    if (!text || text.length < 2 || !pane || !root.contains(pane)) { fab.style.display = 'none'; return; }
    const r = sel.getRangeAt(0).getBoundingClientRect(), box = root.getBoundingClientRect();
    fab.style.left = Math.min(r.left - box.left, box.width - 110) + 'px';
    fab.style.top = (r.top - box.top - 38) + 'px';
    fab.style.display = 'block';
    fab.onmousedown = (ev) => ev.preventDefault();
    fab.onclick = () => { fab.style.display = 'none'; send({ kind: 'redact', text, doc: +pane.dataset.doc }); };
  }, 10);

  // Remember scroll positions across reruns, and optionally scroll the two panes together.
  const panes = Array.from(root.querySelectorAll('.pane'));
  panes.forEach((p) => {
    const k = 'dpScroll:' + p.dataset.doc + ':' + p.dataset.version;
    p.scrollTop = window[k] || 0;
    p.addEventListener('scroll', () => { window[k] = p.scrollTop; fab.style.display = 'none'; }, { passive: true });
  });
  if (!data.sync || panes.length !== 2) return;
  const sameDoc = panes[0].dataset.doc === panes[1].dataset.doc;
  let lock = null;
  const pos = (p) => {
    const top = p.scrollTop, a = p.querySelectorAll('a.ln');
    let i = -1;
    for (let k = 0; k < a.length; k++) { if (a[k].offsetTop - p.offsetTop <= top) i = k; else break; }
    const y0 = i < 0 ? 0 : a[i].offsetTop - p.offsetTop;
    const y1 = i + 1 < a.length ? a[i + 1].offsetTop - p.offsetTop : p.scrollHeight;
    return [i, y1 > y0 ? (top - y0) / (y1 - y0) : 0];
  };
  const follow = (src, dst) => {
    if (!sameDoc) {
      dst.scrollTop = src.scrollTop / Math.max(1, src.scrollHeight - src.clientHeight) * (dst.scrollHeight - dst.clientHeight);
      return;
    }
    const [i, f] = pos(src), a = dst.querySelectorAll('a.ln');
    const y0 = i < 0 ? 0 : a[i].offsetTop - dst.offsetTop;
    const y1 = i + 1 < a.length ? a[i + 1].offsetTop - dst.offsetTop : dst.scrollHeight;
    dst.scrollTop = y0 + f * (y1 - y0);
  };
  panes.forEach((p, n) => p.addEventListener('scroll', () => {
    if (lock && lock !== p) return;
    lock = p; follow(p, panes[1 - n]);
    clearTimeout(p._t); p._t = setTimeout(() => { lock = null; }, 80);
  }, { passive: true }));
}
"""
doc_panes = st.components.v2.component("doc_panes", js=DOC_JS, isolate_styles=False)


def zip_outputs(names: list[str], outputs: list[str], res, only: list[str] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, out in zip(names, outputs):
            zf.writestr(f"{Path(name).stem}.anonymized.txt", out)
        zf.writestr("redaction_log.csv", log_csv(res, only))
    return buf.getvalue()


def log_csv(res, only: list[str] | None = None) -> str:
    rows = redaction_log(res)
    if only:
        rows = [r for r in rows if any(n in r["documents"] for n in only)]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else ["original"])
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def short(name: str, n: int = 26) -> str:
    return name if len(name) <= n else name[:n - 1] + "…"


def is_current(sess: Session, res, doc: int) -> bool:
    return doc in sess.approved and sess.approved[doc]["output"] == res.outputs[doc]


@st.dialog("Redact text")
def redact_dialog(sess: Session, res, payload: dict) -> None:
    phrase = " ".join(payload["text"].split())
    body = r"\s+".join(map(re.escape, phrase.split()))
    hits = [(i, len(re.findall(r"(?<![\w])" + body + r"(?![\w])", t))) for i, t in enumerate(res.texts)]
    hits = [(i, n) for i, n in hits if n]
    st.markdown(f'<div class="form-row"><span class="form">{html.escape(phrase)}</span></div>', unsafe_allow_html=True)
    if not hits:
        st.warning("That exact text isn't in the original documents. It may already be inside a placeholder — "
                   "select plain text, or use ✎ on the placeholder instead.")
        return
    total = sum(n for _, n in hits)
    st.caption(f"Found {total} time{'s' if total != 1 else ''} in " + ", ".join(res.names[i] for i, _ in hits)
               + ". The redaction applies to every occurrence, so it stays consistent.")
    kinds = {"PERSON": "Person", "ORGANIZATION": "Organization", "ACCOUNT_NUMBER": "Account number",
             "CASE_NUMBER": "Case / docket number", "STREET_ADDRESS": "Address", "DATE": "Date",
             "MANUAL": "Just redact it (Redacted 1, 2…)", "OTHER": "Other…"}
    kind = st.selectbox("What is it?", list(kinds), format_func=kinds.get, key="rd_kind")
    desc = ""
    if kind == "OTHER":
        desc = st.text_input("Describe it", placeholder="e.g. Project codename, Fund name, Drug candidate",
                             key="rd_desc").strip()
    rk, rv = replacement_picker(res, None, "rd_pick", "")
    c1, c2 = st.columns(2)
    ready = not (kind == "OTHER" and not desc and rk == "new")
    if c1.button("Redact everywhere", type="primary", width="stretch", disabled=not ready):
        if rk == "existing":
            sess.redact_as(rv, phrase)
        elif kind == "OTHER":
            sess.redact(phrase, f"OTHER:{desc}", rv.strip(), f"{desc} {{1}}")
        else:
            sess.redact(phrase, kind, rv.strip())
        st.session_state.focus_note = f"Redacted “{phrase}” in {total} place{'s' if total != 1 else ''}"
        st.rerun()
    if c2.button("Cancel", width="stretch"):
        st.rerun()
    if not ready:
        st.caption("Describe what it is to create a new label.")


@st.dialog("Change a redaction")
def edit_dialog(sess: Session, res, ref: str) -> None:
    item = res.item(ref)
    if item is None:
        st.warning("That item no longer exists.")
        return
    is_flag = ref.startswith("F|")
    forms = forms_of(item)
    current = item.replacement_for(item.text if is_flag else item.canonical)
    st.markdown(f'<span class="card-type">{"Relationship phrase" if is_flag else type_name(item.type)}'
                f'</span>', unsafe_allow_html=True)
    st.markdown("".join(
        f'<div class="form-row"><span class="form">{html.escape(f)}</span> <span class="dim">×{n} · '
        f'{html.escape(", ".join(res.names[d] for d in docs))}</span></div>' for f, _, n, docs, _ in forms),
        unsafe_allow_html=True)
    st.caption(f"Now shown as “{current}”. Rule: {item.rule}. Changes apply to every occurrence.")
    kind, value = replacement_picker(res, item, f"ed-{ref}", item.custom or item.proposed)
    c1, c2, c3 = st.columns(3)
    if c1.button("Save", type="primary", width="stretch"):
        apply_replacement(sess, res, ref, item, kind, value)
        st.rerun()
    if c2.button("Keep original", width="stretch"):
        sess.decide(ref, "keep")
        st.rerun()
    if not is_flag and item.rule == "manual" and c3.button("Remove redaction", width="stretch"):
        sess.unredact(item.key)
        st.rerun()
    if not is_flag:
        with st.expander("Split a form off into another entry"):
            split_controls(sess, res, item, f"ed-split-{ref}")


def documents(sess: Session, res) -> None:
    n = len(res.names)
    approved = sum(is_current(sess, res, i) for i in range(n))
    st.session_state.setdefault("doc_pick", 0)
    if st.session_state.doc_pick not in range(n):
        st.session_state.doc_pick = 0

    def step(delta: int) -> None:
        st.session_state.doc_pick = (st.session_state.doc_pick + delta) % n

    def next_unapproved() -> None:
        cur = st.session_state.doc_pick
        todo = [i for i in list(range(cur + 1, n)) + list(range(cur)) if not is_current(sess, res, i)]
        if todo:
            st.session_state.doc_pick = todo[0]

    st.markdown(f'<p class="count-top"><b>{approved}/{n}</b> approved</p><p class="ll-label">Documents</p>',
                unsafe_allow_html=True)
    d1, d2, d3, d4 = st.columns([4.6, .55, .55, 1.7], vertical_alignment="bottom")
    # A dropdown scales to large sets (type to search); ✓ marks approved documents.
    doc = d1.selectbox("Document", list(range(n)), key="doc_pick", label_visibility="collapsed",
                       format_func=lambda i: f"{'✓ ' if is_current(sess, res, i) else ''}{res.names[i]}")
    d2.button("←", on_click=step, args=(-1,), help="Previous document", width="stretch", key="prev_doc")
    d3.button("→", on_click=step, args=(1,), help="Next document", width="stretch", key="next_doc")
    d4.button("Next to approve", on_click=next_unapproved, disabled=approved == n, width="stretch")

    edit = st.session_state.get("edit_mode", False)
    options = [(i, v) for i in range(n) for v in ("original", "anonymized")]
    fmt = lambda o: f"{short(res.names[o[0]])} · {o[1]}"
    c1, c2, c3, c4 = st.columns([1.55, 1.75, 1.75, 1.45], vertical_alignment="bottom")
    layout = c1.segmented_control("Layout", ["Side by side", "Single"], default="Side by side", key="doc_layout")
    if layout == "Single":
        panes, sync = [(doc, "anonymized")], False
    else:
        left = c2.selectbox("Left", options, index=options.index((doc, "original")), format_func=fmt, key=f"left_{doc}")
        right = c3.selectbox("Right", options, index=options.index((doc, "anonymized")), format_func=fmt,
                             key=f"right_{doc}")
        sync = c4.toggle("Scroll together", value=True, key="sync_scroll",
                         help="Scrolling either pane moves the other to the same passage.")
        panes = [left, right]

    present = sorted({g.type for g in res.segments[doc]})
    st.markdown('<div class="legend">' + "".join(
        f'<span><span class="chip t-{color_type(t)}">&nbsp;</span> {type_name(t)}</span>' for t in present)
        + '<span>Hover any highlight to see the other version. Dashed = not reviewed yet.</span></div>',
        unsafe_allow_html=True)
    if edit:
        st.markdown('<p class="edit-hint">Editing: select any text to redact it, or click ✎ next to a placeholder '
                    'to change or remove it.</p>', unsafe_allow_html=True)

    def head(d, v):
        btn = (f'<button class="pane-edit{" on" if edit else ""}" data-act="toggle-edit">'
               f'{"✓ Done" if edit else "✎ Edit"}</button>') if v == "anonymized" else ""
        tick = '<span class="ok">✓ approved</span>' if is_current(sess, res, d) else ""
        return (f'<div class="pane-head"><div class="pane-top"><span class="pane-tag {v}">{v}</span>{btn}</div>'
                f'<div class="pane-name">{html.escape(res.names[d])}{tick}</div></div>')

    body = "".join(
        f'<div class="pane-wrap">{head(d, v)}'
        f'<div class="pane doc" data-doc="{d}" data-version="{v}">{render_doc(res, d, v, edit)}</div></div>'
        for d, v in panes)
    result = doc_panes(data={"html": f'<div class="panes n{len(panes)}">{body}</div>', "sync": bool(sync),
                             "edit": edit}, key="doc_panes", on_action_change=lambda: None)
    action = json.loads(result.action) if getattr(result, "action", None) else None
    if action and action.get("t") != st.session_state.get("last_action"):
        st.session_state.last_action = action.get("t")
        if action["kind"] == "toggle_edit":
            st.session_state.edit_mode = not edit
            st.rerun()
        elif action["kind"] == "redact":
            redact_dialog(sess, res, action)
        elif action["kind"] == "edit":
            edit_dialog(sess, res, action["ref"])


    # Approval: a clear button at the bottom moves the document to the final stage.
    st.markdown('<div class="ll-rule"></div>', unsafe_allow_html=True)
    stale = doc in sess.approved and not is_current(sess, res, doc)
    a1, a2, a3 = st.columns(3, vertical_alignment="center")
    if is_current(sess, res, doc):
        a1.button("✓ Approved · undo", width="stretch", key=f"unapprove_{doc}",
                  on_click=lambda: sess.unapprove(doc), help="Move this document back out of the final stage.")
    else:
        if a1.button("✓ Approve new version" if stale else "✓ Approve document", type="primary", width="stretch",
                     key=f"approve_{doc}"):
            sess.approve(doc, res)
            st.session_state.focus_note = f"Approved {res.names[doc]} — it's in the final stage"
            st.rerun()
    a2.download_button("Download this document", res.outputs[doc], f"{Path(res.names[doc]).stem}.anonymized.txt",
                       "text/plain", width="stretch")
    a3.download_button("Download all (.zip)", zip_outputs(res.names, res.outputs, res), "anonymized_documents.zip",
                       "application/zip", type="primary", width="stretch")
    if stale:
        st.warning("This document changed after you approved it. Approve the new version to update the final "
                   "stage; until then it keeps the version you approved.")

    with st.expander("Every change in this document"):
        seen = {}
        for g in res.segments[doc]:
            seen.setdefault((g.original, g.replacement), type_name(g.type))
        st.dataframe(pd.DataFrame([{"original": o, "replacement": r, "type": t} for (o, r), t in seen.items()]),
                     hide_index=True, width="stretch")


def approved_view(sess: Session, res) -> None:
    n = len(res.names)
    items = sorted(sess.approved.items())
    st.markdown(f'<p class="count-top"><b>{len(items)}/{n}</b> approved</p><h2>Final stage</h2>'
                f'<p class="ll-note">Approved documents are stored here as the exact version you approved.</p>',
                unsafe_allow_html=True)
    if not items:
        st.info("Nothing approved yet. Open Documents and tick the check-mark at the bottom of a document.")
        return
    rows = [{"document": res.names[d], "approved": a["at"], "redactions": a["changes"],
             "status": "current" if a["output"] == res.outputs[d] else "changed since approval"} for d, a in items]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    names = [res.names[d] for d, _ in items]
    outputs = [a["output"] for _, a in items]
    st.download_button(f"Download approved documents (.zip, {len(items)})", zip_outputs(names, outputs, res, names),
                       "approved_documents.zip", "application/zip", type="primary")
    for d, a in items:
        with st.expander(f"✓ {res.names[d]} — approved {a['at']}"):
            c1, c2, _ = st.columns([1.4, 1, 2])
            c1.download_button("Download", a["output"], f"{Path(res.names[d]).stem}.anonymized.txt", "text/plain",
                               key=f"dl_appr_{d}", width="stretch")
            if c2.button("Move back", key=f"unappr_{d}", width="stretch"):
                sess.unapprove(d)
                st.rerun()
            st.markdown(f'<div class="doc" style="max-height:340px;overflow:auto">{html.escape(a["output"])}</div>',
                        unsafe_allow_html=True)


# --------------------------------------------------------------------------- log & consistency
def log_view(sess: Session, res) -> None:
    cc = consistency_check(res)
    label("Consistency check")
    n = len(cc["rows"])
    lines = [
        (cc["all_consistent"], f"{n} entities · each has one replacement across every document"
         if cc["all_consistent"] else "Some entities have differing replacements — see table"),
        (cc["leak_free"], "No original text found in the anonymized output" if cc["leak_free"]
         else "Original text still present — see 'leaks' column"),
        (not cc["unreviewed"], "Review queue complete" if not cc["unreviewed"]
         else f"{len(cc['unreviewed'])} item(s) still unreviewed (proposed replacements applied)"),
    ]
    st.markdown('<div class="check">' + "<br>".join(
        f'<span class="{"ok" if ok else "warn"}">{"✓" if ok else "!"}</span>&nbsp; {html.escape(t)}' for ok, t in lines)
        + "</div>", unsafe_allow_html=True)
    if cc["unreviewed"]:
        for k in cc["unreviewed"]:
            it = res.item(k)
            st.markdown(f"<span class='mono dim'>· {html.escape(it.text if k.startswith('F|') else it.canonical)}"
                        f" → {html.escape(it.proposed)}</span>", unsafe_allow_html=True)
    st.dataframe(pd.DataFrame(cc["rows"]), hide_index=True, width="stretch")

    st.markdown('<div class="ll-rule"></div>', unsafe_allow_html=True)
    label("Redaction log")
    st.dataframe(pd.DataFrame(redaction_log(res)), hide_index=True, width="stretch")
    st.download_button("Download redaction log (.csv)", log_csv(res), "redaction_log.csv", "text/csv", type="primary")


# --------------------------------------------------------------------------- main
inject_css()
header()
sess: Session | None = st.session_state.get("session")
if sess is None:
    home()
else:
    res = sess.run()
    sidebar(sess, res)
    res = sess.run()  # toggles in the sidebar may have changed settings this run
    if st.session_state.get("focus_note"):
        st.toast(st.session_state.pop("focus_note"))
    n_ok = sum(is_current(sess, res, i) for i in range(len(res.names)))
    labels = {"Review": "Review", "Documents": f"Documents {n_ok}/{len(res.names)}",
              "Approved": f"✓ Approved ({len(sess.approved)})", "Log & consistency": "Log & consistency"}
    view = st.segmented_control("View", VIEWS, key="view", format_func=labels.get,
                                label_visibility="collapsed") or "Review"
    st.markdown('<div style="height:.6rem"></div>', unsafe_allow_html=True)
    {"Review": review, "Documents": documents, "Approved": approved_view, "Log & consistency": log_view}[view](sess, res)
