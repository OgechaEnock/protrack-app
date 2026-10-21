"""
Safaricom airtime line management - store + domain logic.

Persisted in the existing SQLite database (backend/data/protrack.db by
default) alongside the other ProTrack data, so the project keeps a single
database for local development.

TWO TABLES
    airtime_lines       - the registered Safaricom lines and their schedule
    recharge_attempts   - every recharge attempt (started / pending / confirmed
                          / failed / cancelled)

WHAT "EXPIRED" ACTUALLY MEANS HERE
---------------------------------
This module deliberately keeps two concepts apart:

  1. REMINDER OVERDUE - our own weekly reminder date has passed. This says
     nothing about the real SIM state. Those lines are shown as
     "Overdue reminder - SIM expiry unverified".

  2. EXPIRY REPORTED - a human (or a trusted integration) explicitly told us
     the SIM has expired. Only those lines may be labelled "SIM expiry
     reported".

We never claim Safaricom itself verified a SIM's expiry, because nothing in
this codebase talks to Safaricom's systems.

STATE MACHINE (per recharge attempt)
    scheduled -> due -> attempt_started -> pending_confirmation -> confirmed
                                \\-> failed / cancelled

Only a CONFIRMED attempt may:
    * set last_confirmed_recharge_date
    * count as a successful transaction
    * reset the weekly next_reminder_date

Failed and cancelled attempts never move the reminder date, so a failed
attempt can be retried without losing the original schedule.
"""

import os
import sqlite3
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BACKEND_DIR, "data", "protrack.db")

# Default reminder frequency: every 7 days (weekly).
DEFAULT_FREQUENCY_DAYS = 7
MIN_FREQUENCY_DAYS = 1
MAX_FREQUENCY_DAYS = 90

DEFAULT_REMINDER_TIME = "09:00"
DEFAULT_TIMEZONE = "Africa/Nairobi"

# Smallest / largest airtime we allow (whole Kenyan shillings).
MIN_AMOUNT = 1
MAX_AMOUNT = 100000

# Line management statuses.
STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_EXPIRY_REPORTED = "expiry_reported"

# Derived (read-time) statuses surfaced to the UI.
STATUS_DUE = "due"
STATUS_OVERDUE = "overdue"
STATUS_RECHARGE_PENDING = "recharge_pending"
STATUS_RECHARGE_CONFIRMED = "recharge_confirmed"
STATUS_RECHARGE_FAILED = "recharge_failed"

ALL_STATUSES = (
    STATUS_ACTIVE,
    STATUS_DUE,
    STATUS_OVERDUE,
    STATUS_EXPIRY_REPORTED,
    STATUS_RECHARGE_PENDING,
    STATUS_RECHARGE_CONFIRMED,
    STATUS_RECHARGE_FAILED,
    STATUS_PAUSED,
)

STATUS_LABELS = {
    STATUS_ACTIVE: "Active",
    STATUS_DUE: "Reminder due",
    STATUS_OVERDUE: "Overdue reminder",
    STATUS_EXPIRY_REPORTED: "SIM expiry reported",
    STATUS_RECHARGE_PENDING: "Recharge awaiting confirmation",
    STATUS_RECHARGE_CONFIRMED: "Recharge confirmed",
    STATUS_RECHARGE_FAILED: "Recharge failed",
    STATUS_PAUSED: "Paused",
}

# Reason text shown on the Expired tab. Deliberately explicit that an
# overdue reminder is NOT proof that the SIM expired.
REASON_OVERDUE = "Overdue reminder - SIM expiry unverified"
REASON_DUE = "Reminder due - SIM expiry unverified"
REASON_EXPIRY_REPORTED = "SIM expiry reported"
REASON_PENDING = "Recharge started - awaiting confirmation"
REASON_FAILED = "Last recharge attempt failed - SIM expiry unverified"

# Attempt statuses.
ATTEMPT_STARTED = "attempt_started"
ATTEMPT_PENDING = "pending_confirmation"
ATTEMPT_CONFIRMED = "confirmed"
ATTEMPT_FAILED = "failed"
ATTEMPT_CANCELLED = "cancelled"

ATTEMPT_STATUSES = (
    ATTEMPT_STARTED,
    ATTEMPT_PENDING,
    ATTEMPT_CONFIRMED,
    ATTEMPT_FAILED,
    ATTEMPT_CANCELLED,
)

ATTEMPT_LABELS = {
    ATTEMPT_STARTED: "Started",
    ATTEMPT_PENDING: "Awaiting confirmation",
    ATTEMPT_CONFIRMED: "Confirmed",
    ATTEMPT_FAILED: "Failed",
    ATTEMPT_CANCELLED: "Cancelled",
}

