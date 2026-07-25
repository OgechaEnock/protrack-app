"""
Matches device-registry entries to WhatsApp recharge events, and computes
each device's next recharge due date (last recharge + 3 months).
"""

import re
from datetime import date, datetime


def _normalize(s):
    """Uppercase, alphanumerics only - used to compare plate/device tokens."""
    if not s:
        return ""
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _add_months(d, months):
    """Adds `months` calendar months to a date, clamping the day if needed
    (e.g. Jan 31 + 1 month -> Feb 28/29)."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = d.day
    while True:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1  # walk back until a valid day exists in that month


def _match_device_to_registry(device_token, registry_index):
    """
    registry_index: dict of normalized_plate -> registry record
    Tries exact match first, then substring containment (either direction)
    for cases like registry plate "KCJ616H hilux" vs chat token "KCJ616H".
    """
    norm = _normalize(device_token)
    if not norm:
        return None

    if norm in registry_index:
        return registry_index[norm]

    for reg_norm, record in registry_index.items():
        if len(norm) >= 4 and len(reg_norm) >= 4:
            if norm in reg_norm or reg_norm in norm:
                return record
    return None


def build_schedule(registry, recharge_events, recharge_interval_months=3, due_soon_days=14):
    """
    registry: list of dicts from device_registry.load_registry()
    recharge_events: list of dicts from whatsapp_parser.load_all() (events only)

    Returns (schedule, unmatched_events):
        schedule: one row per registry device with recharge history + due date
        unmatched_events: recharge events that didn't match any registry device
    """
    registry_index = {_normalize(r["plate"]): r for r in registry}

    # Group matched events per registry plate (by normalized key)
    events_by_plate = {}
    unmatched_events = []

    for event in recharge_events:
        record = _match_device_to_registry(event["device"], registry_index)
        if record is None:
            unmatched_events.append(event)
            continue
        key = _normalize(record["plate"])
        events_by_plate.setdefault(key, []).append(event)

    today = datetime.now().date()
    schedule = []

    for record in registry:
        key = _normalize(record["plate"])
        history = sorted(
            events_by_plate.get(key, []),
            key=lambda e: (e["date"] or "", e["time"] or ""),
            reverse=True,
        )

        last_event = history[0] if history else None
        last_date = None
        next_due = None
        status = "no_data"
        days_until_due = None

        if last_event and last_event["date"]:
            try:
                last_date = datetime.strptime(last_event["date"], "%Y-%m-%d").date()
                next_due_date = _add_months(last_date, recharge_interval_months)
                next_due = next_due_date.isoformat()
                days_until_due = (next_due_date - today).days
                if days_until_due < 0:
                    status = "overdue"
                elif days_until_due <= due_soon_days:
                    status = "due_soon"
                else:
                    status = "ok"
            except ValueError:
                pass

        schedule.append({
            "plate": record["plate"],
            "sim": record["sim"],
            "imei": record["imei"],
            "accountName": record["accountName"],
            "clientMobile": record["clientMobile"],
            "lastRechargeDate": last_event["date"] if last_event else None,
            "lastRechargeAmount": last_event["amount"] if last_event else None,
            "rechargeCount": len(history),
            "nextDueDate": next_due,
            "daysUntilDue": days_until_due,
            "status": status,  # overdue | due_soon | ok | no_data
        })

    # Most urgent first: overdue, then due_soon, then ok, then no_data
    status_order = {"overdue": 0, "due_soon": 1, "ok": 2, "no_data": 3}
    schedule.sort(key=lambda r: (status_order[r["status"]], r["daysUntilDue"] if r["daysUntilDue"] is not None else 99999))

    return schedule, unmatched_events


def summarize(schedule, unmatched_events):
    counts = {"overdue": 0, "due_soon": 0, "ok": 0, "no_data": 0}
    for row in schedule:
        counts[row["status"]] += 1
    return {
        "totalDevices": len(schedule),
        "overdue": counts["overdue"],
        "dueSoon": counts["due_soon"],
        "ok": counts["ok"],
        "noData": counts["no_data"],
        "unmatchedRechargeEvents": len(unmatched_events),
    }