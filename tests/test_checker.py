from datetime import UTC, datetime, timedelta

from electricity_alert.checker import OutageChecker
from electricity_alert.models import NetworkError, Outage, ProviderId, Success
from electricity_alert.providers import ProviderRegistry
from electricity_alert.storage import Storage

# 13.10.2026 12:00 in Warsaw.
NOW = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
TOMORROW = Outage("u1", ("5",), datetime(2026, 10, 14, 8), datetime(2026, 10, 14, 16))


class FakeProvider:
    id = ProviderId.STOEN
    display_name = "Fake"

    def __init__(self, result):
        self.result = result
        self.windows = []

    def planned_outages(self, address, window):
        self.windows.append(window)
        return self.result


class Notifier:
    def __init__(self, shows=True):
        self.shows = shows
        self.new = []
        self.revoked = []
        self.silent = []

    def show_new(self, address, outage, silent):
        self.new.append(outage.uuid)
        self.silent.append(silent)
        return self.shows

    def show_revoked(self, address, outage, silent):
        self.revoked.append(outage.uuid)
        return self.shows


def setup(result, shows=True, now=NOW, quiet=False):
    storage = Storage()
    address = storage.add_address(ProviderId.STOEN, "Warszawa, ul. Koszykowa", {"street": "ul. Koszykowa"})
    provider = FakeProvider(result)
    notifier = Notifier(shows)
    clock = [now]
    checker = OutageChecker(storage, ProviderRegistry([provider]), notifier, clock=lambda: clock[0], is_quiet=lambda _: quiet)
    return storage, address, provider, notifier, checker, clock


def test_new_outage_notified_once_and_address_paused():
    storage, address, _, notifier, checker, _ = setup(Success([TOMORROW]))
    report = checker.run()
    assert notifier.new == ["u1"]
    assert [o.uuid for _, o in report.notified] == ["u1"]
    assert storage.addresses()[0].paused_until == datetime(2026, 10, 13, 21, 59, 59, tzinfo=UTC)
    checker.run()
    assert notifier.new == ["u1"]


def test_reminder_next_day_before_the_outage():
    _, _, _, notifier, checker, clock = setup(Success([Outage("u1", (), datetime(2026, 10, 16, 8), datetime(2026, 10, 16, 16))]))
    checker.run(days=4)
    clock[0] = NOW + timedelta(hours=22)
    checker.run(days=4)
    assert notifier.new == ["u1", "u1"]


def test_nothing_recorded_when_notification_not_shown():
    storage, address, _, notifier, checker, _ = setup(Success([TOMORROW]), shows=False)
    checker.run()
    assert storage.notified_for(address.id) == []
    assert storage.addresses()[0].paused_until is None


def test_revocation_after_notification():
    storage, address, provider, notifier, checker, _ = setup(Success([TOMORROW]))
    checker.run()
    provider.result = Success([Outage("u1", revoked=True)])
    checker.run()
    assert notifier.revoked == ["u1"]
    checker.run()
    assert notifier.revoked == ["u1"]


def test_revocation_disabled():
    _, _, provider, notifier, checker, _ = setup(Success([TOMORROW]))
    checker.run()
    provider.result = Success([Outage("u1", revoked=True)])
    checker.run(notify_revoked=False)
    assert notifier.revoked == []


def test_errors_reported_not_raised():
    _, address, _, notifier, checker, _ = setup(NetworkError())
    report = checker.run()
    assert report.results[address.id] == NetworkError()
    assert report.connection_problem
    assert notifier.new == []


def test_days_clamped_to_1_4():
    _, _, provider, _, checker, _ = setup(Success([]))
    checker.run(days=9)
    checker.run(days=0)
    assert provider.windows[0].end_text == "2026-10-17 23:59:59"
    assert provider.windows[1].end_text == "2026-10-14 23:59:59"


def test_quiet_hours_make_notifications_silent():
    _, _, _, notifier, checker, _ = setup(Success([TOMORROW]), quiet=True)
    checker.run()
    assert notifier.silent == [True]
