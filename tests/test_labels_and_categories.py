"""Number labels keep their meaning, and categories you make yourself ("Age") behave like built-in ones."""
import pathlib

from engine import Session, consistency_check

ROOT = pathlib.Path(__file__).resolve().parent.parent


def sample():
    s = Session.from_texts([(p.name, p.read_text()) for p in sorted((ROOT / "tests" / "fixtures" / "sample_case").glob("*.txt"))])
    return s, s.run()


def test_numbers_keep_their_label_instead_of_a_bare_1():
    _, res = sample()
    out = res.outputs[0]
    # "CRD No. 2849117" used to come out as "CRD No. 1" — readable as the real CRD number 1
    assert "CRD No. [CRD 1]" in out and "CRD No. 1." not in out
    assert "Case No. [Case 1]" in out
    assert "brokerage account [Account 1]" in out
    assert "Case No. Case" not in out and "CRD No. CRD" not in out
    assert consistency_check(res)["leak_free"]


def test_a_label_without_its_type_word_before_it_is_unchanged():
    _, res = sample()
    # "…and a second account, 5520-117384" has no "account" right before the number: plain label, no brackets
    assert "a second account, Account 2" in res.outputs[1]


def test_custom_category_numbers_in_the_order_you_add_them():
    s, _ = sample()
    s.redact("47", "OTHER:Age", "", "Age {1}")          # Okafor's age, in the third file
    s.redact("52", "OTHER:Age", "", "Age {1}")          # Mercer's age, in the first file — added second
    ages = {e.canonical: e.label for e in s.run().entities if e.type == "OTHER:Age"}
    assert ages == {"47": "Age 1", "52": "Age 2"}


def test_move_a_value_to_another_category():
    s, res = sample()
    crd = next(e for e in res.entities if e.type == "CRD_NUMBER")
    s.retype(crd.key, "ACCOUNT_NUMBER")
    moved = s.run().entity(crd.key)
    assert moved.type == "ACCOUNT_NUMBER" and moved.label.startswith("Account ") and moved.is_redacted
    s.retype(crd.key, "OTHER:Age")
    moved = s.run().entity(crd.key)
    assert moved.type == "OTHER:Age" and moved.label == "Age 1" and moved.is_redacted
    assert "2849117" not in s.run().outputs[0]


# ---------------------------------------------------------------- labels with context (roles) vs simple
def test_titles_become_the_kind_of_role():
    from engine.registry import general_role
    assert general_role("Assistant Secretary") == "Assistant secretary"
    assert general_role("Senior Vice President, Corporate Governance and Corporate Secretary") == "Secretary"
    assert general_role("Executive Vice President, General Counsel and Secretary") == "Legal officer"
    assert general_role("Senior Vice President and Chief Financial Officer") == "Finance officer"
    assert general_role("President and Chief Executive Officer") == "Chief executive"
    assert general_role("Head of Strategy") == "Officer"


def _signature(role_labels: bool) -> str:
    from engine.extract import extract_document
    pdf = ROOT / "tests" / "fixtures" / "msft_8k_2022-01-18.pdf"
    s = Session.from_texts([(pdf.name, extract_document(pdf.name, pdf.read_bytes()).text)])
    s.settings["role_labels"] = role_labels
    out = s.run().outputs[0]
    return out[out.index("/s/"):][:60]


def test_signature_block_with_context_and_simple():
    # Context: who signed and what kind of role they hold — not "Person A … Title 1"
    assert _signature(True).startswith("/s/ Signatory\nSignatory\nAssistant secretary")
    assert _signature(False).startswith("/s/ Person A\nPerson A\nTitle 1")
    assert "Dolliver" not in _signature(True)


def test_specific_title_is_still_not_shown_with_context():
    s = Session.from_texts([("atvi.txt", (ROOT / "tests" / "fixtures" / "atvi_8k_excerpt.txt").read_text())])
    out = s.run().outputs[0]
    assert "Name: Signatory" in out and "Title: Secretary" in out
    assert "Corporate Governance" not in out and "Ellenberg" not in out


# ---------------------------------------------------------------- roles from SEC cover pages and defined terms
PROXY = ("ACME HOLDINGS, INC.\n(Name of Registrant as Specified In Its Charter)\n\n"
         "Agreement and Plan of Merger by and among Beta Software Corporation, a Washington corporation (“Parent”), "
         "Gamma Merger Sub Inc., a Delaware corporation and a wholly owned subsidiary of Beta Software Corporation "
         "(“Merger Sub”), and Acme Holdings, Inc., a Delaware corporation (the “Company”).\n")


def test_proxy_cover_names_the_registrant_and_parent_keeps_its_own_term():
    res = Session.from_texts([("proxy.txt", PROXY)]).run()
    labels = {e.canonical: e.label for e in res.entities if e.type == "ORGANIZATION"}
    # the cover page beats the defined term found later ("the Company")
    assert labels["Acme Holdings, Inc."] == "Registrant"
    # "…a wholly owned subsidiary of Beta Software Corporation (“Merger Sub”)" names the subsidiary, not Beta
    assert labels["Beta Software Corporation"] == "Parent"
    assert "ACME HOLDINGS" not in res.outputs[0] and "REGISTRANT\n(Name of Registrant" in res.outputs[0]


# ---------------------------------------------------------------- "Don't hide it" sticks, and is learned
def _taught_session(policy="Finance"):
    """A made-up word no built-in rule keeps, so the test is about what a reviewer teaches, not the pack."""
    from engine import Detection
    txt = "Due to Northstar-9, Acme Holdings Inc. met online. Later, Northstar-9 rules applied to Acme Holdings Inc."
    s = Session.from_texts([("memo.txt", txt)], policy)
    first, second = txt.index("Northstar-9"), txt.rindex("Northstar-9")
    # the detector calls the same word a company in one place and a person in another
    s.detections = s.detections + [Detection(0, first, first + 11, "Northstar-9", "ORGANIZATION", 0.9, "test"),
                                   Detection(0, second, second + 10, "Northstar-", "PERSON", 0.9, "test")]
    return s


def _northstar(res):
    return [e for e in res.entities if "northstar" in e.key]


def test_dont_hide_sticks_when_the_detector_changes_its_mind_about_the_type():
    s = _taught_session()
    org = next(e for e in _northstar(s.run()) if e.type == "ORGANIZATION")
    s.delete(org.key)
    res = s.run()
    assert not _northstar(res)                                          # not back as a person
    assert res.outputs[0].count("Northstar-9") == 2
    assert any(e.canonical.startswith("Acme") for e in res.entities)   # other names untouched


def test_dont_hide_is_remembered_for_the_next_set():
    from engine import load_policy_text
    s = _taught_session()
    s.delete(_northstar(s.run())[0].key)
    saved = s.export_rules("My rules")
    assert "never_hide" in saved and "northstar" in saved
    nxt = _taught_session(load_policy_text(saved, "My rules"))         # a new set, using the saved rules
    res = nxt.run()
    assert not _northstar(res) and res.outputs[0].count("Northstar-9") == 2
    # a name you listed to always hide still wins over a lesson
    nxt.add_party("Northstar-9", "ORGANIZATION")
    assert _northstar(nxt.run())
