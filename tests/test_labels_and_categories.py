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
