"""Number labels keep their meaning, and categories you make yourself ("Age") behave like built-in ones."""
import pathlib

from engine import Session, consistency_check

ROOT = pathlib.Path(__file__).resolve().parent.parent


def sample():
    s = Session.from_texts([(p.name, p.read_text()) for p in sorted((ROOT / "samples").glob("*.txt"))])
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
