"""
Tests for the Safaricom airtime feature (USSD builder, store, API routes).

Every test runs against a THROWAWAY SQLite database via the AIRTIME_DB_PATH
environment variable, so the real protrack.db is never touched.

Run with:
    cd backend && .venv/bin/python -m pytest tests -v
"""

import os
import sys
import tempfile

import pytest

# The backend modules import each other by bare name (e.g. "import airtime_lines"),
# so the backend directory has to be on sys.path.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    """Points the airtime store at a fresh temporary database."""
    path = tmp_path / "airtime_test.db"
    monkeypatch.setenv("AIRTIME_DB_PATH", str(path))

    import airtime_lines

    airtime_lines._SCHEMA_READY["path"] = None
    airtime_lines.ensure_schema()
    yield str(path)
    airtime_lines._SCHEMA_READY["path"] = None


@pytest.fixture()
def client(db_path, monkeypatch):
    """A Flask test client for the whole API, with the airtime key set."""
    monkeypatch.setenv("AIRTIME_API_KEY", "test-key-123")

    import api_auth

    api_auth.AIRTIME_API_KEY = "test-key-123"

    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


AUTH = {"X-API-Key": "test-key-123"}


def make_line(client, **overrides):
    """Creates a line through the API and returns the response JSON."""
    payload = {
        "label": "Test line",
        "phoneNumber": "0712345678",
        "amount": 10,
        "reminderFrequencyDays": 7,
    }
    payload.update(overrides)
    response = client.post("/api/airtime/lines", json=payload, headers=AUTH)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["line"]


# --------------------------------------------------------------------------
# USSD builder
# --------------------------------------------------------------------------

class TestUssdBuilder:
    def test_builds_local_format(self):
        import airtime_ussd

        result = airtime_ussd.build_ussd(10, "254712345678")
        assert result["ussd"] == "*140*10*0712345678#"
        assert result["amount"] == 10
        assert result["phone"] == "0712345678"

    def test_builds_e164_format(self):
        import airtime_ussd

        result = airtime_ussd.build_ussd(50, "254712345678", number_format="e164")
        assert result["ussd"] == "*140*50*+254712345678#"
        assert result["phone"] == "+254712345678"

    def test_uri_encodes_hash_but_keeps_asterisk(self):
        import airtime_ussd

        result = airtime_ussd.build_ussd(10, "254712345678")
        assert result["uri"] == "tel:*140*10*0712345678%23"
        assert result["uri"].startswith("tel:*140*")
        assert "#" not in result["uri"]

    def test_rejects_non_integer_amount(self):
        import airtime_ussd

        with pytest.raises(airtime_ussd.UssdError):
            airtime_ussd.build_ussd(10.5, "254712345678")
        with pytest.raises(airtime_ussd.UssdError):
            airtime_ussd.build_ussd("abc", "254712345678")
        with pytest.raises(airtime_ussd.UssdError):
            airtime_ussd.build_ussd(0, "254712345678")

    def test_rejects_non_kenyan_number(self):
        import airtime_ussd

        with pytest.raises(airtime_ussd.UssdError):
            airtime_ussd.build_ussd(10, "1234567890")


# --------------------------------------------------------------------------
# Phone normalisation
# --------------------------------------------------------------------------

class TestPhoneNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("0712345678", "254712345678"),
            ("+254712345678", "254712345678"),
            ("254712345678", "254712345678"),
            ("00712345678", "254712345678"),
            ("0712 345 678", "254712345678"),
            ("0712-345-678", "254712345678"),
            ("0112345678", "254112345678"),
            ("0722345678", "254722345678"),
        ],
    )
    def test_normalizes(self, raw, expected):
        import airtime_lines

        assert airtime_lines.normalize_phone(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "12345", "002345678", "+25471234567890"])
    def test_rejects_invalid(self, raw):
        import airtime_lines

        with pytest.raises(airtime_lines.ValidationError):
            airtime_lines.normalize_phone(raw)

    def test_masking_hides_middle_digits(self):
        import airtime_lines

        assert airtime_lines.mask_phone("254712345678") == "0712 *** 678"

    def test_safaricom_prefix_is_detected(self):
        import airtime_lines

        assert airtime_lines.carrier_hint("254712345678") == "Safaricom"
        assert "not Safaricom" in airtime_lines.carrier_hint("254734567890")


