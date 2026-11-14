from datetime import UTC, date, datetime, timedelta

from conftest import fixture_json, fixture_text, window_on

from electricity_alert.models import Address, FormatError, ProviderId, Success
from electricity_alert.providers import energa
from electricity_alert.providers.energa import EnergaPlace, EnergaProvider, InMemoryDocumentCache
from electricity_alert.text import mentions_whole_word

SHUTDOWNS = energa.shutdowns_of(fixture_json("energa/shutdowns.json"))


def place(county, commune, ctype, city, street=None):
    return EnergaPlace(county, commune, ctype, city, street)


def outages(p, day):
    return energa.to_outages(SHUTDOWNS, p, window_on(day))


OCT14 = date(2026, 10, 14)


def test_three_different_nowa_wies_told_apart():
    assert [o.uuid for o in outages(place("3020", "Pleszew", "05", "Nowa Wieś"), OCT14)] == ["96a5ba05-2a58-4251-97d2-97df145f3331"]
    gizalki = outages(place("3020", "Gizałki", "02", "Nowa Wieś"), OCT14)
    assert len(gizalki) == 1 and gizalki[0].description.startswith("Nowa Wieś 27")
    assert outages(place("2807", "Iława", "02", "Nowa Wieś"), OCT14) == []
    assert len(outages(place("2807", "Iława", "02", "Nowa Wieś"), date(2026, 10, 19))) == 1


def test_hand_typed_address_matched_by_town_and_street_only():
    assert len(outages(EnergaPlace(None, None, None, "Nowa Wieś"), OCT14)) == 2
    plock = EnergaPlace(None, None, None, "Płock", "Nizinna")
    assert len(outages(plock, date(2026, 10, 16))) == 1
    assert outages(EnergaPlace(None, None, None, "Płock", "Jędrzejewo"), date(2026, 10, 16)) == []


def test_town_part_and_rural_part_are_different_areas():
    assert len(outages(place("3020", "Pleszew", "04", "Pleszew"), OCT14)) == 1
    assert outages(place("3020", "Pleszew", "05", "Pleszew"), OCT14) == []


def test_street_must_be_mentioned_when_chosen():
    oct16 = date(2026, 10, 16)
    assert len(outages(place("1462", "Płock", "01", "Płock", "Nizinna"), oct16)) == 1
    assert outages(place("1462", "Płock", "01", "Płock", "Jędrzejewo"), oct16) == []
    assert len(outages(place("1462", "Płock", "01", "Płock"), oct16)) == 1
    assert len(outages(place("1462", "Płock", "01", "Płock", "Narodowych Sił Zbrojnych"), date(2026, 10, 23))) == 1


def test_dates_converted_from_utc_to_polish_time():
    (o,) = outages(place("1462", "Płock", "01", "Płock", "Nizinna"), date(2026, 10, 16))
    assert o.start_at == datetime(2026, 10, 16, 8, 45)


def test_failures_skipped():
    assert outages(place("2262", "Gdynia", "01", "Gdynia"), OCT14) == []
    assert outages(place("3216", "Sławoborze", "02", "Słowenkowo"), OCT14) == []


def test_multi_day_shutdown_takes_interval_of_requested_day():
    zalewo = place("2807", "Zalewo", "05", "Półwieś")
    (d13,) = outages(zalewo, date(2026, 10, 13))
    (d14,) = outages(zalewo, OCT14)
    assert (d13.start_at.day, d14.start_at.day) == (13, 14)
    assert d13.uuid == d14.uuid


def test_two_breaks_same_day_whole_span_breaks_in_description():
    (o,) = outages(place("2201", "Studzienice", "02", "Kłączno"), OCT14)
    assert (o.start_at, o.stop_at) == (datetime(2026, 10, 14, 8), datetime(2026, 10, 14, 14, 30))
    assert o.description.startswith("08:00–09:00, 13:30–14:30\nKłączno ulice")


