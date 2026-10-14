"""Smoke test the Streamlit UI end to end (headless): every view renders, the release gate holds."""
import pathlib

from streamlit.testing.v1 import AppTest

APP = str(pathlib.Path(__file__).resolve().parent.parent / "app.py")


def test_sample_flow_all_views():
    at = AppTest.from_file(APP, default_timeout=180).run()
    assert not at.exception
    next(b for b in at.button if b.label == "Try with sample case").click().run()
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
    # ↶ Undo reverses the last change
    before = len(sess.undo_stack)
    next(b for b in at.button if b.label == "↶ Undo").click().run()
    assert not at.exception and len(at.session_state.session.undo_stack) == before - 1


def test_add_documents_keeps_pseudonyms():
    from engine import Session
    root = pathlib.Path(APP).parent / "samples"
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
    root = pathlib.Path(APP).parent / "samples"
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
    fix = next(b for b in at.button if b.label == "Redact it")
    fix.click().run()
    assert not at.exception, at.exception
    assert "Mercer" not in at.session_state.session.run().outputs[0]
    assert show is not None


def test_search_panel_and_entity_search():
    at = AppTest.from_file(APP, default_timeout=180).run()
    next(b for b in at.button if b.label == "Try with sample case").click().run()
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
    root = pathlib.Path(APP).parent / "samples"
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
    next(b for b in at.button if b.label == "Try with sample case").click().run()
    sess = at.session_state.session
    sess.settings["placeholder_style"] = "redacted"
    sess.add_party("Northwind Holdings", "ORGANIZATION")
    carried = sess.export_rules(sess.policy.name)
    at = AppTest.from_file(APP, default_timeout=180)
    at.session_state.carry_rules = carried
    at.run()
    assert not at.exception
    assert at.session_state.rules_pick == "Your rules from the last set"
    next(b for b in at.button if b.label == "Try with sample case").click().run()
    assert not at.exception
    again = at.session_state.session
    assert again.settings["placeholder_style"] == "redacted"
    assert any(p["name"] == "Northwind Holdings" for p in again.ops["parties"])