# --------------------------------------------------------------------------
# API - authentication
# --------------------------------------------------------------------------

class TestApiAuth:
    def test_requires_api_key(self, client):
        response = client.get("/api/airtime/lines")
        assert response.status_code == 401

    def test_rejects_wrong_api_key(self, client):
        response = client.get("/api/airtime/lines", headers={"X-API-Key": "nope"})
        assert response.status_code == 401

    def test_accepts_valid_api_key(self, client):
        response = client.get("/api/airtime/lines", headers=AUTH)
        assert response.status_code == 200


# --------------------------------------------------------------------------
# API - line registration
# --------------------------------------------------------------------------

class TestLineRegistration:
    def test_creates_a_line(self, client):
        response = client.post(
            "/api/airtime/lines",
            json={"label": "Truck 1", "phoneNumber": "0712345678", "amount": 20},
            headers=AUTH,
        )
        assert response.status_code == 201
        line = response.get_json()["line"]
        assert line["label"] == "Truck 1"
        assert line["amount"] == 20
        assert line["phoneMasked"] == "0712 *** 678"
        # A brand new line is due for its first recharge straight away.
        assert line["status"] == "due"
        assert line["reminderFrequencyDays"] == 7

    def test_rejects_duplicate_phone_number(self, client):
        make_line(client, phoneNumber="0712345678")
        response = client.post(
            "/api/airtime/lines",
            json={"label": "Copy", "phoneNumber": "+254 712 345 678"},
            headers=AUTH,
        )
        assert response.status_code == 409

    def test_rejects_invalid_phone_number(self, client):
        response = client.post(
            "/api/airtime/lines",
            json={"label": "Bad", "phoneNumber": "12345"},
            headers=AUTH,
        )
        assert response.status_code == 400
        assert response.get_json()["field"] == "phoneNumber"

    def test_rejects_missing_label(self, client):
        response = client.post(
            "/api/airtime/lines", json={"phoneNumber": "0712345678"}, headers=AUTH
        )
        assert response.status_code == 400
        assert response.get_json()["field"] == "label"

    def test_rejects_invalid_frequency(self, client):
        response = client.post(
            "/api/airtime/lines",
            json={"label": "X", "phoneNumber": "0712345678", "reminderFrequencyDays": 0},
            headers=AUTH,
        )
        assert response.status_code == 400

    def test_masks_phone_by_default_and_reveals_on_request(self, client):
        make_line(client, phoneNumber="0712345678")
        listing = client.get("/api/airtime/lines", headers=AUTH).get_json()
        assert "phoneNumber" not in listing["lines"][0]
        # The unmasked local form must NOT leak in the default listing either.
        assert "phoneDisplay" not in listing["lines"][0]
        assert listing["lines"][0]["phoneMasked"] == "0712 *** 678"

        revealed = client.get(
            "/api/airtime/lines?revealPhone=true", headers=AUTH
        ).get_json()
        assert revealed["lines"][0]["phoneNumber"] == "254712345678"
        assert revealed["lines"][0]["phoneDisplay"] == "0712345678"

    def test_single_line_is_masked_by_default(self, client):
        line = make_line(client, phoneNumber="0712345678")
        fetched = client.get(
            f"/api/airtime/lines/{line['id']}", headers=AUTH
        ).get_json()["line"]
        assert "phoneNumber" not in fetched
        assert "phoneDisplay" not in fetched
        assert fetched["phoneMasked"] == "0712 *** 678"

        revealed = client.get(
            f"/api/airtime/lines/{line['id']}?revealPhone=true", headers=AUTH
        ).get_json()["line"]
        assert revealed["phoneNumber"] == "254712345678"


