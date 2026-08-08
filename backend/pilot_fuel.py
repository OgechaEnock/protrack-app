"""
Pilot Fuel device store.

Pilot Fuel devices are manually managed (not parsed from WhatsApp). An admin
enters a plate number and the date the device was last given the fixed 70mb
recharge. The next due date is 2 months after that date.

Data is persisted as simple JSON at data/pilot_fuel.json.
"""

import json
import os
from datetime import datetime

from recharge_schedule import _add_months

PILOT_FUEL_DIR = os.environ.get(
    "PILOT_FUEL_DIR", os.path.join(os.path.dirname(__file__), "data", "pilot_fuel")
)
PILOT_FUEL_FILE = os.path.join(PILOT_FUEL_DIR, "pilot_fuel.json")

DUE_SOON_DAYS = 14  # matches admin default for other programs


def _ensure_storage():
    os.makedirs(PILOT_FUEL_DIR, exist_ok=True)
    if not os.path.exists(PILOT_FUEL_FILE):
        with open(PILOT_FUEL_FILE, "w") as f:
            json.dump([], f)


def _normalize_plate(plate):
    if not plate:
        return None
    return str(plate).strip().upper()


def load_devices():
    """Returns a list of dicts: {plate, lastRechargeDate}."""
    _ensure_storage()
    try:
        with open(PILOT_FUEL_FILE, "r") as f:
            records = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    devices = []
    for r in records:
        plate = _normalize_plate(r.get("plate"))
        if not plate:
            continue
        devices.append({
            "plate": plate,
            "lastRechargeDate": r.get("lastRechargeDate"),
        })
    return devices


def _save_devices(devices):
    _ensure_storage()
    with open(PILOT_FUEL_FILE, "w") as f:
        json.dump(devices, f, indent=2)


def add_device(plate, recharge_date):
    """
    Adds or updates a pilot fuel device. `recharge_date` must be a
    'YYYY-MM-DD' string. Returns (record, error_tuple).
    """
    plate = _normalize_plate(plate)
    if not plate:
        return None, ({"error": "Plate number is required."}, 400)

    # Validate the date format
    try:
        parsed_date = datetime.strptime(recharge_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None, ({"error": "Invalid date. Expected YYYY-MM-DD format."}, 400)

    devices = load_devices()
    record = {
        "plate": plate,
        "lastRechargeDate": parsed_date.isoformat(),
    }

    # Replace existing entry for the same plate, otherwise append
    for i, existing in enumerate(devices):
        if _normalize_plate(existing.get("plate")) == plate:
            devices[i] = record
            break
    else:
        devices.append(record)

    _save_devices(devices)
    return record, None


def delete_device(plate):
    """Removes a pilot fuel device by plate. Returns True if deleted."""
    plate = _normalize_plate(plate)
    if not plate:
        return False

    devices = load_devices()
    remaining = [d for d in devices if _normalize_plate(d.get("plate")) != plate]
    if len(remaining) == len(devices):
        return False

    _save_devices(remaining)
    return True


def build_schedule(due_soon_days=DUE_SOON_DAYS):
    """
    Computes the pilot fuel schedule from stored devices.

    Returns (schedule, summary):
        schedule: one row per device with nextDueDate, daysUntilDue, status
        summary:  counts for the summary cards
    """
    devices = load_devices()
    today = datetime.now().date()

    schedule = []
    for d in devices:
        last_date = None
        next_due = None
        days_until_due = None
        status = "no_data"

        try:
            last_date = datetime.strptime(d["lastRechargeDate"], "%Y-%m-%d").date()
            next_due_date = _add_months(last_date, 2)  # 70mb recharge every 2 months
            next_due = next_due_date.isoformat()
            days_until_due = (next_due_date - today).days
            if days_until_due < 0:
                status = "overdue"
            elif days_until_due <= due_soon_days:
                status = "due_soon"
            else:
                status = "ok"
        except (ValueError, TypeError, KeyError):
            pass

        schedule.append({
            "plate": d["plate"],
            "lastRechargeDate": d.get("lastRechargeDate"),
            "amount": "70MB",
            "nextDueDate": next_due,
            "daysUntilDue": days_until_due,
            "status": status,  # overdue | due_soon | ok | no_data
        })

    # Most urgent first: overdue, then due_soon, then ok, then no_data
    status_order = {"overdue": 0, "due_soon": 1, "ok": 2, "no_data": 3}
    schedule.sort(key=lambda r: (status_order[r["status"]], r["daysUntilDue"] if r["daysUntilDue"] is not None else 99999))

    counts = {"overdue": 0, "due_soon": 0, "ok": 0, "no_data": 0}
    for row in schedule:
        counts[row["status"]] += 1

    summary = {
        "totalDevices": len(schedule),
        "overdue": counts["overdue"],
        "dueSoon": counts["due_soon"],
        "ok": counts["ok"],
        "noData": counts["no_data"],
    }

    return schedule, summary