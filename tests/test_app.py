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
    # Downloads stay locked while items are unreviewed
    at.session_state.view = "Documents"
    at.run()
    zips = [d for d in at.get("download_button") if "Download all" in d.proto.label]
    assert zips and zips[0].proto.disabled
    # …and unlock once everything is reviewed
    for k in sess.run().queue:
        sess.decide(k, "accept")
    at.run()
    zips = [d for d in at.get("download_button") if "Download all" in d.proto.label]
    assert not zips[0].proto.disabled
    # Approve everything -> the final package is downloadable as a ZIP and as one combined file
    at.session_state.view = "Final"
    at.run()
    for i in range(len(sess.names)):
        sess.approve(i, sess.run())
    at.run()
    assert not at.exception, at.exception
    labels = [d.proto.label for d in at.get("download_button") if not d.proto.disabled]
    assert any("Download package" in l for l in labels) and any("combined" in l for l in labels)


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
    assert getattr(at.session_state.session, "VERSION", 0) > 0
