"""Pure notification logic for one address.

- A new outage (not revoked, not finished, never notified) is notified at once, also while the
  address is paused and on the outage day itself.
- A reminder about an already notified outage: at most once per ~day (REPEAT_AFTER since the last
  notification), only when the address is not paused and the outage starts tomorrow or later.
- Revoked outages (optional): notified once if we told the user about the outage before.
- Revoked and never notified: ignored.
- After a notification the address is paused until the end of today (no more reminders today).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, tzinfo

from .models import WARSAW, NotificationDecision, NotifiedOutage, Outage

# "Once a day", but 21 h: checks run every 3 h, so 24 h would drift a few hours later every day.
REPEAT_AFTER = timedelta(hours=21)


def decide(
    outages: list[Outage],
    notified: list[NotifiedOutage],
    notify_revoked: bool,
    is_paused: bool,
    now: datetime,
    zone: tzinfo = WARSAW,
) -> NotificationDecision:
    sent = {n.uuid: n for n in notified}
    unique: dict[str, Outage] = {}
    for outage in outages:
        unique.setdefault(outage.uuid, outage)
    local = now.astimezone(zone).replace(tzinfo=None)

    new = [
        o
        for o in unique.values()
        if not o.revoked and not _ended_by(o, local) and _due_for_push(o, sent.get(o.uuid), now, local.date(), is_paused)
    ]
    revoked = [o for o in unique.values() if o.revoked and o.uuid in sent and not sent[o.uuid].revoked_notified] if notify_revoked else []
    paused_until = _end_of_today(now, zone) if new else None
    return NotificationDecision(new_outages=new, revoked_outages=revoked, paused_until=paused_until)


def _due_for_push(outage: Outage, previous: NotifiedOutage | None, now: datetime, today: date, is_paused: bool) -> bool:
    if previous is None:
        return True
    if is_paused:
        return False
    if outage.start_at is None or outage.start_at.date() <= today:
        return False
    if previous.notified_at is None:
        return False
    return now >= previous.notified_at + REPEAT_AFTER


def _ended_by(outage: Outage, local_now: datetime) -> bool:
    return outage.stop_at is not None and outage.stop_at <= local_now


def _end_of_today(now: datetime, zone: tzinfo) -> datetime:
    local_day = now.astimezone(zone).date()
    return datetime.combine(local_day, time(23, 59, 59), tzinfo=zone)
