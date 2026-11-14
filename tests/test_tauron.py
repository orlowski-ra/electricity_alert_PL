from datetime import date, datetime

from conftest import fixture_json, fixture_text, window_on

from electricity_alert.models import Address, FormatError, ProviderId, Success
from electricity_alert.providers import tauron
from electricity_alert.providers.tauron import TauronCity, TauronProvider, TauronStreet


def uuids(items):
    return [o.uuid for o in items]


def test_cities_from_real_response():
    cities = tauron.to_cities(fixture_json("tauron/cities_mokra.json"))
    first = cities[0]
    assert (first.gaid, first.name, first.commune, first.county, first.voivodeship) == (
        103126,
        "Mokra",
        "Skierniewice",
        "skierniewicki",
        "Łódzkie",
    )
    assert len(cities) == 15


def test_streets():
    assert tauron.to_streets(fixture_json("tauron/onlyonestreet_one.json")) == [TauronStreet(923336, "Czerwienne")]
    assert tauron.to_streets(fixture_json("tauron/streets_row.json")) == [TauronStreet(306285, "ul. Równoległa")]


def test_records_without_gaid_or_name_skipped():
    logs = []
    cities = tauron.to_cities([None, {"Name": "X"}, {"GAID": 1}, {"GAID": 2, "Name": "Y", "ProvinceName": "MAŁOPOLSKIE"}], logs.append)
    assert cities == [TauronCity(2, "Y", None, None, "Małopolskie")]
    assert len(logs) == 2


def test_recognized_house_number_only_outages_with_its_address_point():
    items = tauron.to_outages(fixture_json("tauron/outages_mokrzesz_point.json"), "Mokrzesz", window_on(date(2026, 10, 14)))
    assert uuids(items) == ["84924277-8613-40eb-bf5f-cc7f4a00ca01"]
    (o,) = items
    assert o.start_at == datetime(2026, 10, 14, 8, 0)  # 06:00Z = 08:00 summer time
    assert o.stop_at == datetime(2026, 10, 14, 16, 0)
    assert o.description.startswith("Mokrzesz, ul. Równoległa od nr 5 do nr 11")
    assert o.numbers == () and not o.revoked


def test_region_wide_list_narrowed_to_our_town():
    data = fixture_json("tauron/outages_czerwienne_region.json")
    assert uuids(tauron.to_outages(data, "Czerwienne", window_on(date(2026, 10, 14)))) == ["ba17ba4a-cb7e-4e02-aeec-05e54c07e4d1"]
    assert tauron.to_outages(data, "Pieniążkowice", window_on(date(2026, 10, 14))) == []
    assert uuids(tauron.to_outages(data, "Pieniążkowice", window_on(date(2026, 10, 15)))) == ["26c5d561-7cdf-4b5e-9444-81d9028a814f"]


def test_only_outages_overlapping_window_duplicates_merged():
    data = fixture_json("tauron/outages_krakow_duplicates.json")
    assert tauron.to_outages(data, "Kraków", window_on(date(2026, 10, 14))) == []
    (o,) = tauron.to_outages(data, "Kraków", window_on(date(2026, 10, 16)))
    assert o.start_at == datetime(2026, 10, 16, 8, 0)


def test_inactive_is_revoked_missing_id_skipped_bad_date_null():
    data = {
        "OutageItems": [
            None,
            {"Message": "Mokra"},
            {"OutageId": "a", "IsActive": False, "StartDate": "jutro", "EndDate": None, "Message": "w miejscowości Mokra"},
        ]
    }
    (o,) = tauron.to_outages(data, "Mokra", window_on(date(2026, 10, 15)))
    assert o.revoked and o.start_at is None


def test_failures_skipped_fractional_seconds_parsed():
    data = {
        "OutageItems": [
            {
                "OutageId": "awaria",
                "TypeId": 2,
                "StartDate": "2026-10-15T05:39:09.4Z",
                "EndDate": "2026-10-15T07:45:00Z",
                "Message": "Pawłowice: Górnicza",
            },
            {
                "OutageId": "plan",
                "TypeId": 1,
                "StartDate": "2026-10-15T05:39:09.4Z",
                "EndDate": "2026-10-15T07:45:00Z",
                "Message": "Pawłowice: Polna",
            },
        ]
    }
    (o,) = tauron.to_outages(data, "Pawłowice", window_on(date(2026, 10, 15)))
    assert o.uuid == "plan"
    assert o.start_at == datetime(2026, 10, 15, 7, 39, 9, 400_000)


def test_empty_response():
    assert tauron.to_outages({"OutageItems": []}, "X", window_on(date(2026, 10, 15))) == []
    assert tauron.to_outages({}, "X", window_on(date(2026, 10, 15))) == []