# --------------------------------------------------------------------------
# Recharge lifecycle
# --------------------------------------------------------------------------

class TestRechargeLifecycle:
    def test_start_returns_ussd_and_does_not_advance_schedule(self, client):
        import airtime_lines

        line = make_line(client, phoneNumber="0712345678", amount=10)
        original_next = line["nextReminderDate"]

        response = client.post(
            f"/api/airtime/lines/{line['id']}/recharge", json={}, headers=AUTH
        )
        assert response.status_code == 201
        data = response.get_json()
        assert data["ussd"] == "*140*10*0712345678#"
        assert data["uri"] == "tel:*140*10*0712345678%23"
        assert data["confirmationRequired"] is True
        assert data["attempt"]["status"] == "pending_confirmation"

        # The schedule must NOT have moved - only a confirmed recharge moves it.
        after = client.get(f"/api/airtime/lines/{line['id']}", headers=AUTH).get_json()
        assert after["line"]["nextReminderDate"] == original_next
        assert after["line"]["lastConfirmedRechargeDate"] is None

    def test_confirm_advances_schedule_and_sets_last_confirmed(self, client):
        import airtime_lines
        from datetime import date, timedelta

        line = make_line(client, phoneNumber="0712345678", reminderFrequencyDays=7)
        attempt = client.post(
            f"/api/airtime/lines/{line['id']}/recharge", json={}, headers=AUTH
        ).get_json()["attempt"]

        response = client.post(
            f"/api/airtime/attempts/{attempt['id']}/confirm",
            json={"transactionRef": "TX-123"},
            headers=AUTH,
        )
        assert response.status_code == 200
        assert response.get_json()["attempt"]["status"] == "confirmed"
        assert response.get_json()["attempt"]["transactionRef"] == "TX-123"

        after = client.get(f"/api/airtime/lines/{line['id']}", headers=AUTH).get_json()["line"]
        expected = (date.today() + timedelta(days=7)).isoformat()
        assert after["lastConfirmedRechargeDate"] == date.today().isoformat()
        assert after["nextReminderDate"] == expected

    def test_confirm_response_includes_the_updated_line(self, client):
        """The client needs the new next-reminder date without a second call."""
        line = make_line(client, phoneNumber="0712345678", reminderFrequencyDays=7)
        attempt = client.post(
            f"/api/airtime/lines/{line['id']}/recharge", json={}, headers=AUTH
        ).get_json()["attempt"]

        payload = client.post(
            f"/api/airtime/attempts/{attempt['id']}/confirm", json={}, headers=AUTH
        ).get_json()

        assert "line" in payload
        assert payload["line"]["id"] == line["id"]
        assert payload["line"]["nextReminderDate"] != line["nextReminderDate"]
        # The line is still masked in the response.
        assert "phoneNumber" not in payload["line"]

    def test_confirming_twice_returns_409(self, client):
        line = make_line(client, phoneNumber="0712345678")
        attempt = client.post(
            f"/api/airtime/lines/{line['id']}/recharge", json={}, headers=AUTH
        ).get_json()["attempt"]

        first = client.post(
            f"/api/airtime/attempts/{attempt['id']}/confirm", json={}, headers=AUTH
        )
        assert first.status_code == 200

        second = client.post(
            f"/api/airtime/attempts/{attempt['id']}/confirm", json={}, headers=AUTH
        )
        assert second.status_code == 409

    def test_failed_attempt_preserves_schedule(self, client):
        line = make_line(client, phoneNumber="0712345678")
        original_next = line["nextReminderDate"]
        attempt = client.post(
            f"/api/airtime/lines/{line['id']}/recharge", json={}, headers=AUTH
        ).get_json()["attempt"]

        response = client.post(
            f"/api/airtime/attempts/{attempt['id']}/fail",
            json={"note": "USSD rejected"},
            headers=AUTH,
        )
        assert response.status_code == 200
        assert response.get_json()["attempt"]["status"] == "failed"

        after = client.get(f"/api/airtime/lines/{line['id']}", headers=AUTH).get_json()["line"]
        assert after["nextReminderDate"] == original_next
        assert after["lastConfirmedRechargeDate"] is None

    def test_cancelled_attempt_preserves_schedule(self, client):
        line = make_line(client, phoneNumber="0712345678")
        original_next = line["nextReminderDate"]
        attempt = client.post(
            f"/api/airtime/lines/{line['id']}/recharge", json={}, headers=AUTH
        ).get_json()["attempt"]

        response = client.post(
            f"/api/airtime/attempts/{attempt['id']}/cancel", json={}, headers=AUTH
        )
        assert response.status_code == 200
        assert response.get_json()["attempt"]["status"] == "cancelled"

        after = client.get(f"/api/airtime/lines/{line['id']}", headers=AUTH).get_json()["line"]
        assert after["nextReminderDate"] == original_next
        assert after["lastConfirmedRechargeDate"] is None

    def test_failed_attempt_cannot_be_confirmed_afterwards(self, client):
        line = make_line(client, phoneNumber="0712345678")
        attempt = client.post(
            f"/api/airtime/lines/{line['id']}/recharge", json={}, headers=AUTH
        ).get_json()["attempt"]

        client.post(f"/api/airtime/attempts/{attempt['id']}/fail", json={}, headers=AUTH)
        response = client.post(
            f"/api/airtime/attempts/{attempt['id']}/confirm", json={}, headers=AUTH
        )
        assert response.status_code == 409

    def test_unknown_attempt_returns_404(self, client):
        response = client.post("/api/airtime/attempts/9999/confirm", json={}, headers=AUTH)
        assert response.status_code == 404

    def test_recharge_on_unknown_line_returns_404(self, client):
        response = client.post("/api/airtime/lines/9999/recharge", json={}, headers=AUTH)
        assert response.status_code == 404

    def test_a_line_can_be_recharged_again_after_a_new_cycle(self, client):
        """
        A weekly line must be confirmable again in the NEXT cycle. An earlier
        draft of the schema used a unique index on (line_id, outcome), which
        silently allowed only ONE confirmed recharge per line, ever.
        """
        line = make_line(client, phoneNumber="0712345678", reminderFrequencyDays=7)

        first = client.post(
            f"/api/airtime/lines/{line['id']}/recharge", json={}, headers=AUTH
        ).get_json()["attempt"]
        assert client.post(
            f"/api/airtime/attempts/{first['id']}/confirm", json={}, headers=AUTH
        ).status_code == 200

        # Move the line back so the next cycle is due, then recharge again.
        client.patch(
            f"/api/airtime/lines/{line['id']}",
            json={"nextReminderDate": "2000-01-01"},
            headers=AUTH,
        )

        second = client.post(
            f"/api/airtime/lines/{line['id']}/recharge", json={}, headers=AUTH
        ).get_json()["attempt"]
        assert second["id"] != first["id"]

        # A duplicate confirmation on the same calendar day is rejected...
        assert client.post(
            f"/api/airtime/attempts/{second['id']}/confirm", json={}, headers=AUTH
        ).status_code == 409

    def test_next_cycles_recharge_can_be_confirmed(self, client, db_path):
        """
        The per-day guard must not block the FOLLOWING week's recharge, which
        is the normal case for a weekly line.
        """
        import sqlite3

        line = make_line(client, phoneNumber="0712345678", reminderFrequencyDays=7)

        first = client.post(
            f"/api/airtime/lines/{line['id']}/recharge", json={}, headers=AUTH
        ).get_json()["attempt"]
        assert client.post(
            f"/api/airtime/attempts/{first['id']}/confirm", json={}, headers=AUTH
        ).status_code == 200

        # Backdate the first confirmation so it falls on an earlier day.
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "UPDATE recharge_attempts SET resolved_at = '2000-01-01T00:00:00' "
                "WHERE id = ?",
                (first["id"],),
            )
            conn.commit()
        finally:
            conn.close()

        client.patch(
            f"/api/airtime/lines/{line['id']}",
            json={"nextReminderDate": "2000-01-01"},
            headers=AUTH,
        )
        second = client.post(
            f"/api/airtime/lines/{line['id']}/recharge", json={}, headers=AUTH
        ).get_json()["attempt"]

        response = client.post(
            f"/api/airtime/attempts/{second['id']}/confirm", json={}, headers=AUTH
        )
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["attempt"]["status"] == "confirmed"


