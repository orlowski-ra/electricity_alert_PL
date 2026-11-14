from datetime import date, datetime

import httpx
from conftest import FakeServer, fixture_json, fixture_text, window_on

from electricity_alert.jsonutil import BadFormat
from electricity_alert.models import Address, FormatError, HttpError, Outage, ProviderId, Success, TimeWindow
from electricity_alert.providers import pge
from electricity_alert.providers.pge import PgeProvider, PgeStreet

SUWALKI = "0977456"


def outages(data, city_sym=SUWALKI, street_sym=None, window=None, log=None):
    return pge.to_outages(data, city_sym, street_sym, window, log or (lambda _: None))


def test_cities_from_real_response():
    cities = pge.to_cities(fixture_json("pge/cities.json"))
    assert len(cities) == 4
    babice = next(c for c in cities if c.city_sym == "0005167")
    assert babice.name == "Stare Babice"
    assert babice.county == "warszawski zachodni"
    assert babice.voivodeship == "Mazowieckie"
    assert cities[-1].voivodeship == "Świętokrzyskie"


def test_streets_keep_quotes_in_names():
    streets = pge.to_streets(fixture_json("pge/streets.json"))
    assert len(streets) == 4
    assert streets[0] == PgeStreet("11793", "ul. Marszałkowska")
    assert streets[2].name == 'rondo NSZZ "Solidarność"'
    assert streets[3].name == 'ul. Podpułkownika Jerzego Dąbrowskiego ps. "Łupaszka"'


def test_outages_from_real_response():
    items = outages(fixture_json("pge/outages_suwalki.json"))
    assert len(items) == 4
    first = items[0]
    assert first.uuid == "5dcd560e-c101-44ba-bcce-0e9df8a7aba0"
    assert first.numbers == ("7",)
    assert first.start_at == datetime(2026, 10, 12, 8, 0)
    assert first.stop_at == datetime(2026, 10, 14, 15, 0)
    assert not first.revoked
    assert items[1].revoked and items[1].numbers == ()
    # Also covers Sobolewo: only numbers from Suwałki.
    assert items[2].numbers == ("11",)
    # Two streets in Suwałki: without streetSym both.
    assert items[3].numbers == ("numery: 5, 9, 9A", "numery: 1, 2")


def test_numbers_filtered_by_street_or_all_when_none_matches():
    data = fixture_json("pge/outages_suwalki.json")
    assert outages(data, street_sym="08828")[3].numbers == ("numery: 5, 9, 9A",)
    assert outages(data, city_sym="9999999")[2].numbers == (
        "3, 5, 6, 8, 9, 12, 13, 13A, 15, 16, 16A, 17, 18B, 24A",
        "11",
    )


def test_breaks_on_different_days_listed_with_dates():
    data = [
        {
            "uuid": "m",
            "startAt": "2026-10-14 08:00:00",
            "stopAt": "2026-10-16 10:00:00",
            "dateRanges": [
                {"startAt": "2026-10-16 08:00:00", "stopAt": "2026-10-16 10:00:00"},
                {"startAt": "2026-10-14 08:00:00", "stopAt": "2026-10-14 10:00:00"},
            ],
        }
    ]
    window = TimeWindow(datetime(2026, 10, 14), datetime(2026, 10, 17, 23, 59, 59))
    (o,) = outages(data, window=window)
    assert o.start_at == datetime(2026, 10, 14, 8)
    assert o.stop_at == datetime(2026, 10, 16, 10)
    assert o.description == "14.10 08:00–10:00, 16.10 08:00–10:00"


def test_missing_fields_and_explicit_nulls():
    assert outages([{"uuid": "a"}]) == [Outage("a")]
    data = [
        {"uuid": "a", "startAt": None, "stopAt": None, "revoked": None, "addresses": None},
        {"uuid": "b", "addresses": [{"numbers": None, "teryt": None}, None]},
    ]
    assert outages(data) == [Outage("a"), Outage("b")]


def test_record_without_uuid_skipped_and_logged():
    logs = []
    items = outages([{"startAt": "2026-10-14 08:00:00"}, {"uuid": ""}, {"uuid": "b"}], log=logs.append)
    assert [o.uuid for o in items] == ["b"]
    assert len(logs) == 2


def test_unknown_fields_ignored_numeric_uuid_accepted_bad_date_null():
    data = [
        {
            "uuid": "a",
            "revoked": True,
            "brandNewField": {"x": [1, 2]},
            "addresses": [{"numbers": "1", "extra": 42, "teryt": {"citySym": SUWALKI, "newKey": "v"}}],
        }
    ]
    assert outages(data)[0].numbers == ("1",) and outages(data)[0].revoked
    assert [o.uuid for o in outages([{"uuid": 123}])] == ["123"]
    o = outages([{"uuid": "a", "startAt": "jutro rano", "stopAt": "2026-10-14T15:00:00"}])[0]
    assert o.start_at is None and o.stop_at is None


def test_wrong_types_raise_bad_format():
    for data in ([{"uuid": "a", "addresses": "abc"}], [{"uuid": "a", "revoked": "maybe"}], {"error": "not a list"}):
        try:
            outages(data)
        except BadFormat:
            continue
        raise AssertionError(f"no BadFormat for {data}")


def test_city_and_street_without_key_fields_skipped():
    cities = pge.to_cities([{"cityName": "X"}, {"citySym": "1"}, {"cityName": "Y", "citySym": "2"}])
    assert [c.city_sym for c in cities] == ["2"] and cities[0].voivodeship is None
    streets = pge.to_streets([{"name": "ul. A"}, {"symUl": "1"}, {"name": "ul. B", "symUl": "2"}])
    assert streets == [PgeStreet("2", "ul. B")]