def test_city_mention():
    assert tauron.mentions_city("w miejscowości Czerwienne, budynki…", "Czerwienne")
    assert tauron.mentions_city("Kraków ul Kosynierów 19B", "Kraków")
    assert tauron.mentions_city("prace w Krakowie przy ul. X", "Kraków")
    assert not tauron.mentions_city("w miejscowości Pieniążkowice", "Czerwienne")
    assert not tauron.mentions_city("w miejscowości Pomokra", "Mokra")
    assert not tauron.mentions_city(None, "Mokra")
    assert tauron.mentions_city("cokolwiek", None)


def test_in_a_city_the_region_wide_outage_must_name_our_street():
    data = {
        "OutageItems": [
            {
                "OutageId": "jasna",
                "TypeId": 1,
                "StartDate": "2026-10-15T06:00:00Z",
                "EndDate": "2026-10-15T13:00:00Z",
                "AddressPointIds": [],
                "Message": "Gliwice: Jasna 31b.",
            },
            {
                "OutageId": "zwyc",
                "TypeId": 1,
                "StartDate": "2026-10-16T06:00:00Z",
                "EndDate": "2026-10-16T13:00:00Z",
                "AddressPointIds": [],
                "Message": "Gliwice: Zwycięstwa 10-14, Nowy Świat 59.",
            },
            {
                "OutageId": "nowy",
                "TypeId": 1,
                "StartDate": "2026-10-16T06:00:00Z",
                "EndDate": "2026-10-16T13:00:00Z",
                "AddressPointIds": [],
                "Message": "Gliwice: Nowy Świat budynek przy numerze 59.",
            },
        ]
    }
    oct16 = window_on(date(2026, 10, 16))
    assert uuids(tauron.to_outages(data, "Gliwice", oct16, "ul. Zwycięstwa")) == ["zwyc"]
    assert uuids(tauron.to_outages(data, "Gliwice", oct16, "ul. Nowy Świat")) == ["zwyc", "nowy"]
    assert uuids(tauron.to_outages(data, "Gliwice", oct16)) == ["zwyc", "nowy"]


def test_street_mention():
    assert tauron.mentions_street("Kraków ul Kosynierów 19B", "ul. Kosynierów", "Kraków")
    assert tauron.mentions_street("ul. Hallera 5", "ul. gen. Józefa Hallera", "Kraków")
    assert tauron.mentions_street("os. Armii Krajowej bl. 3", "Armii Krajowej", "Kraków")
    assert not tauron.mentions_street("Gliwice: Jasna 31b.", "ul. Zwycięstwa", "Gliwice")
    assert not tauron.mentions_street("Kraków: Polanka 3", "ul. Polna", "Kraków")
    assert tauron.mentions_street("w miejscowości Czerwienne, budynki 200", "Czerwienne", "Czerwienne")
    assert tauron.mentions_street("cokolwiek", None, "Kraków")
    assert tauron.mentions_street("Radwanice: Herberta, Kościuszki", "ul. Zbigniewa Herberta", "Radwanice")
    assert not tauron.mentions_street("Opole: Stanisława Wyspiańskiego 3", "ul. Stanisława Moniuszki", "Opole")
    assert tauron.mentions_street("Gliwice: Skłodowskiej 3", "ul. Marii Skłodowskiej-Curie", "Gliwice")
    assert tauron.mentions_street("Gliwice: Curie-Skłodowskiej 3", "ul. Marii Skłodowskiej-Curie", "Gliwice")


def test_provider_request_uses_wider_dates_and_house_number_zero(server):
    server.enqueue(fixture_text("tauron/outages_czerwienne_region.json"))
    address = Address(1, ProviderId.TAURON, "Czerwienne", {"cityGAID": "1", "streetGAID": "923336", "cityName": "Czerwienne"})
    result = TauronProvider(server.client()).planned_outages(address, window_on(date(2026, 10, 14)))
    assert isinstance(result, Success) and uuids(result.data) == ["ba17ba4a-cb7e-4e02-aeec-05e54c07e4d1"]
    params = server.requests[0].url.params
    assert params["houseNo"] == "0"
    assert params["fromDate"] == "2026-10-13T00:00:00.000Z"
    assert params["toDate"] == "2026-10-15T23:59:59.999Z"


def test_provider_errors(server):
    p = TauronProvider(server.client())
    broken = Address(1, ProviderId.TAURON, "x", {"cityGAID": "abc"})
    assert p.planned_outages(broken, window_on(date(2026, 10, 14))) == FormatError()
    assert server.requests == []
    server.enqueue('{"OutageItems":"x"}')
    good = Address(1, ProviderId.TAURON, "x", {"cityGAID": "1", "streetGAID": "2"})
    assert p.planned_outages(good, window_on(date(2026, 10, 14))) == FormatError()
    server.enqueue("[]")
    assert p.planned_outages(good, window_on(date(2026, 10, 14))) == FormatError()


def test_404_is_not_retried_outside_pge(server):
    server.enqueue("", status=404)
    server.enqueue("[]")
    from electricity_alert.models import HttpError

    assert TauronProvider(server.client()).search_cities("Mokra") == HttpError(404)
    assert len(server.requests) == 1
