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


def _ensure_fresh_engine() -> None:
    """After a deploy, the server can keep the previous engine modules loaded while app.py is new. Compare the
    engine and policy files on disk with what's loaded, and reload the engine if they differ."""
    import hashlib
    import importlib
    import sys

    import engine as loaded
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for path in sorted(list((root / "engine").glob("*.py")) + list((root / "policies").rglob("*.yaml"))):
        h.update(path.read_bytes())
    if h.hexdigest()[:12] != getattr(loaded, "_CODE_VERSION", None):
        for name in ("engine.policy", "engine.extract", "engine.detect", "engine.registry", "engine.export", "engine"):
            if name in sys.modules:
                importlib.reload(sys.modules[name])


_ensure_fresh_engine()

from engine import (Session, available_policies, consistency_check, load_policy, load_policy_text, redaction_log,
                    search)
from engine.export import buyer_zip, internal_log_csv
from engine.extract import SUPPORTED
from engine.registry import norm_form, split_key, type_title

SAMPLES = Path(__file__).parent / "samples"
TYPE_NAMES = {
    "PERSON": "Person", "ORGANIZATION": "Organization", "ACCOUNT_NUMBER": "Account number",
    "CRD_NUMBER": "CRD number", "CUSIP": "CUSIP", "TICKER": "Ticker", "CASE_NUMBER": "Case / docket number",
    "EMAIL_ADDRESS": "Email", "PHONE_NUMBER": "Phone", "US_SSN": "SSN", "STREET_ADDRESS": "Street address",
    "DATE": "Date", "FLAG": "Flag", "MANUAL": "Manual redaction", "MONEY": "Amount", "SHARE_COUNT": "Share count",
    "URL": "Website", "TAX_ID": "Tax ID",
}
TYPE_COLORS = {  # restrained hues; used at low alpha so they read on cream and on charcoal
    "PERSON": "120,195,228", "ORGANIZATION": "205,170,120", "ACCOUNT_NUMBER": "29,181,218",
    "CRD_NUMBER": "29,181,218", "CUSIP": "150,140,200", "TICKER": "150,140,200",
    "CASE_NUMBER": "110,150,200", "EMAIL_ADDRESS": "120,180,150", "PHONE_NUMBER": "120,180,150",
    "US_SSN": "210,120,110", "STREET_ADDRESS": "190,175,130", "DATE": "150,150,150", "FLAG": "29,181,218",
    "MANUAL": "39,39,39", "MONEY": "160,170,90", "SHARE_COUNT": "160,170,90", "URL": "120,180,150",
    "TAX_ID": "232,170,90",
}
VIEWS = ["Start", "Review", "Documents", "Final"]

