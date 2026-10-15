"""Smoke test the Streamlit UI end to end (headless): every view renders, the release gate holds."""
import pathlib

from streamlit.testing.v1 import AppTest

APP = str(pathlib.Path(__file__).resolve().parent.parent / "app.py")


def test_sample_flow_all_views():
    at = AppTest.from_file(APP, default_timeout=180).run()
    assert not at.exception
    next(b for b in at.button if b.label == "Try the sample case").click().run()
    assert not at.exception
    sess = at.session_state.session
    for view in ("Start", "Review", "Documents", "Final"):
        at.session_state.view = view
        at.run()
        assert not at.exception, (view, at.exception)
    # Approve everything while items are still open: the package download stays locked…
    at.session_state.view = "Final"
    at.run()
    for i in range(len(sess.names)):
        sess.approve(i, sess.run())
    at.run()
    zips = [d for d in at.get("download_button") if "Download approved files" in d.proto.label]
    assert zips and zips[0].proto.disabled
    # …and unlocks once everything is checked (approvals are refreshed for the final wording)
    for k in sess.run().queue:
        sess.decide(k, "accept")
    for i in range(len(sess.names)):
        sess.approve(i, sess.run())
    at.run()
    assert not at.exception, at.exception
    labels = [d.proto.label for d in at.get("download_button") if not d.proto.disabled]
    assert any("Download approved files" in l for l in labels) and any("combined" in l for l in labels)
    assert any("Ready to share" in m.value for m in at.markdown)
    # ↶ Undo lives where edits and approvals happen, and reverses the most recent one — here an approval
    assert not any(b.label == "↶ Undo" for b in at.button)
    at.session_state.view = "Documents"
    at.run()
    sess.unapprove(2)
    sess.approve(2, sess.run())
    before = len(sess.undo_stack)
    next(b for b in at.button if b.label == "↶ Undo").click().run()
    assert not at.exception and len(at.session_state.session.undo_stack) == before - 1
    assert 2 not in at.session_state.session.approved and sess.last_change == "Move 03_settlement_order.txt back"


def test_add_documents_keeps_pseudonyms():
    from engine import Session
    root = pathlib.Path(APP).parent / "tests" / "fixtures" / "sample_case"
    files = sorted(root.glob("*.txt"))
    s = Session.from_files([(f.name, f.read_bytes()) for f in files[:1]])
    first = {e.key: e.label for e in s.run().entities if e.type == "PERSON"}
    s.add_files([(f.name, f.read_bytes()) for f in files[1:]])
    after = {e.key: e.label for e in s.run().entities if e.type == "PERSON"}
    assert all(after.get(k) == v for k, v in first.items())
    assert len(s.names) == len(files)


def test_upload_needs_rights_checkbox():
    at = AppTest.from_file(APP, default_timeout=180).run()
    btn = next(b for b in at.button if b.label == "Anonymize my files")
    assert btn.disabled


def test_old_session_is_upgraded_not_crashing():
    """A viewer's session created by an older app version (no warnings/VERSION) is rebuilt, not a crash."""
    from types import SimpleNamespace
    root = pathlib.Path(APP).parent / "tests" / "fixtures" / "sample_case"
    files = sorted(root.glob("*.txt"))
    old = SimpleNamespace(names=[f.name for f in files], texts=[f.read_text() for f in files],
                          ops={"decisions": {}, "history": []}, approved={}, policy=None, settings={})
    at = AppTest.from_file(APP, default_timeout=180)
    at.session_state.session = old
    at.session_state.view = "Documents"
    at.run()
    assert not at.exception, at.exception
    from engine import Session
    assert getattr(at.session_state.session, "VERSION", None) == Session.VERSION


