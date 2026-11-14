import json
from datetime import date, datetime

from conftest import fixture_json, fixture_text, window_on

from electricity_alert.models import Address, FormatError, ProviderId, Success
from electricity_alert.providers import stoen
from electricity_alert.providers.stoen import StoenProvider

ITEMS = fixture_json("stoen/outages.json")
OCT15 = window_on(date(2026, 10, 15))


def test_street_names_match_and_only_the_window():
    (bruzdowa,) = stoen.to_outages(ITEMS, "ul. Bruzdowa", OCT15)
    assert bruzdowa.uuid == "stoen-168534"
    assert bruzdowa.start_at == datetime(2026, 10, 15, 7, 30)
    assert bruzdowa.description.startswith("ul. Bruzdowa 24, 26")
    assert len(stoen.to_outages(ITEMS, 'Aleja gen. Antoniego Chruściela "Montera"', OCT15)) == 1
    assert len(stoen.to_outages(ITEMS, "ul. Jana Miklaszewskiego", window_on(date(2026, 10, 21)))) == 1
    assert [o.uuid for o in stoen.to_outages(ITEMS, "ul. Narcyzowa", window_on(date(2026, 10, 14)))] == ["stoen-169010"]
    assert [o.uuid for o in stoen.to_outages(ITEMS, "ul. Narcyzowa", OCT15)] == ["stoen-169011"]
    assert stoen.to_outages(ITEMS, "ul. Puławska", OCT15) == []


def test_street_comparison():
    assert stoen.same_street("Koszykowa", "ul. Koszykowa")
    assert stoen.same_street("ul. Miklaszewskiego", "ul. Jana Miklaszewskiego")
    assert stoen.same_street("al. Aleja Generała Antoniego Chruściela „Montera”", 'Aleja gen. Antoniego Chruściela "Montera"')
    assert not stoen.same_street("ul. Daleka", "ul. Dalekowschodnia")
    assert not stoen.same_street("ul. Jana Kowalskiego", "ul. Piotra Kowalskiego")
    assert not stoen.same_street(None, "ul. Daleka")


def test_request_body_and_paging(server):
    server.enqueue(json.dumps([{"id": i} for i in range(1, stoen.PAGE_SIZE + 1)]))
    server.enqueue(fixture_text("stoen/outages.json"))
    result = StoenProvider(server.client()).planned_outages(Address(1, ProviderId.STOEN, "Warszawa", {"street": "ul. Bruzdowa"}), OCT15)
    assert isinstance(result, Success) and len(result.data) == 1
    first = server.requests[0]
    assert first.method == "POST"
    assert first.url.path == "/public/api/planned-outage/search/compressed-report"
    assert json.loads(first.content) == {"page": {"limit": 100, "offset": 0}}
    assert json.loads(server.requests[1].content) == {"page": {"limit": 100, "offset": 100}}


def test_errors_and_missing_street(server):
    p = StoenProvider(server.client())
    assert p.planned_outages(Address(1, ProviderId.STOEN, "Warszawa", {}), OCT15) == FormatError()
    assert server.requests == []
    server.enqueue("<html>")
    assert p.planned_outages(Address(1, ProviderId.STOEN, "Warszawa", {"street": "ul. Daleka"}), OCT15) == FormatError()
