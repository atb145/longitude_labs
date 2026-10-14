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
    for view in ("Review", "Documents", "Approved", "Log & consistency"):
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


def test_upload_needs_rights_checkbox():
    at = AppTest.from_file(APP, default_timeout=180).run()
    btn = next(b for b in at.button if b.label == "Anonymize my files")
    assert btn.disabled
