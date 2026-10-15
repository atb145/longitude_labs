"""Longitude Anonymizer — Streamlit UI. All logic lives in /engine; this file is presentation only."""
from __future__ import annotations

import bisect
import csv
import functools
import json
import html
import io
import os
import re
import zipfile
from collections import Counter
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
from engine.export import FORMATS, buyer_zip, internal_log_csv, render_file
from engine.extract import SUPPORTED
from engine.registry import GENERIC_ORG_WORDS, deleted_text, norm_form, split_key, type_title

# Bundled demo: two public SEC filings on Microsoft's acquisition of Activision Blizzard. ANONYMIZER_SAMPLES points
# "Try the sample case" at another folder (the tests use a small fictional case).
SAMPLES = Path(os.environ.get("ANONYMIZER_SAMPLES") or Path(__file__).parent / "samples")
TYPE_NAMES = {
    "PERSON": "Person", "ORGANIZATION": "Company", "ACCOUNT_NUMBER": "Account number",
    "CRD_NUMBER": "CRD number", "CUSIP": "CUSIP", "TICKER": "Ticker", "CASE_NUMBER": "Case / docket number",
    "EMAIL_ADDRESS": "Email", "PHONE_NUMBER": "Phone", "US_SSN": "SSN", "STREET_ADDRESS": "Address",
    "DATE": "Date", "FLAG": "Flag", "MANUAL": "Hidden by hand", "MONEY": "Amount", "SHARE_COUNT": "Share count",
    "URL": "Website", "TAX_ID": "Tax ID", "SHARE_PRICE": "Share price", "JOB_TITLE": "Job title",
    "POSTAL_CODE": "ZIP code",
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
    /* The sidebar is one scroll area; a name's controls open inside it (not in a floating box), so they scroll too */
    [data-testid="stSidebarContent"] { overflow-y: auto !important; overscroll-behavior: contain; }
    /* No link icons next to headings */
    [data-testid="stHeaderActionElements"] { display: none !important; }
    /* Layout reacts to the space the content actually has (sidebar open or closed), not the window */
    [data-testid="stMainBlockContainer"] { container-type: inline-size; }
    .ll-head { flex-wrap: wrap; row-gap: .35rem; }
    .ll-word, .ll-coord { white-space: nowrap; }
    /* Text runs the full width of its column, like the boxes and buttons around it (no narrower "measure") */
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
    .ll-note { color:var(--dim); font-size:.95rem; line-height:1.65; }
    .ll-rule { border-top:1px solid var(--rule); margin:1.8rem 0; }
    .dim { color:var(--dim); }

    /* progress */
    .fix { margin:.4rem 0 2rem; } .fix.tight { margin:0; }
    .fix-row { display:flex; justify-content:space-between; font-size:.85rem; color:var(--dim); margin-bottom:.5rem; }
    .fix-row .now { color:var(--fg); font-weight:500; }
    .track { position:relative; height:8px; border-radius:999px; background:var(--rule); overflow:hidden; }
    .track .fill { position:absolute; inset:0 auto 0 0; border-radius:999px; background:var(--soft); }
    .track .mark { display:none; }
    .fix-note { font-size:.85rem; color:var(--dim); margin-top:.5rem; }  /* same size and gap as the row above */

    /* review card */
    .card { border:1px solid var(--rule); border-radius:1.2rem; padding:1.6rem 1.8rem 1.2rem; margin-bottom:1rem; }
    .card-type { display:inline-block; font-size:.75rem; font-weight:500; letter-spacing:.08em; text-transform:uppercase;
                 padding:.2rem .7rem; border-radius:999px; background:var(--mark); }
    .card-ctx { font-family:var(--sans); font-weight:400; font-size:1rem; line-height:1.6; margin:1rem 0 1.3rem; }
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
    .why { font-size: .92rem; line-height: 1.6; margin: .9rem 0 .4rem; }
    .why b { font-weight: 600; margin-right: .35rem; }
    .keybox { border: 1px solid #c27a1a; background: color-mix(in srgb, #e0a050 14%, transparent);
              border-radius: .8rem; padding: .7rem 1rem; margin: .8rem 0 .6rem; font-size: .92rem; }
    .sb-warn { font-size: .76rem; color: #c27a1a; margin: -.5rem 0 .8rem; line-height: 1.4; }
    [data-testid="stSidebar"] [data-testid="stPopover"] button { justify-content: flex-start; text-align: left; }
    [data-testid="stButtonGroup"] div:has(> button) { flex-wrap: wrap; row-gap: .3rem; }
    [data-testid="stSidebar"] [data-testid="stPopover"] button p { text-align: left; }
    /* One corner size for every name row (one line or two) and its open box: same edges as a one-line pill */
    .st-key-entlist .stButton button { justify-content: flex-start; text-align: left; white-space: normal;
                                       border-radius: 1.25rem; }
    .st-key-entlist .stButton button > div, .st-key-entlist .stButton button > div > span { justify-content: flex-start; }
    .st-key-entlist .stButton button p { text-align: left; }
    .st-key-entlist [class*="st-key-ented-"] { padding: .7rem .75rem; margin: -.2rem 0 .3rem; border-radius: 1.25rem; }
    .st-key-stepnav { margin-top: .4rem; }
    /* Streamlit pulls every markdown block up 1rem to cancel a last paragraph's margin. Rows built from our own
       HTML (no paragraph) then sit on the card's border or the button below; give them back that space. */
    [data-testid="stMarkdownContainer"]:has(> :is(.qrow, .hit-row, .lib-sum, .checked-row, .lblov):last-child) { margin-bottom: 0; }
    .checked-row { font-size: .95rem; overflow-wrap: anywhere; }
    [class*="st-key-checked_"] { border-bottom: 1px solid var(--rule); padding: .3rem 0; }
    .checked-row .st { font-size: .8rem; color: var(--dim); margin-left: .4rem; }
    .lib-ask { font-family: var(--serif); font-size: 1.25rem; margin: .2rem 0 .1rem; }
    /* Label overview on ① Files: type, label, one example before and after; text wraps, long sets scroll */
    .lblov { max-height: 34rem; overflow-y: auto; border: 1px solid var(--rule); border-radius: .8rem; }
    .lblov table { width: 100%; table-layout: fixed; border-collapse: collapse; font-size: .9rem; line-height: 1.5; }
    .lblov th:nth-child(1) { width: 8.5rem; } .lblov th:nth-child(2) { width: 9.5rem; }
    .lblov th { position: sticky; top: 0; background: var(--background-color, Canvas); text-align: left;
                font-size: .72rem; font-weight: 500; letter-spacing: .1em; text-transform: uppercase; color: var(--dim);
                padding: .6rem .8rem; border-bottom: 1px solid var(--rule); }
    .lblov td { padding: .55rem .8rem; border-bottom: 1px solid var(--rule); vertical-align: top; overflow-wrap: break-word; }
    .lblov tr:last-child td { border-bottom: 0; }
    .lblov td.t { color: var(--dim); } .lblov td.l { font-weight: 500; }
    .lblov mark { background: var(--soft); color: #272727; padding: 0 .2em; border-radius: .3rem; }
    .st-key-selall { align-items: flex-start; padding-left: 1rem; margin-bottom: -.6rem; }
    .st-key-selall [data-testid="stCheckbox"] { width: auto; }
    .st-key-selall [data-testid="stCheckbox"] label { font-size: .85rem; }
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
    /* review list */
    .qrow-top { display:flex; justify-content:space-between; align-items:center; gap:.4rem 1rem; flex-wrap:wrap;
                font-size:.8rem; }
    .qrow-top .card-type { margin:0; }
    .qrow-main { font-size:1rem; margin:.45rem 0 .25rem; overflow-wrap:anywhere; }
    .qrow-main b { font-weight:500; }
    .qrow-ctx { font-size:.9rem; line-height:1.55; color:var(--dim); }
    .qrow-ctx mark { background:var(--soft); color:#272727; padding:0 .2em; border-radius:.3rem; }
    .st-key-qlist [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; }
    .st-key-qlist [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child { flex: 0 0 1.6rem !important; min-width: 1.6rem !important; }
    .st-key-qlist [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2) { flex: 1 1 auto !important; min-width: 0 !important; }
    .st-key-qlist [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child { flex: 0 0 4.6rem !important; min-width: 4.6rem !important; }
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
def progress_bar():
    """A bar that follows the engine's (message, fraction) progress; long filings take about a minute."""
    bar = st.progress(0.0, text="Starting…")
    return bar, lambda message, fraction: bar.progress(min(max(fraction, 0.0), 1.0), text=message)


def start_session(files: list[tuple[str, bytes]], policy, rights: bool = False) -> None:
    bar, progress = progress_bar()
    sess = Session.from_files(files, policy, progress)
    progress("Matching names across files…", 0.98)
    sess.run()
    sess.rights_confirmed = rights
    st.session_state.session = sess
    bar.empty()
    st.session_state.view = "Start"  # open on ① Files: what's in the set first
    st.session_state.editing = False


def reset() -> None:
    sess = st.session_state.get("session")
    if sess is not None and hasattr(sess, "export_rules"):
        try:  # "Start over" clears the files, not your rules
            files = sess.names[0] if len(sess.names) == 1 else f"{', '.join(sess.names[:-1])} and {sess.names[-1]}"
            st.session_state.carry_rules = sess.export_rules(
                f"{sess.policy.name}", f"Your settings and names from {files} ({sess.policy.name} rules).")
            st.session_state.carry_from = sess.names[0] + (f" + {len(sess.names) - 1} more" if len(sess.names) > 1
                                                           else "")
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
    before = set(getattr(sess, "approved", {}))
    what = sess.undo() if hasattr(sess, "undo_stack") else None
    st.session_state.editing = False
    changed = before ^ set(sess.approved)
    if changed and st.session_state.get("view") == "Documents":  # show the file whose approval was undone
        st.session_state.doc_pick = min(changed)
    if what:
        st.session_state.focus_note = f"Undone: {what}"


def type_name(t: str) -> str:
    return TYPE_NAMES.get(t) or type_title(t)


def color_type(t: str) -> str:
    return t if t in TYPE_COLORS else "MANUAL"


def cat_name(t: str) -> str:
    """A category in a dropdown: "Person", "Company", "CRD number", "Age", "Other"."""
    return KIND_NAMES.get(t) or type_name(t)


def noun(t: str) -> str:
    """A type as it reads mid-sentence: "person", "company", "CRD number", "account number", "age"."""
    if t in ("PERSON", "ORGANIZATION", "MANUAL"):
        return {"PERSON": "person", "ORGANIZATION": "company", "MANUAL": "item"}[t]
    name = type_name(t)
    return name if name[1:2].isupper() else name[:1].lower() + name[1:]


def a_noun(t: str) -> str:
    """The type with its article: "a person", "an account number", "an SSN", "a CRD number"."""
    n = noun(t)
    acronym = n.split()[0].isupper()
    vowel_sound = n[:1] in ("AEFHILMNORSX" if acronym else "aeiou")
    return f"{'an' if vowel_sound else 'a'} {n}"


def categories(sess: Session, res, keep: str = "") -> list[str]:
    """Every category a name or value can be put in: person, company, the rules' redacted types, the categories
    you made yourself ("OTHER:Age" — offered everywhere as soon as one exists), and plain "Other"."""
    ops = sess.ops
    made = [t for t in dict.fromkeys([e.type for e in res.entities]
                                     + [m.get("type") or "" for m in ops.get("manual", [])]
                                     + [p.get("type") or "" for p in ops.get("parties", [])]
                                     + list((ops.get("retype") or {}).values()))
            if t.startswith("OTHER:")]
    built_in = ["PERSON", "ORGANIZATION"] + [t for t, sp in sess.policy.entities.items()
                                             if sp.get("action") in ("redact", "generalize")]  # Address, Date too
    out = sorted(dict.fromkeys(built_in + made + ([keep] if keep and keep != "MANUAL" else [])), key=lambda t: cat_name(t).lower())
    return out + ["MANUAL"]  # A–Z, with plain "Other" last


def _cat_word(text: str) -> str:
    t = " ".join(text.split()).casefold()
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t  # "Ages" is "Age"


def existing_category(sess: Session, res, desc: str) -> str | None:
    """The category a typed description already is ("age" → OTHER:Age, "account number" → ACCOUNT_NUMBER), so
    the same field isn't created twice."""
    want = _cat_word(desc)
    if not want:
        return None
    return next((t for t in categories(sess, res) if t != "MANUAL"
                 and want in {_cat_word(type_name(t)), _cat_word(noun(t)), _cat_word(cat_name(t))}), None)


def examples(e, k: int = 3) -> str:
    seen = []
    for m in e.mentions:
        f = " ".join(m[3].split())
        if f not in seen:
            seen.append(f)
    return ", ".join(seen[:k]) + ("…" if len(seen) > k else "")


def delete_entity(sess: Session, ent) -> None:
    """'Don't hide it': a false detection; its text appears as written in every file (undoable)."""
    if ent.rule == "manual":
        sess.unredact(ent.key, note=f"Show “{ent.canonical}” again")
    else:
        sess.delete(ent.key, note=f"Show “{ent.canonical}” again")
    later = "" if ent.rule == "manual" else ", here and in your next sets"  # a false detection is remembered
    st.session_state.focus_note = f"“{ent.canonical}” will appear as written{later} — ↶ Undo if that was a mistake"
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
        where = f"a new {noun(new_type or ent.type)}"
    else:
        sess.split(ent.key, form, None, dest, note=f"Move “{form}” out of {ent.label}")
        st.session_state.focus = dest
        where = "the label you picked"
    st.session_state.focus_note = f"“{form}” is now separate from {ent.label} ({where})"


def form_groups(ent) -> dict[str, list]:
    """Written forms that go together when splitting ("Okafor", "Mr. Okafor"): {norm: [[forms], count]}."""
    groups: dict[str, list] = {}
    for f, _, cnt, _, _ in forms_of(ent):
        g = groups.setdefault(norm_form(ent.type, f), [[], 0])
        g[0].append(" ".join(f.split()))
        g[1] += cnt
    return groups


def not_all_same(t: str) -> str:
    return f"Not all the same {noun(t)}? Separate one"


def split_controls(sess: Session, res, ent, key: str) -> None:
    """Pick a written form, then where all its variants go: a new entry (any category) or an existing label."""
    groups = form_groups(ent)
    ask = "Which one is someone else?" if ent.type == "PERSON" else f"Which one is a different {noun(ent.type)}?"
    form = st.selectbox(ask, list(groups), key=f"{key}_form", index=None,
                        placeholder="Choose…", format_func=lambda n: f"{', '.join(groups[n][0])}  (×{groups[n][1]})")
    types = [ent.type] + [t for t in categories(sess, res) if t != ent.type]
    others = sorted((e for e in res.entities if e.key != ent.key and e.is_redacted),
                    key=lambda e: (e.type != ent.type, e.label))
    dests = [f"new:{t}" for t in types] + [e.key for e in others]
    by = {e.key: e for e in others}
    st.selectbox("It is…", dests, key=f"{key}_dest",
                 format_func=lambda d: f"A new {noun(d[4:])}" if d.startswith("new:")
                 else f"Part of {by[d].label} ({examples(by[d], 2)})")
    st.button("Separate", key=f"{key}_go", width="stretch", disabled=form is None,
              on_click=lambda: do_split(sess, ent, groups[st.session_state[f"{key}_form"]][0][0],
                                        st.session_state[f"{key}_dest"]))


def suggested_labels(res, item) -> list[str]:
    """A plainer label to offer for a name: its category on its own ("Address" for "Address of principal executive
    offices"), lettered for people and companies ("Company A"), numbered if taken ("Address 2")."""
    base = cat_name(item.type)
    taken = {e.label.casefold() for e in res.entities if e.key != item.key}
    letters = item.type in ("PERSON", "ORGANIZATION")
    cands = ((f"{base} {chr(65 + i)}" for i in range(26)) if letters
             else iter([base] + [f"{base} {n}" for n in range(2, 100)]))
    pick = next((c for c in cands if c.casefold() not in taken), None)
    return [pick] if pick and pick != item.label else []


def replacement_picker(res, item, key: str, default: str, title: str = "Replacement",
                       suggest: str | None = None) -> tuple[str, str]:
    """Dropdown: "Add new" (empty = keep what it is now), a suggested plainer label, and the existing labels to
    join (with examples). Returns ("new", text) | ("existing", entity key) | ("text", flag replacement).
    ``suggest`` (an entity key) is preselected: the label this probably belongs in."""
    is_flag = item is not None and not hasattr(item, "variants")
    if is_flag:
        pool = sorted({f.replacement_for(f.text) for f in res.flags if f.status in ("accepted", "edited")}
                      | {f.proposed for f in res.flags})
        choice = st.selectbox("Replacement", ["__new__"] + pool, key=key, index=1 + pool.index(default) if default in pool else 0,
                              format_func=lambda k: "➕ Add new replacement" if k == "__new__" else k)
        if choice == "__new__":
            typed = st.text_input("New replacement", "", key=f"{key}_txt",
                                  placeholder=f"Leave empty to keep “{_flat(default, 60)}”" if default else "")
            return "text", typed.strip() or default
        return "text", choice
    own = getattr(item, "key", None) if item is not None else None
    t = getattr(item, "type", None)
    pool = sorted((e for e in res.entities if e.is_redacted and e.key != own),
                  key=lambda e: (e.type != t, e.type, e.label))
    by = {e.key: e for e in pool}
    tips = suggested_labels(res, item) if item is not None else []
    keys = ["__new__"] + [f"tip:{x}" for x in tips] + list(by)
    choice = st.selectbox(title, keys, key=key, index=keys.index(suggest) if suggest in by else 0,
                          format_func=lambda k: "➕ Add new replacement" if k == "__new__"
                          else f"★ Suggested: {k[4:]}" if k.startswith("tip:")
                          else f"{by[k].label} — e.g. {examples(by[k])}")
    if choice == "__new__":
        now = getattr(item, "label", "") if item is not None else ""
        return "new", st.text_input("New replacement", "", key=f"{key}_txt",
                                    placeholder=f"Leave empty to keep “{now}”" if now
                                    else "Leave empty for the next free label")
    if choice.startswith("tip:"):
        st.caption(f"Becomes “{choice[4:]}” in every file.")
        return "new", choice[4:]
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
        st.session_state.focus_note = f"{item.canonical} is now “{value.strip()}” in every file"
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


_LIST_NO = re.compile(r"\s*(?:\d{1,3}|[IVX]+)")


@functools.lru_cache(maxsize=16)
def _sentence_cuts(text: str) -> tuple[int, ...]:
    """Where sentences start, once per document (a 230-page filing has thousands of review cards)."""
    cuts = [0]
    for m in _CUT.finditer(text):
        at = m.start()
        if m.group()[0] in ".!?":
            # "Mr." / "No." / initials / paragraph numbers ("1.") are not sentence ends; only the few
            # characters before the full stop matter, so this stays fast on long documents
            nl = text.rfind("\n", max(0, at - 40), at)
            if _ABBR.search(text[max(0, at - 16):at]) or (
                    (nl != -1 or at <= 40) and _LIST_NO.fullmatch(text, nl + 1, at)):
                continue
        cuts.append(m.end())
    return tuple(sorted(set(cuts + [len(text)])))


def sentence_context(text: str, start: int, end: int) -> tuple[str, str, str, str, str]:
    """(previous sentence, text before span, span, text after span, next sentence)."""
    cuts = _sentence_cuts(text)
    i = min(bisect.bisect_right(cuts, start) - 1, len(cuts) - 2)
    j = max(bisect.bisect_left(cuts, end), 1)
    prev = text[cuts[i - 1]:cuts[i]] if i > 0 else ""
    nxt = text[cuts[j]:cuts[j + 1]] if j + 1 < len(cuts) else ""
    return (_flat(prev, 320), _flat(text[cuts[i]:start], 420), _flat(text[start:end]),
            _flat(text[end:cuts[j]], 420, "start"), _flat(nxt, 320, "start"))


def anonymized_html(res, doc: int, start: int, end: int) -> str:
    """The text between start and end as it reads in the output: what's already hidden shows as its label, so a
    sentence to rewrite is shown as it is now, not as the original."""
    text, out, pos = res.texts[doc], [], start
    flat = lambda t: html.escape(re.sub(r"\s+", " ", t))
    for g in sorted(res.segments[doc], key=lambda g: g.start):
        if g.kind != "entity" or g.start < pos or g.end > end:
            continue
        out.append(flat(text[pos:g.start]))
        if g.replacement:
            out.append(f'<span class="chip t-{color_type(g.type)}">{html.escape(g.replacement)}</span>')
        pos = g.end
    out.append(flat(text[pos:end]))
    return "".join(out).strip()


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
    last = f"Your rules from {st.session_state.get('carry_from') or 'the last set'}"  # names the set it came from
    if st.session_state.get("carry_rules"):
        choices[last] = ("yaml", st.session_state.carry_rules)
    for nm in sorted(library):
        choices[f"★ {nm}"] = ("yaml", library[nm]["yaml"])
    for nm in policies:
        choices[f"{nm} (built-in)"] = ("file", nm)
    labels_ = list(choices)
    if not st.session_state.get("rules_touched"):  # default: last set's rules, else your newest saved rules
        newest = max(library, key=lambda nm: library[nm].get("at", ""), default=None)
        st.session_state.rules_pick = (last if st.session_state.get("carry_rules")
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
                     help="★ = saved in My rules (this browser). Save rules from Settings in the sidebar once you've "
                          "set them up.")
        pol = load_choice(st.session_state.rules_pick)
        st.caption(pol.raw.get("description", "") + (f" · {len(pol.known_parties)} name{'s' if len(pol.known_parties) != 1 else ''} always hidden"
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
    if c2.button("Try the sample case", width="stretch"):
        start_session([(p.name, p.read_bytes()) for p in sorted(SAMPLES.glob("*")) if p.suffix in SUPPORTED], pol,
                      rights=True)  # public SEC filings
        st.rerun()
    if not (uploads and rights):
        st.caption("Add files and tick the box to continue — or try the sample: two public SEC filings on Microsoft's "
                   "acquisition of Activision Blizzard, the 8-K announcing the deal (2022) and the 8-K closing it (2023).")
    st.caption("Nothing is saved — refreshing or closing this tab clears your work. Download first.")


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


def toggle_entity(key: str) -> None:
    st.session_state.sb_open = None if st.session_state.get("sb_open") == key else key


def entity_row(sess: Session, res, e, focus) -> None:
    """One name in the sidebar list. Clicking it opens its controls right below, inside the sidebar, so they
    scroll with it (a floating popover ran off the bottom of the window)."""
    status = {"pending": " · needs a look", "setting": " · off", "manual": " · by you"}.get(e.status, "")
    is_open = st.session_state.get("sb_open") == e.key
    mark = "● " if e.key == focus else ""
    st.button(f"{mark}{e.label} · {e.canonical[:24]} ({len(e.mentions)}){status}  {'▾' if is_open else '▸'}",
              key=f"ent-{e.key}", width="stretch", type="primary" if is_open else "secondary",
              on_click=toggle_entity, args=(e.key,))
    if is_open:
        with st.container(border=True, key=f"ented-{e.key}"):
            entity_editor(sess, res, e)


def move_to_category(sess: Session, e, new_type: str) -> None:
    sess.retype(e.key, new_type, note=f"“{e.canonical}” is {a_noun(new_type)}")
    st.session_state.focus = e.key
    st.session_state.pop("_ent_focus_seen", None)  # the list jumps to its new category
    st.session_state.focus_note = f"“{e.canonical}” is now {a_noun(new_type)}"


def entity_editor(sess: Session, res, e) -> None:
    """A name's label, where it's found (pick a file to see only what's in it), how it's written, and one
    question: what should happen to it?"""
    status = STATUS_WORDS.get(e.status, e.status)
    st.caption(status[:1].upper() + status[1:])
    if e.is_redacted and not e.nick_target:  # stacked: the sidebar is too narrow for a field and a button side by side
        new = st.text_input("Label", e.label, key=f"lbl-{e.key}-{e.label}", help="Replaces it in every file.")
        if st.button("Rename", key=f"rn-{e.key}", width="stretch") and new.strip() not in ("", e.label):
            try:
                sess.rename(e.key, new, note=f"Rename {e.label} → “{new.strip()}”")
                st.session_state.focus = e.key
                st.session_state.focus_note = f"{e.canonical} is now “{new.strip()}”"
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    per_doc = Counter(m[0] for m in e.mentions)
    if len(per_doc) > 1:
        doc = st.selectbox("Found in", [None] + sorted(per_doc), key=f"in-{e.key}",
                           format_func=lambda d: f"All {len(per_doc)} files · ×{len(e.mentions)}" if d is None
                           else f"{short(sess.names[d], 30)} · ×{per_doc[d]}")
    else:
        doc = None
        only = next(iter(per_doc))
        st.markdown(f'<p class="ll-sub">Found in {html.escape(short(sess.names[only], 30))} · ×{per_doc[only]}</p>',
                    unsafe_allow_html=True)
    shown = Counter(m[3] for m in e.mentions if doc is None or m[0] == doc)
    st.markdown("<br>".join(f"<span class='form'>{html.escape(f)}</span> <span class='dim'>×{n}</span>"
                            for f, n in shown.items()), unsafe_allow_html=True)
    others = sorted((o for o in res.entities if o.key != e.key and o.is_redacted),
                    key=lambda o: (o.type != e.type, o.label))
    actions = {"move": "Move to another category"}  # short: the sidebar is narrow
    if others:
        actions["join"] = "Belongs in another label"
    if len(form_groups(e)) > 1:
        actions["split"] = "Not all the same — split"
    if e.is_redacted:
        actions["show"] = "Don't hide it"
    act = st.selectbox("What should happen to it?", list(actions), key=f"act-{e.key}", index=None,
                       format_func=actions.get, placeholder="Choose…")
    if act == "move":
        cats = [t for t in categories(sess, res) if t != e.type]
        tgt = st.selectbox("Move it to", cats, key=f"cat-{e.key}", format_func=cat_name, index=None,
                           placeholder="Choose a category…")
        st.button(f"Move to {cat_name(tgt)}" if tgt else "Move", key=f"catgo-{e.key}", width="stretch",
                  disabled=tgt is None, on_click=move_to_category, args=(sess, e, tgt))
        st.caption("It gets a new label from that category, the same in every file.")
    elif act == "join":
        names = {o.key: f"{o.label} ({examples(o, 2)})" for o in others}
        tgt = st.selectbox("Which label?", list(names), key=f"mv-{e.key}", format_func=names.get, index=None,
                           placeholder="Choose a label…")
        st.button(f"Add to {res.entity(tgt).label}" if tgt else "Add to that label", key=f"mb-{e.key}",
                  on_click=merge_into, width="stretch", disabled=tgt is None,
                  args=(sess, e, next((o for o in others if o.key == tgt), None)))
    elif act == "split":
        split_controls(sess, res, e, f"sb-{e.key}")
    elif act == "show":
        st.caption("It will appear as written in every file — not hidden, not replaced. ↶ Undo brings it back.")
        st.button(f"Leave “{short(e.canonical, 28)}” as written", key=f"del-{e.key}", on_click=delete_entity,
                  args=(sess, e), width="stretch")


def party_hits(res, name: str) -> tuple[int, int]:
    """How often a known party's name is found (occurrences, files)."""
    hits = search(res, name)
    return len(hits), len({h["doc"] for h in hits})


def known_parties_form(sess: Session, res, key: str, compact: bool = False) -> None:
    """Add a name, pick what it is, see how often it's found; remove with ×. Always hidden, in every spelling."""
    kinds = {"ORGANIZATION": "Company", "PERSON": "Person",
             **{t: type_name(t) for t in categories(sess, res) if t.startswith("OTHER:")},  # ones you've made
             "MANUAL": "Other (codename, project…)"}
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
    """Four collapsible sections: Settings · Known parties · Rules · Names & labels."""
    focus = st.session_state.get("focus")
    s = sess.settings
    with st.sidebar:
        st.markdown(f'<p class="sb-title">{html.escape(sess.policy.name)} rules · '
                    f'{len(res.names)} file{"s" if len(res.names) != 1 else ""}</p>'
                    '<p class="sb-warn">Nothing is saved — refreshing or closing this tab clears your work.</p>',
                    unsafe_allow_html=True)

        # ---- Settings
        with st.expander("Settings"):
            # Labels: with context (roles) or simple (Person A) — first, it changes every label
            label_modes = {"context": "With context", "simple": "Simple"}
            st.session_state.setdefault("t_labels", "context" if s.get("role_labels", False) else "simple")
            st.segmented_control("Labels", list(label_modes), key="t_labels", format_func=label_modes.get,
                                 width="stretch", help="With context: roles and kinds of title — Defendant, Signatory, "
                                                       "Assistant secretary. Simple: Person A, Company B, Title 1.")
            date_modes = {"month_year": "Month + year", "shifted": "Shifted (same gaps)",
                          "relative": "Relative (Day 0, Day 6…)", "exact": "Exact"}
            styles = {"labels": "Labels (Person A, Acquirer…)", "redacted": "[REDACTED]", "remove": "Remove the text"}
            st.session_state.setdefault("t_style", s.get("placeholder_style", "labels"))
            st.selectbox("Label style", list(styles), key="t_style", format_func=styles.get,
                         help="Labels show when two mentions are the same. [REDACTED] or removing gives nothing back.")
            st.session_state.setdefault("t_dates", s.get("date_mode", "month_year"))
            st.selectbox("Dates", list(date_modes), key="t_dates", format_func=date_modes.get)
            st.caption(date_explainer(res, st.session_state.t_dates))
            pub_modes = {"review": "Ask if it's a party", "keep": "Always keep", "redact": "Always hide"}
            st.session_state.setdefault("t_pub", s.get("public_companies", "review"))
            st.selectbox("Public companies", list(pub_modes), key="t_pub", format_func=pub_modes.get)
            st.session_state.setdefault("t_addr", s.get("generalize_addresses", True))
            st.toggle("Addresses → city, state", key="t_addr",
                      help="Used when the file doesn't say what the address is (e.g. “Principal executive offices”).")
            with st.popover("Save these rules", width="stretch"):
                save_rules_panel(sess)
            with st.popover("Use other rules", width="stretch"):
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
        s["role_labels"] = {"context": True, "simple": False}.get(st.session_state.t_labels, s.get("role_labels", False))
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
        res = sess.run()  # the switches above may have just changed every label
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
                st.session_state.sb_open = focus                # …with its controls open
            if st.session_state.get("ent_type") not in options:
                st.session_state.ent_type = "__all__"
            eq = st.text_input("Find a name", key="ent_search", label_visibility="collapsed",
                               placeholder="🔍 Find a name or label…").strip().lower()
            if eq:
                found = alpha(e for e in res.entities if eq in e.label.lower() or eq in e.canonical.lower()
                              or any(eq in m[3].lower() for m in e.mentions))
                st.caption(f"{len(found)} match{'es' if len(found) != 1 else ''}" if found else "No matches.")
                with st.container(key="entlist"):
                    for e in found[:40]:
                        entity_row(sess, res, e, focus)
            else:
                pick = st.selectbox("Show", options, key="ent_type", label_visibility="collapsed",
                                    format_func=lambda t: f"Show all ({len(res.entities)})" if t == "__all__"
                                    else f"Needs a look ({len(pending)})" if t == "__review__"
                                    else f"{type_name(t)} ({len(by_type[t])})")
                shown = (res.entities if pick == "__all__" else pending if pick == "__review__"
                         else by_type.get(pick, []))
                with st.container(key="entlist"):
                    for e in alpha(shown):
                        entity_row(sess, res, e, focus)

        st.markdown('<div class="ll-rule"></div>', unsafe_allow_html=True)
        if st.button("Start over", width="stretch", key="sb_start_over"):
            confirm_start_over()


# --------------------------------------------------------------------------- adding documents / start
def add_documents_form(sess: Session, key: str) -> None:
    """Upload more files into the current set (same registry, so pseudonyms stay consistent)."""
    ups = st.file_uploader("Add files", type=[x.strip(".") for x in SUPPORTED], accept_multiple_files=True,
                           key=f"{key}_files", label_visibility="collapsed")
    ok = st.checkbox("I have the right to share these files too.", key=f"{key}_rights",
                     help="Anonymizing doesn't cure privilege, NDAs or patient confidentiality.")
    if st.button("Add to this set", type="primary", disabled=not (ups and ok), key=f"{key}_go"):
        before = len(sess.run().queue)
        bar, progress = progress_bar()
        try:
            added = sess.add_files([(u.name, u.getvalue()) for u in ups], progress)
        except ValueError as exc:
            bar.empty()
            st.error(str(exc))
            return
        progress("Matching names across files…", 0.98)
        new_items = len(sess.run().queue) - before
        st.session_state.focus_note = (f"Added {len(added)} file{'s' if len(added) != 1 else ''}"
                                       + (f" — {new_items} new item{'s' if new_items != 1 else ''} to review"
                                          if new_items > 0 else ""))
        st.session_state.pop(f"{key}_files", None)
        st.rerun()


@st.dialog("Add files", width="large")
def add_documents_dialog(sess: Session) -> None:
    st.caption("Your labels and decisions carry over. Anything new found in these files joins ② Check.")
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


def _reads_now_html(res, doc: int, a: int, b: int) -> str:
    """The text between a and b as it reads in the output (every replacement applied, labels as chips)."""
    text, out, pos = res.texts[doc], [], a
    flat = lambda t: html.escape(re.sub(r"\s+", " ", t))   # keep the spacing, one space per gap
    for g in sorted(res.segments[doc], key=lambda g: g.start):
        if g.start < pos or g.end > b:
            continue
        out.append(flat(text[pos:g.start]))
        if g.kind == "tidy" or not g.replacement:
            out.append(html.escape(g.replacement))
        else:
            out.append(f'<span class="chip t-{color_type(g.type)}">{html.escape(g.replacement)}</span>')
        pos = g.end
    out.append(flat(text[pos:b]))
    return "".join(out).strip()


def example_pair(res, e) -> tuple[str, str]:
    """One place a label is used, with a few words either side, as HTML: (as written with the name marked, as
    it reads now)."""
    doc, s, end, orig = e.mentions[0]
    text, segs = res.texts[doc], res.segments[doc]
    a, b = max(0, s - 32), min(len(text), end + 32)
    if a > 0 and " " in text[a:s]:
        a = text.index(" ", a) + 1              # start and end on whole words
    if b < len(text) and " " in text[end:b]:
        b = text.rindex(" ", end, b)
    for g in segs:                              # and never cut through another hidden name
        if g.start < a < g.end:
            a = g.start
        if g.start < b < g.end:
            b = g.end
    pre, post = "…" if a > 0 else "", "…" if b < len(text) else ""
    flat = lambda t: html.escape(re.sub(r"\s+", " ", t))
    before = f"{pre}{(flat(text[a:s]) + f'<mark>{flat(orig)}</mark>' + flat(text[end:b])).strip()}{post}"
    return before, f"{pre}{_reads_now_html(res, doc, a, b)}{post}"


def start_view(sess: Session, res) -> None:
    """Step 1 while a set is open: the files in it, add more, the labels. (Next is the button at the bottom.)"""
    st.markdown('<h2>Your files</h2>', unsafe_allow_html=True)
    rows = [{"File": name, "Status": ("✓ Approved" if is_current(sess, res, i) else
                                      "Changed since approval" if i in sess.approved else "In progress"),
             "Hidden": len([g for g in res.segments[i] if g.kind != "tidy"]),
             "Notes": "; ".join(w["message"] for w in (sess.warnings[i] if i < len(getattr(sess, 'warnings', []) or []) else []))}
            for i, name in enumerate(res.names)]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.markdown('<div style="height:.6rem"></div>', unsafe_allow_html=True)
    label("Add files to this set")
    add_documents_form(sess, "start_add")
    st.markdown('<div class="ll-rule"></div>', unsafe_allow_html=True)
    label("Labels")
    st.caption("Every label in this set, with one place it's used: as written, and as it reads now. To rename a "
               "label, open it under Names & labels in the sidebar, or click it in a document.")
    natural = lambda v: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", v.lower())]  # Person 2 before 10
    ents = sorted((e for e in res.entities if e.is_redacted and not e.nick_target and e.mentions),
                  key=lambda e: (cat_name(e.type), natural(e.label)))
    rows = []
    for e in ents:
        before, after = example_pair(res, e)
        rows.append(f'<tr><td class="t">{html.escape(cat_name(e.type))}</td><td class="l">{html.escape(e.label)}</td>'
                    f'<td>{before}</td><td>{after}</td></tr>')
    if rows:
        st.markdown('<div class="lblov"><table><thead><tr><th>Type</th><th>Label</th><th>Before</th><th>After</th>'
                    f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------- review
def bearing(done: int, total: int, title: str = "Items", unit: str = "checked", tight: bool = False,
            note: str = "") -> None:
    frac = done / total if total else 1.0
    left = total - done
    st.markdown(
        f'<div class="fix{" tight" if tight else ""}"><div class="fix-row"><span>{title}</span>'
        f'<span class="now">{done} of {total} {unit}{f" · {left} left" if left else " ✓"}</span></div>'
        f'<div class="track"><div class="fill" style="width:{frac * 100:.1f}%"></div></div>'
        + (f'<div class="fix-note">{html.escape(note)}</div>' if note else "") + '</div>',
        unsafe_allow_html=True)


def item_kind(sess: Session, key: str, item) -> str:
    """The short tag on a review card: what kind of question this is."""
    if key.startswith("F|"):
        flag_title = next((f.title for f in sess.policy.flags if f.id == item.rule and f.title), "")
        return {"quasi.combination": "Could identify someone"}.get(item.rule, flag_title or "Needs a look")
    if item.rule == "names.public_company_party_check":
        return "Public company · is it involved?"
    if getattr(item, "nick_target", ""):
        return "Possible nickname"
    return f"{type_name(item.type)} · not sure"


def open_item(key: str) -> None:
    st.session_state.update(review_pick=key, review_mode="one", review_return=True, editing=False)


def set_picks(keys: list[str], value: bool) -> None:
    for k in keys:
        st.session_state[f"pick_{k}"] = value


def bulk_decide(keys: list[str], action: str) -> None:
    sess = st.session_state.session
    sess.decide_many(keys, action, f"{'Approve' if action == 'accept' else 'Keep'} {len(keys)} items")
    for k in keys:
        st.session_state.pop(f"pick_{k}", None)
    n = f"{len(keys)} item{'s' if len(keys) != 1 else ''}"
    st.session_state.focus_note = ((f"Approved {n}" if action == "accept" else f"Kept {n} as written")
                                   + " — ↶ Undo reverses " + ("all of them" if len(keys) != 1 else "it"))


def review_list(sess: Session, res, pending: list[str]) -> None:
    """Every open item on one page: tick the ones to approve, open any to change by hand."""
    items = [(k, res.item(k)) for k in pending]
    for k, it in items:  # ticked by default, except sentences that need your own wording
        st.session_state.setdefault(f"pick_{k}", it.rule != "quasi.combination")
    chosen = [k for k, _ in items if st.session_state.get(f"pick_{k}")]
    keys = [k for k, _ in items]

    def bar(where: str) -> None:
        with st.container(key=f"bulkbar_{where}"):
            b1, b2 = st.columns(2, vertical_alignment="center")
            b1.button(f"✓ Approve {len(chosen)} selected", type="primary", disabled=not chosen, width="stretch",
                      key=f"bulk_ok_{where}", on_click=bulk_decide, args=(chosen, "accept"))
            b2.button(f"Keep {len(chosen)} as written", disabled=not chosen, width="stretch",
                      key=f"bulk_keep_{where}", on_click=bulk_decide, args=(chosen, "keep"),
                      help="Not sensitive: leave these as they are in the original")

    bar("top")
    st.caption("Approve uses the suggestion everywhere it appears. Untick anything you want to change, then open it.")
    with st.container(key="selall"):  # one tick box above the list, top right: ticked = every item selected
        st.session_state.sel_all = bool(keys) and len(chosen) == len(keys)
        st.checkbox("Select all", key="sel_all", on_change=lambda: set_picks(keys, st.session_state.sel_all))
    with st.container(key="qlist"):
        queue_rows(sess, res, items)
    if len(items) > 4:  # the same actions at the bottom, so a long list doesn't send you back up
        bar("bottom")


def queue_rows(sess: Session, res, items: list) -> None:
    for k, it in items:
        doc, s0, e0, _ = it.mentions[0]
        n = len(it.mentions)
        is_flag = k.startswith("F|")
        sentence = is_flag and getattr(it, "scope", "") == "sentence"
        found = it.text if is_flag else it.canonical
        if it.rule == "quasi.combination":
            change = '<span class="warn">Needs your wording — open it to rewrite the sentence</span>'
        elif sentence:
            change = f'Becomes <b>{html.escape(_flat(it.proposed, 160))}</b>'
        else:
            change = f'<b>{html.escape(found)}</b> <span class="dim">→</span> <b>{html.escape(it.proposed)}</b>'
        with st.container(border=True):
            c0, c1, c2 = st.columns([.32, 6, 1.05], vertical_alignment="center")
            c0.checkbox("Approve", key=f"pick_{k}", label_visibility="collapsed")
            c1.markdown(
                f'<div class="qrow"><div class="qrow-top"><span class="card-type">{html.escape(item_kind(sess, k, it))}'
                f'</span><span class="dim">{html.escape(res.names[doc])} · {n} place{"s" if n != 1 else ""}</span></div>'
                f'<div class="qrow-main">{change}</div>'
                f'<div class="qrow-ctx">{anonymized_html(res, doc, s0, e0) if sentence else context_html(res, doc, s0, e0, neighbours=False)}'
                '</div></div>',
                unsafe_allow_html=True)
            c2.button("Open", key=f"open_{k}", width="stretch", on_click=open_item, args=(k,),
                      help="Change the label, the type or the wording")


REVIEW_MODES = {"list": "All at once", "one": "One at a time"}
CHECKED_WORDS = {"accepted": "hidden", "edited": "your wording", "kept": "kept as written", "manual": "hidden by you"}


def reopen_item(key: str) -> None:
    """"Change" on a checked item: open it again, as a card, with the same answers as before."""
    sess = st.session_state.session
    it = sess.run().item(key)
    sess.reopen(key, note=f"Reopen “{(it.text if key.startswith('F|') else it.canonical)[:40]}”")
    st.session_state.update(review_pick=key, review_mode="one", review_return=True, editing=False)
    st.session_state.focus_note = "Open again — answer it, or ↶ Undo to keep your earlier answer"


def checked_list(res, queue: list[str]) -> None:
    """Every item you answered in ② Check, as it now reads — and "Change" to answer any of them again."""
    for k in queue:
        it = res.item(k)
        doc = it.mentions[0][0]
        word = CHECKED_WORDS.get(it.status, STATUS_WORDS.get(it.status, it.status))
        if k.startswith("F|") and getattr(it, "scope", "") == "sentence":
            main = f'Sentence: {html.escape(_flat(it.replacement_for(it.text), 160, "start"))}'
        else:
            found = it.text if k.startswith("F|") else it.canonical
            shown = it.replacement_for(found)
            main = (f'<b>{html.escape(found)}</b>' + (f' → <b>{html.escape(shown)}</b>' if shown != found else ""))
        with st.container(key=f"checked_{queue.index(k)}"):
            c1, c2 = st.columns([6, 1], vertical_alignment="center")
            c1.markdown(f'<div class="checked-row">{main}<span class="st">{html.escape(word)} · '
                        f'{html.escape(short(res.names[doc], 30))}</span></div>', unsafe_allow_html=True)
            c2.button("Change", key=f"reopen_{k}", width="stretch", on_click=reopen_item, args=(k,),
                      help="Open it again to answer differently (↶ Undo brings your answer back)")


def review(sess: Session, res) -> None:
    queue = res.queue
    pending = [k for k in queue if res.item(k).status == "pending"]
    # An item opened from the list goes back to the list once it's answered.
    pick = st.session_state.get("review_pick")
    if pick and pick not in pending:
        st.session_state.pop("review_pick", None)
        if st.session_state.pop("review_return", False):
            st.session_state.review_mode = "list"
    bearing(len(queue) - len(pending), len(queue))
    if pending:
        st.session_state.setdefault("review_mode", "list")
        m1, _, m2 = st.columns([2.2, 1.6, 1], vertical_alignment="center")
        with m1:
            st.segmented_control("Show", list(REVIEW_MODES), key="review_mode", format_func=REVIEW_MODES.get,
                                 label_visibility="collapsed",
                                 on_change=lambda: st.session_state.pop("review_return", None))
        if m2.button("＋ Add files", key="rv_add", width="stretch"):
            add_documents_dialog(sess)

    if not pending:
        st.markdown('<h2>Everything is checked</h2>', unsafe_allow_html=True)
        st.markdown(f'<p class="ll-note">{sum(e.status == "auto" for e in res.entities)} clear cases were hidden '
                    'automatically.</p>', unsafe_allow_html=True)
        c1, _ = st.columns([1.4, 3])
        if c1.button("＋ Add files", width="stretch", key="rv_add_done"):
            add_documents_dialog(sess)
        if queue:
            label("What you checked")
            checked_list(res, queue)
        return

    if (st.session_state.get("review_mode") or "list") == "list":
        review_list(sess, res, pending)
        return
    key = pick if pick in pending else pending[0]
    if st.session_state.get("review_return"):
        st.button("← Back to the list", key="rv_back",
                  on_click=lambda: st.session_state.update(review_mode="list", review_return=False,
                                                           review_pick=None, editing=False))
    item = res.item(key)
    is_flag = key.startswith("F|")
    sentence = is_flag and getattr(item, "scope", "") == "sentence"
    doc, s, e, _ = item.mentions[0]
    docs = sorted({m[0] for m in item.mentions})
    etype = item_kind(sess, key, item)
    n = len(item.mentions)
    shown = item.text if is_flag else item.canonical
    st.markdown(
        f'<div class="card"><div class="card-top"><span class="card-type">{html.escape(etype)}</span>'
        f'<span class="dim">{html.escape(res.names[doc])} · {n} place{"s" if n != 1 else ""}</span></div>'
        f'<div class="card-ctx">{anonymized_html(res, doc, s, e) if sentence else context_html(res, doc, s, e)}</div>'
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
        if not is_flag and not getattr(item, "nick_target", ""):
            t1, _ = st.columns([1.4, 3])
            kinds = categories(sess, res, keep=item.type)
            t1.selectbox("This is a", kinds, index=kinds.index(item.type), format_func=cat_name,
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
                with st.expander(not_all_same(item.type)):
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
        sess.retype(item.key, new, note=f"“{item.canonical}” is {a_noun(new)}")
        st.session_state.focus_note = f"“{item.canonical}” is now treated as {a_noun(new)}"


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

  // Select text -> floating "Hide" button (edit mode only).
  const fab = document.createElement('button');
  fab.className = 'redact-fab'; fab.textContent = '✎ Hide'; fab.style.display = 'none';
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
  // Where each line starts in each pane, measured once (a 230-page filing has ~20,000 lines; measuring them on
  // every scroll event made scrolling lag) and again only when the panes change size.
  const tops = new Map();
  const lines = (p) => {
    if (!tops.has(p)) tops.set(p, Array.from(p.querySelectorAll('a.ln'), (x) => x.offsetTop - p.offsetTop));
    return tops.get(p);
  };
  const ro = new ResizeObserver((entries) => entries.forEach((e) => {  // only a new width rewraps the lines
    const w = Math.round(e.contentRect.width);
    if (e.target._w !== undefined && e.target._w !== w) tops.clear();
    e.target._w = w;
  }));
  panes.forEach((p) => ro.observe(p));
  const later = window.requestIdleCallback || ((f) => setTimeout(f, 300));
  later(() => panes.forEach(lines));  // measure while idle, not on the first scroll
  const lineAt = (a, y) => {  // last line starting at or above y
    let lo = 0, hi = a.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] <= y) lo = m + 1; else hi = m; }
    return lo - 1;
  };
  const pos = (p) => {
    const top = p.scrollTop, a = lines(p), i = lineAt(a, top);
    const y0 = i < 0 ? 0 : a[i];
    const y1 = i + 1 < a.length ? a[i + 1] : p.scrollHeight;
    return [i, y1 > y0 ? (top - y0) / (y1 - y0) : 0];
  };
  const follow = (src, dst) => {
    if (!sameDoc) {
      dst.scrollTop = src.scrollTop / Math.max(1, src.scrollHeight - src.clientHeight) * (dst.scrollHeight - dst.clientHeight);
      return;
    }
    const [i, f] = pos(src), a = lines(dst);
    const y0 = i < 0 ? 0 : a[i];
    const y1 = i + 1 < a.length ? a[i + 1] : dst.scrollHeight;
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
    st.session_state.focus_note = f"“{lk['text']}” is now hidden as {lk['label']} everywhere"


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
                c3.button("Hide it", key=f"leak_fix_{st.session_state.get('view', '')}_{i}", width="stretch",
                          type="primary", on_click=fix_leak, args=(sess, lk))
    return blockers


def short(name: str, n: int = 26) -> str:
    return name if len(name) <= n else name[:n - 1] + "…"


def is_current(sess: Session, res, doc: int) -> bool:
    return doc in sess.approved and sess.approved[doc]["output"] == res.outputs[doc]


_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]{3,}")
_NOT_A_NAME = GENERIC_ORG_WORDS | {"company", "corporation", "limited", "inc", "llc", "the", "and", "with", "from"}


def likely_label(res, phrase: str):
    """The existing label a phrase probably belongs in: one that shares a name word with it ("D. Okafor" →
    the label for Daniel K. Okafor). None if nothing matches."""
    words = {w.casefold() for w in _WORD.findall(phrase) if w[:1].isupper()} - _NOT_A_NAME
    if not words or re.search(r"\d", phrase):  # numbers: an address, a date, an account — not part of a name
        return None
    best = (0, None)
    for e in res.entities:
        if not e.is_redacted or e.nick_target or not (e.type in ("PERSON", "ORGANIZATION") or e.type.startswith("OTHER:")):
            continue
        n = len(words & {w.casefold() for m in e.mentions for w in _WORD.findall(m[3])})
        if n > best[0] and n * 2 >= len(words):  # at least half the phrase's name words are that name's
            best = (n, e)
    return best[1]


def hidden_owner(res, phrase: str):
    """The hidden name a selected phrase already is: every place it appears sits inside one hidden name's
    mention, or the phrase is that name's label. None if any of it is still showing."""
    body = r"\s+".join(map(re.escape, phrase.split()))
    if not body:
        return None
    spans = [(i, m.start(), m.end()) for i, t in enumerate(res.texts)
             for m in re.finditer(r"(?<![\w])" + body + r"(?![\w])", t)]
    owners = {next((e.key for e in res.entities if e.is_redacted for md, ms, me, _ in e.mentions
                    if md == d and ms <= a and b <= me), None) for d, a, b in spans}
    if spans and len(owners) == 1 and None not in owners:
        return res.entity(owners.pop())
    if not spans:  # the selection is a label as shown ("Registrant A")
        return next((e for e in res.entities if e.is_redacted and
                     phrase.lower() in {e.replacement_for(m[3]).lower() for m in e.mentions}), None)
    return None


@st.dialog("Hide text")
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
        st.caption(f"Already hidden as **{owner.label}** ({examples(owner, 2)}). Change what it becomes:")
        rk, rv = replacement_picker(res, owner, "rd_owner", owner.label)
        c1, c2 = st.columns(2)
        if c1.button("Save", type="primary", width="stretch", key="rd_owner_save"):
            if apply_replacement(sess, res, "E|" + owner.key, owner, rk, rv):
                st.rerun()
        if c2.button("Cancel", width="stretch", key="rd_owner_cancel"):
            st.rerun()
        return
    if not hits:
        st.warning("Not found in the original files. Select plain text, or click a label to change it.")
        return
    total = sum(n for _, n in hits)
    st.caption((f"{total} places" if total != 1 else "1 place") + " in " + ", ".join(res.names[i] for i, _ in hits)
               + (" — all of them will be hidden." if total != 1 else " — it will be hidden."))
    guess = likely_label(res, phrase)
    kinds = {"PERSON": "Person", "ORGANIZATION": "Company", "ACCOUNT_NUMBER": "Account number",
             "CASE_NUMBER": "Case / docket number", "STREET_ADDRESS": "Address", "DATE": "Date",
             **{t: type_name(t) for t in categories(sess, res) if t.startswith("OTHER:")}}  # ones you've made
    kinds = {**dict(sorted(kinds.items(), key=lambda kv: kv[1].lower())),  # A–Z, then the two catch-alls
             "MANUAL": "Just hide it (Redacted 1, 2…)", "OTHER": "Other — a new category…"}
    # No silent default (A–Z would make it "Account number"): empty, unless it's part of a name you already have
    kind = st.selectbox("What is it?", list(kinds), format_func=kinds.get, key="rd_kind", placeholder="Choose…",
                        index=list(kinds).index(guess.type) if guess is not None and guess.type in kinds else None)
    desc, same = "", None
    if kind == "OTHER":
        desc = " ".join(st.text_input("Name the new category", placeholder="e.g. Age, Project codename, Fund name",
                                      key="rd_desc").split())
        same = existing_category(sess, res, desc)
        if same:  # the same field again (another person's age): file it with the first one, not as a new category
            first = next((e for e in res.entities if e.type == same and e.is_redacted), None)
            st.info(f"“{desc}” is already a category"
                    + (f" — {first.label} is “{short(' '.join(first.canonical.split()), 30)}”" if first else "")
                    + f". This will be added to it as the next {cat_name(same)}, not as a second category. "
                    + "Next time, pick it from the list above.")
    if guess is not None:
        st.caption(f"Looks like part of **{guess.label}** ({examples(guess, 2)}), so that label is selected. "
                   "Choose ➕ Add new replacement if it's something else.")
    rk, rv = replacement_picker(res, None, "rd_pick", "", title="Label",
                                suggest=guess.key if guess is not None else None)
    c1, c2 = st.columns(2)
    ready = rk == "existing" or (kind is not None and not (kind == "OTHER" and not desc))
    if c1.button("Hide everywhere", type="primary", width="stretch", disabled=not ready):
        if rk == "existing":
            sess.redact_as(rv, phrase, note=f"Hide “{phrase[:40]}”")
        elif kind == "OTHER" and not same:
            sess.redact(phrase, f"OTHER:{desc}", rv.strip(), f"{desc} {{1}}")
        else:
            t = same or kind
            sess.redact(phrase, t, rv.strip(), f"{type_name(t)} {{1}}" if t.startswith("OTHER:") else "")
        st.session_state.focus_note = f"“{phrase}” is hidden in {total} place{'s' if total != 1 else ''}"
        st.rerun()
    if c2.button("Cancel", width="stretch"):
        st.rerun()
    if not ready:
        st.caption("Name the new category to create the label." if kind == "OTHER" else "Choose what it is.")


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
    if not is_flag and not item.nick_target:
        kinds = categories(sess, res, keep=item.type)
        st.selectbox("This is a", kinds, index=kinds.index(item.type), format_func=cat_name,
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
    if not is_flag and c3.button("Don't hide it", width="stretch",
                                 help="Not a real name? It will appear as written in every file."):
        delete_entity(sess, item)
        st.rerun()
    if not is_flag and len(forms) > 1:
        with st.expander(not_all_same(item.type)):
            split_controls(sess, res, item, f"ed-split-{ref}")


STATUS_TEXT = {"redacted": "hidden", "generalized": "still showing (generalized)", "kept": "kept",
               "flagged": "flagged to check", "changed": "changed", "partly redacted": "partly hidden",
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
    q = st.text_input("Search the files", key="doc_search", label_visibility="collapsed",
                      placeholder="🔍 Search every file for a word or phrase…")
    if not q.strip():
        return
    hits = search(res, q)
    labelled = [e for e in res.entities if q.strip().lower() in e.label.lower() and e.label not in ("Kept", "")]
    for e in labelled[:3]:
        st.caption(f"“{e.label}” stands for {e.canonical} — {len(e.mentions)}× in "
                   f"{(nf := len({m[0] for m in e.mentions}))} file{'s' if nf != 1 else ''}.")
    if not hits:
        if not labelled:
            st.caption(f"“{q.strip()}” doesn't appear in any original file.")
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
    if showing and st.button(f"Hide every “{q.strip()}”", key="search_redact", type="primary"):
        redact_dialog(sess, res, {"text": q.strip()})


def documents(sess: Session, res) -> None:
    n = len(res.names)
    approved = sum(is_current(sess, res, i) for i in range(n))
    st.session_state.setdefault("doc_pick", 0)
    if st.session_state.doc_pick not in range(n):
        st.session_state.doc_pick = 0

    def step(delta: int) -> None:
        st.session_state.doc_pick = (st.session_state.doc_pick + delta) % n

    bearing(approved, n, "Files", "approved")
    d1, d2, d3 = st.columns([5.6, .6, .6], vertical_alignment="bottom")
    # A dropdown scales to large sets (type to search); ✓ marks approved documents.
    doc = d1.selectbox("Document", list(range(n)), key="doc_pick", label_visibility="collapsed",
                       format_func=lambda i: f"{'✓ ' if is_current(sess, res, i) else ''}{res.names[i]}")
    d2.button("←", on_click=step, args=(-1,), help="Previous file", width="stretch", key="prev_doc")
    d3.button("→", on_click=step, args=(1,), help="Next file", width="stretch", key="next_doc")

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
        (st.error if w["level"] == "block" else st.caption)(("Blocks download — " if w["level"] == "block" else "Note: ")
                                                            + w["message"])
    if edit:
        st.markdown('<p class="edit-hint">Select any text to hide it. (Click any label to change it — that works '
                    'any time.)</p>', unsafe_allow_html=True)

    def head(d, v):
        btn = (f'<button class="pane-edit{" on" if edit else ""}" data-act="toggle-edit">'
               f'{"✓ Done" if edit else "✎ Hide"}</button>') if v == "anonymized" else ""
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
            owner = hidden_owner(res, " ".join(action["text"].split()))
            if owner is not None:  # already hidden: the same "Change label" as clicking the highlight
                edit_dialog(sess, res, "E|" + owner.key)
            else:
                redact_dialog(sess, res, action)
        elif action["kind"] == "edit":
            edit_dialog(sess, res, action["ref"])


    # Approval: one clear primary action; downloads live on ④ Download.
    st.markdown('<div class="ll-rule"></div>', unsafe_allow_html=True)
    stale = doc in sess.approved and not is_current(sess, res, doc)
    open_here = [k for k in res.queue if res.item(k).status == "pending"
                 and any(m[0] == doc for m in res.item(k).mentions)]
    a1, a2 = st.columns([1.7, 2.8], vertical_alignment="center")
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
                st.session_state.focus_note = "Every file is approved — ④ Download is next, at the bottom"

        a1.button("✓ Approve again" if stale else ("✓ Approve and open next" if approved < n - 1 else "✓ Approve"),
                  type="primary", width="stretch", key=f"approve_{doc}", on_click=approve_and_advance)
    with a2:  # the count again where you approve, so you don't scroll back up to see what's left
        bearing(approved, n, "Files", "approved", tight=True,
                note=f'{len(open_here)} item{"s" if len(open_here) != 1 else ""} in this file still '
                     f'need{"s" if len(open_here) == 1 else ""} a look' if open_here else "")
    if stale:
        st.warning("Changed since you approved it. Approve it again to update the download.")
    release_box(sess, res)

    with st.expander("Every change in this file"):
        seen = {}
        for g in res.segments[doc]:
            if g.kind != "tidy":
                seen.setdefault((g.original, g.replacement), type_name(g.type))
        st.dataframe(pd.DataFrame([{"Original": o, "Shown as": r, "Type": t} for (o, r), t in seen.items()]),
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
         else f"{len(cc['unreviewed'])} item{'s' if len(cc['unreviewed']) != 1 else ''} still "
              f"need{'s' if len(cc['unreviewed']) == 1 else ''} a look"),
        (cc["leak_free"], "No original names left" if cc["leak_free"] else "Some original text is still showing"),
        (current == n, f"{current} of {n} files approved"),
    ]
    ready = all(ok for ok, _ in checks)
    st.markdown(f'<h2>{"Ready to share" if ready else "Almost ready"}</h2><div class="check">' + "<br>".join(
        f'<span class="{"ok" if ok else "warn"}">{"✓" if ok else "○"}</span>&nbsp; {html.escape(t)}' for ok, t in checks)
        + "</div>", unsafe_allow_html=True)
    blocked = bool(release_box(sess, res))   # always visible, so leaks surface before anything is approved
    if items:
        names = [res.names[d] for d, _ in items]
        outputs = [a["output"] for _, a in items]
        combined = "\n\n".join(f"{'=' * 72}\n{name}\n{'=' * 72}\n\n{out}" for name, out in zip(names, outputs))
        st.markdown('<div style="height:.5rem"></div>', unsafe_allow_html=True)
        st.session_state.setdefault("dl_format", "pdf")
        fmt = st.segmented_control("File type", list(FORMATS), key="dl_format",
                                   format_func=lambda f: FORMATS[f][0]) or "pdf"
        c1, c2, _ = st.columns([1.6, 1.6, 1.2])
        # Built when clicked, not on every rerun: a PDF of a long set takes a moment.
        c1.download_button(f"Download approved files (.zip, {len(items)})", lambda: buyer_zip(names, outputs, fmt),
                           "anonymized_package.zip", "application/zip", type="primary", disabled=blocked,
                           width="stretch")
        c2.download_button(f"As one combined {FORMATS[fmt][0]} file",
                           lambda: render_file(list(zip(names, outputs)), fmt), f"anonymized_package.{fmt}",
                           FORMATS[fmt][1], disabled=blocked, width="stretch")
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
    library_offer(sess, res)


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


# --------------------------------------------------------------------------- library (presentation prototype)
# A saved setup: the settings and rule switches, plus the labels and corrections made by hand. Entries live in
# this browser tab's session only (no database); "Use again" applies one to the next set that's started.
LIBRARY_OPS = ("labels", "decisions", "retype", "manual", "merges", "splits", "deleted", "aliases", "parties")
LIBRARY_EXAMPLES = [  # invented names; keys match the bundled 8-Ks, so "Use again" + "Try the sample case" shows it
    {"name": "Project Granite — merger 8-Ks", "date": "Sep 30, 2026",
     "settings": {"disabled": ["MONEY", "SHARE_COUNT"]},
     "ops": {"labels": {"ORGANIZATION:microsoft": "Acquirer", "ORGANIZATION:activision blizzard": "Target",
                        "ORGANIZATION:anchorage merger sub": "Merger Sub"}}},
    {"name": "Item 5.02 officer-change review", "date": "Aug 12, 2026",
     "settings": {"disabled": ["URL", "flag.group_inference"], "date_mode": "relative"},
     "ops": {"labels": {"PERSON:robert kotick": "Departing CEO", "PERSON:luci altman": "Company signatory"}}},
]


def library_entry(name: str, when: str, policy, settings: dict, ops: dict) -> dict:
    import copy
    disabled = settings.get("disabled") or []
    return {"name": name, "date": when, "policy": policy.name, "settings": copy.deepcopy(settings),
            "ops": {k: copy.deepcopy(ops[k]) for k in LIBRARY_OPS if ops.get(k)},
            "toggles_on": sum(bid not in disabled for _, bid, _, _ in policy.buckets())}


def library_entries() -> list[dict]:
    if "library" not in st.session_state:
        finance = load_policy(available_policies()["Finance"])
        st.session_state.library = [library_entry(x["name"], x["date"], finance, x["settings"], x["ops"])
                                    for x in LIBRARY_EXAMPLES]
    return st.session_state.library


def library_summary(entry: dict) -> str:
    ops = entry["ops"]
    n_labels = len(ops.get("labels", {}))
    fixes = sum(len(ops.get(k, ())) for k in LIBRARY_OPS if k not in ("labels", "parties"))
    return (f'{entry["toggles_on"]} rules on · {n_labels} custom label{"s" if n_labels != 1 else ""}'
            + (f' · {fixes} correction{"s" if fixes != 1 else ""}' if fixes else ""))


def manual_changes(sess: Session, res) -> list[tuple[str, str, str, object]]:
    """Everything changed by hand in this set, one line each: (id, what it says, ops key, which entries). The same
    change made twice ("emerging" and "Emerging" hidden as Person A) is one line."""
    by_key = {e.key: e for e in res.entities}
    name = lambda k: by_key[k].canonical if k in by_key else deleted_text(k)
    label_of = lambda k: by_key[k].label if k in by_key else deleted_text(k)
    flat = lambda t: " ".join(str(t).split())
    ops, out = sess.ops, []

    def grouped(key: str, what, same):
        """One line per distinct change in a list op; the line covers every index that's the same change."""
        lines: dict = {}
        for i, entry in enumerate(ops.get(key) or []):
            lines.setdefault(same(entry), [what(entry), []])[1].append(i)
        for n, (text, idx) in enumerate(lines.values()):
            out.append((f"{key}:{n}", text, key, idx))

    for k, v in (ops.get("labels") or {}).items():
        out.append((f"labels:{k}", f"Label: {name(k)} → {v}", "labels", k))
    grouped("parties", lambda p: f"Always hide: {flat(p['name'])}", lambda p: flat(p["name"]).casefold())
    grouped("manual", lambda m: f"Hidden by hand: {flat(m['text'])}"
            + (f" ({cat_name(m.get('type'))})" if (m.get("type") or "MANUAL") != "MANUAL" else ""),
            lambda m: (flat(m["text"]).casefold(), m.get("type") or "MANUAL"))
    grouped("aliases", lambda a: f"Hidden as {label_of(a[0])}: {flat(a[1])}", lambda a: (a[0], flat(a[1]).casefold()))
    grouped("deleted", lambda k: f"Not a name, left as written: {deleted_text(k)}", lambda k: deleted_text(k).casefold())
    for k, t in (ops.get("retype") or {}).items():
        out.append((f"retype:{k}", f"{name(k)} is {a_noun(t)}", "retype", k))
    grouped("merges", lambda m: f"{deleted_text(m[0])} is the same as {label_of(m[1])}", lambda m: (m[0], m[1]))
    grouped("splits", lambda op: f"Separated “{op[1]}” from {label_of(op[0])}", lambda op: (op[0], flat(op[1]).casefold()))
    return out


def send_to_library(sess: Session, items: list) -> None:
    """Save what's ticked: the settings, your ② Check answers, and the edits picked under Entities."""
    import copy
    from datetime import date
    picked = [it for it in items if st.session_state.get(f"libpick_{it[0]}", True)]
    ops: dict = {}
    for _, _, key, ref in picked:
        if key == "decisions":
            ops["decisions"] = copy.deepcopy(sess.ops["decisions"])
        elif key in ("labels", "retype"):
            ops.setdefault(key, {})[ref] = sess.ops[key][ref]
        elif key != "settings":
            ops.setdefault(key, []).extend(copy.deepcopy(sess.ops[key][i]) for i in ref)
    settings = sess.settings if any(key == "settings" for _, _, key, _ in picked) else {}
    name = " ".join(st.session_state.get("lib_name", "").split()) or f"{sess.policy.name} set"
    d = date.today()
    library_entries().insert(0, library_entry(name, f"{d:%b} {d.day}, {d.year}", sess.policy, settings, ops))
    st.session_state.focus_note = f"Saved to Library: “{name}”"


def set_lib_picks(ids: list[str], value: bool) -> None:
    for iid in ids:
        st.session_state[f"libpick_{iid}"] = value


def library_offer(sess: Session, res) -> None:
    """After the label key: would you like to send this to the Library for reuse? Tick the settings and your
    answers; under Entities, every edit is listed, all ticked, to untick what you don't want to keep."""
    from datetime import date
    st.markdown('<div class="ll-rule"></div>', unsafe_allow_html=True)
    label("Library")
    st.markdown('<p class="lib-ask">Would you like to send this to the Library for reuse?</p>', unsafe_allow_html=True)
    st.caption("Tick what to keep. “Use again” starts your next set with it.")
    disabled = sess.settings.get("disabled") or []
    on = sum(bid not in disabled for _, bid, _, _ in sess.policy.buckets())
    top = [("settings", f"Settings and rule switches ({on} on)", "settings", None)]
    if sess.ops.get("decisions"):
        top.append(("decisions", f"Your answers in ② Check ({len(sess.ops['decisions'])})", "decisions", None))
    edits = manual_changes(sess, res)
    for iid, text, _, _ in top:
        st.session_state.setdefault(f"libpick_{iid}", True)
        st.checkbox(text, key=f"libpick_{iid}")
    if edits:
        ids = [iid for iid, _, _, _ in edits]
        for iid in ids:
            st.session_state.setdefault(f"libpick_{iid}", True)
        n_on = sum(bool(st.session_state.get(f"libpick_{iid}")) for iid in ids)
        with st.expander(f"Entities you edited ({n_on} of {len(ids)} ticked)"):
            st.session_state.lib_all = n_on == len(ids)
            st.checkbox("Select all", key="lib_all", on_change=lambda: set_lib_picks(ids, st.session_state.lib_all))
            for iid, text, _, _ in edits:
                st.checkbox(text, key=f"libpick_{iid}")
    else:
        st.caption("You didn't edit any names or labels in this set, so only the settings and answers are kept.")
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    d = date.today()
    c1.text_input("Name", f"{sess.policy.name} set · {d:%b} {d.day}", key="lib_name",
                  label_visibility="collapsed")
    c2.button("Send to Library", type="primary", width="stretch", key="lib_send", on_click=send_to_library,
              args=(sess, top + edits))


def use_library_entry(i: int, start_over: bool = False) -> None:
    """Arm an entry for the next set (after closing the open one, if asked) and go to the upload page."""
    if start_over:
        reset()
    st.session_state.library_use = library_entries()[i]
    st.session_state.page = "Anonymize"


def apply_library_entry(sess: Session, entry: dict) -> None:
    import copy
    sess.settings.update(copy.deepcopy(entry["settings"]))
    sess.ops.update(copy.deepcopy(entry["ops"]))
    for k in [k for k in st.session_state if k.startswith(("b_", "t_"))]:
        st.session_state.pop(k, None)  # the sidebar switches pick the saved values up again
    st.session_state.focus_note = f"Using “{entry['name']}” from the Library — its settings and corrections are on"


@st.dialog("Use this setup for a new set?")
def confirm_use_again(i: int) -> None:
    st.write(f"“{library_entries()[i]['name']}” will be used for your next files. This closes the current set — "
             "download what you need first.")
    c1, c2 = st.columns(2)
    if c1.button("Yes, start a new set", type="primary", width="stretch", on_click=use_library_entry, args=(i, True)):
        st.rerun()
    if c2.button("Cancel", width="stretch"):
        st.rerun()


def library_view(sess: Session | None) -> None:
    st.html("<style>.lib-name { font-family:var(--serif); font-size:1.2rem; line-height:1.3; margin:.1rem 0 .3rem; }"
            ".lib-meta { font-size:.85rem; color:var(--dim); } .lib-sum { font-size:.95rem; margin:.7rem 0 .2rem; }"
            "</style>")
    st.markdown('<h2>Library</h2>', unsafe_allow_html=True)
    st.caption("Saved setups. Use one again and your next set starts with its rule switches, labels and corrections.")
    entries = library_entries()
    cols = st.columns(3)
    for i, entry in enumerate(entries):
        with cols[i % 3].container(border=True):
            st.markdown(f'<div class="lib-name">{html.escape(entry["name"])}</div>'
                        f'<div class="lib-meta">{html.escape(entry["date"])} · {html.escape(entry["policy"])} rules</div>'
                        f'<div class="lib-sum">{library_summary(entry)}</div>', unsafe_allow_html=True)
            if sess is None:
                st.button("Use again", key=f"lib_use_{i}", width="stretch", on_click=use_library_entry, args=(i,))
            elif st.button("Use again", key=f"lib_use_{i}", width="stretch"):
                confirm_use_again(i)
    st.caption("Prototype: the Library is kept for this browser tab only — refreshing resets it.")


def library_banner() -> None:
    entry = st.session_state.get("library_use")
    if entry:
        st.info(f"Using **{entry['name']}** from the Library ({library_summary(entry)}). It's applied when you "
                "anonymize your files or try the sample case.")


# --------------------------------------------------------------------------- step navigation
STEP_NAMES = {"Start": "① Files", "Review": "② Check", "Documents": "③ Read & approve", "Final": "④ Download"}


def go_to(view: str, doc: int | None = None) -> None:
    st.session_state.view = view
    if doc is not None:
        st.session_state.doc_pick = doc
    st.session_state.scroll_top = True  # the new step opens at its top, not where the old one was scrolled to


def step_nav(view: str, done: bool = False, todo_doc: int | None = None) -> None:
    """← Back / Next → at the bottom of every step: the only buttons that change step, so none is repeated on the
    page. Next turns blue once this step is done (everything checked, every file approved)."""
    i = VIEWS.index(view)
    st.markdown('<div class="ll-rule"></div>', unsafe_allow_html=True)
    with st.container(key="stepnav"):
        c1, c2 = st.columns(2)
        if i > 0:
            c1.button(f"← Back: {STEP_NAMES[VIEWS[i - 1]]}", key="nav_back", width="stretch", on_click=go_to,
                      args=(VIEWS[i - 1], todo_doc if VIEWS[i - 1] == "Documents" else None))
        if i + 1 < len(VIEWS):
            c2.button(f"Next: {STEP_NAMES[VIEWS[i + 1]]} →", key="nav_next", width="stretch", on_click=go_to,
                      args=(VIEWS[i + 1],), type="primary" if done else "secondary")
    if st.session_state.pop("scroll_top", False):
        import time
        st.html(f"<script>/* {time.time()} */ document.querySelector('[data-testid=\"stMain\"]')"
                "?.scrollTo({top: 0});</script>", unsafe_allow_javascript=True)


# --------------------------------------------------------------------------- main
inject_css()
sess: Session | None = st.session_state.get("session")
if sess is not None and (type(sess) is not Session or getattr(sess, "VERSION", 0) != Session.VERSION):
    # The app was updated while this browser session was open: rebuild the set with the new engine,
    # keeping every decision, instead of crashing on the old object.
    with st.spinner("The app was updated — re-checking your files with the new rules…"):
        sess = st.session_state.session = Session.upgrade(sess)
    st.toast("Updated to the latest rules — your decisions were kept")
header()
st.session_state.my_rules = my_rules()  # mounted every run, so a save/delete from anywhere is applied
if sess is not None and st.session_state.get("library_use"):  # the first run of a set started after "Use again"
    apply_library_entry(sess, st.session_state.pop("library_use"))
st.tabs(["Anonymize", "Library"], key="page", on_change="rerun")  # the tab bar; each page renders below it
if st.session_state.get("page") == "Library":
    library_view(sess)
elif sess is None:
    library_banner()
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
        st.session_state.view = "Start"
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
        if view in ("Review", "Documents"):  # where the edits and approvals happen
            s2.button("↶ Undo", key="undo_top", on_click=undo, disabled=not can_undo, width="stretch",
                      help=f"Undo: {sess.last_change}" if can_undo else "Nothing to undo yet")
    # Ask before leaving: nothing is saved, so a refresh would wipe the work.
    st.html("<script>window.onbeforeunload = (e) => { e.preventDefault(); e.returnValue = ''; };</script>",
            unsafe_allow_javascript=True)
    # ⌘Z / Ctrl+Z presses ↶ Undo, except while typing in a field (which keeps its own undo).
    st.html("""<script>if (!window.__llUndoKey) { window.__llUndoKey = true;
      document.addEventListener('keydown', (e) => {
        if (e.key.toLowerCase() !== 'z' || !(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return;
        const a = document.activeElement;
        if (a && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))) return;
        const b = [...document.querySelectorAll('button')].find((x) => x.textContent.trim() === '↶ Undo' && !x.disabled);
        if (b) { e.preventDefault(); b.click(); }
      }); }</script>""", unsafe_allow_javascript=True)
    st.markdown('<div style="height:.6rem"></div>', unsafe_allow_html=True)
    {"Start": start_view, "Review": review, "Documents": documents, "Final": final_view}[view](sess, res)
    res = sess.run()  # a click on this step may have changed what's done
    n_ok = sum(is_current(sess, res, i) for i in range(len(res.names)))
    todo_doc = next((i for i in range(len(res.names)) if not is_current(sess, res, i)), None)
    done = {"Start": True, "Review": not any(res.item(k).status == "pending" for k in res.queue),
            "Documents": n_ok == len(res.names)}.get(view, False)
    step_nav(view, done, todo_doc)
