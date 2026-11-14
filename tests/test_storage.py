from datetime import UTC, datetime, timedelta

from electricity_alert.models import Outage, ProviderId
from electricity_alert.storage import Storage

NOW = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)


def test_addresses_round_trip_and_remove(tmp_path):
    db = Storage(tmp_path / "data.db")
    a = db.add_address(ProviderId.PGE, "Warszawa, ul. Marszałkowska", {"citySym": "0918123", "streetSym": "11793"})
    db.set_paused_until(a.id, NOW)
    (loaded,) = db.addresses()
    assert loaded.label == "Warszawa, ul. Marszałkowska"
    assert loaded.params == {"citySym": "0918123", "streetSym": "11793"}
    assert loaded.paused_until == NOW
    assert db.remove_address(a.id)
    assert not db.remove_address(a.id)
    assert db.addresses() == []


def test_mark_notified_first_time_then_reminder():
    db = Storage()
    a = db.add_address(ProviderId.STOEN, "Warszawa", {"street": "ul. Koszykowa"})
    outage = Outage("u1", start_at=datetime(2026, 10, 14, 8))
    assert db.mark_notified(a.id, outage, NOW) is True
    assert db.mark_notified(a.id, outage, NOW + timedelta(hours=21)) is False
    (n,) = db.notified_for(a.id)
    assert n.uuid == "u1" and n.notified_at == NOW + timedelta(hours=21) and not n.revoked_notified
    db.mark_revoked_notified(a.id, "u1")
    assert db.notified_for(a.id)[0].revoked_notified


def test_cleanup_removes_entries_older_than_30_days():
    db = Storage()
    a = db.add_address(ProviderId.STOEN, "Warszawa", {"street": "x"})
    db.mark_notified(a.id, Outage("old"), NOW - timedelta(days=31))
    db.mark_notified(a.id, Outage("new"), NOW - timedelta(days=29))
    assert db.cleanup_old_notified(NOW) == 1
    assert [n.uuid for n in db.notified_for(a.id)] == ["new"]


def test_removing_address_removes_its_notifications():
    db = Storage()
    a = db.add_address(ProviderId.STOEN, "Warszawa", {"street": "x"})
    db.mark_notified(a.id, Outage("u1"), NOW)
    db.remove_address(a.id)
    assert db.notified_for(a.id) == []