def test_release_box_lists_leaks_with_show_and_fix():
    from engine import Session
    txt = ("Jonathan Mercer opened the account. Mercer later cited SEC v. Mercer, 401 F.2d 833 (2d Cir. 1968) "
           "in his defense.")
    sess = Session.from_texts([("memo.txt", txt)])
    sess.rights_confirmed = True
    for k in sess.run().queue:
        sess.decide(k, "accept")
    at = AppTest.from_file(APP, default_timeout=180)
    at.session_state.session = sess
    at.session_state.view = "Final"
    at.run()
    assert not at.exception, at.exception
    assert any("Where the original text is still showing" in m.value for m in at.markdown)
    show = next(b for b in at.button if b.label == "Show")
    fix = next(b for b in at.button if b.label == "Hide it")
    fix.click().run()
    assert not at.exception, at.exception
    assert "Mercer" not in at.session_state.session.run().outputs[0]
    assert show is not None


def test_search_panel_and_entity_search():
    at = AppTest.from_file(APP, default_timeout=180).run()
    next(b for b in at.button if b.label == "Try the sample case").click().run()
    at.session_state.view = "Documents"
    at.run()
    at.text_input(key="doc_search").set_value("Mercer").run()
    assert not at.exception, at.exception
    assert any("match" in m.value and "still showing" in m.value for m in at.markdown)
    assert [b for b in at.button if b.label == "Show"]
    at.text_input(key="ent_search").set_value("okafor").run()
    assert not at.exception, at.exception
    assert any("match" in c.value for c in at.sidebar.caption)


def test_session_from_previous_engine_is_rebuilt_even_with_matching_version():
    """The crash from the live app: a session object from the previous engine (no can_undo / undo_stack) that
    still reports the current VERSION must be rebuilt, not used."""
    from types import SimpleNamespace
    from engine import Session
    root = pathlib.Path(APP).parent / "tests" / "fixtures" / "sample_case"
    files = sorted(root.glob("*.txt"))
    stale = SimpleNamespace(VERSION=Session.VERSION, names=[f.name for f in files], texts=[f.read_text() for f in files],
                            ops={"decisions": {}, "history": []}, approved={}, policy=None, settings={},
                            warnings=[[] for _ in files], rights_confirmed=True)
    at = AppTest.from_file(APP, default_timeout=180)
    at.session_state.session = stale
    at.session_state.view = "Review"
    at.run()
    assert not at.exception, at.exception
    rebuilt = at.session_state.session
    assert hasattr(rebuilt, "undo_stack") and rebuilt.rights_confirmed
    for view in ("Start", "Documents", "Final"):
        at.session_state.view = view
        at.run()
        assert not at.exception, (view, at.exception)


def test_stale_engine_module_is_reloaded():
    """If the server kept the previous engine loaded after a deploy, app.py reloads it from disk."""
    import engine
    real = engine._CODE_VERSION
    engine._CODE_VERSION = "stale-build"
    try:
        at = AppTest.from_file(APP, default_timeout=180).run()
        assert not at.exception, at.exception
        import sys
        assert sys.modules["engine"]._CODE_VERSION == real
    finally:
        sys_engine = __import__("sys").modules["engine"]
        if sys_engine._CODE_VERSION == "stale-build":
            sys_engine._CODE_VERSION = real


def test_rules_from_the_last_set_are_used_again():
    # "Start over" stores the session's rules in carry_rules (checked in the browser; AppTest can't click inside
    # a dialog). The home page then offers them first and the next set starts with them.
    at = AppTest.from_file(APP, default_timeout=180).run()
    next(b for b in at.button if b.label == "Try the sample case").click().run()
    sess = at.session_state.session
    sess.settings["placeholder_style"] = "redacted"
    sess.add_party("Northwind Holdings", "ORGANIZATION")
    carried = sess.export_rules(sess.policy.name)
    at = AppTest.from_file(APP, default_timeout=180)
    at.session_state.carry_rules = carried
    at.session_state.carry_from = "01_complaint.txt + 2 more"  # which set the rules came from
    at.run()
    assert not at.exception
    assert at.session_state.rules_pick == "Your rules from 01_complaint.txt + 2 more"
    next(b for b in at.button if b.label == "Try the sample case").click().run()
    assert not at.exception
    again = at.session_state.session
    assert again.settings["placeholder_style"] == "redacted"
    assert any(p["name"] == "Northwind Holdings" for p in again.ops["parties"])