st.set_page_config(page_title="Longitude Anonymizer", page_icon="◐", layout="wide",
                   initial_sidebar_state="auto")


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
    [data-testid="stSidebar"][aria-expanded="true"] { min-width: 300px; }  /* closed sidebar must free its space */
    /* Layout reacts to the space the content actually has (sidebar open or closed), not the window */
    [data-testid="stMainBlockContainer"] { container-type: inline-size; }
    .ll-head { flex-wrap: wrap; row-gap: .35rem; }
    .ll-word, .ll-coord { white-space: nowrap; }
    /* Prose stays at a readable measure instead of running the full page width */
    [data-testid="stCaptionContainer"], [data-testid="stAlert"], [data-testid="stAlertContainer"], .stAlert, .ll-note, .gate,
    [data-testid="stCheckbox"] label, [data-testid="stExpanderDetails"] p, .edit-hint { max-width: 62ch; }
    .kv dd { max-width: 60ch; }
    @media (max-width: 640px) { .ll-coord { display:none; } .ll-word { font-size:1.15rem; } }
    .ll-word .dim { color:var(--dim); font-family:var(--sans); font-size:.85rem; font-weight:400; margin-left:.3rem; }
    .ll-coord { font-size:.8rem; color:var(--dim); padding:.25rem .8rem; border:1px solid var(--rule); border-radius:999px; }
    .wave { display:block; width:100%; height:18px; color:var(--soft); margin:.2rem 0 2rem; }
    .ll-label { font-family:var(--sans); text-transform:uppercase; letter-spacing:.12em; font-size:.72rem;
                font-weight:500; color:var(--dim); margin:0 0 .5rem 0; }
    .ll-label b { color:var(--fg); font-weight:500; }
    .hero { text-align:center; max-width:760px; margin:2.2rem auto 2.6rem; }
    .hero h1 { font-size:clamp(2.4rem, 5.5vw, 3.6rem) !important; line-height:1.08; margin:0 0 1rem; padding:0; }
    .hero p { font-size:1.15rem; line-height:1.6; color:var(--dim); margin:0 auto; max-width:56ch; }
    .steps { display:grid; grid-template-columns:repeat(4, 1fr); gap:1rem; margin:0 0 2.4rem; }
    .step { border:1px solid var(--rule); border-radius:1rem; padding:1.1rem 1.2rem; }
    .step b { display:block; font-family:var(--serif); font-size:1.15rem; font-weight:500; margin-bottom:.35rem; }
    .step span { color:var(--dim); font-size:.93rem; line-height:1.55; }
    .step i { font-style:normal; display:inline-block; width:1.6rem; height:1.6rem; line-height:1.6rem; text-align:center;
              border-radius:999px; background:var(--soft); color:#272727; font-size:.8rem; font-weight:500; margin-bottom:.6rem; }
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
    .card-type { display:inline-block; font-size:.75rem; font-weight:500; letter-spacing:.08em; text-transform:uppercase;
                 padding:.2rem .7rem; border-radius:999px; background:var(--mark); }
    .card-ctx { font-family:var(--sans); font-weight:400; font-size:1rem; line-height:1.6; margin:1rem 0 1.3rem; max-width:62ch; }
    .card-ctx mark { background:var(--soft); color:#272727; padding:.02em .3em; border-radius:.35rem; }
    .card-top { display:flex; justify-content:space-between; align-items:center; gap:1rem; font-size:.85rem; flex-wrap:wrap; }
    .ctx-dim { color:var(--dim); }
    .form { font-weight:500; padding:.05em .45em; border-radius:.4rem; background:var(--mark); }
    .form-row { padding:.55rem 0; border-bottom:1px solid var(--rule); }
    .form-ctx { font-size:.92rem; line-height:1.6; margin-top:.3rem; }
    .form-ctx mark { background:var(--soft); color:#272727; padding:0 .2em; border-radius:.3rem; }
    .ll-sub { font-size:.78rem; font-weight:500; color:var(--dim); margin:.8rem 0 .3rem; }
    .kv { font-family:var(--sans); display:grid; grid-template-columns: 9rem 1fr; gap:.55rem 1rem; font-size:1rem; margin:0; }
    .kv dt { font-size:.72rem; font-weight:500; letter-spacing:.1em; text-transform:uppercase; color:var(--dim); padding-top:.25rem; }
    .kv dd { margin:0; line-height:1.55; }
    .kv dd.big { font-weight:400; font-size:1.05rem; }
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
    .tipcard { position: fixed; z-index: 2000; max-width: 300px; padding: .5rem .7rem; border-radius: .6rem;
               background: #272727; color: #F7F3EA; font-size: .8rem; line-height: 1.45; pointer-events: none;
               box-shadow: 0 4px 16px rgba(0,0,0,.2); }
    .tipcard span { color: #A7DAEF; }
    [data-ref] { cursor: pointer; }
    .chip-edit { display:inline-block; font-size:.78em; margin-left:.12em; padding:0 .2em; cursor:pointer; color:var(--dim);
                 border-radius:.3rem; vertical-align:baseline; user-select:none; }
    .chip-edit:hover { background:var(--soft); color:#272727; }
    .dp-root { position:relative; }
    .redact-fab { position:absolute; z-index:20; font:inherit; font-size:.8rem; font-weight:500; cursor:pointer; border-radius:999px;
                  padding:.3rem .8rem; border:1px solid #272727; background:#272727; color:#F7F3EA; box-shadow:0 2px 8px rgba(0,0,0,.18); }
    .edit-hint { font-size:.85rem; color:var(--dim); margin:-.2rem 0 .6rem; }
    .st-key-prev_doc button, .st-key-next_doc button { padding:.45rem 0 !important; min-width:0; font-size:1rem; }
    .gate { border:1px solid color-mix(in srgb, #c27a1a 45%, transparent); border-radius:.8rem; padding:.7rem 1rem;
            margin:.8rem 0; font-size:.9rem; }
    .gate ul { margin:.3rem 0 0 1.1rem; padding:0; } .gate li { margin:.15rem 0; color:var(--dim); }
    .chip-long { white-space: normal; box-decoration-break: clone; -webkit-box-decoration-break: clone; }
    .why { font-size: .92rem; line-height: 1.6; margin: .9rem 0 .4rem; max-width: 62ch; }
    .why b { font-weight: 600; margin-right: .35rem; }
    .keybox { border: 1px solid #c27a1a; background: color-mix(in srgb, #e0a050 14%, transparent);
              border-radius: .8rem; padding: .7rem 1rem; margin: .8rem 0 .6rem; font-size: .92rem; max-width: 62ch; }
    .sb-warn { font-size: .76rem; color: #c27a1a; margin: -.5rem 0 .8rem; line-height: 1.4; }
    [data-testid="stSidebar"] [data-testid="stPopover"] button { justify-content: flex-start; text-align: left; }
    [data-testid="stButtonGroup"] div:has(> button) { flex-wrap: wrap; row-gap: .3rem; }
    [data-testid="stSidebar"] [data-testid="stPopover"] button p { text-align: left; }
    /* Busy indicator: a "Working…" pill, top right, whenever the app is processing (fades in after .25s) */
    [data-testid="stApp"][data-test-script-state="running"]::after {
        content: "Working…"; position: fixed; top: .75rem; right: 1rem; z-index: 100000; box-sizing: border-box;
        width: 7.4rem; padding: .38rem .9rem .38rem 2.1rem; border-radius: 999px; background: #272727;
        color: #F7F3EA; font: 500 .8rem/1.2 "Libre Franklin", system-ui, sans-serif;
        box-shadow: 0 2px 12px rgba(0,0,0,.18); animation: ll-fade .2s .25s both; }
    [data-testid="stApp"][data-test-script-state="running"]::before {
        content: ""; position: fixed; top: calc(.75rem + .48rem); right: calc(1rem + 7.4rem - 1.55rem);
        z-index: 100001; width: .8rem; height: .8rem; box-sizing: border-box; border-radius: 50%;
        border: 2px solid #A7DAEF; border-top-color: transparent;
        animation: ll-fade .2s .25s both, ll-spin .8s linear infinite; }
    @keyframes ll-spin { to { transform: rotate(360deg); } }
    @keyframes ll-fade { from { opacity: 0; } to { opacity: 1; } }
    .st-key-rules_lib_host { display: none; }
    .count-top { font-size:.85rem; color:var(--dim); margin:0 0 .15rem; }
    .count-top b { color:var(--fg); font-weight:500; }
    .doc-count b { color:var(--fg); }
    .pane-tag { font-size:.7rem; font-weight:500; letter-spacing:.08em; text-transform:uppercase; padding:.12rem .6rem; border-radius:999px; }
    .pane-tag.original { border:1px solid var(--rule); color:var(--dim); }
    .pane-tag.anonymized { background:var(--soft); color:#272727; }
    .pane { height:68vh; overflow-y:auto; padding:1rem 1.2rem; }
    .panes.n1 .pane { height:auto; max-height:none; }
    .doc { font-family:var(--sans); font-weight:400; white-space:pre-wrap; font-size:.97rem; line-height:1.8; }
    a.ln { display:inline; }
    .chip { font-size:.86em; font-weight:500; padding:.04em .45em; border:1px solid; border-radius:.4rem; cursor:help; white-space:nowrap; }
    .chip.pending { border-style:dashed; }
    .chip.kept { border-color:transparent !important; background:transparent !important; font-weight:inherit;
                 font-size:inherit; padding:0; border-bottom:1px dotted var(--dim) !important; border-radius:0; }
    .orig { border-radius:.3rem; padding:0 .1em; cursor:help; }
    .orig.kept { background:transparent !important; border-bottom:1px dotted var(--dim); }
    .flag { border-bottom:2px dashed #1DB5DA; cursor:help; }
    mark.hit { background: color-mix(in srgb, #A7DAEF 75%, transparent); color: inherit; padding: 0 .1em; border-radius: .2rem; }
    .hit-chip { outline: 2px solid #1DB5DA; outline-offset: 1px; }
    mark.hit.pulse, .hit-chip.pulse { animation: leakpulse 1.2s ease-in-out 2; }
    .search-sum { font-size: .9rem; margin: .2rem 0 .4rem; color: var(--dim); }
    .search-sum b { color: var(--fg); } .search-sum b.warn { color: #c27a1a; } .search-sum b.ok { color: #2f8f62; }
    .hit-row { font-size: .88rem; line-height: 1.5; }
    .hit-st { font-size: .74rem; font-weight: 600; padding: .05rem .5rem; border-radius: 999px; }
    .st-redacted, .st-changed { background: color-mix(in srgb, #2f8f62 18%, transparent); }
    .st-still, .st-visible, .st-partly { background: color-mix(in srgb, #e0a050 30%, transparent); }
    .st-generalized { background: color-mix(in srgb, #e0a050 22%, transparent); }
    .st-kept, .st-flagged { background: color-mix(in srgb, #1DB5DA 18%, transparent); }
    mark.leak { background: color-mix(in srgb, #e0a050 45%, transparent); color: inherit; padding: 0 .15em;
                border-radius: .25rem; outline: 1.5px solid #c27a1a; cursor: help; }
    mark.leak.pulse { animation: leakpulse 1.2s ease-in-out 2; }
    @keyframes leakpulse { 50% { outline-width: 4px; } }
    .legend { display:flex; flex-wrap:wrap; gap:.4rem 1rem; font-size:.8rem; color:var(--dim); margin:.4rem 0 1rem; align-items:center; }

    .ok { color:#2f8f62; } .warn { color:#c27a1a; }
    .check { font-size:.98rem; line-height:2; }

    /* widgets */
    .stButton button, .stDownloadButton button, .stFormSubmitButton button {
        font-weight:500; letter-spacing:0; border-radius:999px; box-shadow:none; padding:.5rem .9rem; white-space:nowrap; }
    .stButton button[kind="primary"], .stDownloadButton button[kind="primary"], .stFormSubmitButton button {
        background:#A7DAEF !important; color:#272727 !important; border:1px solid #A7DAEF !important; }
    .stButton button:disabled, .stDownloadButton button:disabled, .stFormSubmitButton button:disabled {
        opacity: 1 !important; background: transparent !important; color: var(--dim) !important;
        border: 1px dashed var(--rule) !important; cursor: not-allowed !important; }
    .stButton button[kind="primary"]:hover, .stDownloadButton button[kind="primary"]:hover,
    .stFormSubmitButton button:hover { background:#8fcbe4 !important; border-color:#8fcbe4 !important; }
    [data-testid="stFileUploaderDropzone"] { border:1px dashed var(--rule); background:transparent; border-radius:1rem; }
    [data-testid="stSidebar"] .ll-label { margin-top:1.2rem; }
    .sb-title { font-size:.8rem; font-weight:600; color:var(--dim); margin:.2rem 0 .8rem; letter-spacing:.02em; }
    [data-testid="stSidebar"] [data-testid="stExpander"] { margin-bottom:.15rem; }
    [data-testid="stSidebar"] [data-testid="stExpander"] summary p { font-weight:600; }
    /* narrow content area (small window or sidebar open) — last, so it wins */
    @container (max-width: 760px) {
      .ll-coord { display: none; }
      .steps { grid-template-columns: 1fr 1fr; }
      .panes.n2 { grid-template-columns: 1fr; }
      .kv { grid-template-columns: 1fr; gap: .1rem 0; }
      .kv dt { padding-top: .55rem; }
      .card { padding: 1.1rem 1.15rem .9rem; }
      .card-ctx { font-size: 1.08rem; }
      .card-top { flex-direction: column; align-items: flex-start; gap: .35rem; text-align: left; }
      [data-testid="stButtonGroup"] button { padding: .3rem .6rem; font-size: .82rem; }
      .hero h1 { font-size: 2.1rem !important; }
      /* control rows wrap instead of truncating their labels */
      [data-testid="stHorizontalBlock"] { flex-wrap: wrap; row-gap: .5rem; }
      .st-key-stepbar [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child,
      .st-key-hits [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child { flex: 1 1 14rem !important; }
      .st-key-hits [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child { flex: 0 0 5rem !important; min-width: 5rem; }
      .st-key-stepbar [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child { flex: 0 0 6.5rem !important; min-width: 6.5rem; }
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { flex: 1 1 10rem !important; min-width: min(100%, 10rem); }
      .st-key-prev_doc, .st-key-next_doc { min-width: 3rem; }
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has(.st-key-prev_doc),
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has(.st-key-next_doc) { flex: 0 0 3.2rem !important; min-width: 3.2rem; }
    }
    @container (max-width: 520px) {
      .ll-word .dim { display: none; }
      .ll-word { font-size: 1.1rem; }
    }
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
    coord = f'<span class="ll-coord">{len(sess.names)} files · {html.escape(sess.policy.name)} rules</span>' if sess else ""
    st.markdown(f'<div class="ll-head"><span class="ll-word">Longitude Anonymizer <span class="dim">— prototype</span></span>'
                f'{coord}</div>{WAVE}', unsafe_allow_html=True)


# --------------------------------------------------------------------------- state helpers
def start_session(files: list[tuple[str, bytes]], policy, rights: bool = False) -> None:
    with st.spinner("Reading documents and detecting entities…"):
        st.session_state.session = Session.from_files(files, policy)
        st.session_state.session.rights_confirmed = rights
    st.session_state.view = "Review"
    st.session_state.editing = False


def reset() -> None:
    sess = st.session_state.get("session")
    if sess is not None and hasattr(sess, "export_rules"):
        try:  # "Start over" clears the files, not your rules
            st.session_state.carry_rules = sess.export_rules(
                f"{sess.policy.name}", f"Your settings and names from the last set ({sess.policy.name}).")
        except Exception:  # noqa: BLE001 — never block a reset
            pass
    for k in [k for k in st.session_state if k.startswith(("b_", "t_"))] + ["session", "view", "editing", "focus", "edit_mode", "doc_pick", "last_action", "rights_ok", "rules_pick", "rules_touched"]:
        st.session_state.pop(k, None)


STATUS_WORDS = {"auto": "hidden automatically", "accepted": "you approved", "edited": "you changed it",
                "pending": "needs a look", "manual": "hidden by you", "kept": "kept as written",
                "public": "kept (public company)", "setting": "kept (setting)"}
KIND_NAMES = {"PERSON": "Person", "ORGANIZATION": "Company", "MANUAL": "Other"}


def decide(item_key: str, action: str, value: str = "", note: str = "") -> None:
    st.session_state.session.decide(item_key, action, value, note=note)
    st.session_state.editing = False


def undo() -> None:
    sess = st.session_state.session
    what = sess.undo() if hasattr(sess, "undo_stack") else None
    st.session_state.editing = False
    if what:
        st.session_state.focus_note = f"Undone: {what}"


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


def delete_entity(sess: Session, ent) -> None:
    """'Don't hide this': a false detection; its text appears as written in every file (undoable)."""
    if ent.rule == "manual":
        sess.unredact(ent.key, note=f"Show “{ent.canonical}” again")
    else:
        sess.delete(ent.key, note=f"Show “{ent.canonical}” again")
    st.session_state.focus_note = f"“{ent.canonical}” will appear as written — ↶ Undo if that was a mistake"
    st.session_state.editing = False


def merge_into(sess: Session, src, dst) -> None:
    sess.merge(src.key, dst.key, note=f"Treat “{src.canonical}” as {dst.label}")
    st.session_state.focus = dst.key
    st.session_state.focus_note = f"“{src.canonical}” is now {dst.label} everywhere"


def do_split(sess: Session, ent, form: str, dest: str) -> None:
    if dest.startswith("new:"):
        new_type = None if dest[4:] == ent.type else dest[4:]
        sess.split(ent.key, form, new_type, note=f"Separate “{form}” from {ent.label}")
        st.session_state.focus = split_key(ent.key, ent.type, form, new_type)
        where = f"a new {KIND_NAMES.get(new_type or ent.type, type_name(new_type or ent.type)).lower()}"
    else:
        sess.split(ent.key, form, None, dest, note=f"Move “{form}” out of {ent.label}")
        st.session_state.focus = dest
        where = "the one you picked"
    st.session_state.focus_note = f"“{form}” is now separate from {ent.label} ({where})"


def split_controls(sess: Session, res, ent, key: str) -> None:
    """Pick a written form, then where all its variants go: a new entry (any type) or an existing entity."""
    groups: dict[str, list] = {}
    for f, _, cnt, _, _ in forms_of(ent):
        g = groups.setdefault(norm_form(ent.type, f), [[], 0])
        g[0].append(" ".join(f.split()))
        g[1] += cnt
    form = st.selectbox("Which way of writing it is someone else?", list(groups), key=f"{key}_form", index=None,
                        placeholder="Choose…", format_func=lambda n: f"{', '.join(groups[n][0])}  (×{groups[n][1]})")
    types = list(dict.fromkeys([ent.type, "PERSON", "ORGANIZATION"]
                               + [t for t, sp in sess.policy.entities.items() if sp.get("action") == "redact"] + ["MANUAL"]))
    others = sorted((e for e in res.entities if e.key != ent.key and e.is_redacted),
                    key=lambda e: (e.type != ent.type, e.label))
    dests = [f"new:{t}" for t in types] + [e.key for e in others]
    by = {e.key: e for e in others}
    st.selectbox("It is…", dests, key=f"{key}_dest",
                 format_func=lambda d: (f"A new {KIND_NAMES.get(d[4:], type_name(d[4:])).lower()}")
                 if d.startswith("new:") else f"Same as {by[d].label} ({examples(by[d], 2)})")
    st.button("Separate", key=f"{key}_go", width="stretch", disabled=form is None,
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
    st.caption(f"Joins {by[choice].label} everywhere.")
    return "existing", choice


def apply_replacement(sess: Session, res, item_key: str, item, kind: str, value: str) -> bool:
    """Existing label → join that entity. New text → for an entity, rename its label (so the registry and every
    document agree); for a flag, use the text as its replacement. Returns False if the label is taken."""
    if kind == "existing":
        merge_into(sess, item, res.entity(value))
    elif hasattr(item, "variants") and value.strip() and value.strip() != item.label:
        try:
            sess.rename(item.key, value, note=f"Rename “{item.canonical}” → “{value.strip()}”")
        except ValueError as exc:
            st.error(str(exc))
            return False
        st.session_state.focus_note = f"{item.canonical} is now “{value.strip()}” in every document"
        if item.status == "pending":
            item_after = sess.run().item(item_key)
            if item_after is not None and item_after.status == "pending":
                sess.ops["decisions"][item_key] = {"action": "accept", "value": ""}  # part of the same change
    elif hasattr(item, "variants"):
        sess.decide(item_key, "accept", note=f"Accept “{item.canonical}”")
    else:
        sess.decide(item_key, "edit", value.strip() or item.proposed, note=f"Change wording of “{item.text[:40]}”")
    st.session_state.editing = False
    return True


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
        '<p>Upload a set of files. Get them back anonymized: one person, one label, in every file.</p></div>'
        '<div class="steps">'
        '<div class="step"><i>1</i><b>Files</b><span>Add PDF, Word, HTML or text. Read in memory, never saved.</span></div>'
        '<div class="step"><i>2</i><b>Check</b><span>Names, companies, accounts and dates are found. You check the '
        'unsure ones.</span></div>'
        '<div class="step"><i>3</i><b>Read & approve</b><span>Compare each file with the original.</span></div>'
        '<div class="step"><i>4</i><b>Download</b><span>Get the approved files.</span></div></div>',
        unsafe_allow_html=True)

    label("Your files")
    uploads = st.file_uploader("Files", type=[s.strip(".") for s in SUPPORTED], accept_multiple_files=True,
                               label_visibility="collapsed")
    rights = st.checkbox("I have the right to share these files, including for my client and any counterparties.",
                         key="rights_ok", help="Anonymizing doesn't cure privilege, NDAs or patient confidentiality.")
    policies = available_policies()
    library = st.session_state.get("my_rules") or {}
    choices: dict[str, tuple[str, str]] = {}           # label → (kind, value)
    if st.session_state.get("carry_rules"):
        choices["Your rules from the last set"] = ("yaml", st.session_state.carry_rules)
    for nm in sorted(library):
        choices[f"★ {nm}"] = ("yaml", library[nm]["yaml"])
    for nm in policies:
        choices[f"{nm} (built-in)"] = ("file", nm)
    labels_ = list(choices)
    if not st.session_state.get("rules_touched"):  # default: last set's rules, else your newest saved rules
        newest = max(library, key=lambda nm: library[nm].get("at", ""), default=None)
        st.session_state.rules_pick = ("Your rules from the last set" if st.session_state.get("carry_rules")
                                       else f"★ {newest}" if newest else labels_[0])
    if st.session_state.get("rules_pick") not in choices:
        st.session_state.rules_pick = labels_[0]

    def load_choice(lbl: str):
        kind, value = choices[lbl]
        return load_policy(policies[value]) if kind == "file" else load_policy_text(value, lbl.lstrip("★ ").strip())

    try:
        pol = load_choice(st.session_state.rules_pick)
    except ValueError:
        pol = load_policy(policies[next(iter(policies))])
    with st.expander(f"Rules: {pol.name}"):
        st.selectbox("Rules", labels_, key="rules_pick", on_change=lambda: st.session_state.update(rules_touched=True),
                     help="★ = saved in My rules (this browser). Save rules from ⚙ Settings once you've set them up.")
        pol = load_choice(st.session_state.rules_pick)
        st.caption(pol.raw.get("description", "") + (f" · {len(pol.known_parties)} name(s) always hidden"
                                                     if pol.known_parties else ""))
        if library:
            st.markdown('<p class="ll-sub">My rules (this browser)</p>', unsafe_allow_html=True)
            for i, nm in enumerate(sorted(library)):
                r1, r2 = st.columns([5, 1], vertical_alignment="center")
                r1.markdown(f'★ {html.escape(nm)} <span class="dim">· saved {html.escape(library[nm].get("at", ""))}'
                            '</span>', unsafe_allow_html=True)
                r2.button("×", key=f"lib_rm_{i}", help=f"Delete “{nm}” from My rules",
                          on_click=library_command, args=("delete", nm))
        custom = st.file_uploader("Upload your own rules file (.yaml)", type=["yaml", "yml"], key="policy_upload")
        if custom is not None:
            try:
                pol = load_policy_text(custom.getvalue().decode("utf-8"), Path(custom.name).stem)
                st.success(f"Using “{pol.name}” · {len(pol.buckets())} rules")
            except (ValueError, UnicodeDecodeError) as exc:
                st.error(str(exc))
    c1, c2, _ = st.columns([1.3, 1.3, 2])
    if c1.button("Anonymize my files", type="primary", disabled=not (uploads and rights), width="stretch"):
        try:
            start_session([(u.name, u.getvalue()) for u in uploads], pol, rights)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if c2.button("Try with sample case", width="stretch"):
        start_session([(p.name, p.read_bytes()) for p in sorted(SAMPLES.glob("*")) if p.suffix in SUPPORTED], pol,
                      rights=True)  # fictional placeholder documents
        st.rerun()
    if not (uploads and rights):
        st.caption("Add files and tick the box to continue — or try the sample: a complaint, a staff memo and a "
                   "settlement order from one fictional matter.")
    st.caption("Nothing is saved. Refreshing or closing this tab clears your work — download first.")


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


def entity_popover(sess: Session, res, e, focus) -> None:
    status = {"pending": " · needs a look", "setting": " · off", "manual": " · by you"}.get(e.status, "")
    mark = "● " if e.key == focus else ""
    with st.popover(f"{mark}{e.label} · {e.canonical[:24]} ({len(e.mentions)}){status}", width="stretch"):
        docs = sorted({m[0] for m in e.mentions})
        st.caption(f"{type_name(e.type)} · {STATUS_WORDS.get(e.status, e.status)} · in "
                   f"{', '.join(short(sess.names[d], 22) for d in docs)}")
        if e.is_redacted and not e.nick_target:
            r1, r2 = st.columns([2.2, 1], vertical_alignment="bottom")
            new = r1.text_input("Label", e.label, key=f"lbl-{e.key}", help="Replaces the name in every file.")
            if r2.button("Rename", key=f"rn-{e.key}", width="stretch") and new.strip() != e.label:
                try:
                    sess.rename(e.key, new, note=f"Rename {e.label} → “{new.strip()}”")
                    st.session_state.focus = e.key
                    st.session_state.focus_note = f"{e.canonical} is now “{new.strip()}”"
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        st.markdown("<br>".join(f"<span class='form'>{html.escape(f)}</span> <span class='dim'>×{n}</span>"
                                for f, _, n, _, _ in forms_of(e)), unsafe_allow_html=True)
        if e.type in KIND_NAMES:
            kinds = list(KIND_NAMES)
            st.selectbox("This is a", kinds, index=kinds.index(e.type), format_func=KIND_NAMES.get,
                         key=f"kind_{e.key}", on_change=lambda k=e.key, it=e: retype_from_widget(k, it))
        if len(forms_of(e)) > 1:
            st.markdown('<p class="ll-sub">Different people or things?</p>', unsafe_allow_html=True)
            split_controls(sess, res, e, f"sb-{e.key}")
        others = sorted((o for o in res.entities if o.key != e.key and o.is_redacted),
                        key=lambda o: (o.type != e.type, o.label))
        if others:
            st.markdown('<p class="ll-sub">The same as another name?</p>', unsafe_allow_html=True)
            names = {o.key: f"{o.label} ({examples(o, 2)})" for o in others}
            tgt = st.selectbox("Same as", list(names), key=f"mv-{e.key}", format_func=names.get, index=None,
                               placeholder="Choose who this is the same as…", label_visibility="collapsed")
            st.button("Treat as the same", key=f"mb-{e.key}", on_click=merge_into, width="stretch",
                      disabled=tgt is None, args=(sess, e, next((o for o in others if o.key == tgt), None)))
        st.button("Don't hide this", key=f"del-{e.key}", on_click=delete_entity, args=(sess, e),
                  width="stretch", help="Not a real name? It will appear as written in every file.")


def party_hits(res, name: str) -> tuple[int, int]:
    """How often a known party's name is found (occurrences, files)."""
    hits = search(res, name)
    return len(hits), len({h["doc"] for h in hits})


def known_parties_form(sess: Session, res, key: str, compact: bool = False) -> None:
    """Add a name, pick what it is, see how often it's found; remove with ×. Always hidden, in every spelling."""
    kinds = {"ORGANIZATION": "Company", "PERSON": "Person", "MANUAL": "Other (codename, project…)"}
    with st.form(f"{key}_form", border=False, clear_on_submit=True):
        c1, c2, c3 = st.columns([2.4, 1.4, .9] if not compact else [1, 1, 1], vertical_alignment="bottom")
        name = c1.text_input("Name", placeholder="e.g. Project Falcon, Acme Bank, Jane Doe", key=f"{key}_name")
        kind = c2.selectbox("What is it?", list(kinds), format_func=kinds.get, key=f"{key}_kind")
        if c3.form_submit_button("Add", type="primary", width="stretch") and name.strip():
            sess.add_party(name, kind)
            st.session_state.focus_note = f"“{name.strip()}” will be hidden in every file"
            st.rerun()
    for i, party in enumerate(sess.ops.get("parties", [])):
        found, files = party_hits(res, party["name"])
        r1, r2 = st.columns([5, 1], vertical_alignment="center")
        r1.markdown(f'<span class="form">{html.escape(party["name"])}</span> '
                    f'<span class="dim">{kinds.get(party["type"], "Other").split(" (")[0]} · '
                    + (f'found {found}× in {files} file{"s" if files != 1 else ""}' if found else "not found yet")
                    + '</span>', unsafe_allow_html=True)
        r2.button("×", key=f"{key}_rm_{i}", help=f"Stop always hiding “{party['name']}”",
                  on_click=sess.remove_party, args=(party["name"],))


def date_explainer(res, mode: str) -> str:
    dates = sorted((e for e in res.entities if e.type == "DATE"), key=lambda e: e.mentions[0][:2])[:2]
    ex = " · ".join(f"{' '.join(e.mentions[0][3].split())} → {e.proposed if e.status != 'setting' else e.mentions[0][3]}"
                    for e in dates)
    text = {"month_year": "Each date becomes its month and year. Simple, but events in the same month look "
                          "like they happened together.",
            "shifted": "Every date moves by the same hidden number of days, so the gaps between events stay "
                       "true (trades 6 days apart are still 6 days apart) while the real dates are hidden.",
            "relative": "Dates become days counted from the earliest date in your files (Day 0, Day 6…). "
                        "Order and gaps stay true; no calendar dates are shown.",
            "exact": "Dates stay as written."}[mode]
    return text + (f" Example from your files: {ex}." if ex and mode != "exact" else "")


def sidebar(sess: Session, res) -> None:
    """Four collapsible sections: Settings · Known parties · Rules · Entities."""
    focus = st.session_state.get("focus")
    s = sess.settings
    with st.sidebar:
        st.markdown(f'<p class="sb-title">{html.escape(sess.policy.name)} rules · '
                    f'{len(res.names)} file{"s" if len(res.names) != 1 else ""}</p>'
                    '<p class="sb-warn">Nothing is saved — refreshing or closing this tab clears your work.</p>',
                    unsafe_allow_html=True)

        # ---- Settings
        with st.expander("⚙ Settings"):
            st.session_state.setdefault("t_roles", bool(s.get("role_labels", False)))
            st.toggle("Label by role and context", key="t_roles",
                      help="Defendant, Buyer, 'Address of principal executive offices'… instead of Company A.")
            date_modes = {"month_year": "Month + year", "shifted": "Shifted (same gaps)",
                          "relative": "Relative (Day 0, Day 6…)", "exact": "Exact"}
            styles = {"labels": "Labels (Person A, Acquirer…)", "redacted": "[REDACTED]", "remove": "Remove the text"}
            st.session_state.setdefault("t_style", s.get("placeholder_style", "labels"))
            st.selectbox("Label style", list(styles), key="t_style", format_func=styles.get,
                         help="Labels show when two mentions are the same. [REDACTED] or removing gives nothing back.")
            st.session_state.setdefault("t_dates", s.get("date_mode", "month_year"))
            st.selectbox("Dates", list(date_modes), key="t_dates", format_func=date_modes.get)
            st.caption(date_explainer(res, st.session_state.t_dates))
            pub_modes = {"review": "Ask if it's a party", "keep": "Always keep", "redact": "Always redact"}
            st.session_state.setdefault("t_pub", s.get("public_companies", "review"))
            st.selectbox("Public companies", list(pub_modes), key="t_pub", format_func=pub_modes.get)
            st.session_state.setdefault("t_addr", s.get("generalize_addresses", True))
            st.toggle("Addresses → city, state", key="t_addr", help="When the document gives no caption.")
            with st.popover("Save these rules", width="stretch"):
                save_rules_panel(sess)
            with st.popover("Change rules (policy pack)", width="stretch"):
                policies = available_policies()
                library = st.session_state.get("my_rules") or {}
                packs = [f"★ {nm}" for nm in sorted(library)] + list(policies)
                pick = st.selectbox("Rules", packs, key="swap_pick", help="★ = saved in My rules (this browser)")
                up = st.file_uploader("Or upload (.yaml)", type=["yaml", "yml"], key="swap_upload")
                if st.button("Apply", width="stretch", key="swap_apply"):
                    try:
                        pol = (load_policy_text(up.getvalue().decode("utf-8"), Path(up.name).stem) if up
                               else load_policy_text(library[pick[2:]]["yaml"], pick[2:]) if pick.startswith("★ ")
                               else load_policy(policies[pick]))
                        swap_policy(sess, pol)
                        st.rerun()
                    except (ValueError, UnicodeDecodeError) as exc:
                        st.error(str(exc))
                st.caption("Your decisions are kept.")
        s["role_labels"] = st.session_state.t_roles
        s["placeholder_style"] = st.session_state.t_style
        s["date_mode"] = st.session_state.t_dates
        s["public_companies"] = st.session_state.t_pub
        s["generalize_addresses"] = st.session_state.t_addr

        # ---- Known parties
        n_parties = len(sess.ops.get("parties", []))
        with st.expander(f"Always hide these names ({n_parties})"):
            st.caption("Client, counterparties, codenames — hidden in every file and every spelling.")
            known_parties_form(sess, res, "sb_parties", compact=True)

        # ---- Rules
        groups: dict[str, list] = {}
        for group, bid, title, help_ in sess.policy.buckets():
            groups.setdefault(group, []).append((bid, title, help_))
            st.session_state.setdefault(f"b_{bid}", bid not in s.get("disabled", []))
        with st.expander("Rules"):  # static label so it stays open while toggling
            for group, items in groups.items():
                st.markdown(f'<p class="ll-sub">{html.escape(group)}</p>', unsafe_allow_html=True)
                for bid, title, help_ in items:
                    st.toggle(title, key=f"b_{bid}", help=help_ or None)
        s["disabled"] = [bid for items in groups.values() for bid, _, _ in items if not st.session_state[f"b_{bid}"]]

        # ---- Names & labels: filter (All · To review · by type), alphabetical
        natural = lambda v: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", v.lower())]  # Amount 2 before 10
        alpha = lambda items: sorted(items, key=lambda x: (natural(x.label), x.canonical.lower()))
        by_type: dict[str, list] = {}
        for e in res.entities:
            by_type.setdefault(e.type, []).append(e)
        focus_type = next((e.type for e in res.entities if e.key == focus), None)
        with st.expander(f"Names & labels ({len(res.entities)})", expanded=bool(focus_type)):
            pending = [e for e in res.entities if e.status == "pending"]
            options = ["__all__"] + (["__review__"] if pending else []) + sorted(by_type, key=type_name)
            if focus_type and st.session_state.get("_ent_focus_seen") != focus:
                st.session_state.ent_type = focus_type          # jump to a just-split/merged/shown name once
                st.session_state._ent_focus_seen = focus
            if st.session_state.get("ent_type") not in options:
                st.session_state.ent_type = "__all__"
            eq = st.text_input("Find a name", key="ent_search", label_visibility="collapsed",
                               placeholder="🔍 Find a name or label…").strip().lower()
            if eq:
                found = alpha(e for e in res.entities if eq in e.label.lower() or eq in e.canonical.lower()
                              or any(eq in m[3].lower() for m in e.mentions))
                st.caption(f"{len(found)} match{'es' if len(found) != 1 else ''}" if found else "No name matches.")
                for e in found[:40]:
                    entity_popover(sess, res, e, focus)
            else:
                pick = st.selectbox("Show", options, key="ent_type", label_visibility="collapsed",
                                    format_func=lambda t: f"Show all ({len(res.entities)})" if t == "__all__"
                                    else f"Needs a look ({len(pending)})" if t == "__review__"
                                    else f"{type_name(t)} ({len(by_type[t])})")
                shown = (res.entities if pick == "__all__" else pending if pick == "__review__"
                         else by_type.get(pick, []))
                for e in alpha(shown):
                    entity_popover(sess, res, e, focus)

        st.markdown('<div class="ll-rule"></div>', unsafe_allow_html=True)
        if st.button("Start over", width="stretch", key="sb_start_over"):
            confirm_start_over()


# --------------------------------------------------------------------------- adding documents / start
def add_documents_form(sess: Session, key: str) -> None:
    """Upload more files into the current set (same registry, so pseudonyms stay consistent)."""
    ups = st.file_uploader("Add documents", type=[x.strip(".") for x in SUPPORTED], accept_multiple_files=True,
                           key=f"{key}_files", label_visibility="collapsed")
    ok = st.checkbox("I have the right to share these files too.", key=f"{key}_rights",
                     help="Anonymizing doesn't cure privilege, NDAs or patient confidentiality.")
    if st.button("Add to this set", type="primary", disabled=not (ups and ok), key=f"{key}_go"):
        before = len(sess.run().queue)
        with st.spinner("Reading and detecting…"):
            try:
                added = sess.add_files([(u.name, u.getvalue()) for u in ups])
            except ValueError as exc:
                st.error(str(exc))
                return
        new_items = len(sess.run().queue) - before
        st.session_state.focus_note = (f"Added {len(added)} document{'s' if len(added) != 1 else ''}"
                                       + (f" — {new_items} new item{'s' if new_items != 1 else ''} to review"
                                          if new_items > 0 else ""))
        st.session_state.pop(f"{key}_files", None)
        st.rerun()


@st.dialog("Add documents", width="large")
def add_documents_dialog(sess: Session) -> None:
    st.caption("Existing pseudonyms and decisions carry over. New findings join the review queue.")
    add_documents_form(sess, "dlg_add")


@st.dialog("Start over?")
def confirm_start_over() -> None:
    st.write("This clears every file and every decision. Download what you need first. Your settings and "
             "names to always hide carry over to the next set.")
    c1, c2 = st.columns(2)
    if c1.button("Yes, start over", type="primary", width="stretch"):
        reset()
        st.rerun()
    if c2.button("Cancel", width="stretch"):
        st.rerun()


def start_view(sess: Session, res) -> None:
    """Step 1 while a set is open: the files, names to always hide, labels, add more."""
    st.markdown('<h2>Your files</h2>', unsafe_allow_html=True)
    label("Add files to this set")
    add_documents_form(sess, "start_add")
    st.markdown('<div style="height:.6rem"></div>', unsafe_allow_html=True)
    rows = [{"file": name, "status": ("✓ approved" if is_current(sess, res, i) else
                                      "changed since approval" if i in sess.approved else "in progress"),
             "hidden": len([g for g in res.segments[i] if g.kind != "tidy"]),
             "notes": "; ".join(w["message"] for w in (sess.warnings[i] if i < len(getattr(sess, 'warnings', []) or []) else []))}
            for i, name in enumerate(res.names)]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    open_items = sum(res.item(k).status == "pending" for k in res.queue)
    n_ok = sum(is_current(sess, res, i) for i in range(len(res.names)))
    nxt = (("Continue checking →", "Review") if open_items else
           ("Read & approve →", "Documents") if n_ok < len(res.names) else ("Go to download →", "Final"))
    c1, c2, _ = st.columns([1.5, 1.2, 2])
    c1.button(nxt[0], type="primary", width="stretch", on_click=lambda v=nxt[1]: st.session_state.update(view=v))
    if c2.button("Start over", width="stretch", key="files_start_over"):
        confirm_start_over()
    st.markdown('<div class="ll-rule"></div>', unsafe_allow_html=True)
    label("Names to always hide")
    st.caption("Know who the parties are? Add them — every spelling is hidden in every file, whatever the "
               "detectors find. Most reliable for clients, counterparties and codenames.")
    known_parties_form(sess, res, "files_parties")
    st.markdown('<div class="ll-rule"></div>', unsafe_allow_html=True)
    label("Labels")
    st.caption("Rename a label (Company B → Receiving bank). Each label is used for one name only.")
    ents = [e for e in res.entities if e.is_redacted and e.type in ("PERSON", "ORGANIZATION", "MANUAL")
            and not e.nick_target]
    table = pd.DataFrame([{"label": e.label, "type": type_name(e.type), "examples": examples(e),
                           "documents": len({m[0] for m in e.mentions}), "_key": e.key} for e in ents])
    if not table.empty:
        edited = st.data_editor(table, hide_index=True, width="stretch", key="labels_editor",
                                column_order=["label", "type", "examples", "documents"],
                                disabled=["type", "examples", "documents"],
                                column_config={"label": st.column_config.TextColumn("Label", required=True)})
        if st.button("Apply labels", type="primary", key="apply_labels"):
            errors, changed = [], 0
            for (_, before), (_, after) in zip(table.iterrows(), edited.iterrows()):
                if str(after["label"]).strip() and str(after["label"]).strip() != before["label"]:
                    try:
                        sess.rename(before["_key"], str(after["label"]),
                                    note=f"Rename {before['label']} → “{str(after['label']).strip()}”")
                        changed += 1
                    except ValueError as exc:
                        errors.append(str(exc))
            for err in errors:
                st.error(err)
            if changed and not errors:
                st.session_state.focus_note = f"Renamed {changed} label{'s' if changed != 1 else ''}"
                st.rerun()


# --------------------------------------------------------------------------- review
def bearing(done: int, total: int) -> None:
    frac = done / total if total else 1.0
    st.markdown(
        f'<div class="fix"><div class="fix-row"><span>Review queue</span>'
        f'<span class="now">{done} of {total} reviewed</span></div>'
        f'<div class="track"><div class="fill" style="width:{frac * 100:.1f}%"></div></div></div>',
        unsafe_allow_html=True)


def review(sess: Session, res) -> None:
    with st.expander("Quick tips"):
        st.markdown("- Each card asks one question; the buttons answer it, for every place it appears\n"
                    "- Wrong type? Use **This is a** (Person / Company / Other)\n"
                    "- **↶ Undo** (top right) reverses your last change, whatever it was\n"
                    "- Missed something? Read & approve → **✎ Edit**, select it, **Redact**")
    queue = res.queue
    pending = [k for k in queue if res.item(k).status == "pending"]
    bearing(len(queue) - len(pending), len(queue))
    if pending and st.button("＋ Add files", key="rv_add"):
        add_documents_dialog(sess)

    if not pending:
        st.markdown('<h2>Everything is checked</h2>', unsafe_allow_html=True)
        st.markdown(f'<p class="ll-note">{sum(e.status == "auto" for e in res.entities)} clear cases were hidden '
                    'automatically. Next: read each file and approve it.</p>', unsafe_allow_html=True)
        c1, c2, _ = st.columns([1.6, 1.4, 1.6])
        c1.button("Read & approve →", type="primary", width="stretch",
                  on_click=lambda: st.session_state.update(view="Documents"))
        if c2.button("＋ Add files", width="stretch", key="rv_add_done"):
            add_documents_dialog(sess)
        return

    key = pending[0]
    item = res.item(key)
    is_flag = key.startswith("F|")
    sentence = is_flag and getattr(item, "scope", "") == "sentence"
    doc, s, e, _ = item.mentions[0]
    docs = sorted({m[0] for m in item.mentions})
    flag_title = next((f.title for f in sess.policy.flags if f.id == item.rule and f.title), "")
    if is_flag:
        etype = {"quasi.combination": "Could identify someone"}.get(item.rule, flag_title or "Needs a look")
    elif item.rule == "names.public_company_party_check":
        etype = "Public company · is it involved?"
    elif getattr(item, "nick_target", ""):
        etype = "Possible nickname"
    else:
        etype = f"{type_name(item.type)} · not sure"
    n = len(item.mentions)
    shown = item.text if is_flag else item.canonical
    st.markdown(
        f'<div class="card"><div class="card-top"><span class="card-type">{html.escape(etype)}</span>'
        f'<span class="dim">{html.escape(res.names[doc])} · {n} place{"s" if n != 1 else ""}</span></div>'
        f'<div class="card-ctx">{context_html(res, doc, s, e)}</div>'
        '<dl class="kv">'
        + ("" if sentence else f'<dt>Found</dt><dd class="big">{html.escape(shown)}</dd>')
        + (f'<dt>Becomes</dt><dd class="big">{html.escape(item.proposed)}</dd>' if not sentence
           or item.rule != "quasi.combination" else "")
        + '</dl></div>', unsafe_allow_html=True)

    editing = st.session_state.get("editing")
    note = f"“{shown[:40]}”"
    if not editing:
        # Buttons that answer the card's question, right under it
        if item.rule == "quasi.combination":
            labels = ("Rewrite sentence", "It's fine as is")
        elif item.rule == "rights.privileged":
            labels = ("Withhold this sentence", "I have consent — keep it")
        elif sentence:
            labels = ("Use the suggested wording", "Keep as written")
        elif item.rule == "names.public_company_party_check":
            labels = ("Yes, hide it", "No, keep the name")
        elif getattr(item, "nick_target", ""):
            labels = (f"Yes — it's {item.proposed}", "No, someone else")
        else:
            labels = (f"Use “{item.proposed[:28]}”", "Keep as written")
        c1, c2, c3 = st.columns([1.5, 1.3, 1.1])
        if item.rule == "quasi.combination":
            if c1.button(labels[0], type="primary", width="stretch", key=f"rw_{key}"):
                st.session_state.editing = True
                st.rerun()
        else:
            c1.button(labels[0], type="primary", width="stretch", on_click=decide,
                      args=(key, "accept", "", f"Accept {note}"))
        c2.button(labels[1], width="stretch", on_click=decide, args=(key, "keep", "", f"Keep {note}"))
        if not sentence or item.rule != "quasi.combination":
            if c3.button("Change label" if not is_flag else "Change wording", width="stretch", key=f"chg_{key}"):
                st.session_state.editing = True
                st.rerun()
        if not is_flag and item.type in KIND_NAMES:
            t1, _ = st.columns([1.4, 3])
            kinds = list(KIND_NAMES)
            t1.selectbox("This is a", kinds, index=kinds.index(item.type), format_func=KIND_NAMES.get,
                         key=f"kind_{key}", on_change=lambda k=key, it=item: retype_from_widget(k, it))
    else:
        if sentence:
            val = st.text_area("Rewrite the sentence", item.custom or item.proposed, height=120, key=f"ta_{key}")
            b1, b2, _ = st.columns([1.3, 1, 2.5])
            if b1.button("Save sentence", type="primary", width="stretch"):
                decide(key, "edit", val.strip() or item.proposed, f"Rewrite {note}")
                st.rerun()
        elif is_flag:
            val = st.text_input("New wording", item.custom or item.proposed, key=f"tw_{key}")
            b1, b2, _ = st.columns([1.3, 1, 2.5])
            if b1.button("Save wording", type="primary", width="stretch"):
                decide(key, "edit", val.strip() or item.proposed, f"Change wording of {note}")
                st.rerun()
        else:
            forms = forms_of(item)
            if len(forms) > 1:
                st.markdown(f'<p class="ll-label" style="margin-top:1rem">The label covers {len(forms)} ways this '
                            'name is written</p>', unsafe_allow_html=True)
                for f, _, cnt, fdocs, (md, ms, me, _) in forms:
                    st.markdown(
                        f'<div class="form-row"><span class="form">{html.escape(f)}</span> '
                        f'<span class="dim">×{cnt} · {html.escape(", ".join(res.names[d] for d in fdocs))}</span>'
                        f'<div class="form-ctx">{context_html(res, md, ms, me, neighbours=False)}</div></div>',
                        unsafe_allow_html=True)
            kind, value = replacement_picker(res, item, f"rp-{key}", getattr(item, "label", "") or item.proposed)
            b1, b2, _ = st.columns([1.3, 1, 2.5], vertical_alignment="bottom")
            if b1.button("Save label", type="primary", width="stretch"):
                if apply_replacement(sess, res, key, item, kind, value):
                    st.rerun()
            if len(forms) > 1:
                with st.expander("These are different people or things → split one off"):
                    split_controls(sess, res, item, f"rv-{key}")
        if b2.button("Cancel", width="stretch"):
            st.session_state.editing = False
            st.rerun()

    st.markdown(f'<div class="why"><b>Why</b> {html.escape(item.reason)}<br><span class="dim">Appears {n}× in '
                f'{html.escape(", ".join(res.names[d] for d in docs))}</span></div>', unsafe_allow_html=True)
    if n > 1:
        with st.expander(f"See all {n} places"):
            for md, ms, me, _ in item.mentions:
                st.markdown(f'<div class="form-row"><span class="dim">{html.escape(res.names[md])}</span>'
                            f'<div class="form-ctx">{context_html(res, md, ms, me, neighbours=False)}</div></div>',
                            unsafe_allow_html=True)


def retype_from_widget(key: str, item) -> None:
    new = st.session_state.get(f"kind_{key}")
    if new and new != item.type:
        sess = st.session_state.session
        sess.retype(item.key, new, note=f"“{item.canonical}” is a {KIND_NAMES[new].lower()}")
        st.session_state.focus_note = f"“{item.canonical}” is now treated as a {KIND_NAMES[new].lower()}"


# --------------------------------------------------------------------------- documents
def _search_rx(query: str):
    words = query.split()
    if not words or len(" ".join(words)) < 2:
        return None
    return re.compile(r"(?<![\w])(" + r"\s+".join(map(re.escape, (html.escape(w) for w in words))) + r")(?![\w])",
                      re.IGNORECASE)


def _plain(text: str, line: list[int], leaks: list[str] | None = None, rx=None) -> str:
    """Escape text and drop a line anchor after each newline so two panes can scroll in step. Leaked originals
    (text that should have been redacted) are highlighted."""
    escaped = html.escape(text)
    for lk in leaks or []:
        escaped = re.sub(r"(?<![\w-])(" + re.escape(html.escape(lk)) + r")(?![\w-])",
                         r'<mark class="leak" title="Original text still showing — should be redacted">\1</mark>',
                         escaped, flags=re.IGNORECASE)
    if rx is not None:  # the viewer's search term (passed in, never shared between sessions)
        escaped = rx.sub(r'<mark class="hit">\1</mark>', escaped)
    parts = escaped.split("\n")
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


def _tip(ref: str, title: str, line2: str, line3: str) -> str:
    """Attributes for the hover card (shown by DOC_JS) — and data-ref makes the highlight clickable to edit."""
    a = lambda v: html.escape(" ".join(str(v).split()), quote=True)
    return f'data-ref="{a(ref)}" data-tip-title="{a(title)}" data-tip-l2="{a(line2)}" data-tip-l3="{a(line3)}"'


def render_doc(res, doc: int, version: str = "anonymized", edit: bool = False, query: str = "") -> str:
    """HTML for one document. ``version`` is "anonymized" (labels) or "original" (source text with every detected
    span highlighted). Every highlight shows a hover card (what it is, what it was or becomes) and opens
    'Change label' when clicked."""
    text, out, pos, line = res.texts[doc], [], 0, [0]
    by_key = {e.key: e for e in res.entities}
    flags = {f.key: f for f in res.flags}
    leaks = ([lk["text"] for lk in consistency_check(res)["leak_items"] if lk["doc"] == doc]
             if version == "anonymized" else [])
    rx_plain = _search_rx(query)
    for g in res.segments[doc]:
        out.append(_plain(text[pos:g.start], line, leaks, rx_plain))
        hitcls = " hit-chip" if rx_plain is not None and rx_plain.search(html.escape(" ".join(g.original.split()))) else ""
        orig = " ".join(g.original.split())
        if g.kind == "tidy":  # wording tidy-ups ("On" → "In", a repeated label dropped): no highlight
            out.append(_plain(g.original, line) if version == "original"
                       else html.escape(g.replacement) + _anchors(g.original, line))
            pos = g.end
            continue
        long_cls = " chip-long" if len(g.replacement) > 40 else ""
        if g.kind == "flag":
            f = flags.get(g.ref)
            changed = g.status in ("accepted", "edited")
            ftitle = next((r.title for r in st.session_state.session.policy.flags if f and r.id == f.rule and r.title),
                          "Flagged phrase")
            tip = _tip("F|" + g.ref, ftitle,
                       (f"was “{orig[:80]}”" if version == "anonymized" else f"becomes “{g.replacement[:80]}”")
                       if changed else "left as written for now", "click to change")
            if version == "anonymized" and changed:
                out.append(f'<span class="chip t-FLAG{long_cls}" {tip} data-orig="{html.escape(orig, quote=True)}">'
                           f'{html.escape(g.replacement)}</span>{_anchors(g.original, line)}')
            else:
                out.append(f'<span class="flag" {tip}>{_plain(g.original, line)}</span>')
        else:
            e = by_key[g.ref]
            cls = "kept" if not e.is_redacted else ("pending" if e.status == "pending" else "")
            status = STATUS_WORDS.get(e.status, e.status)
            kind = type_name(e.type)
            if version == "original":
                tip = _tip("E|" + g.ref, orig, f"→ {g.replacement}" if e.is_redacted else "kept as written",
                           f"{kind} · {status} · click to change")
                out.append(f'<span class="orig t-{color_type(g.type)} {cls}{hitcls}" {tip}>{_plain(g.original, line)}</span>')
            elif e.is_redacted:
                tip = _tip("E|" + g.ref, f"{e.label} · {kind}", f"was “{orig[:80]}”", f"{status} · click to change")
                out.append(f'<span class="chip t-{color_type(g.type)} {cls}{hitcls}{long_cls}" {tip} '
                           f'data-orig="{html.escape(orig, quote=True)}">'
                           f'{html.escape(g.replacement)}</span>{_anchors(g.original, line)}')
            else:
                tip = _tip("E|" + g.ref, orig, f"{kind} · {status}", "click to change")
                out.append(f'<span class="chip kept" {tip}>{_plain(g.original, line)}</span>')
        pos = g.end
    out.append(_plain(text[pos:], line, leaks, rx_plain))
    return "".join(out)


DOC_JS = """
export default function (component) {
  const { data, setTriggerValue, parentElement } = component;
  let root = parentElement.querySelector('.dp-root');
  if (!root) { root = document.createElement('div'); root.className = 'dp-root'; parentElement.appendChild(root); }
  root.innerHTML = data.html;
  const send = (o) => setTriggerValue('action', JSON.stringify(Object.assign({ t: Date.now() }, o)));

  root.querySelectorAll('[data-act="toggle-edit"]').forEach((b) => { b.onclick = () => send({ kind: 'toggle_edit' }); });

  // Hover card on every highlight: what it is, what it was / becomes. Click (without selecting) → Change label.
  const tip = document.createElement('div');
  tip.className = 'tipcard'; tip.style.display = 'none';
  root.appendChild(tip);
  const esc = (t) => (t || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  root.querySelectorAll('[data-ref]').forEach((el) => {
    el.addEventListener('mouseenter', () => {
      tip.innerHTML = '<b>' + esc(el.dataset.tipTitle) + '</b>' + (el.dataset.tipL2 ? '<br>' + esc(el.dataset.tipL2) : '')
        + (el.dataset.tipL3 ? '<br><span>' + esc(el.dataset.tipL3) + '</span>' : '');
      tip.style.display = 'block';
      const r = el.getBoundingClientRect();
      const top = r.top > 90 ? r.top - tip.offsetHeight - 8 : r.bottom + 8;
      tip.style.left = Math.max(8, Math.min(r.left, window.innerWidth - tip.offsetWidth - 8)) + 'px';
      tip.style.top = top + 'px';
    });
    el.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
    el.addEventListener('click', (ev) => {
      const sel = window.getSelection();
      if (sel && sel.toString().trim().length > 1) return;  // selecting text, not clicking
      ev.stopPropagation(); tip.style.display = 'none';
      send({ kind: 'edit', ref: el.dataset.ref });
    });
  });

  // Select text -> floating "Redact" button (edit mode only).
  const fab = document.createElement('button');
  fab.className = 'redact-fab'; fab.textContent = '✎ Redact'; fab.style.display = 'none';
  root.appendChild(fab);
  root.onmouseup = () => setTimeout(() => {
    if (!data.edit) return;
    const sel = window.getSelection();
    // Read the selection with every placeholder swapped back to its original words, so a selection that runs
    // across a chip ("2701 Olympic Boulevard … ZIP 1") becomes the real phrase.
    let text = '';
    if (sel && sel.rangeCount) {
      const frag = sel.getRangeAt(0).cloneContents();
      frag.querySelectorAll('[data-orig]').forEach((n) => n.replaceWith(document.createTextNode(n.dataset.orig)));
      let anc = sel.getRangeAt(0).commonAncestorContainer;
      anc = anc.nodeType === 1 ? anc : anc.parentElement;
      const inside = anc && anc.closest ? anc.closest('[data-orig]') : null;
      text = (inside ? inside.dataset.orig : frag.textContent).replace(/\\s+/g, ' ').trim();
    }
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
    p.addEventListener('scroll', () => { window[k] = p.scrollTop; fab.style.display = 'none'; tip.style.display = 'none'; },
                       { passive: true });
  });
  // "Show" from the release check: scroll the anonymized pane to the leaked text.
  if (data.leak) {
    const target = Array.from(root.querySelectorAll('.pane[data-version="anonymized"] mark.leak'))
      .find((m) => m.textContent.toLowerCase() === data.leak.toLowerCase())
      || root.querySelector('.pane[data-version="anonymized"] mark.leak');
    if (target) {
      const pane = target.closest('.pane');
      pane.scrollTop = Math.max(0, target.offsetTop - pane.offsetTop - 80);
      target.classList.add('pulse');
    }
  }
  // "Show" from search: scroll to the k-th match in the original pane (the other pane follows when synced).
  if (typeof data.hit === 'number' && data.hit >= 0) {
    const pane = root.querySelector('.pane[data-version="original"]') || root.querySelector('.pane');
    const marks = pane ? Array.from(pane.querySelectorAll('mark.hit, .hit-chip')) : [];
    const target = marks[Math.min(data.hit, marks.length - 1)];
    if (target) {
      pane.scrollTop = Math.max(0, target.offsetTop - pane.offsetTop - 80);
      target.classList.add('pulse');
      pane.dispatchEvent(new Event('scroll'));
    }
  }
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

# "My rules": saved rule sets live in this browser's localStorage (never on the server). The component applies
# a save/delete command once (by nonce) and reports the whole library back to Python as state.
LIB_JS = """
export default function (component) {
  const { data, setStateValue } = component;
  const KEY = 'longitude.rules.v1';
  let lib = {};
  try { lib = JSON.parse(window.localStorage.getItem(KEY) || '{}') || {}; } catch (e) { lib = {}; }
  const cmd = data && data.cmd;
  if (cmd && cmd.nonce && window.__llRulesNonce !== cmd.nonce) {
    window.__llRulesNonce = cmd.nonce;
    if (cmd.op === 'save') lib[cmd.name] = { yaml: cmd.yaml, at: cmd.at };
    if (cmd.op === 'delete') delete lib[cmd.name];
    try { window.localStorage.setItem(KEY, JSON.stringify(lib)); } catch (e) {}
  }
  setStateValue('library', JSON.stringify(lib));
}
"""
rules_library = st.components.v2.component("rules_library", js=LIB_JS, isolate_styles=False)


def my_rules() -> dict:
    """{name: {"yaml", "at"}} from this browser (empty until the component has reported, or if storage is off)."""
    with st.container(key="rules_lib_host"):
        res_c = rules_library(data={"cmd": st.session_state.pop("lib_cmd", None)}, key="rules_lib",
                              on_library_change=lambda: None)
    try:
        return json.loads(getattr(res_c, "library", None) or "{}")
    except (TypeError, ValueError):
        return {}


def library_command(op: str, name: str, yaml_text: str = "") -> None:
    import time
    from datetime import date
    st.session_state.lib_cmd = {"op": op, "name": name, "yaml": yaml_text, "at": date.today().isoformat(),
                                "nonce": str(time.time())}


def save_rules_panel(sess: Session) -> None:
    """Save the current rules (settings, rule switches, always-hide names) to My rules, or download them."""
    default = st.session_state.get("save_rules_name") or f"{sess.policy.name} (my rules)"
    name = st.text_input("Name", default, key="save_rules_name")
    c1, c2 = st.columns(2)
    if c1.button("Save to My rules", type="primary", width="stretch", key="save_rules_btn") and name.strip():
        library_command("save", name.strip(), sess.export_rules(name.strip()))
        st.session_state.focus_note = f"Saved “{name.strip()}” to My rules in this browser"
        st.rerun()
    c2.download_button("Download (.yaml)", sess.export_rules(name.strip() or sess.policy.name),
                       f"{(name.strip() or 'rules').replace(' ', '_')}.yaml", "text/yaml", width="stretch")
    st.caption("Saves your settings, which rules are on, and the names to always hide. My rules live in this "
               "browser only — download the file to share it with your team or use it on another computer.")


def show_leak(lk: dict) -> None:
    """Jump to the document with the leak (highlighted, scrolled to) and open its name in the sidebar."""
    st.session_state.update(view="Documents", doc_pick=lk["doc"], doc_layout="Side by side",
                            leak_focus=lk["text"], focus=lk["key"], ent_search="")
    st.session_state.pop("_ent_focus_seen", None)


def fix_leak(sess: Session, lk: dict) -> None:
    """Add the leaked wording to its entity, so it gets the same pseudonym everywhere."""
    sess.redact_as(lk["key"], lk["text"], note=f"Hide “{lk['text']}” as {lk['label']}")
    st.session_state.focus_note = f"“{lk['text']}” is now redacted as {lk['label']} everywhere"


def release_box(sess: Session, res) -> list[str]:
    """Release gate: downloads stay off until every check passes."""
    blockers = sess.release_blockers(res)
    if not sess.rights_confirmed:
        if st.checkbox("I have the right to share every file in this set.",
                       key=f"rights_late_{st.session_state.get('view', '')}"):
            sess.rights_confirmed = True
            st.rerun()
    if blockers:
        st.markdown('<div class="gate"><b>Before download</b><ul>'
                    + "".join(f"<li>{html.escape(b)}</li>" for b in blockers) + "</ul></div>", unsafe_allow_html=True)
        if any(b.startswith("Finish review") for b in blockers) and st.session_state.get("view") != "Review":
            st.button("Go to ② Check →", key=f"gate_review_{st.session_state.get('view', '')}",
                      on_click=lambda: st.session_state.update(view="Review"))
        leaks = consistency_check(res)["leak_items"]
        if leaks:
            st.markdown('<p class="ll-sub">Where the original text is still showing</p>', unsafe_allow_html=True)
            seen = set()
            for i, lk in enumerate(leaks):
                if (lk["text"].lower(), lk["doc"]) in seen:
                    continue
                seen.add((lk["text"].lower(), lk["doc"]))
                c1, c2, c3 = st.columns([3, 1, 1.2], vertical_alignment="center")
                c1.markdown(f'<span class="form">{html.escape(lk["text"])}</span> '
                            f'<span class="dim">in {html.escape(short(res.names[lk["doc"]], 28))} · '
                            f'should be {html.escape(lk["label"])}</span>', unsafe_allow_html=True)
                c2.button("Show", key=f"leak_show_{st.session_state.get('view', '')}_{i}", width="stretch",
                          on_click=show_leak, args=(lk,))
                c3.button("Redact it", key=f"leak_fix_{st.session_state.get('view', '')}_{i}", width="stretch",
                          type="primary", on_click=fix_leak, args=(sess, lk))
    return blockers


def short(name: str, n: int = 26) -> str:
    return name if len(name) <= n else name[:n - 1] + "…"


def is_current(sess: Session, res, doc: int) -> bool:
    return doc in sess.approved and sess.approved[doc]["output"] == res.outputs[doc]


@st.dialog("Redact text")
def redact_dialog(sess: Session, res, payload: dict) -> None:
    phrase = " ".join(payload["text"].split())
    body = r"\s+".join(map(re.escape, phrase.split()))
    spans = [(i, m.start(), m.end()) for i, t in enumerate(res.texts)
             for m in re.finditer(r"(?<![\w])" + body + r"(?![\w])", t)]
    hits = [(i, sum(1 for d, _, _ in spans if d == i)) for i in range(len(res.texts))]
    hits = [(i, n) for i, n in hits if n]
    st.markdown(f'<div class="form-row"><span class="form">{html.escape(phrase)}</span></div>', unsafe_allow_html=True)

    # Already redacted? The selection is a placeholder ("Person A") or sits inside a redaction ("Redmond,
    # Washington" inside the full address). Then the useful action is to change that redaction.
    def covering(d, a, b):
        return next((e for e in res.entities for md, ms, me, _ in e.mentions
                     if md == d and ms <= a and b <= me and (ms, me) != (a, b) and e.is_redacted), None)
    owner = None
    if spans and all(covering(*sp) for sp in spans):
        owner = covering(*spans[0])
    elif not spans:
        owner = next((e for e in res.entities if e.is_redacted and
                      phrase.lower() in {e.replacement_for(m[3]).lower() for m in e.mentions}), None)
    if owner is not None:
        st.caption(f"Already redacted as **{owner.label}** ({examples(owner, 2)}). Change what it becomes:")
        rk, rv = replacement_picker(res, owner, "rd_owner", owner.label)
        c1, c2 = st.columns(2)
        if c1.button("Save", type="primary", width="stretch", key="rd_owner_save"):
            if apply_replacement(sess, res, "E|" + owner.key, owner, rk, rv):
                st.rerun()
        if c2.button("Cancel", width="stretch", key="rd_owner_cancel"):
            st.rerun()
        return
    if not hits:
        st.warning("Not found in the originals. Select plain text, or use ✎ on a placeholder.")
        return
    total = sum(n for _, n in hits)
    st.caption(f"{total} occurrence{'s' if total != 1 else ''} in " + ", ".join(res.names[i] for i, _ in hits)
               + " — all will be redacted.")
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
            sess.redact_as(rv, phrase, note=f"Hide “{phrase[:40]}”")
        elif kind == "OTHER":
            sess.redact(phrase, f"OTHER:{desc}", rv.strip(), f"{desc} {{1}}")
        else:
            sess.redact(phrase, kind, rv.strip())
        st.session_state.focus_note = f"Redacted “{phrase}” in {total} place{'s' if total != 1 else ''}"
        st.rerun()
    if c2.button("Cancel", width="stretch"):
        st.rerun()
    if not ready:
        st.caption("Describe it to create the label.")


@st.dialog("Change label")
def edit_dialog(sess: Session, res, ref: str) -> None:
    item = res.item(ref)
    if item is None:
        st.warning("That item no longer exists.")
        return
    is_flag = ref.startswith("F|")
    forms = forms_of(item)
    current = item.replacement_for(item.text if is_flag else item.canonical)
    st.markdown("".join(
        f'<div class="form-row"><span class="form">{html.escape(f)}</span> <span class="dim">×{n} · '
        f'{html.escape(", ".join(short(res.names[d], 22) for d in docs))}</span></div>' for f, _, n, docs, _ in forms),
        unsafe_allow_html=True)
    st.caption(f"Now shown as “{current}”. Changes apply in every file.")
    if not is_flag and item.type in KIND_NAMES:
        kinds = list(KIND_NAMES)
        st.selectbox("This is a", kinds, index=kinds.index(item.type), format_func=KIND_NAMES.get,
                     key=f"kind_dlg_{item.key}",
                     on_change=lambda: (sess.retype(item.key, st.session_state[f"kind_dlg_{item.key}"],
                                                    note=f"Change type of “{item.canonical}”")))
    kind, value = replacement_picker(res, item, f"ed-{ref}",
                                     item.custom or (item.label if not is_flag else "") or item.proposed)
    c1, c2, c3 = st.columns(3)
    if c1.button("Save", type="primary", width="stretch"):
        if apply_replacement(sess, res, ref, item, kind, value):
            st.rerun()
    if c2.button("Keep as written", width="stretch"):
        sess.decide(ref, "keep", note=f"Keep “{(item.text if is_flag else item.canonical)[:40]}”")
        st.rerun()
    if not is_flag and c3.button("Don't hide this", width="stretch",
                                 help="Not a real name? It will appear as written in every file."):
        delete_entity(sess, item)
        st.rerun()
    if not is_flag and len(forms) > 1:
        with st.expander("Different people or things? Separate one"):
            split_controls(sess, res, item, f"ed-split-{ref}")


STATUS_TEXT = {"redacted": "redacted", "generalized": "still showing (generalized)", "kept": "kept",
               "flagged": "flagged for review", "changed": "changed", "partly redacted": "partly redacted",
               "visible": "still showing"}


def show_hit(doc: int, k: int, entity_key: str = "") -> None:
    """Jump to the k-th match in a document; if it belongs to a name, open that name in the sidebar."""
    st.session_state.update(doc_pick=doc, search_focus=k, doc_layout="Side by side")
    if entity_key:
        st.session_state.focus = entity_key
        st.session_state.pop("_ent_focus_seen", None)
        st.session_state.ent_search = ""


def search_panel(sess: Session, res) -> None:
    """Search every document for a word or phrase and see what happened to each occurrence."""
    q = st.text_input("Search the documents", key="doc_search", label_visibility="collapsed",
                      placeholder="🔍 Search every file for a word or phrase…")
    if not q.strip():
        return
    hits = search(res, q)
    labelled = [e for e in res.entities if q.strip().lower() in e.label.lower() and e.label not in ("Kept", "")]
    for e in labelled[:3]:
        st.caption(f"“{e.label}” stands for {e.canonical} — {len(e.mentions)}× in "
                   f"{len({m[0] for m in e.mentions})} file(s).")
    if not hits:
        if not labelled:
            st.caption(f"“{q.strip()}” doesn't appear in any original document.")
        return
    showing = [h for h in hits if h["status"] in ("visible", "generalized", "partly redacted")]
    docs_hit = sorted({h["doc"] for h in hits})
    st.markdown(f'<p class="search-sum"><b>{len(hits)}</b> match{"es" if len(hits) != 1 else ""} in '
                f'{len(docs_hit)} document{"s" if len(docs_hit) != 1 else ""} · '
                f'<b>{len(hits) - len(showing)}</b> handled · <b class="{"warn" if showing else "ok"}">'
                f'{len(showing)}</b> still showing</p>', unsafe_allow_html=True)
    per_doc_index: dict[int, int] = {}
    with st.container(height=min(64 + 58 * len(hits), 300), border=True, key="hits"):
        for i, h in enumerate(hits[:80]):
            k = per_doc_index.get(h["doc"], 0)
            per_doc_index[h["doc"]] = k + 1
            t = res.texts[h["doc"]]
            before = " ".join(t[max(0, h["start"] - 55):h["start"]].split())
            after = " ".join(t[h["end"]:h["end"] + 55].split())
            c1, c2 = st.columns([6, 1], vertical_alignment="center")
            c1.markdown(
                f'<div class="hit-row"><span class="dim">{html.escape(short(res.names[h["doc"]], 24))}</span> · '
                f'<span class="hit-st st-{h["status"].split()[0]}">{STATUS_TEXT[h["status"]]}'
                f'{" → " + html.escape(h["shown"]) if h["status"] in ("redacted", "changed") else ""}</span><br>'
                f'…{html.escape(before)} <mark class="hit">{html.escape(h["text"])}</mark> {html.escape(after)}…</div>',
                unsafe_allow_html=True)
            c2.button("Show", key=f"hit_{i}", width="stretch",
                      on_click=show_hit, args=(h["doc"], k, h["ref"] if h["ref"] and not h["ref"].startswith("flag")
                                               and h["status"] != "flagged" else ""))
    if showing and st.button(f"Redact every “{q.strip()}”", key="search_redact", type="primary"):
        redact_dialog(sess, res, {"text": q.strip()})


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

    st.markdown(f'<p class="count-top"><b>{approved}/{n}</b> approved</p><p class="ll-label">Read & approve</p>',
                unsafe_allow_html=True)
    d1, d2, d3, d4 = st.columns([4.4, .55, .55, 1.7], vertical_alignment="bottom")
    # A dropdown scales to large sets (type to search); ✓ marks approved documents.
    doc = d1.selectbox("Document", list(range(n)), key="doc_pick", label_visibility="collapsed",
                       format_func=lambda i: f"{'✓ ' if is_current(sess, res, i) else ''}{res.names[i]}")
    d2.button("←", on_click=step, args=(-1,), help="Previous file", width="stretch", key="prev_doc")
    d3.button("→", on_click=step, args=(1,), help="Next file", width="stretch", key="next_doc")
    d4.button("Next to approve", on_click=next_unapproved, disabled=approved == n, width="stretch")

    search_panel(sess, res)

    edit = st.session_state.get("edit_mode", False)
    options = [(i, v) for i in range(n) for v in ("original", "anonymized")]
    fmt = lambda o: f"{o[1].capitalize()} · {short(res.names[o[0]], 22)}"
    st.session_state.setdefault("doc_layout", "Side by side")
    v1, v2 = st.columns([1.2, 4], vertical_alignment="center")
    with v1.popover("View", width="stretch"):
        layout = st.segmented_control("Layout", ["Side by side", "Single"], key="doc_layout")
        if layout != "Single":
            st.selectbox("Left", options, index=options.index((doc, "original")), format_func=fmt, key=f"left_{doc}")
            st.selectbox("Right", options, index=options.index((doc, "anonymized")), format_func=fmt,
                         key=f"right_{doc}")
            st.toggle("Scroll together", value=True, key="sync_scroll", help="Keeps both panes on the same passage.")
    layout = st.session_state.get("doc_layout") or "Side by side"
    if layout == "Single":
        panes, sync = [(doc, "anonymized")], False
    else:
        panes = [st.session_state.get(f"left_{doc}", (doc, "original")),
                 st.session_state.get(f"right_{doc}", (doc, "anonymized"))]
        sync = st.session_state.get("sync_scroll", True)
    v2.markdown('<div class="legend"><span><span class="chip">Label</span> hidden</span>'
                '<span><span class="chip pending">Label</span> needs a look</span>'
                '<span><span class="flag">phrase</span> flagged</span>'
                '<span>Hover a highlight to see what it is · click to change it</span></div>', unsafe_allow_html=True)

    for w in (sess.warnings[doc] if getattr(sess, 'warnings', None) and doc < len(sess.warnings) else []):
        (st.error if w["level"] == "block" else st.caption)(("Blocks release — " if w["level"] == "block" else "Note: ")
                                                            + w["message"])
    if edit:
        st.markdown('<p class="edit-hint">Select any text to hide it. (Click any label to change it — that works '
                    'any time.)</p>', unsafe_allow_html=True)

    def head(d, v):
        btn = (f'<button class="pane-edit{" on" if edit else ""}" data-act="toggle-edit">'
               f'{"✓ Done" if edit else "✎ Select to hide"}</button>') if v == "anonymized" else ""
        tick = '<span class="ok">✓ approved</span>' if is_current(sess, res, d) else ""
        return (f'<div class="pane-head"><div class="pane-top"><span class="pane-tag {v}">{v}</span>{btn}</div>'
                f'<div class="pane-name">{html.escape(res.names[d])}{tick}</div></div>')

    body = "".join(
        f'<div class="pane-wrap">{head(d, v)}'
        f'<div class="pane doc" data-doc="{d}" data-version="{v}">'
        f'{render_doc(res, d, v, edit, st.session_state.get("doc_search", ""))}</div></div>'
        for d, v in panes)
    result = doc_panes(data={"html": f'<div class="panes n{len(panes)}">{body}</div>', "sync": bool(sync),
                             "edit": edit, "leak": st.session_state.pop("leak_focus", ""),
                             "hit": st.session_state.pop("search_focus", -1)},
                       key="doc_panes", on_action_change=lambda: None)
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


    # Approval: one clear primary action; downloads live on ④ Download.
    st.markdown('<div class="ll-rule"></div>', unsafe_allow_html=True)
    stale = doc in sess.approved and not is_current(sess, res, doc)
    open_here = [k for k in res.queue if res.item(k).status == "pending"
                 and any(m[0] == doc for m in res.item(k).mentions)]
    a1, a2, _ = st.columns([1.7, 1.3, 1.5], vertical_alignment="center")
    if is_current(sess, res, doc):
        a1.button("✓ Approved · undo", width="stretch", key=f"unapprove_{doc}",
                  on_click=lambda: sess.unapprove(doc))
    else:
        def approve_and_advance() -> None:
            sess.approve(doc, res)
            todo = [i for i in list(range(doc + 1, n)) + list(range(doc)) if not is_current(sess, res, i)]
            if todo:
                st.session_state.doc_pick = todo[0]
                st.session_state.focus_note = f"Approved {res.names[doc]} — next: {res.names[todo[0]]}"
            else:
                st.session_state.focus_note = "Every file is approved — go to ④ Download"

        a1.button("✓ Approve again" if stale else ("✓ Approve and open next" if approved < n - 1 else "✓ Approve"),
                  type="primary", width="stretch", key=f"approve_{doc}", on_click=approve_and_advance)
    if open_here:
        a2.markdown(f'<span class="dim">{len(open_here)} item{"s" if len(open_here) != 1 else ""} in this file '
                    'still need a look</span>', unsafe_allow_html=True)
    if approved == n:
        st.success("Every file is approved.")
        st.button("Go to ④ Download →", type="primary", key="to_final",
                  on_click=lambda: st.session_state.update(view="Final"))
    if stale:
        st.warning("Changed since you approved it. Approve again to update the package.")
    release_box(sess, res)

    with st.expander("Every change in this file"):
        seen = {}
        for g in res.segments[doc]:
            if g.kind != "tidy":
                seen.setdefault((g.original, g.replacement), type_name(g.type))
        st.dataframe(pd.DataFrame([{"original": o, "shown as": r, "type": t} for (o, r), t in seen.items()]),
                     hide_index=True, width="stretch")


def final_view(sess: Session, res) -> None:
    """Step 4: one checklist, one package, and the internal key kept apart."""
    n = len(res.names)
    items = sorted(sess.approved.items())
    cc = consistency_check(res)
    current = sum(is_current(sess, res, i) for i in range(n))
    checks = [
        (sess.rights_confirmed, "Rights confirmed"),
        (not cc["unreviewed"], "All items checked" if not cc["unreviewed"]
         else f"{len(cc['unreviewed'])} item(s) still need a look"),
        (cc["leak_free"], "No original names left" if cc["leak_free"] else "Some original text is still showing"),
        (current == n, f"{current} of {n} files approved"),
    ]
    ready = all(ok for ok, _ in checks)
    st.markdown(f'<h2>{"Ready to share" if ready else "Almost ready"}</h2><div class="check">' + "<br>".join(
        f'<span class="{"ok" if ok else "warn"}">{"✓" if ok else "○"}</span>&nbsp; {html.escape(t)}' for ok, t in checks)
        + "</div>", unsafe_allow_html=True)
    todo = [i for i in range(n) if not is_current(sess, res, i)]
    if todo:
        st.button("Approve the rest →", key="final_to_docs",
                  on_click=lambda: st.session_state.update(view="Documents", doc_pick=todo[0]))
    blocked = bool(release_box(sess, res))   # always visible, so leaks surface before anything is approved
    if items:
        names = [res.names[d] for d, _ in items]
        outputs = [a["output"] for _, a in items]
        combined = "\n\n".join(f"{'=' * 72}\n{name}\n{'=' * 72}\n\n{out}" for name, out in zip(names, outputs))
        c1, c2, _ = st.columns([1.6, 1.6, 1.2])
        c1.download_button(f"Download approved files (.zip, {len(items)})", buyer_zip(names, outputs),
                           "anonymized_package.zip", "application/zip", type="primary", disabled=blocked,
                           width="stretch")
        c2.download_button("As one combined file", combined, "anonymized_package.txt", "text/plain",
                           disabled=blocked, width="stretch")
        st.caption("Only approved files are included. The label key is never in the download.")
        with st.expander(f"Approved files ({len(items)}) and preview"):
            for d, a in items:
                c1, c2 = st.columns([4, 1], vertical_alignment="center")
                c1.markdown(f'✓ {html.escape(res.names[d])} <span class="dim">· approved {a["at"]}'
                            f'{" · changed since" if a["output"] != res.outputs[d] else ""}</span>',
                            unsafe_allow_html=True)
                if c2.button("Move back", key=f"unappr_{d}", width="stretch"):
                    sess.unapprove(d)
                    st.rerun()
            st.markdown(f'<div class="doc" style="max-height:420px;overflow:auto">{html.escape(combined)}</div>',
                        unsafe_allow_html=True)
    else:
        st.info("Nothing approved yet.")
    st.markdown('<div class="ll-rule"></div>', unsafe_allow_html=True)
    audit_trail(sess, res)


# --------------------------------------------------------------------------- log & consistency
def audit_trail(sess: Session, res) -> None:
    cc = consistency_check(res)
    with st.expander("Details: every name and its label"):
        st.caption("Each name should have one label in every file, and none should still be showing.")
        rows = [{"Label": r["registry_id"], "Type": type_name(r["entity_type"]), "Original": r["original"],
                 "Shown as": r["replacement"], "Files": r["documents"], "Same everywhere": r["consistent"],
                 "Still showing": r["leaks"]} for r in cc["rows"]]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.markdown('<div class="keybox"><b>Label key — do not send.</b> It turns labels back into real names. Keep it '
                'inside your team; it is never part of the download.</div>', unsafe_allow_html=True)
    st.download_button("Download the label key (.csv)", internal_log_csv(res), "INTERNAL_label_key.csv", "text/csv")


# --------------------------------------------------------------------------- main
inject_css()
sess: Session | None = st.session_state.get("session")
if sess is not None and (type(sess) is not Session or getattr(sess, "VERSION", 0) != Session.VERSION):
    # The app was updated while this browser session was open: rebuild the set with the new engine,
    # keeping every decision, instead of crashing on the old object.
    with st.spinner("The app was updated — re-checking your documents with the new rules…"):
        sess = st.session_state.session = Session.upgrade(sess)
    st.toast("Updated to the latest rules — your decisions were kept.")
header()
st.session_state.my_rules = my_rules()  # mounted every run, so a save/delete from anywhere is applied
if sess is None:
    home()
else:
    res = sess.run()
    sidebar(sess, res)
    res = sess.run()  # toggles in the sidebar may have changed settings this run
    if st.session_state.get("focus_note"):
        st.toast(st.session_state.pop("focus_note"))
    n_ok = sum(is_current(sess, res, i) for i in range(len(res.names)))
    open_items = sum(res.item(k).status == "pending" for k in res.queue)
    if st.session_state.get("view") not in VIEWS:
        st.session_state.view = "Review"
    labels = {"Start": "① Files",
              "Review": "② Check" + (f" · {open_items} open" if open_items else " ✓"),
              "Documents": f"③ Read & approve · {n_ok}/{len(res.names)}",
              "Final": "④ Download" + (" ✓" if n_ok == len(res.names) else "")}
    with st.container(key="stepbar"):
        s1, s2 = st.columns([5, 1.1], vertical_alignment="center")
        with s1:
            view = st.segmented_control("Step", VIEWS, key="view", format_func=labels.get,
                                        label_visibility="collapsed") or "Review"
        can_undo = bool(getattr(sess, "undo_stack", None))
        s2.button("↶ Undo", key="undo_top", on_click=undo, disabled=not can_undo, width="stretch",
                  help=f"Undo: {sess.last_change}" if can_undo else "Nothing to undo yet")
    # Ask before leaving: nothing is saved, so a refresh would wipe the work.
    st.html("<script>window.onbeforeunload = (e) => { e.preventDefault(); e.returnValue = ''; };</script>",
            unsafe_allow_javascript=True)
    st.markdown('<div style="height:.6rem"></div>', unsafe_allow_html=True)
    {"Start": start_view, "Review": review, "Documents": documents, "Final": final_view}[view](sess, res)
