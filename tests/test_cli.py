import io
from datetime import UTC, datetime

import httpx
from conftest import FakeServer, fixture_text

from electricity_alert.cli import main

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)  # 08:00 in Warsaw


def run(args, tmp_path, server=None, now=NOW):
    out = io.StringIO()
    http = (server or FakeServer()).client()
    code = main(["--home", str(tmp_path), *args], out=out, http=http, now=now)
    return code, out.getvalue()


def test_providers(tmp_path):
    code, out = run(["providers"], tmp_path)
    assert code == 0
    assert "pge" in out and "stoen" in out and "Energa-Operator" in out


def test_add_list_remove(tmp_path):
    code, out = run(["add", "stoen", "--label", "Warszawa, ul. Bruzdowa", "--param", "street=ul. Bruzdowa"], tmp_path)
    assert code == 0 and "saved address 1" in out
    code, out = run(["list"], tmp_path)
    assert "Warszawa, ul. Bruzdowa" in out and "street=ul. Bruzdowa" in out
    assert run(["remove", "1"], tmp_path)[0] == 0
    assert run(["remove", "1"], tmp_path)[0] == 1


def test_bad_param_is_a_usage_error(tmp_path):
    code, out = run(["add", "stoen", "--label", "x", "--param", "street"], tmp_path)
    assert code == 2 and "key=value" in out


def stoen_server():
    server = FakeServer()
    server.route = lambda request: httpx.Response(200, content=fixture_text("stoen/outages.json"))
    return server


def test_check_prints_new_outage_once(tmp_path):
    run(["add", "stoen", "--label", "Warszawa, ul. Bruzdowa", "--param", "street=ul. Bruzdowa"], tmp_path)
    code, out = run(["check"], tmp_path, stoen_server())
    assert code == 0
    assert "[NEW] Warszawa, ul. Bruzdowa" in out
    assert "15.10.2026 07:30 – 18:00" in out
    assert "1 notification(s)" in out
    code, out = run(["check"], tmp_path, stoen_server())
    assert "[NEW]" not in out and "0 notification(s)" in out


def test_check_reports_errors_with_exit_code(tmp_path):
    run(["add", "stoen", "--label", "Warszawa", "--param", "street=ul. Bruzdowa"], tmp_path)
    server = FakeServer()

    def fail(request):
        raise httpx.ConnectError("offline")

    server.route = fail
    code, out = run(["check"], tmp_path, server)
    assert code == 1 and "[ERROR] Warszawa: network error" in out


def test_outages_without_saving(tmp_path):
    code, out = run(["outages", "stoen", "--param", "street=ul. Bruzdowa"], tmp_path, stoen_server())
    assert code == 0 and "stoen-168534" in out
    assert run(["list"], tmp_path)[1] == ""


def test_search_pge(tmp_path):
    server = FakeServer()
    server.route = lambda request: httpx.Response(200, content=fixture_text("pge/cities.json"))
    code, out = run(["search", "pge", "Babice"], tmp_path, server)
    assert code == 0 and "0005167\tStare Babice" in out


def test_streets_tauron(tmp_path):
    server = FakeServer()
    server.route = lambda request: httpx.Response(200, content=fixture_text("tauron/onlyonestreet_one.json"))
    code, out = run(["streets", "tauron", "1"], tmp_path, server)
    assert code == 0 and "923336\tCzerwienne" in out
    assert run(["streets", "tauron", "abc"], tmp_path, server)[0] == 2