def test_voivodeship_formatting():
    assert pge.format_voivodeship("MAZOWIECKIE") == "Mazowieckie"
    assert pge.format_voivodeship("ŁÓDZKIE") == "Łódzkie"
    assert pge.format_voivodeship("KUJAWSKO-POMORSKIE") == "Kujawsko-pomorskie"
    assert pge.format_voivodeship(" podlaskie ") == "Podlaskie"


# --- Provider over HTTP ---


def provider(server: FakeServer) -> PgeProvider:
    return PgeProvider(server.client(), falcon_base_url="https://falcon.gkpge.pl/", outage_base_url="https://power-outage.gkpge.pl/")


def address(params):
    return Address(1, ProviderId.PGE, "Suwałki", params)


def test_planned_outages_request_parameters(server):
    server.enqueue(fixture_text("pge/outages_suwalki.json"))
    window = TimeWindow(datetime(2026, 10, 13, 12), datetime(2026, 10, 14, 23, 59, 59))
    result = provider(server).planned_outages(address({"citySym": SUWALKI}), window)
    assert isinstance(result, Success) and len(result.data) == 4
    url = server.requests[0].url
    assert url.path == "/api/power-outage"
    assert url.params["type"] == "teryt"
    assert url.params["stopAtFrom"] == "2026-10-13 12:00:00"
    assert url.params["startAtTo"] == "2026-10-14 23:59:59"
    assert url.params["citySym"] == SUWALKI
    assert url.params["types[]"] == "2"
    assert "streetSym" not in url.params


def test_address_without_city_sym_is_format_error_without_request(server):
    assert provider(server).planned_outages(address({}), window_on(date(2026, 10, 14))) == FormatError()
    assert server.requests == []


def test_html_instead_of_json_is_format_error(server):
    server.enqueue("<html>502 Bad Gateway</html>")
    assert provider(server).planned_outages(address({"citySym": SUWALKI}), window_on(date(2026, 10, 14))) == FormatError()


def test_flaky_404_is_retried_for_pge(server):
    server.enqueue("404 page not found", status=404)
    server.enqueue("404 page not found", status=404)
    server.enqueue("[]")
    assert provider(server).planned_outages(address({"citySym": SUWALKI}), window_on(date(2026, 10, 14))) == Success([])
    assert len(server.requests) == 3


# --- Cities with districts ---


def _city(name, sym, ctype, county="1061", commune=None):
    return {"cityName": name, "citySym": sym, "communeTypeCode": ctype, "countyCode": county, "communeName": commune}


LODZ = [
    _city("Łódź", "0957650", "01"),
    _city("Łódź-Bałuty", "0957666", "09"),
    _city("Łódź-Śródmieście", "0958430", "09"),
    _city("Łódź-Kolonia", "9999999", "09", county="1234"),  # another "Łódź-…" outside the city county
    _city("Łódzkie Pole", "0000001", "02", county="1234"),
]


def district_server(server, streets, cities=LODZ):
    import json

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/public/teryt-city":
            return httpx.Response(200, json=cities)
        sym = request.url.params["citySym"]
        value = streets.get(sym, [])
        if isinstance(value, int):
            return httpx.Response(value)
        return httpx.Response(200, content=json.dumps(value))

    server.route = route


def test_city_with_districts_gets_merged_streets(server):
    district_server(
        server,
        {
            "0957666": [{"name": "ul. Zgierska", "symUl": "1"}, {"name": "ul. Piotrkowska", "symUl": "9"}],
            "0958430": [{"name": "ul. Piotrkowska", "symUl": "9"}, {"name": "ul. Andrzeja", "symUl": "2"}],
        },
    )
    result = provider(server).streets_with_districts("0957650", "Łódź")
    assert isinstance(result, Success)
    streets = result.data
    assert [s.name for s in streets] == ["ul. Andrzeja", "ul. Piotrkowska", "ul. Piotrkowska", "ul. Zgierska"]
    assert (streets[0].city_sym, streets[0].district) == ("0958430", "Śródmieście")
    assert len({s.key for s in streets}) == 4


def test_city_parts_hidden_from_town_search(server):
    def place(name, commune, ctype):
        return {"cityName": name, "communeName": commune, "communeTypeCode": ctype, "citySym": name}

    district_server(
        server,
        {},
        cities=[
            place("Czechów", "Kije", "02"),
            place("Czechów Dolny", "Lublin", "01"),
            place("Lublin", "Lublin", "01"),
            place("Retkinia", "Łódź-Polesie", "09"),
            place("Łódź-Polesie", "Łódź-Polesie", "09"),
            place("Brodnica", "Brodnica", "04"),
        ],
    )
    expected = ["Czechów", "Lublin", "Łódź-Polesie", "Brodnica"]
    p = provider(server)
    assert [c.name for c in p.search_cities("x").data] == expected
    assert [c.name for c in p.search_cities("x", nationwide=True).data] == expected
    assert server.requests[0].url.params["scope"] == "PgeDystrybucja"
    assert "scope" not in server.requests[1].url.params


def test_ordinary_city_keeps_own_streets_without_extra_requests(server):
    district_server(server, {"0954700": [{"name": "ul. Narutowicza", "symUl": "5"}]})
    result = provider(server).streets_with_districts("0954700", "Lublin", nationwide=True)
    assert [s.district for s in result.data] == [None]
    assert len(server.requests) == 1


def test_village_without_streets_stays_empty_district_error_reported(server):
    district_server(server, {}, cities=[_city("Mokra", "1", "02")])
    assert provider(server).streets_with_districts("1", "Mokra") == Success([])

    broken = FakeServer()
    district_server(broken, {"0958430": 400})
    assert PgeProvider(broken.client()).streets_with_districts("0957650", "Łódź") == HttpError(400)