def test_full_teryt_street_name_matched_by_key_word():
    shutdown = {
        "guid": "g1",
        "areas": ["Gdynia gmina miejska"],
        "counties": [2262],
        "message": "Gdynia ulice Chwarznieńska, kpt. Zdzisława Pieńkawy, Mieczysławy Pobłockiej.",
        "shutdownType": 2,
        "startDate": "2026-10-14T06:00:00Z",
        "endDate": "2026-10-14T10:00:00Z",
    }
    gdynia = place("2262", "Gdynia", "01", "Gdynia", "Kapitana Zdzisława Pieńkawy")
    assert len(energa.to_outages([shutdown], gdynia, window_on(OCT14))) == 1
    assert energa.to_outages([shutdown], place("2262", "Gdynia", "01", "Gdynia", "Leśna"), window_on(OCT14)) == []


def test_unknown_commune_type_falls_back_to_name_prefix():
    assert len(outages(place("3020", "Gizałki", None, "Nowa Wieś"), OCT14)) == 1


def test_whole_word_matching():
    assert mentions_whole_word("Nowa Wieś 27, 28.", "nowa wieś")
    assert mentions_whole_word("Płock ulice Jordanowska 25, Nizinna 44", "Nizinna")
    assert not mentions_whole_word("Płock ulice Nizinnaa 44", "Nizinna")


# --- Provider: document cache ---

GIZA = Address(
    1,
    ProviderId.ENERGA,
    "Nowa Wieś",
    {"citySym": "x", "cityName": "Nowa Wieś", "countyCode": "3020", "communeName": "Gizałki", "communeType": "02"},
)


class Counting:
    def __init__(self, body):
        self.body = body
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return Success(self.body)


def make(fetch, now):
    return EnergaProvider(http=None, cache=InMemoryDocumentCache(), clock=lambda: now[0], fetch=fetch)


def test_document_downloaded_once_per_3_hours():
    fetch = Counting(fixture_text("energa/shutdowns.json"))
    now = [datetime(2026, 10, 14, 10, 0, tzinfo=UTC)]
    p = make(fetch, now)
    window = window_on(OCT14)
    assert len(p.planned_outages(GIZA, window).data) == 1
    now[0] += timedelta(hours=2, minutes=59)
    p.planned_outages(GIZA, window)
    assert fetch.calls == 1
    now[0] += timedelta(minutes=2)
    p.planned_outages(GIZA, window)
    assert fetch.calls == 2


def test_broken_document_is_format_error_and_not_cached():
    fetch = Counting("<html>error</html>")
    p = make(fetch, [datetime(2026, 10, 14, tzinfo=UTC)])
    window = window_on(OCT14)
    assert p.planned_outages(GIZA, window) == FormatError()
    assert p.planned_outages(GIZA, window) == FormatError()
    assert fetch.calls == 2
    fetch.body = '{"document":{}}'
    assert p.planned_outages(GIZA, window) == FormatError()


def test_address_without_town_is_format_error_without_download():
    fetch = Counting(fixture_text("energa/shutdowns.json"))
    params = {k: v for k, v in GIZA.params.items() if k != "cityName"}
    p = make(fetch, [datetime(2026, 10, 14, tzinfo=UTC)])
    assert p.planned_outages(Address(1, ProviderId.ENERGA, "x", params), window_on(OCT14)) == FormatError()
    assert fetch.calls == 0


def test_hand_typed_address_without_teryt_codes_still_checked():
    params = {"cityName": "Nowa Wieś"}
    p = make(Counting(fixture_text("energa/shutdowns.json")), [datetime(2026, 10, 14, tzinfo=UTC)])
    assert isinstance(p.planned_outages(Address(1, ProviderId.ENERGA, "x", params), window_on(OCT14)), Success)


def test_file_cache_round_trip(tmp_path):
    cache = energa.FileDocumentCache(tmp_path / "sub" / "energa.json")
    assert cache.read() is None
    at = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    cache.write(at, "{}")
    assert cache.read() == (at, "{}")
