"""The Library prototype: save a setup on ④ Download, see it in the Library, use it on the next set."""
import pathlib

from streamlit.testing.v1 import AppTest

APP = str(pathlib.Path(__file__).resolve().parent.parent / "app.py")


def click(at: AppTest, label: str, i: int = 0, page: str = "Anonymize") -> AppTest:
    [b for b in at.button if b.label == label][i].click()
    at.session_state.page = page  # AppTest doesn't send the open tab back with a click; the browser does
    return at.run()


def open_library(at: AppTest) -> AppTest:
    at.session_state.page = "Library"
    return at.run()


def test_send_to_library_then_use_again():
    at = open_library(AppTest.from_file(APP, default_timeout=180).run())
    assert not at.exception
    assert [e["name"] for e in at.session_state.library] == ["Project Granite — merger 8-Ks",
                                                             "Item 5.02 officer-change review"]
    at.session_state.page = "Anonymize"
    click(at.run(), "Try the sample case")
    sess = at.session_state.session
    sess.rename("ORGANIZATION:meridian clearing", "Clearing firm")
    sess.decide("E|ORGANIZATION:whitcombe", "keep")
    at.session_state.b_URL = False  # switch one rule off in the sidebar
    at.run()

    at.session_state.view = "Final"  # the Library offer sits after the label key, on ④ Download
    at.run()
    at.text_input(key="lib_name").set_value("Mercer complaint set").run()
    click(at, "Send to Library")
    assert not at.exception
    saved = at.session_state.library[0]
    assert saved["name"] == "Mercer complaint set" and "URL" in saved["settings"]["disabled"]
    assert saved["ops"]["labels"] == {"ORGANIZATION:meridian clearing": "Clearing firm"}
    assert any("Saved to Library" in t.value for t in at.toast)

    open_library(at)
    assert not at.exception and len([b for b in at.button if b.label == "Use again"]) == 3
    # With a set open, "Use again" only asks (a dialog, which AppTest doesn't render): nothing is applied yet
    click(at, "Use again", 0, page="Library")
    assert not at.exception and at.session_state.session is sess and "library_use" not in at.session_state

    del at.session_state["session"]  # as "Yes, start a new set" / "Start over" would
    open_library(at)
    click(at, "Use again", 0, page="Library")
    assert at.session_state.page == "Anonymize"
    assert any("Mercer complaint set" in m.value for m in at.info)

    click(at, "Try the sample case")
    assert not at.exception
    new = at.session_state.session
    assert new is not sess and "library_use" not in at.session_state
    assert "URL" in new.settings["disabled"] and at.session_state.b_URL is False
    labels = {e.key: e.label for e in new.run().entities}
    assert labels["ORGANIZATION:meridian clearing"] == "Clearing firm"
    assert new.ops["decisions"]["E|ORGANIZATION:whitcombe"]["action"] == "keep"


def test_example_entry_on_the_bundled_8ks(monkeypatch):
    # The examples are keyed to the bundled SEC 8-Ks (the other UI tests use the small fictional case)
    monkeypatch.setenv("ANONYMIZER_SAMPLES", str(pathlib.Path(APP).parent / "samples"))
    at = open_library(AppTest.from_file(APP, default_timeout=180).run())
    click(at, "Use again", 0, page="Library")  # Project Granite: no set open, so no question
    assert at.session_state.page == "Anonymize" and at.session_state.library_use["name"].startswith("Project Granite")
    click(at, "Try the sample case")
    assert not at.exception
    sess = at.session_state.session
    res = sess.run()
    assert {"MONEY", "SHARE_COUNT"} <= set(sess.settings["disabled"])
    labels = {e.key: e.label for e in res.entities}
    assert (labels["ORGANIZATION:microsoft"], labels["ORGANIZATION:activision blizzard"]) == ("Acquirer", "Target")