# Statuses that make a line "need attention" (i.e. show on the Expired tab).
ATTENTION_STATUSES = (
    STATUS_OVERDUE,
    STATUS_EXPIRY_REPORTED,
    STATUS_RECHARGE_PENDING,
    STATUS_RECHARGE_FAILED,
    STATUS_DUE,
)

# Write lock: SQLite handles concurrent reads fine, but we serialise writes
# so two simultaneous confirmations cannot both create a transaction.
_WRITE_LOCK = threading.Lock()


class ValidationError(ValueError):
    """Raised for bad user input. The API turns this into a 400."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.message = message
        self.field = field


class DuplicateError(ValueError):
    """Raised when a phone number is already registered. API turns this into 409."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.message = message
        self.field = field


class NotFoundError(ValueError):
    """Raised when a referenced record does not exist. API turns this into 404."""

    def __init__(self, message="Not found."):
        super().__init__(message)
        self.message = message


class ConflictError(ValueError):
    """Raised for an illegal state transition (e.g. double confirmation). 409."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS airtime_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        label TEXT NOT NULL,
        phone_number TEXT NOT NULL UNIQUE,
        phone_display TEXT NOT NULL,
        carrier_hint TEXT,
        amount INTEGER NOT NULL DEFAULT 10,
        currency TEXT NOT NULL DEFAULT 'KES',
        reminder_frequency_days INTEGER NOT NULL DEFAULT 7,
        next_reminder_date TEXT,
        reminder_time TEXT NOT NULL DEFAULT '09:00',
        timezone TEXT NOT NULL DEFAULT 'Africa/Nairobi',
        last_confirmed_recharge_date TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        expiry_reported INTEGER NOT NULL DEFAULT 0,
        expiry_reported_at TEXT,
        expiry_source TEXT,
        expiry_note TEXT,
        paused INTEGER NOT NULL DEFAULT 0,
        archived INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recharge_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        line_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        ussd_string TEXT,
        status TEXT NOT NULL DEFAULT 'attempt_started',
        transaction_ref TEXT,
        note TEXT,
        outcome TEXT,
        initiated_at TEXT NOT NULL,
        resolved_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (line_id) REFERENCES airtime_lines (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS airtime_schema_migrations (
        version TEXT PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_attempts_line ON recharge_attempts (line_id)",
    "CREATE INDEX IF NOT EXISTS idx_lines_status ON airtime_lines (status, archived)",
    # An older draft of this schema used a unique index on (line_id, outcome),
    # which wrongly allowed only ONE confirmed recharge per line, ever. It is
    # replaced below by a per-calendar-day index.
    "DROP INDEX IF EXISTS idx_attempts_unique_confirmed",
    # At most one confirmed transaction per line per calendar day - stops a
    # double-clicked "Confirm" from writing the same day's transaction twice,
    # while still allowing the next week's recharge to be confirmed.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_attempts_unique_confirmed_day
        ON recharge_attempts (line_id, date(resolved_at))
        WHERE status = 'confirmed'
    """,
]

MIGRATION_VERSION = "airtime-001"


def db_path():
    """
    Resolved at call time (not import time) so tests can point the store at a
    temporary database via the AIRTIME_DB_PATH environment variable.
    """
    return os.environ.get("AIRTIME_DB_PATH") or DEFAULT_DB_PATH


def _connect():
    path = db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def ensure_schema():
    """Creates the airtime tables if they do not exist yet. Safe to call often."""
    with _WRITE_LOCK:
        conn = _connect()
        try:
            for statement in SCHEMA:
                conn.execute(statement)
            conn.execute(
                "INSERT OR IGNORE INTO airtime_schema_migrations (version, applied_at) "
                "VALUES (?, ?)",
                (MIGRATION_VERSION, _now_iso()),
            )
            conn.commit()
        finally:
            conn.close()


def _now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def _today(tz_name=DEFAULT_TIMEZONE):
    """Today's date in the line's time zone (defaults to Africa/Nairobi)."""
    try:
        tz = ZoneInfo(tz_name or DEFAULT_TIMEZONE)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    return datetime.now(tz).date()


# _SCHEMA_READY tracks the db path we last created the schema for, so we do
# not re-run DDL on every request.
_SCHEMA_READY = {"path": None}


def ensure_ready():
    """Ensures the schema exists for the *current* database path."""
    path = db_path()
    if _SCHEMA_READY["path"] != path:
        ensure_schema()
        _SCHEMA_READY["path"] = path


