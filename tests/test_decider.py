from datetime import UTC, datetime, timedelta

from electricity_alert.decider import decide
from electricity_alert.models import NotifiedOutage, Outage

# 13.10.2026 12:00 in Poland (UTC+2).
NOW = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
END_OF_TODAY = datetime(2026, 10, 13, 21, 59, 59, tzinfo=UTC)


def outage(uuid, revoked=False, start=datetime(2026, 10, 14, 8, 0), stop=datetime(2026, 10, 14, 16, 0)):
    return Outage(uuid, ("5, 7",), start, stop, revoked)


def run(outages, notified=(), premium=False, paused=False, now=NOW):
    return decide(list(outages), list(notified), notify_revoked=premium, is_paused=paused, now=now)


def uuids(items):
    return [o.uuid for o in items]


def test_new_uuid_is_notified_and_pauses_until_end_of_today():
    d = run([outage("u1")])
    assert uuids(d.new_outages) == ["u1"]
    assert d.revoked_outages == []
    assert d.paused_until == END_OF_TODAY


def test_already_notified_uuid_is_not_repeated():
    d = run([outage("u1")], [NotifiedOutage("u1", False)])
    assert d.new_outages == []
    assert d.paused_until is None


def test_no_outages():
    d = run([])
    assert d.new_outages == [] and d.revoked_outages == [] and d.paused_until is None


def test_revoked_and_never_notified_is_ignored():
    for premium in (False, True):
        d = run([outage("u1", revoked=True)], premium=premium)
        assert d.new_outages == [] and d.revoked_outages == [] and d.paused_until is None


def test_revoked_after_notification_notified_only_when_enabled():
    sent = [NotifiedOutage("u1", False)]
    assert uuids(run([outage("u1", revoked=True)], sent, premium=True).revoked_outages) == ["u1"]
    assert run([outage("u1", revoked=True)], sent, premium=False).revoked_outages == []


def test_revocation_is_sent_only_once():
    d = run([outage("u1", revoked=True)], [NotifiedOutage("u1", True)], premium=True)
    assert d.revoked_outages == []


def test_any_push_pauses_until_end_of_today_also_for_long_outages():
    long = outage("u1", start=datetime(2026, 10, 14, 6), stop=datetime(2026, 10, 16, 18))
    month = outage("u2", start=datetime(2026, 10, 7, 9), stop=datetime(2026, 11, 7, 17))
    assert run([long]).paused_until == END_OF_TODAY
    assert run([month]).paused_until == END_OF_TODAY
    assert run([outage("u3", start=None, stop=None)]).paused_until == END_OF_TODAY


def test_several_new_uuids_at_once():
    d = run(
        [outage("a"), outage("b"), outage("old"), outage("gone", revoked=True)],
        [NotifiedOutage("old", False, NOW)],
    )
    assert uuids(d.new_outages) == ["a", "b"]


def test_reminder_at_most_once_per_day():
    def last_push(hours_ago):
        return [NotifiedOutage("u1", False, NOW - timedelta(hours=hours_ago))]

    assert run([outage("u1")], last_push(20)).new_outages == []
    assert uuids(run([outage("u1")], last_push(21)).new_outages) == ["u1"]
    assert uuids(run([outage("u1")], last_push(48)).new_outages) == ["u1"]


def test_outage_announced_for_today_is_pushed_one_already_over_is_not():
    later = outage("later", start=datetime(2026, 10, 13, 15), stop=datetime(2026, 10, 13, 18))
    ongoing = outage("ongoing", start=datetime(2026, 10, 13, 8), stop=datetime(2026, 10, 13, 14))
    over = outage("over", start=datetime(2026, 10, 13, 8), stop=datetime(2026, 10, 13, 11))
    assert uuids(run([later, ongoing, over]).new_outages) == ["later", "ongoing"]
    assert run([over]).paused_until is None


def test_reminders_only_before_the_outage_day():
    def yesterday(uuid):
        return [NotifiedOutage(uuid, False, NOW - timedelta(hours=30))]

    today = outage("today", start=datetime(2026, 10, 13, 15), stop=datetime(2026, 10, 13, 18))
    since = outage("since", start=datetime(2026, 10, 12, 8), stop=datetime(2026, 10, 14, 16))
    no_date = outage("nodate", start=None, stop=None)
    assert run([today], yesterday("today")).new_outages == []
    assert run([since], yesterday("since")).new_outages == []
    assert run([no_date], yesterday("nodate")).new_outages == []
    assert uuids(run([outage("u1")], yesterday("u1")).new_outages) == ["u1"]


def test_unknown_time_of_last_push_no_reminder():
    assert run([outage("u1")], [NotifiedOutage("u1", False)]).new_outages == []


def test_duplicate_uuid_notified_once():
    assert len(run([outage("u1"), outage("u1")]).new_outages) == 1


def test_paused_address_new_outage_still_pushed():
    d = run([outage("u2"), outage("u1", revoked=True)], [NotifiedOutage("u1", False)], paused=True)
    assert uuids(d.new_outages) == ["u2"]
    assert d.revoked_outages == []
    assert d.paused_until == END_OF_TODAY


def test_paused_address_no_reminder():
    d = run([outage("u1")], [NotifiedOutage("u1", False, NOW - timedelta(hours=30))], paused=True)
    assert d.new_outages == [] and d.paused_until is None


def test_paused_with_revocations_enabled():
    d = run([outage("u2"), outage("u1", revoked=True)], [NotifiedOutage("u1", False)], premium=True, paused=True)
    assert uuids(d.new_outages) == ["u2"]
    assert uuids(d.revoked_outages) == ["u1"]


def test_end_of_today_follows_polish_winter_time():
    winter_now = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
    next_day = outage("u1", start=datetime(2026, 10, 27, 8), stop=datetime(2026, 10, 27, 16))
    assert run([next_day], now=winter_now).paused_until == datetime(2026, 10, 26, 22, 59, 59, tzinfo=UTC)