def test_review_all_at_once_and_bulk_approve():
    at, _ = _sample_app()
    assert not any(e.label == "Quick tips" for e in at.expander)
    sess = at.session_state.session
    pending = [k for k in sess.run().queue if sess.run().item(k).status == "pending"]
    assert pending and len(at.checkbox) >= len(pending)          # every open item on one page
    undo_before = len(sess.undo_stack)
    next(b for b in at.button if b.label.startswith("✓ Approve") and "selected" in b.label).click().run()
    assert not at.exception
    left = [k for k in sess.run().queue if sess.run().item(k).status == "pending"]
    # Everything ticked is approved in one step (sentences that need your wording stay open)…
    assert all(sess.run().item(k).rule == "quasi.combination" for k in left)
    assert len(sess.undo_stack) == undo_before + 1                 # …and one ↶ Undo reverses the batch
    sess.undo()
    assert len([k for k in sess.run().queue if sess.run().item(k).status == "pending"]) == len(pending)


def test_open_item_from_list_and_return():
    at, sess = _sample_app()
    second = [k for k in sess.run().queue if sess.run().item(k).status == "pending"][1]
    at.button(key=f"open_{second}").click().run()
    assert at.session_state.review_mode == "one" and at.session_state.review_pick == second
    keep = ("Keep as written", "No, keep the name", "It's fine as is", "No, someone else", "I have consent — keep it")
    next(b for b in at.button if b.label in keep).click().run()
    assert not at.exception
    assert sess.run().item(second).status != "pending" and at.session_state.review_mode == "list"


def test_documents_progress_and_no_next_to_approve():
    at = AppTest.from_file(APP, default_timeout=180).run()
    next(b for b in at.button if b.label == "Try the sample case").click().run()
    at.session_state.view = "Documents"
    at.run()
    assert not at.exception
    assert not any(b.label == "Next to approve" for b in at.button)
    assert sum("0 of 3 approved" in m.value for m in at.markdown) == 2   # top and next to the approve button


def _sample_app(view: str = "Review"):
    at = AppTest.from_file(APP, default_timeout=180).run()
    next(b for b in at.button if b.label == "Try the sample case").click().run()
    assert at.session_state.view == "Start"                      # a new set opens on ① Files
    at.session_state.view = view
    return at.run(), at.session_state.session


def _helpers():
    """app.py's plain functions (run outside a server, so Streamlit calls are no-ops)."""
    import logging
    import runpy
    logging.disable(logging.WARNING)
    try:
        return runpy.run_path(APP, run_name="app_helpers")
    finally:
        logging.disable(logging.NOTSET)


def test_sidebar_names_open_inline_with_one_question():
    at, sess = _sample_app()
    assert not at.exception
    # names no longer open in floating popovers (only Settings' two short ones remain)
    assert len(at.sidebar.get("popover")) == 2
    assert any(e.label == "Settings" for e in at.sidebar.expander)   # no ⚙
    crd = next(e for e in sess.run().entities if e.type == "CRD_NUMBER")
    at.sidebar.button(key=f"ent-{crd.key}").click().run()
    act = at.sidebar.selectbox(key=f"act-{crd.key}")
    assert act.options == ["Move to another category", "Belongs in another label", "Don't hide it"]
    act.set_value("move").run()
    at.sidebar.selectbox(key=f"cat-{crd.key}").set_value("ACCOUNT_NUMBER").run()
    at.sidebar.button(key=f"catgo-{crd.key}").click().run()
    assert not at.exception, at.exception
    assert sess.run().entity(crd.key).type == "ACCOUNT_NUMBER"
    assert any("now an account number" in t.value for t in at.toast)