# --------------------------------------------------------------------------
# Phone number validation / normalisation
# --------------------------------------------------------------------------

# Kenyan mobile prefixes: 07xx and 01xx. Safaricom's own ranges are listed
# below - a non-Safaricom prefix is flagged as a *warning* (a hint), never as
# a hard error, because number reallocation happens.
SAFARICOM_PREFIXES = ("011", "012", "070", "071", "072", "074", "077", "078", "079")
SAFARICOM_PREFIXES_LONG = ("0757", "0763", "0766", "0767")


def normalize_phone(raw):
    """
    Normalises a Kenyan mobile number to E.164 without the '+'
    (e.g. 0712345678 -> 254712345678).

    Accepts: 0712345678, 254712345678, +254712345678, 00712345678.
    Raises ValidationError for anything that is not a Kenyan mobile number.
    """
    if raw is None:
        raise ValidationError("A phone number is required.", field="phoneNumber")
    if not isinstance(raw, (str, int)):
        raise ValidationError("Invalid phone number.", field="phoneNumber")

    phone = str(raw).strip()
    if not phone:
        raise ValidationError("A phone number is required.", field="phoneNumber")

    # Strip spaces, dashes, brackets and dots - people paste these.
    for ch in (" ", "-", "(", ")", "."):
        phone = phone.replace(ch, "")
    if not phone:
        raise ValidationError("A phone number is required.", field="phoneNumber")

    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("00"):
        # International access prefix: 00712345678 -> 254712345678
        phone = phone[2:]
        if not phone.startswith("254"):
            phone = "254" + phone

    # Local Kenyan format: 07xxxxxxxx / 01xxxxxxxx (10 digits starting with 0)
    if phone.startswith("0") and len(phone) == 10 and phone[1] in "17":
        phone = "254" + phone[1:]
    elif phone.startswith("254") and len(phone) == 12 and phone[3] in "17":
        pass
    else:
        raise ValidationError(
            "Invalid Kenyan phone number. Use a format like 0712345678 or +254712345678.",
            field="phoneNumber",
        )

    return phone


def display_phone(e164):
    """254712345678 -> 0712345678"""
    if e164 and e164.startswith("254"):
        return "0" + e164[3:]
    return e164 or ""


def mask_phone(e164):
    """
    Masks a phone number for display: 0712345678 -> 0712 *** 678
    Used by default in the UI so numbers are not casually exposed.
    """
    local = display_phone(e164)
    if len(local) < 6:
        return local
    return f"{local[:4]} *** {local[-3:]}"


def carrier_hint(e164):
    """
    Best-effort carrier guess from the prefix. Returns a short label such as
    "Safaricom" or "Other Kenyan carrier (not Safaricom)". This is a HINT
    derived from the number itself - it is NOT a statement from Safaricom.
    """
    if not e164 or len(e164) < 6:
        return "Unknown"
    # Work on the local 10-digit form: 254712345678 -> 0712345678
    local = display_phone(e164)
    if local[:4] in SAFARICOM_PREFIXES_LONG:
        return "Safaricom"
    if local[:3] in SAFARICOM_PREFIXES:
        return "Safaricom"
    return "Other Kenyan carrier (not Safaricom)"


# --------------------------------------------------------------------------
# Other field validation
# --------------------------------------------------------------------------

def validate_label(raw):
    if raw is None:
        raw = ""
    label = str(raw).strip()
    if not label:
        raise ValidationError("A label is required.", field="label")
    if len(label) > 80:
        raise ValidationError("Label must be 80 characters or fewer.", field="label")
    return label


def validate_amount(raw, default=None):
    if raw is None or raw == "":
        if default is None:
            raise ValidationError("An airtime amount is required.", field="amount")
        raw = default
    if isinstance(raw, bool):
        raise ValidationError("Invalid airtime amount.", field="amount")
    if isinstance(raw, str):
        raw = raw.strip()
    try:
        amount = int(raw)
    except (TypeError, ValueError):
        raise ValidationError(
            "Airtime amount must be a whole number of shillings.", field="amount"
        )
    if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
        raise ValidationError(
            f"Airtime amount must be between {MIN_AMOUNT} and {MAX_AMOUNT}.",
            field="amount",
        )
    return amount