# --------------------------------------------------------------------------
# Overdue reminder vs. reported SIM expiry
# --------------------------------------------------------------------------

class TestOverdueIsNotExpiry:
    def test_overdue_reminder_is_not_labelled_as_expired(self, client):
        from datetime import date, timedelta

        import airtime_lines

        overdue = (date.today() - timedelta(days=3)).isoformat()
        make_line(client, phoneNumber="0712345678", nextReminderDate=overdue)

        line = client.get("/api/airtime/lines", headers=AUTH).get_json()["lines"][0]
        assert line["status"] == "overdue"
        assert line["isOverdue"] is True
        assert line["expiryReported"] is False
        # The reason must make clear the SIM state is unverified.
        assert "unverified" in line["reason"]
        assert line["reason"] != airtime_lines.REASON_EXPIRY_REPORTED

    def test_due_today_is_due_not_overdue(self, client):
        from datetime import date

        make_line(client, phoneNumber="0712345678", nextReminderDate=date.today().isoformat())
        line = client.get("/api/airtime/lines", headers=AUTH).get_json()["lines"][0]
        assert line["status"] == "due"

    def test_reporting_expiry_sets_expiry_reported_status(self, client):
        line = make_line(client, phoneNumber="0712345678")

        response = client.post(
            f"/api/airtime/lines/{line['id']}/expiry",
            json={"reported": True, "source": "field technician", "note": "No network"},
            headers=AUTH,
        )
        assert response.status_code == 200
        updated = response.get_json()["line"]
        assert updated["status"] == "expiry_reported"
        assert updated["expiryReported"] is True
        assert updated["expirySource"] == "field technician"
        assert updated["expiryNote"] == "No network"

    def test_expiry_report_can_be_cleared(self, client):
        line = make_line(client, phoneNumber="0712345678")
        client.post(
            f"/api/airtime/lines/{line['id']}/expiry", json={"reported": True}, headers=AUTH
        )
        response = client.post(
            f"/api/airtime/lines/{line['id']}/expiry", json={"reported": False}, headers=AUTH
        )
        assert response.status_code == 200
        updated = response.get_json()["line"]
        assert updated["status"] != "expiry_reported"
        assert updated["expiryReported"] is False

    def test_expiry_report_on_unknown_line_returns_404(self, client):
        response = client.post(
            "/api/airtime/lines/9999/expiry", json={"reported": True}, headers=AUTH
        )
        assert response.status_code == 404


