"""General rules for what is not a name (found on a 228-page proxy statement; meant for any document)."""
import pytest

from engine import Session, available_policies, load_policy
from engine.detect import Detection
from engine.registry import _clean_name, defined_terms

POL = load_policy(available_policies()["Finance"])


def clean(text: str, etype: str = "ORGANIZATION", defined=frozenset()):
    c = _clean_name(Detection(0, 0, len(text), text, etype, 0.85, "SpacyRecognizer"), POL, set(), defined)
    return c.text if c else None


@pytest.mark.parametrize("raw, kept", [
    ("Dear Activision Blizzard", "Activision Blizzard"),               # a greeting is not part of the name
    ("Anchorage Merger Sub Inc.", "Anchorage Merger Sub Inc."),        # a legal name isn't cut at "Merger"
    ("Microsoft Announces Partnership", "Microsoft"),                  # …but a headline still is
    ("Activision Blizzard 2", "Activision Blizzard"),                  # page number from a table of contents
    ("Non-Contravention A-24", "Non-Contravention"),                   # section reference
    ("Studio 54", "Studio 54"),                                        # a number that is part of a name stays
    ("The Activision Blizzard Board of Directors", "Activision Blizzard"),
    ("Activision Blizzard Common Stock Beneficially Owned", "Activision Blizzard"),
    ("Activision Blizzard-", "Activision Blizzard"),                   # hyphen left by a line break
    ("Time Warner Inc.", "Time Warner Inc."),                          # a thing-word first is fine
])
def test_names_are_trimmed_not_lost(raw, kept):
    assert clean(raw) == kept


@pytest.mark.parametrize("raw", [
    "Pacific Time", "Effective Time", "California Consumer Privacy Act", "MIT License", "U.S. Holders",
    "20th Floor", "University Avenue", "Delaware Court", "Code Sections 409A(a)(2", "U.S. Department",
    "PA 19355", "3/228 10/14/26", "Attn: Investor Relations", "GAAP", "EBITDA", "Article",
])
def test_things_that_are_not_names(raw):
    assert clean(raw) is None


def test_a_documents_own_defined_terms_are_vocabulary_but_party_aliases_stay_names():
    text = ("“Governmental Authority” means any government or court. Each such person is an “ERISA Affiliate”. "
            "Microsoft Corporation (“Microsoft”) and Activision Blizzard, Inc. (the “Company”) agree.")
    terms = defined_terms([text])
    assert terms == {"governmental authority", "erisa affiliate"}
    assert clean("Governmental Authorities", defined=terms) is None   # plural of a defined term
    assert clean("ERISA Affiliates", defined=terms) is None
    assert clean("Microsoft", defined=terms) == "Microsoft"


def test_public_terms_stay_readable_and_parties_are_still_hidden():
    text = ("Due to COVID-19, the special meeting will be held at 10:00 a.m., Pacific Time. See Article VI and "
            "ARTICLE IX and Annex C. The FTC and the European Commission reviewed the merger of Microsoft "
            "Corporation and Anchorage Merger Sub Inc. with Activision Blizzard, Inc.\n\n"
            "Dear Activision Blizzard Stockholders,\nRobert Kotick signed for Activision Blizzard.")
    res = Session.from_texts([("proxy.txt", text)], POL).run()
    out = res.outputs[0]
    for public in ("COVID-19", "Pacific Time", "Article VI", "ARTICLE IX", "Annex C", "FTC", "European Commission"):
        assert public in out, public
    for party in ("Microsoft", "Anchorage", "Activision", "Kotick"):
        assert party not in out, party
    names = {e.canonical for e in res.entities}
    assert not any(n.startswith(("Dear", "Pacific", "Article", "Annex", "Covid", "COVID")) for n in names), names


def test_parenthetical_definitions_with_a_lead_in_are_vocabulary():
    text = ("the last trading day (such fraction, the “Exchange Ratio”). Shares held by the Company "
            "(collectively, the “Excluded Shares”). Any option above the price (any such Company Option, an “Underwater\n"
            "Option”). Microsoft Corporation (“Microsoft”) and Activision Blizzard, Inc. (the “Company”) and "
            "Acme Corp. (“Falcon”) on the deal (collectively, the “Project Falcon”).")
    terms = defined_terms([text])
    assert {"exchange ratio", "excluded share", "underwater option"} <= terms
    # a bare (“X”) / (the “X”) names a party, nickname or codename; a codename never counts as vocabulary
    assert not terms & {"microsoft", "company", "falcon", "project falcon"}
    assert clean("Excluded Share", "PERSON", terms) is None
    assert clean("Exchange Ratio", defined=terms) is None


@pytest.mark.parametrize("role", ["Parent", "Sub", "Merger Sub", "Purchaser", "Notification"])
def test_deal_roles_are_not_names(role):
    assert clean(role) is None


def test_keep_text_never_protects_half_a_name():
    # The "Rule … under the Exchange Act" keep pattern used to run on to the "Act" in "Activision", leaving
    # "Activision" readable next to the pseudonym of another company that owns "Blizzard".
    text = ("Activision Blizzard, Inc. and its subsidiary Blizzard Entertainment, Inc. agree. Pursuant to Rule 14a-8 "
            "under the Exchange Act and the Activision Blizzard bylaws, proposals must be received by Activision "
            "Blizzard\nbetween February and March.")
    out = Session.from_texts([("bylaws.txt", text)], POL).run().outputs[0]
    assert "Activision" not in out and "Blizzard" not in out
    assert "Rule 14a-8 under the Exchange Act" in out