def validate_frequency(raw, default=DEFAULT_FREQUENCY_DAYS):
    if raw is None or raw == "":
        return default
    try:
        days = int(raw)
    except (TypeError, ValueError):
        raise ValidationError(
            "Reminder frequency must be a whole number of days.",
            field="reminderFrequencyDays",
        )
    if days < MIN_FREQUENCY_DAYS or days > MAX_FREQUENCY_DAYS:
        raise ValidationError(
            f"Reminder frequency must be between {MIN_FREQUENCY_DAYS} "
            f"and {MAX_FREQUENCY_DAYS} days.",
            field="reminderFrequencyDays",
        )
    return days


def validate_date(raw, field="nextReminderDate", required=True):
    """Validates a YYYY-MM-DD date string and returns it unchanged."""
    if raw is None or raw == "":
        if required:
            raise ValidationError("A date is required.", field=field)
        return None
    value = str(raw).strip()[:10]
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValidationError("Invalid date. Expected YYYY-MM-DD format.", field=field)
    return value


def validate_time(raw, field="reminderTime"):
    if raw is None or raw == "":
        return DEFAULT_REMINDER_TIME
    value = str(raw).strip()
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError:
        raise ValidationError("Invalid time. Expected HH:MM (24-hour) format.", field=field)
    return value


def validate_timezone(raw, field="timezone"):
    if raw is None or raw == "":
        return DEFAULT_TIMEZONE
    value = str(raw).strip()
    try:
        ZoneInfo(value)
    except Exception:
        raise ValidationError("Unknown time zone identifier.", field=field)
    return value


# --------------------------------------------------------------------------
# Status computation + row serialisation
# --------------------------------------------------------------------------

def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _latest_attempt(conn, line_id):
    """Returns the most recent attempt row for a line, or None."""
    return conn.execute(
        "SELECT * FROM recharge_attempts WHERE line_id = ? "
        "ORDER BY datetime(initiated_at) DESC, id DESC LIMIT 1",
        (line_id,),
    ).fetchone()


def compute_status(row, attempt=None, today=None):
    """
    Works out the *effective* status of a line.

    Precedence (most specific first):
      paused                - reminders are suspended, never auto-due
      expiry_reported       - a human explicitly reported the SIM as expired
      recharge_pending      - an attempt is waiting for the user's confirmation
      recharge_failed       - the last attempt failed / was cancelled
      overdue / due         - derived purely from the reminder date
      active                - everything else

    Note: an OVERDUE reminder is NOT proof of SIM expiry. The caller must
    surface REASON_OVERDUE rather than REASON_EXPIRY_REPORTED.
    """
    if row["paused"]:
        return STATUS_PAUSED, "Reminders paused"
    if row["expiry_reported"]:
        return STATUS_EXPIRY_REPORTED, REASON_EXPIRY_REPORTED
    if attempt is not None:
        if attempt["status"] in (ATTEMPT_PENDING, ATTEMPT_STARTED):
            return STATUS_RECHARGE_PENDING, REASON_PENDING
        if attempt["status"] in (ATTEMPT_FAILED, ATTEMPT_CANCELLED):
            return STATUS_RECHARGE_FAILED, REASON_FAILED

    if today is None:
        today = _today(row["timezone"])
    next_date = _parse_date(row["next_reminder_date"])
    if next_date is not None:
        if next_date < today:
            return STATUS_OVERDUE, REASON_OVERDUE
        if next_date == today:
            return STATUS_DUE, REASON_DUE

    return STATUS_ACTIVE, "Reminder scheduled"