# --------------------------------------------------------------------------
# Attention filter + reminders
# --------------------------------------------------------------------------

class TestAttentionAndReminders:
    def test_attention_filter_returns_only_flagged_lines(self, client):
        from datetime import date, timedelta

        make_line(
            client,
            label="Overdue",
            phoneNumber="0712345678",
            nextReminderDate=(date.today() - timedelta(days=1)).isoformat(),
        )
        make_line(
            client,
            label="Healthy",
            phoneNumber="0712345679",
            nextReminderDate=(date.today() + timedelta(days=5)).isoformat(),
        )

        lines = client.get("/api/airtime/lines?attention=true", headers=AUTH).get_json()["lines"]
        assert len(lines) == 1
        assert lines[0]["label"] == "Overdue"

    def test_due_reminders_endpoint(self, client):
        from datetime import date, timedelta

        make_line(
            client,
            label="Due now",
            phoneNumber="0712345678",
            nextReminderDate=date.today().isoformat(),
        )
        make_line(
            client,
            label="Later",
            phoneNumber="0712345679",
            nextReminderDate=(date.today() + timedelta(days=10)).isoformat(),
        )

        data = client.get("/api/airtime/reminders/due", headers=AUTH).get_json()
        assert data["count"] == 1
        assert data["reminders"][0]["label"] == "Due now"

    def test_paused_lines_are_excluded_from_reminders(self, client):
        from datetime import date

        line = make_line(
            client, phoneNumber="0712345678", nextReminderDate=date.today().isoformat()
        )
        client.patch(
            f"/api/airtime/lines/{line['id']}", json={"paused": True}, headers=AUTH
        )

        data = client.get("/api/airtime/reminders/due", headers=AUTH).get_json()
        assert data["count"] == 0


