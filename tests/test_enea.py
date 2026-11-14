from datetime import date, datetime
from urllib.parse import parse_qs

from conftest import fixture_text, window_on

from electricity_alert.models import Address, FormatError, ProviderId, Success, TimeWindow
from electricity_alert.providers import enea
from electricity_alert.providers.enea import EneaProvider


def address(city, street=None):
    params = {"city": city}
    if street is not None:
        params["street"] = street
    return Address(1, ProviderId.ENEA, city, params)


def test_posts_search_form_with_city_and_street_without_prefix(server):
    server.enqueue(fixture_text("enea/none.html"))
    EneaProvider(server.client()).planned_outages(address("Szczecin", "ul. Górnicza"), window_on(date(2026, 10, 15)))
    request = server.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/"
    assert request.content.decode() == "page=unpl&unpl_id=&unpl_city=Szczecin&unpl_street=G%C3%B3rnicza"


def test_full_teryt_street_name_only_key_word_sent(server):
    server.enqueue(fixture_text("enea/none.html"))
    EneaProvider(server.client()).planned_outages(address("Poznań", "ul. Stefana Żeromskiego"), window_on(date(2026, 10, 15)))
    assert parse_qs(server.requests[0].content.decode())["unpl_street"] == ["Żeromskiego"]


def test_real_page_only_tomorrow_and_only_our_town(server):
    server.enqueue(fixture_text("enea/poznan_city.html"))
    result = EneaProvider(server.client()).planned_outages(address("Poznań"), window_on(date(2026, 10, 15)))
    assert isinstance(result, Success) and result.data
    assert all(o.start_at.date() == date(2026, 10, 15) for o in result.data)
    assert all(enea.mentions_town(o.description, "Poznań") for o in result.data)
    assert not any(o.description.startswith("Chociwel") for o in result.data)


def test_page_without_results_empty_foreign_page_format_error(server):
    server.enqueue(fixture_text("enea/none.html"))
    server.enqueue("<html><body>Przerwa techniczna</body></html>")
    p = EneaProvider(server.client())
    window = window_on(date(2026, 10, 15))
    assert p.planned_outages(address("Xyz"), window) == Success([])
    assert p.planned_outages(address("Xyz"), window) == FormatError()


def test_address_without_city_is_format_error_without_request(server):
    no_city = Address(1, ProviderId.ENEA, "x", {})
    assert EneaProvider(server.client()).planned_outages(no_city, window_on(date(2026, 10, 15))) == FormatError()
    assert server.requests == []


MULTI_DAY = """<div class="unpl_cont"><div class="unpl block info">
    <p class="bold subtext">13 października 2026 r. w godz. 07:00 - 14:00<br />14 października 2026 r. w godz. 09:00 - 15:00
    <!-- 2026-10-13 - 2026-10-14, w godz. 2026-10-13 07:00 - 2026-10-13 14:00, 2026-10-14 09:00 - 2026-10-14 15:00 --></p>
    <p class="description">Kwilcz ul. Polna&nbsp;1, &quot;Pod Lipami&quot;</p>
    <!-- 11111111-2222-3333-4444-555555555555 --></div></div>"""


def test_multi_day_block():
    (block,) = enea.parse(MULTI_DAY)
    assert len(block.intervals) == 2
    assert block.description == 'Kwilcz ul. Polna 1, "Pod Lipami"'
    day14 = block.to_outage(window_on(date(2026, 10, 14)))
    assert (day14.start_at, day14.stop_at) == (datetime(2026, 10, 14, 9), datetime(2026, 10, 14, 15))
    assert block.to_outage(window_on(date(2026, 10, 15))) is None
    both = block.to_outage(TimeWindow(datetime(2026, 10, 13), datetime(2026, 10, 16, 23, 59, 59)))
    assert (both.start_at, both.stop_at) == (datetime(2026, 10, 13, 7), datetime(2026, 10, 14, 15))
    assert both.description == '13.10 07:00–14:00, 14.10 09:00–15:00\nKwilcz ul. Polna 1, "Pod Lipami"'


def test_revoked_block_marked_word_in_description_does_not_count():
    html = """<div class="unpl_cont"><div class="unpl block info">
      <p class="bold subtext">
        <!-- 2026-10-15, w godz. 2026-10-15 08:00 - 2026-10-15 12:00 -->
        <p class="bold red">WYŁĄCZENIE ODWOŁANE</p><br />
      </p>
      <p class="description">Gniezno ul. Roosevelta 122, 118, 89A</p>
      <!-- a6c87cb8-8982-4dda-ad8c-a17bbbd33fca -->
    </div>
    <div class="unpl block info">
      <p class="bold subtext"><!-- 2026-10-15, w godz. 2026-10-15 08:00 - 2026-10-15 12:00 --></p>
      <p class="description">Gniezno, wyłączenie odwołane wcześniej zostało przywrócone</p>
      <!-- 11111111-2222-3333-4444-555555555555 -->
    </div></div>"""
    blocks = enea.parse(html)
    assert blocks[0].revoked
    assert blocks[0].to_outage(window_on(date(2026, 10, 15))).revoked
    assert not blocks[1].revoked


def test_town_mention_is_a_whole_word():
    assert enea.mentions_town("Poznań: ul. Obrzeże", "Poznań")
    assert enea.mentions_town("miejscowość: Okonek ul. Niepodległości", "okonek")
    assert not enea.mentions_town("Chociwel ul. Poznańska 5, 9", "Poznań")
    assert not enea.mentions_town(None, "Poznań")