def serialize_attempt(row):
    """Serialises a recharge_attempts row for the API."""
    if row is None:
        return None
    return {
        "id": row["id"],
        "lineId": row["line_id"],
        "amount": row["amount"],
        "ussdString": row["ussd_string"],
        "status": row["status"],
        "statusLabel": ATTEMPT_LABELS.get(row["status"], row["status"]),
        "transactionRef": row["transaction_ref"],
        "note": row["note"],
        "outcome": row["outcome"],
        "initiatedAt": row["initiated_at"],
        "resolvedAt": row["resolved_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def serialize_line(row, attempt=None, reveal_phone=False):
    """
    Serialises an airtime_lines row for the API.

    `reveal_phone` controls whether the full number is included. The masked
    form is ALWAYS included so the UI can show it without a second call.

    Only `phoneMasked` is included by default. The unmasked `phoneDisplay`
    (local format, e.g. 0712345678) and `phoneNumber` (E.164) are returned
    only when `reveal_phone` is True, so a list request does not leak every
    number to anyone holding the API key.
    """
    if row is None:
        return None

    today = _today(row["timezone"])
    status, reason = compute_status(row, attempt=attempt, today=today)
    next_date = _parse_date(row["next_reminder_date"])
    days_until = (next_date - today).days if next_date else None

    phone_e164 = row["phone_number"]
    data = {
        "id": row["id"],
        "label": row["label"],
        "phoneMasked": mask_phone(phone_e164),
        "carrierHint": row["carrier_hint"],
        "amount": row["amount"],
        "currency": row["currency"],
        "reminderFrequencyDays": row["reminder_frequency_days"],
        "nextReminderDate": row["next_reminder_date"],
        "reminderTime": row["reminder_time"],
        "timezone": row["timezone"],
        "lastConfirmedRechargeDate": row["last_confirmed_recharge_date"],
        "status": status,
        "statusLabel": STATUS_LABELS.get(status, status),
        "reason": reason,
        "daysUntilDue": days_until,
        "isOverdue": status == STATUS_OVERDUE,
        "needsAttention": status in ATTENTION_STATUSES,
        "expiryReported": bool(row["expiry_reported"]),
        "expiryReportedAt": row["expiry_reported_at"],
        "expirySource": row["expiry_source"],
        "expiryNote": row["expiry_note"],
        "paused": bool(row["paused"]),
        "archived": bool(row["archived"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "lastAttempt": serialize_attempt(attempt),
    }
    if reveal_phone:
        data["phoneDisplay"] = row["phone_display"]
        data["phoneNumber"] = phone_e164
    return data


def _row_to_dict(row):
    return {k: row[k] for k in row.keys()}


def _schedule_next_reminder(confirmation_date, frequency_days):
    """Next reminder date = confirmation date + frequency (in days)."""
    return (confirmation_date + timedelta(days=frequency_days)).isoformat()


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------

def create_line(data):
    """
    Registers a new airtime line.

    `data` keys (camelCase, as sent by the frontend):
        label, phoneNumber, amount, reminderFrequencyDays, nextReminderDate,
        reminderTime, timezone, expiryReported, expirySource, expiryNote

    Returns the serialised line. Raises ValidationError / DuplicateError.
    """
    ensure_ready()
    data = data or {}

    label = validate_label(data.get("label"))
    phone = normalize_phone(data.get("phoneNumber"))
    amount = validate_amount(data.get("amount"), default=10)
    frequency = validate_frequency(data.get("reminderFrequencyDays"))
    timezone = validate_timezone(data.get("timezone"))
    reminder_time = validate_time(data.get("reminderTime"))

    next_reminder = validate_date(
        data.get("nextReminderDate"), field="nextReminderDate", required=False
    )
    if next_reminder is None:
        next_reminder = _today(timezone).isoformat()

    expiry_reported = bool(data.get("expiryReported"))
    expiry_source = (data.get("expirySource") or "").strip() or None
    expiry_note = (data.get("expiryNote") or "").strip() or None

    now = _now_iso()
    with _WRITE_LOCK:
        conn = _connect()
        try:
            existing = conn.execute(
                "SELECT id FROM airtime_lines WHERE phone_number = ?", (phone,)
            ).fetchone()
            if existing is not None:
                raise DuplicateError(
                    "That phone number is already registered.", field="phoneNumber"
                )

            status = STATUS_EXPIRY_REPORTED if expiry_reported else STATUS_ACTIVE
            cursor = conn.execute(
                """
                INSERT INTO airtime_lines (
                    label, phone_number, phone_display, carrier_hint, amount,
                    currency, reminder_frequency_days, next_reminder_date,
                    reminder_time, timezone, last_confirmed_recharge_date,
                    status, expiry_reported, expiry_reported_at, expiry_source,
                    expiry_note, paused, archived, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                """,
                (
                    label,
                    phone,
                    display_phone(phone),
                    carrier_hint(phone),
                    amount,
                    "KES",
                    frequency,
                    next_reminder,
                    reminder_time,
                    timezone,
                    None,
                    status,
                    1 if expiry_reported else 0,
                    now if expiry_reported else None,
                    expiry_source,
                    expiry_note,
                    now,
                    now,
                ),
            )
            conn.commit()
            line_id = cursor.lastrowid
        finally:
            conn.close()

    return get_line(line_id, reveal_phone=True)


def update_line(line_id, data):
    """Updates a line. Only the keys present in `data` are changed."""
    ensure_ready()
    data = data or {}

    with _WRITE_LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM airtime_lines WHERE id = ?", (line_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("Airtime line not found.")

            updates = {}
            if "label" in data:
                updates["label"] = validate_label(data.get("label"))
            if "phoneNumber" in data:
                phone = normalize_phone(data.get("phoneNumber"))
                if phone != row["phone_number"]:
                    clash = conn.execute(
                        "SELECT id FROM airtime_lines WHERE phone_number = ? AND id != ?",
                        (phone, line_id),
                    ).fetchone()
                    if clash is not None:
                        raise DuplicateError(
                            "That phone number is already registered.",
                            field="phoneNumber",
                        )
                updates["phone_number"] = phone
                updates["phone_display"] = display_phone(phone)
                updates["carrier_hint"] = carrier_hint(phone)
            if "amount" in data:
                updates["amount"] = validate_amount(data.get("amount"))
            if "reminderFrequencyDays" in data:
                updates["reminder_frequency_days"] = validate_frequency(
                    data.get("reminderFrequencyDays")
                )
            if "nextReminderDate" in data:
                updates["next_reminder_date"] = validate_date(
                    data.get("nextReminderDate"), field="nextReminderDate"
                )
            if "reminderTime" in data:
                updates["reminder_time"] = validate_time(data.get("reminderTime"))
            if "timezone" in data:
                updates["timezone"] = validate_timezone(data.get("timezone"))
            if "paused" in data:
                updates["paused"] = 1 if data.get("paused") else 0
            if "archived" in data:
                updates["archived"] = 1 if data.get("archived") else 0
            if "expiryReported" in data:
                reported = bool(data.get("expiryReported"))
                updates["expiry_reported"] = 1 if reported else 0
                updates["expiry_reported_at"] = _now_iso() if reported else None
                if "expirySource" in data:
                    updates["expiry_source"] = (data.get("expirySource") or "").strip() or None
                if "expiryNote" in data:
                    updates["expiry_note"] = (data.get("expiryNote") or "").strip() or None
                updates["status"] = (
                    STATUS_EXPIRY_REPORTED if reported else STATUS_ACTIVE
                )

            if not updates:
                raise ValidationError("No fields to update.")

            updates["updated_at"] = _now_iso()
            assignments = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE airtime_lines SET {assignments} WHERE id = ?",
                list(updates.values()) + [line_id],
            )
            conn.commit()
        finally:
            conn.close()

    return get_line(line_id, reveal_phone=True)


def get_line(line_id, reveal_phone=False):
    """Returns a single serialised line, or raises NotFoundError."""
    ensure_ready()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM airtime_lines WHERE id = ?", (line_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("Airtime line not found.")
        attempt = _latest_attempt(conn, line_id)
        return serialize_line(row, attempt=attempt, reveal_phone=reveal_phone)
    finally:
        conn.close()


def delete_line(line_id):
    """
    Archives a line (soft delete) and removes its attempts.
    Returns True when a line was found and archived.
    """
    ensure_ready()
    with _WRITE_LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT id FROM airtime_lines WHERE id = ?", (line_id,)
            ).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM recharge_attempts WHERE line_id = ?", (line_id,))
            conn.execute(
                "UPDATE airtime_lines SET archived = 1, paused = 1, updated_at = ? "
                "WHERE id = ?",
                (_now_iso(), line_id),
            )
            conn.commit()
            return True
        finally:
            conn.close()


# --------------------------------------------------------------------------
# Listing / summary
# --------------------------------------------------------------------------

def list_lines(status=None, include_archived=False, reveal_phone=False, only_attention=False):
    """
    Returns (lines, summary).

    `status` filters on the *effective* (derived) status, not the stored one.
    `only_attention` restricts the result to the lines the Expired tab cares
    about: overdue reminders, reported expiries, pending/failed recharges and
    reminders due today.

    The returned SUMMARY is always computed over ALL registered (non-archived)
    lines, so the headline numbers on the dashboard stay stable no matter which
    filter is applied to the rows. `summary["filteredLines"]` reports how many
    rows were actually returned, so the UI can show "showing N of M".
    """
    ensure_ready()
    conn = _connect()
    try:
        sql = "SELECT * FROM airtime_lines"
        params = []
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY id ASC"

        rows = conn.execute(sql, params).fetchall()
        all_lines = []
        for row in rows:
            attempt = _latest_attempt(conn, row["id"])
            all_lines.append(
                serialize_line(row, attempt=attempt, reveal_phone=reveal_phone)
            )

        # `lines` is the slice that is returned; `all_lines` is the full set.
        lines = list(all_lines)

        # The summary describes the WHOLE register, not the filtered slice.
        summary = summarize_lines(all_lines)

        if only_attention:
            lines = [l for l in lines if l["needsAttention"]]
        if status:
            lines = [l for l in lines if l["status"] == status]

        summary["filteredLines"] = len(lines)
        return lines, summary
    finally:
        conn.close()


def summarize_lines(lines):
    """Counts per status plus a few headline numbers for the summary cards."""
    counts = {status: 0 for status in ALL_STATUSES}
    for line in lines:
        counts[line["status"]] = counts.get(line["status"], 0) + 1

    total = len(lines)
    return {
        "totalLines": total,
        "overdue": counts.get(STATUS_OVERDUE, 0),
        "due": counts.get(STATUS_DUE, 0),
        "expiryReported": counts.get(STATUS_EXPIRY_REPORTED, 0),
        "rechargePending": counts.get(STATUS_RECHARGE_PENDING, 0),
        "rechargeFailed": counts.get(STATUS_RECHARGE_FAILED, 0),
        "active": counts.get(STATUS_ACTIVE, 0),
        "paused": counts.get(STATUS_PAUSED, 0),
        "needsAttention": sum(1 for l in lines if l["needsAttention"]),
        "counts": counts,
    }


def list_attempts(line_id=None, limit=50):
    """Returns recent recharge attempts, newest first."""
    ensure_ready()
    conn = _connect()
    try:
        if line_id is None:
            rows = conn.execute(
                "SELECT * FROM recharge_attempts ORDER BY datetime(initiated_at) DESC, id DESC "
                "LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM recharge_attempts WHERE line_id = ? "
                "ORDER BY datetime(initiated_at) DESC, id DESC LIMIT ?",
                (line_id, limit),
            ).fetchall()
        return [serialize_attempt(r) for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Recharge attempt lifecycle
# --------------------------------------------------------------------------

def start_attempt(line_id, ussd_string, amount, note=None):
    """
    Records that a recharge was STARTED for a line.

    This deliberately does NOT touch last_confirmed_recharge_date and does NOT
    move next_reminder_date. The attempt stays 'pending_confirmation' until a
    human confirms the recharge actually went through on the handset.

    Returns the serialised attempt. Raises NotFoundError / ValidationError.
    """
    ensure_ready()

    with _WRITE_LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM airtime_lines WHERE id = ?", (line_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("Airtime line not found.")
            if row["archived"]:
                raise ValidationError("Cannot recharge an archived line.")

            amt = validate_amount(amount, default=row["amount"])
            now = _now_iso()
            cursor = conn.execute(
                """
                INSERT INTO recharge_attempts (
                    line_id, amount, ussd_string, status, transaction_ref, note,
                    outcome, initiated_at, resolved_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    line_id,
                    amt,
                    ussd_string,
                    ATTEMPT_PENDING,
                    None,
                    (note or "").strip() or None,
                    None,
                    now,
                    None,
                    now,
                    now,
                ),
            )
            conn.execute(
                "UPDATE airtime_lines SET updated_at = ? WHERE id = ?", (now, line_id)
            )
            conn.commit()
            attempt_id = cursor.lastrowid
        finally:
            conn.close()

    return get_attempt(attempt_id)


def get_attempt(attempt_id):
    """Returns a single serialised attempt, or raises NotFoundError."""
    ensure_ready()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM recharge_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("Recharge attempt not found.")
        return serialize_attempt(row)
    finally:
        conn.close()


def _resolve_attempt(attempt_id, target_status, outcome, transaction_ref, note):
    """
    Shared helper for confirm/fail/cancel. Handles the state machine and the
    unique-constraint check, raising ConflictError for illegal transitions.
    """
    now = _now_iso()
    with _WRITE_LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM recharge_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("Recharge attempt not found.")

            if row["status"] == ATTEMPT_CONFIRMED:
                raise ConflictError(
                    "This recharge was already confirmed. Only a confirmed recharge "
                    "advances the schedule."
                )
            if row["status"] in (ATTEMPT_FAILED, ATTEMPT_CANCELLED):
                raise ConflictError(
                    "This recharge attempt was already closed and cannot be changed."
                )

            if target_status == ATTEMPT_CONFIRMED:
                # Guard against a second confirmation slipping in via a race.
                # Only a confirmation on the SAME day is a duplicate - next
                # week's recharge must be confirmable again.
                today = _today()
                existing = conn.execute(
                    "SELECT id FROM recharge_attempts WHERE line_id = ? "
                    "AND status = ? AND id != ? AND date(resolved_at) = ?",
                    (row["line_id"], ATTEMPT_CONFIRMED, attempt_id, today.isoformat()),
                ).fetchone()
                if existing is not None:
                    raise ConflictError(
                        "This line already has a confirmed recharge today. Only a "
                        "confirmed recharge advances the schedule."
                    )

            try:
                conn.execute(
                    "UPDATE recharge_attempts SET status = ?, outcome = ?, "
                    "transaction_ref = ?, note = ?, resolved_at = ?, updated_at = ? "
                    "WHERE id = ?",
                    (
                        target_status,
                        outcome,
                        (transaction_ref or "").strip() or None,
                        (note or "").strip() or None,
                        now,
                        now,
                        attempt_id,
                    ),
                )
            except sqlite3.IntegrityError:
                # The per-day unique index rejected a duplicate confirmation.
                conn.rollback()
                raise ConflictError(
                    "This line already has a confirmed recharge today. Only a "
                    "confirmed recharge advances the schedule."
                )

            if target_status == ATTEMPT_CONFIRMED:
                line = conn.execute(
                    "SELECT * FROM airtime_lines WHERE id = ?", (row["line_id"],)
                ).fetchone()
                if line is None:
                    raise NotFoundError("Airtime line not found.")

                confirmed_on = _parse_date(now) or _today(line["timezone"])
                next_reminder = _schedule_next_reminder(
                    confirmed_on, line["reminder_frequency_days"]
                )
                conn.execute(
                    "UPDATE airtime_lines SET last_confirmed_recharge_date = ?, "
                    "next_reminder_date = ?, updated_at = ? WHERE id = ?",
                    (confirmed_on.isoformat(), next_reminder, now, line["id"]),
                )

            conn.commit()
        finally:
            conn.close()

    return get_attempt(attempt_id)


def confirm_attempt(attempt_id, transaction_ref=None, note=None, outcome=None):
    """
    Marks a recharge attempt as CONFIRMED.

    This is the ONLY operation that:
      * sets last_confirmed_recharge_date
      * advances next_reminder_date by the line's reminder frequency

    Confirming an already-confirmed attempt raises ConflictError (HTTP 409).
    """
    return _resolve_attempt(
        attempt_id,
        ATTEMPT_CONFIRMED,
        outcome or "recharge_successful",
        transaction_ref,
        note,
    )


def fail_attempt(attempt_id, note=None, outcome=None):
    """
    Marks a recharge attempt as FAILED.

    The reminder schedule is deliberately LEFT UNCHANGED, so the line stays
    overdue/due and can be retried.
    """
    return _resolve_attempt(
        attempt_id,
        ATTEMPT_FAILED,
        outcome or "recharge_failed",
        None,
        note,
    )


def cancel_attempt(attempt_id, note=None):
    """
    Cancels a pending recharge attempt. The reminder schedule is LEFT
    UNCHANGED so the line can still be recharged.
    """
    return _resolve_attempt(
        attempt_id,
        ATTEMPT_CANCELLED,
        "cancelled",
        None,
        note,
    )


# --------------------------------------------------------------------------
# Reminders
# --------------------------------------------------------------------------

def due_reminders(reference_date=None):
    """
    Returns the lines whose reminder is due on or before `reference_date`
    (defaults to today, in each line's own time zone).

    This is what a reminder job would poll. Note that a plain browser cannot
    run in the background reliably, so the UI also surfaces the same list
    on-screen when the Expired tab is opened.
    """
    ensure_ready()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM airtime_lines WHERE archived = 0 AND paused = 0"
        ).fetchall()

        due = []
        for row in rows:
            today = reference_date if reference_date is not None else _today(row["timezone"])
            next_date = _parse_date(row["next_reminder_date"])
            if next_date is not None and next_date <= today:
                attempt = _latest_attempt(conn, row["id"])
                due.append(serialize_line(row, attempt=attempt))
        due.sort(key=lambda l: (l["nextReminderDate"] or "", l["id"]))
        return due
    finally:
        conn.close()


def report_expiry(line_id, reported=True, source=None, note=None):
    """
    Records (or clears) an explicit human report that a SIM has expired.

    This is the ONLY way a line gets the "SIM expiry reported" status. An
    overdue reminder never sets this by itself, because a missed reminder is
    not evidence that the SIM expired.
    """
    ensure_ready()
    now = _now_iso()
    with _WRITE_LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM airtime_lines WHERE id = ?", (line_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("Airtime line not found.")

            conn.execute(
                "UPDATE airtime_lines SET expiry_reported = ?, expiry_reported_at = ?, "
                "expiry_source = ?, expiry_note = ?, status = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    1 if reported else 0,
                    now if reported else None,
                    (source or "").strip() or None if reported else None,
                    (note or "").strip() or None if reported else None,
                    STATUS_EXPIRY_REPORTED if reported else STATUS_ACTIVE,
                    now,
                    line_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    return get_line(line_id, reveal_phone=True)