# --------------------------------------------------------------------------
# USSD preview + deletion
# --------------------------------------------------------------------------

class TestUssdPreviewAndDelete:
    def test_preview_does_not_create_an_attempt(self, client):
        response = client.get(
            "/api/airtime/ussd/preview?phoneNumber=0712345678&amount=15", headers=AUTH
        )
        assert response.status_code == 200
        assert response.get_json()["ussd"] == "*140*15*0712345678#"

        attempts = client.get("/api/airtime/attempts", headers=AUTH).get_json()["attempts"]
        assert attempts == []

    def test_preview_requires_a_key(self, client):
        response = client.get("/api/airtime/ussd/preview?phoneNumber=0712345678")
        assert response.status_code == 401

    def test_delete_archives_the_line(self, client):
        line = make_line(client, phoneNumber="0712345678")
        response = client.delete(f"/api/airtime/lines/{line['id']}", headers=AUTH)
        assert response.status_code == 200

        listing = client.get("/api/airtime/lines", headers=AUTH).get_json()
        assert listing["lines"] == []

    def test_delete_unknown_line_returns_404(self, client):
        response = client.delete("/api/airtime/lines/9999", headers=AUTH)
        assert response.status_code == 404


# --------------------------------------------------------------------------
# Global summary semantics
# --------------------------------------------------------------------------

class TestSummaryIsGlobal:
    """The summary describes the whole register, not the filtered slice."""

    def test_unfiltered_summary_counts_every_line(self, client):
        make_line(client, label="One", phoneNumber="0712345678")
        make_line(client, label="Two", phoneNumber="0712345679")

        body = client.get("/api/airtime/lines", headers=AUTH).get_json()
        assert body["summary"]["totalLines"] == 2
        assert body["summary"]["filteredLines"] == 2

    def test_attention_filter_keeps_summary_global(self, client):
        from datetime import date, timedelta

        make_line(
            client,
            label="Overdue",
            phoneNumber="0712345678",
            nextReminderDate=(date.today() - timedelta(days=1)).isoformat(),
        )
        make_line(
            client,
            label="Healthy",
            phoneNumber="0712345679",
            nextReminderDate=(date.today() + timedelta(days=5)).isoformat(),
        )

        body = client.get("/api/airtime/lines?attention=true", headers=AUTH).get_json()

        # Only the flagged line comes back...
        assert len(body["lines"]) == 1
        assert body["lines"][0]["label"] == "Overdue"
        assert body["summary"]["filteredLines"] == 1

        # ...but the headline numbers still describe every registered line.
        assert body["summary"]["totalLines"] == 2
        assert body["summary"]["overdue"] == 1
        assert body["summary"]["active"] == 1
        assert body["summary"]["needsAttention"] == 1

    def test_status_filter_keeps_summary_global(self, client):
        from datetime import date, timedelta

        make_line(
            client,
            label="Overdue",
            phoneNumber="0712345678",
            nextReminderDate=(date.today() - timedelta(days=1)).isoformat(),
        )
        make_line(
            client,
            label="Healthy",
            phoneNumber="0712345679",
            nextReminderDate=(date.today() + timedelta(days=5)).isoformat(),
        )

        body = client.get("/api/airtime/lines?status=overdue", headers=AUTH).get_json()

        assert len(body["lines"]) == 1
        assert body["summary"]["filteredLines"] == 1
        assert body["summary"]["totalLines"] == 2
        assert body["summary"]["overdue"] == 1
        assert body["summary"]["active"] == 1

    def test_summary_counts_match_the_unfiltered_listing(self, client):
        from datetime import date, timedelta

        make_line(
            client,
            label="Overdue",
            phoneNumber="0712345678",
            nextReminderDate=(date.today() - timedelta(days=1)).isoformat(),
        )
        make_line(
            client,
            label="Healthy",
            phoneNumber="0712345679",
            nextReminderDate=(date.today() + timedelta(days=5)).isoformat(),
        )

        full = client.get("/api/airtime/lines", headers=AUTH).get_json()["summary"]
        filtered = client.get("/api/airtime/lines?attention=true", headers=AUTH).get_json()[
            "summary"
        ]

        assert filtered["totalLines"] == full["totalLines"]
        assert filtered["overdue"] == full["overdue"]
        assert filtered["active"] == full["active"]
        assert filtered["counts"] == full["counts"]


