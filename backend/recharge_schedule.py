"""
Matches device-registry entries to WhatsApp recharge events, and computes
each device's next recharge due date (last recharge + 3 months).

The plate-matching logic (`build_registry_index` / `match_device_to_registry`)
is the single reused component behind three things:
    1. build_schedule()     - per-device due-date schedule (Protrack/Mettax/Pilot tabs)
    2. annotate_events()    - tags each raw recharge message matched/unmatched,
                               for the small ✅ / ❌ icon in the UI
    3. app.py's RechargeLog resource, via annotate_events()
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


def build_registry_index(registry):
    """registry: list of dicts from device_registry.load_registry().
    Returns dict of normalized_plate -> registry record."""
    return {_normalize(r["plate"]): r for r in registry}


def match_device_to_registry(device_token, registry_index):
    """
    Matches a plate/device token (as texted in WhatsApp) against the
    device registry. registry_index comes from build_registry_index().
    Tries exact match first, then substring containment (either direction)
    for cases like registry plate "KCJ616H hilux" vs chat token "KCJ616H".
    Returns the matching registry record, or None.
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


def annotate_events(recharge_events, registry):
    """
    Tags each recharge event with whether its plate/device token matched a
    device in the registry, for the matched/unmatched icon in the UI.
    Returns a new list of event dicts (originals untouched) with two extra
    fields: "matched" (bool) and "matchedPlate" (the registry plate it
    matched, or None).
    """
    registry_index = build_registry_index(registry)
    annotated = []
    for event in recharge_events:
        record = match_device_to_registry(event["device"], registry_index)
        annotated.append({
            **event,
            "matched": record is not None,
            "matchedPlate": record["plate"] if record else None,
        })
    return annotated


def build_schedule(registry, recharge_events, recharge_interval_months=3, due_soon_days=14,
                    category=None, include_no_data=None):
    """
    registry: list of dicts from device_registry.load_registry()
    recharge_events: list of dicts from whatsapp_parser.load_all() (events only)
    category: optional 'protrack' | 'mettax' | 'pilot' - if given, only events
        tagged with that category are considered (used for the dedicated
        Protrack/Mettax/Pilot sections, which all reuse this same function).
        Devices with no recharge history in that category are excluded by
        default (include_no_data=False) so the section only shows devices
        actually on that program.
    include_no_data: whether to keep registry devices with zero matching
        recharge events in the output. Defaults to True for the
        all-categories view, False when filtering to a single category.

    Returns (schedule, unmatched_events):
        schedule: one row per registry device with recharge history + due date
        unmatched_events: recharge events (within the category filter, if any)
            that didn't match any registry device
    """
    if category:
        recharge_events = [e for e in recharge_events if e.get("category") == category]
        if include_no_data is None:
            include_no_data = False
    elif include_no_data is None:
        include_no_data = True

    registry_index = build_registry_index(registry)

    # Group matched events per registry plate (by normalized key)
    events_by_plate = {}
    unmatched_events = []

    for event in recharge_events:
        record = match_device_to_registry(event["device"], registry_index)
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

        if not include_no_data and last_event is None:
            continue

        schedule.append({
            "plate": record["plate"],
            "sim": record["sim"],
            "imei": record["imei"],
            "accountName": record["accountName"],
            "clientMobile": record["clientMobile"],
            "lastRechargeDate": last_event["date"] if last_event else None,
            "lastRechargeAmount": last_event["amount"] if last_event else None,
            "lastRechargeCategory": last_event["category"] if last_event else None,
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