def test_a_category_you_made_is_offered_in_the_dropdowns():
    at, sess = _sample_app()
    sess.redact("47", "OTHER:Age", "", "Age {1}")
    at.run()
    assert "Age" in at.sidebar.selectbox(key="sb_parties_kind").options
    okafor = next(e for e in sess.run().entities if e.canonical == "Daniel K. Okafor")
    at.sidebar.button(key=f"ent-{okafor.key}").click().run()
    at.sidebar.selectbox(key=f"act-{okafor.key}").set_value("split").run()
    dest = at.sidebar.selectbox(key=f"sb-{okafor.key}_dest").options
    assert "A new person" in dest and "A new age" in dest and "A new CRD number" in dest


def test_repeated_category_and_likely_label():
    h = _helpers()
    from engine import Session
    s = Session.from_texts([(p.name, p.read_text()) for p in
                            sorted((pathlib.Path(APP).parent / "tests" / "fixtures" / "sample_case").glob("*.txt"))])
    s.redact("47", "OTHER:Age", "", "Age {1}")
    res = s.run()
    assert h["existing_category"](s, res, "age") == "OTHER:Age"          # typed again: it's the same field
    assert h["existing_category"](s, res, " Ages ") == "OTHER:Age"
    assert h["existing_category"](s, res, "account number") == "ACCOUNT_NUMBER"
    assert h["existing_category"](s, res, "Fund name") is None
    assert h["likely_label"](res, "D. Okafor").canonical == "Daniel K. Okafor"
    assert h["likely_label"](res, "Capital Partners") is None            # generic words alone don't count
    assert [h["a_noun"](t) for t in ("ACCOUNT_NUMBER", "US_SSN", "CRD_NUMBER", "PERSON")] == [
        "an account number", "an SSN", "a CRD number", "a person"]


def test_back_and_next_at_the_bottom_of_every_step():
    at, _ = _sample_app()
    for view, back, nxt in (("Start", False, True), ("Review", True, True), ("Documents", True, True),
                            ("Final", True, False)):
        at.session_state.view = view
        at.run()
        labels = [b.label for b in at.button]
        assert any(l.startswith("← Back") for l in labels) == back, view
        assert any(l.startswith("Next:") for l in labels) == nxt, view
    at.session_state.view = "Review"
    at.run()
    at.button(key="nav_next").click().run()
    assert at.session_state.view == "Documents" and not at.exception
    # the note under the progress bar is part of it (same size and spacing as the row above)
    assert any('class="fix-note"' in m.value and "still need" in m.value for m in at.markdown)


def test_labels_switch_in_settings_changes_every_label():
    at, sess = _sample_app()
    assert at.sidebar.button_group[0].value == "context" and sess.settings["role_labels"]
    names = lambda: " ".join(b.label for b in at.sidebar.button)
    assert "Respondent" in names()
    at.sidebar.button_group[0].set_value("simple").run()
    assert not at.exception and sess.settings["role_labels"] is False
    assert "Respondent" not in names() and "Person" in names()   # the sidebar list follows the same run


def test_found_in_filters_the_spellings_by_document():
    at, sess = _sample_app()
    okafor = next(e for e in sess.run().entities if e.canonical == "Daniel K. Okafor")
    at.sidebar.button(key=f"ent-{okafor.key}").click().run()
    assert "Hidden automatically" in [c.value for c in at.sidebar.caption]   # no "Person ·" in front
    found = at.sidebar.selectbox(key=f"in-{okafor.key}")
    assert found.options[0].startswith("All 3 files")
    forms = lambda: next(m.value for m in at.sidebar.markdown if "class='form'" in m.value)
    assert "×6" in forms()                                   # "Okafor" ×6 across the set
    found.set_value(0).run()                                 # 01_complaint.txt only
    assert "×6" not in forms() and forms().count("×1") == 2