# --------------------------------------------------------------------------
# Clearing an expiry report through PATCH
# --------------------------------------------------------------------------

class TestExpiryClearingViaPatch:
    def test_patch_with_expiry_reported_false_clears_the_report(self, client):
        line = make_line(client, phoneNumber="0712345678")
        client.post(
            f"/api/airtime/lines/{line['id']}/expiry",
            json={"reported": True, "source": "field technician", "note": "No network"},
            headers=AUTH,
        )

        response = client.patch(
            f"/api/airtime/lines/{line['id']}",
            json={"expiryReported": False},
            headers=AUTH,
        )
        assert response.status_code == 200
        updated = response.get_json()["line"]
        assert updated["expiryReported"] is False
        assert updated["status"] != "expiry_reported"
        assert updated["expiryReportedAt"] is None

    def test_patch_with_expiry_reported_false_clears_the_note(self, client):
        line = make_line(client, phoneNumber="0712345678")
        client.post(
            f"/api/airtime/lines/{line['id']}/expiry",
            json={"reported": True, "note": "No network"},
            headers=AUTH,
        )

        response = client.patch(
            f"/api/airtime/lines/{line['id']}",
            json={"expiryReported": False, "expiryNote": None},
            headers=AUTH,
        )
        assert response.status_code == 200
        updated = response.get_json()["line"]
        assert updated["expiryNote"] is None

    def test_patch_omitting_expiry_reported_keeps_the_report(self, client):
        line = make_line(client, phoneNumber="0712345678")
        client.post(
            f"/api/airtime/lines/{line['id']}/expiry",
            json={"reported": True},
            headers=AUTH,
        )

        response = client.patch(
            f"/api/airtime/lines/{line['id']}",
            json={"label": "Renamed"},
            headers=AUTH,
        )
        assert response.status_code == 200
        updated = response.get_json()["line"]
        assert updated["expiryReported"] is True
        assert updated["status"] == "expiry_reported"

    def test_cleared_line_leaves_the_attention_filter(self, client):
        from datetime import date, timedelta

        # A future reminder date means the line is only in the attention
        # filter because of the expiry report, so clearing it must drop the
        # line out of the filter entirely.
        line = make_line(
            client,
            phoneNumber="0712345678",
            nextReminderDate=(date.today() + timedelta(days=5)).isoformat(),
        )
        client.post(
            f"/api/airtime/lines/{line['id']}/expiry",
            json={"reported": True},
            headers=AUTH,
        )
        assert len(
            client.get("/api/airtime/lines?attention=true", headers=AUTH).get_json()["lines"]
        ) == 1

        client.patch(
            f"/api/airtime/lines/{line['id']}",
            json={"expiryReported": False},
            headers=AUTH,
        )
        assert len(
            client.get("/api/airtime/lines?attention=true", headers=AUTH).get_json()["lines"]
        ) == 0

