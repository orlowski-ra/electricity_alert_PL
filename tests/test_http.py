import httpx

from electricity_alert.http import HttpClient, is_pge_host
from electricity_alert.models import FormatError, HttpError, NetworkError, Success

PGE = "https://power-outage.gkpge.pl/api/x"
OTHER = "https://example.com/x"


def test_404_from_another_host_is_not_retried(server):
    server.enqueue("", status=404)
    server.enqueue("[]")
    assert server.client().get_text(OTHER) == HttpError(404)
    assert len(server.requests) == 1


def test_5xx_is_retried_for_every_host(server):
    server.enqueue("", status=503)
    server.enqueue("[]")
    assert server.client().get_text(OTHER) == Success("[]")
    assert len(server.requests) == 2


def test_gives_up_after_max_retries(server):
    server.route = lambda request: httpx.Response(404)
    assert server.client(max_retries=5).get_text(PGE) == HttpError(404)
    assert len(server.requests) == 6


def test_retry_delay_grows_with_attempts(server):
    sleeps = []
    server.enqueue("", status=500)
    server.enqueue("", status=500)
    server.enqueue("ok")
    HttpClient(transport=httpx.MockTransport(server.handle), retry_delay_seconds=0.2, sleep=sleeps.append).get_text(OTHER)
    assert sleeps == [0.2, 0.4]


def test_pge_requests_close_the_connection(server):
    server.enqueue("[]")
    server.client().get_text(PGE)
    assert server.requests[0].headers["Connection"] == "close"


def test_sticky_cookie_kept_only_from_healthy_responses(server):
    server.enqueue("[]", headers={"Set-Cookie": "COK2APER=healthy; Path=/"})
    server.enqueue("[]")
    server.enqueue("", status=404, headers={"Set-Cookie": "COK2APER=broken; Path=/"})
    server.enqueue("[]")
    client = server.client()
    client.get_text(PGE)
    client.get_text(PGE)
    assert server.requests[1].headers["Cookie"] == "COK2APER=healthy"
    client.get_text(PGE)  # 404 then retry
    assert server.requests[2].headers["Cookie"] == "COK2APER=healthy"
    assert "Cookie" not in server.requests[3].headers  # forgotten after the broken node


def test_network_error_is_a_value(server):
    def fail(request):
        raise httpx.ConnectError("no network")

    server.route = fail
    assert server.client().get_text(OTHER) == NetworkError()


def test_json_errors_are_values(server):
    server.enqueue("<html>")
    assert server.client().get_json(OTHER) == FormatError()


def test_none_params_are_dropped(server):
    server.enqueue("[]")
    server.client().get_text(OTHER, {"a": "1", "b": None})
    assert dict(server.requests[0].url.params) == {"a": "1"}


def test_pge_host_check():
    assert is_pge_host("gkpge.pl")
    assert is_pge_host("power-outage.gkpge.pl")
    assert not is_pge_host("notgkpge.pl")
    assert not is_pge_host("tauron.pl")
