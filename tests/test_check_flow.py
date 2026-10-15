"""One forward button per step (the blue Next at the bottom), what you checked, sentences shown as they read now,
and choosing what goes to the Library."""
import pathlib

from streamlit.testing.v1 import AppTest

APP = str(pathlib.Path(__file__).resolve().parent.parent / "app.py")


def _sample():
    at = AppTest.from_file(APP, default_timeout=180).run()
    next(b for b in at.button if b.label == "Try with sample case").click().run()
    return at, at.session_state.session


def _next(at):
    return at.button(key="nav_next")


def test_next_is_the_only_forward_button_and_turns_blue_when_the_step_is_done():
    at, sess = _sample()
    assert _next(at).proto.type == "secondary"                       # items still open on ② Check
    for k in sess.run().queue:
        sess.decide(k, "accept")
    at.run()
    labels = [b.label for b in at.button]
    assert "Read & approve →" not in labels and _next(at).proto.type == "primary"
    assert any(m.value.startswith("<h2>Everything is checked") for m in at.markdown)
    assert any("class=\"checked\"" in m.value for m in at.markdown)  # what you checked, as it now reads
    at.session_state.view = "Documents"
    at.run()
    assert _next(at).proto.type == "secondary"
    # approve every file (outside the page: the test tool can't follow the picker's ✓ labels changing), then reopen
    at.session_state.view = "Final"
    at.run()
    for i in range(len(sess.names)):
        sess.approve(i, sess.run())
    at.session_state.view = "Documents"
    at.run()
    assert not at.exception, at.exception
    assert not any(b.label.startswith("Go to ④") for b in at.button) and _next(at).proto.type == "primary"
    assert not at.success                                            # no "Every file is approved" box


def test_files_step_has_no_repeated_buttons_or_always_hide_form():
    at, _ = _sample()
    at.session_state.view = "Start"
    at.run()
    labels = [b.label for b in at.main.button]
    assert not any(l in labels for l in ("Continue checking →", "Read & approve →", "Go to download →"))
    assert "Start over" not in labels                                # it's in the sidebar
    assert not [t for t in at.main.text_input if t.key == "files_parties_name"]


def test_sentence_to_rewrite_shows_what_is_already_hidden():
    at, sess = _sample()
    k = next(k for k in sess.run().queue if sess.run().item(k).rule == "quasi.combination")
    at.button(key=f"open_{k}").click().run()
    card = next(m.value for m in at.markdown if 'class="card-ctx"' in m.value)
    assert "Harborview Capital Partners" not in card and 'class="chip' in card


def test_library_offer_after_the_label_key_saves_only_what_you_tick():
    at, sess = _sample()
    sess.rename("ORGANIZATION:meridian clearing", "Clearing firm")
    sess.add_party("Project Falcon", "MANUAL")
    at.session_state.view = "Final"
    at.run()
    assert any("Would you like to send this to the Library" in m.value for m in at.markdown)
    picks = {c.label: c for c in at.checkbox if c.key and c.key.startswith("libpick_")}
    assert "Label: Meridian Clearing → Clearing firm" in picks and "Always hide: Project Falcon" in picks
    picks["Always hide: Project Falcon"].uncheck().run()
    at.text_input(key="lib_name").set_value("Only the label").run()
    next(b for b in at.button if b.label == "Send to Library").click().run()
    saved = at.session_state.library[0]
    assert saved["name"] == "Only the label"
    assert saved["ops"].get("labels") == {"ORGANIZATION:meridian clearing": "Clearing firm"}
    assert "parties" not in saved["ops"]
