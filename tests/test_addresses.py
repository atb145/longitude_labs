"""Street addresses are caught by layout, context and the language model, never by exact text."""
import pytest

from engine import Session, available_policies, load_policy
from engine.detect import Detection
from engine.registry import _clean_name

POL = load_policy(available_policies()["Finance"])

COVER_TWO_LINES = """Activision Blizzard, Inc.
(Exact name of registrant as specified in its charter)
Delaware 001-15839 95-4803544
(State or other jurisdiction of (Commission File (IRS Employer
incorporation) Number) Identification No.)
2701 Olympic Boulevard, Building B
Santa Monica, California 90404
(Address of principal executive (Zip Code)
offices)
Registrant’s telephone number, including area code: (310) 255-2000
"""

COVER_ONE_LINE = """Microsoft Corporation
(Exact name of registrant as specified in its charter)
Washington 001-37845 91-1144442
(State or other jurisdiction (Commission (IRS Employer
of incorporation) File Number) Identification No.)
One Microsoft Way, Redmond, Washington 98052-6399
(Address of Principal Executive Offices) (Zip Code)
"""


@pytest.mark.parametrize("cover, parts", [
    (COVER_TWO_LINES, ["2701", "Olympic", "Building B", "Santa Monica", "90404"]),
    (COVER_ONE_LINE, ["Microsoft Way", "Redmond", "98052"]),
])
def test_cover_page_address_is_hidden_and_labelled_by_its_caption(cover, parts):
    res = Session.from_texts([("8-K.txt", cover)], POL).run()
    out = res.outputs[0]
    for part in parts:
        assert part not in out, part
    addresses = [e for e in res.entities if e.type == "STREET_ADDRESS"]
    assert addresses and addresses[0].label == "Address of principal executive offices"


def test_table_addresses_with_numbered_streets_and_floors():
    text = ("Name and address of beneficial owner\n"
            "BlackRock, Inc. 61,381,263 7.88%\n55 East 52nd Street\nNew York, NY 10055\n"
            "Capital International Investors 41,106,446 5.28%\n333 South Hope Street, 55th Fl.\n"
            "Los Angeles, CA 90071\n")
    out = Session.from_texts([("table.txt", text)], POL).run().outputs[0]
    for part in ("52nd Street", "10055", "South Hope", "55th Fl", "90071"):
        assert part not in out, part


def test_a_place_with_a_house_number_is_a_street_address():
    d = Detection(0, 0, 25, "3100 Ocean Park Boulevard", "LOCATION", 0.85, "SpacyRecognizer")
    assert _clean_name(d, POL, set()).type == "STREET_ADDRESS"
    city = Detection(0, 0, 12, "Santa Monica", "LOCATION", 0.85, "SpacyRecognizer")
    assert _clean_name(city, POL, set()).type == "LOCATION"       # a city alone stays a place


def test_numbers_near_capitalized_words_are_not_addresses():
    text = "Under Section 2 of the Act, the 2023 Board Meeting Street fair was approved."
    res = Session.from_texts([("plain.txt", text)], POL).run()
    assert not [e for e in res.entities if e.type == "STREET_ADDRESS"]


def test_two_covers_get_different_address_labels_and_a_rename_still_wins():
    sess = Session.from_texts([("msft_8k.txt", COVER_ONE_LINE), ("atvi_8k.txt", COVER_TWO_LINES)], POL)
    addresses = sorted((e for e in sess.run().entities if e.type == "STREET_ADDRESS"), key=lambda e: e.mentions[0][0])
    assert [e.label for e in addresses] == ["Address of principal executive offices A",
                                            "Address of principal executive offices B"]
    sess.rename(addresses[1].key, "Target headquarters")
    labels = {e.key: e.label for e in sess.run().entities if e.type == "STREET_ADDRESS"}
    assert labels[addresses[1].key] == "Target headquarters"
    assert labels[addresses[0].key] == "Address of principal executive offices"   # alone again: plain caption