def test_select_all_tick_box_replaces_all_and_none():
    at, sess = _sample_app()
    assert not any(b.label in ("All", "None") for b in at.button)
    pending = [k for k in sess.run().queue if sess.run().item(k).status == "pending"]
    sel = at.checkbox(key="sel_all")
    assert sel.label == "Select all" and not sel.value          # sentences needing your wording start unticked
    sel.check().run()
    assert all(at.session_state[f"pick_{k}"] for k in pending)
    at.checkbox(key="sel_all").uncheck().run()                   # click again: the box and every row empty
    assert not any(at.session_state[f"pick_{k}"] for k in pending)
    assert not at.checkbox(key="sel_all").value


def test_bundled_sample_is_three_sec_filings(monkeypatch):
    """The real "Try the sample case": Microsoft's 8-K announcing the Activision Blizzard deal, the first quarter of
    the merger proxy, and Activision Blizzard's 8-K closing it — in date order, named the way a deal team files them."""
    monkeypatch.setenv("ANONYMIZER_SAMPLES", str(pathlib.Path(APP).parent / "samples"))
    at = AppTest.from_file(APP, default_timeout=180).run()
    next(b for b in at.button if b.label == "Try the sample case").click().run()
    assert not at.exception, at.exception
    sess = at.session_state.session
    assert sess.names == ["1 MSFT 8-K 2022-01-18 - Activision Blizzard deal announced.pdf",
                          "2 ATVI PREM14A 2022 - merger proxy statement (pp. 1-57).pdf",
                          "3 ATVI 8-K 2023-10-13 - Microsoft merger completed.pdf"]
    assert sum(len(t) for t in sess.texts) < 300_000          # the proxy is cut to a quarter: the demo starts fast
    res0 = sess.run()
    assert [k for k in res0.queue if res0.item(k).status == "pending"]   # the proxy gives ② Check real items
    res = sess.run()
    for name in ("Microsoft Corporation", "Activision Blizzard, Inc.", "Keith R. Dolliver", "91-1144442"):
        assert all(name not in out for out in res.outputs), name
    for view in ("Start", "Review", "Documents", "Final"):
        at.session_state.view = view
        at.run()
        assert not at.exception, (view, at.exception)


def test_change_label_helpers_suggest_route_and_guess_carefully():
    h = _helpers()
    from engine import Session
    root = pathlib.Path(APP).parent
    s = Session.from_files([(p.name, p.read_bytes()) for p in sorted((root / "samples").glob("*.pdf"))])
    res = s.run()
    addr = next(e for e in res.entities if e.type == "STREET_ADDRESS" and "Microsoft Way" in e.canonical)
    company = next(e for e in res.entities if e.canonical == "Microsoft Corporation")
    # a plainer label to offer: the category alone for an address, a letter for a company
    assert h["suggested_labels"](res, addr) == ["Address"]
    assert h["suggested_labels"](res, company)[0].startswith("Company ")
    # selecting text that's already hidden opens that name's "Change label", not a second dialog
    assert h["hidden_owner"](res, "Microsoft Corporation").key == company.key
    assert h["hidden_owner"](res, company.label).key == company.key           # the label as shown
    assert h["hidden_owner"](res, "Securities and Exchange Commission") is None   # still showing: new hide
    # an address that mentions "Microsoft" isn't "part of" the company
    assert h["likely_label"](res, "One Microsoft Way, Redmond, Washington 98052-6399") is None
    # addresses (and dates) are categories you can move or split something into
    cats = h["categories"](s, res)
    assert "STREET_ADDRESS" in cats and "DATE" in cats and cats[-1] == "MANUAL"


def test_keyboard_undo_presses_the_undo_button():
    src = pathlib.Path(APP).read_text()
    assert "window.__llUndoKey" in src and "e.metaKey || e.ctrlKey" in src and "'↶ Undo'" in src
