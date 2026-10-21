"""
HTTP routes for the Safaricom airtime "Expired" sub-tab.

All routes are protected by the X-API-Key header (see api_auth.py).

ENDPOINTS
    GET    /api/airtime/lines                  list lines (+ summary)
    POST   /api/airtime/lines                  register a line
    GET    /api/airtime/lines/<id>             one line
    PATCH  /api/airtime/lines/<id>             update a line
    DELETE /api/airtime/lines/<id>             archive a line
    POST   /api/airtime/lines/<id>/recharge    start a recharge attempt
    POST   /api/airtime/lines/<id>/expiry      report / clear SIM expiry
    GET    /api/airtime/attempts               recent attempts
    POST   /api/airtime/attempts/<id>/confirm  confirm an attempt
    POST   /api/airtime/attempts/<id>/fail     fail an attempt
    POST   /api/airtime/attempts/<id>/cancel   cancel an attempt
    GET    /api/airtime/reminders/due          lines whose reminder is due
    GET    /api/airtime/ussd/preview           preview a USSD string only
"""

from flask import request
from flask_restful import Resource

import airtime_lines
import airtime_ussd
import api_auth

REVEAL_PARAM = "revealPhone"


def _bool_arg(name, default=False):
    raw = request.args.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _error(message, status=400, field=None):
    payload = {"error": message}
    if field:
        payload["field"] = field
    return payload, status


def _handle_domain_error(exc):
    """Maps a domain exception onto an HTTP response."""
    if isinstance(exc, airtime_lines.ValidationError):
        return _error(exc.message, 400, exc.field)
    if isinstance(exc, airtime_lines.DuplicateError):
        return _error(exc.message, 409, exc.field)
    if isinstance(exc, airtime_lines.NotFoundError):
        return _error(exc.message, 404)
    if isinstance(exc, airtime_lines.ConflictError):
        return _error(exc.message, 409)
    if isinstance(exc, airtime_ussd.UssdError):
        return _error(str(exc), 400, "amount")
    return _error(f"Unexpected error: {exc}", 500)


class AirtimeLines(Resource):
    """GET /api/airtime/lines - list lines with derived status + summary."""

    @api_auth.require_api_key
    def get(self):
        status = (request.args.get("status") or "").strip() or None
        if status and status not in airtime_lines.ALL_STATUSES:
            return _error(
                f"Invalid status '{status}'. Expected one of {list(airtime_lines.ALL_STATUSES)}.",
                400,
                "status",
            )

        only_attention = _bool_arg("attention", False)
        include_archived = _bool_arg("includeArchived", False)
        reveal_phone = _bool_arg(REVEAL_PARAM, False)

        try:
            lines, summary = airtime_lines.list_lines(
                status=status,
                include_archived=include_archived,
                reveal_phone=reveal_phone,
                only_attention=only_attention,
            )
        except airtime_lines.ValidationError as exc:
            return _handle_domain_error(exc)
        except Exception as exc:  # pragma: no cover - defensive
            return _error(f"Could not load airtime lines: {exc}", 500)

        return {
            "summary": summary,
            "lines": lines,
            "statusLabels": airtime_lines.STATUS_LABELS,
        }


class AirtimeLineCreate(Resource):
    """POST /api/airtime/lines - register a new airtime line."""

    @api_auth.require_api_key
    def post(self):
        data = request.get_json(silent=True) or {}
        try:
            line = airtime_lines.create_line(data)
        except (airtime_lines.ValidationError, airtime_lines.DuplicateError) as exc:
            return _handle_domain_error(exc)
        except Exception as exc:  # pragma: no cover - defensive
            return _error(f"Could not create airtime line: {exc}", 500)

        return {"message": "Airtime line registered.", "line": line}, 201


class AirtimeLineItem(Resource):
    """GET / PATCH / DELETE /api/airtime/lines/<id>"""

    @api_auth.require_api_key
    def get(self, line_id):
        try:
            line = airtime_lines.get_line(
                line_id, reveal_phone=_bool_arg(REVEAL_PARAM, False)
            )
        except airtime_lines.NotFoundError as exc:
            return _handle_domain_error(exc)
        return {"line": line}

    @api_auth.require_api_key
    def patch(self, line_id):
        data = request.get_json(silent=True) or {}
        try:
            line = airtime_lines.update_line(line_id, data)
        except (airtime_lines.ValidationError, airtime_lines.DuplicateError,
                airtime_lines.NotFoundError) as exc:
            return _handle_domain_error(exc)
        except Exception as exc:  # pragma: no cover - defensive
            return _error(f"Could not update airtime line: {exc}", 500)
        return {"message": "Airtime line updated.", "line": line}

    @api_auth.require_api_key
    def delete(self, line_id):
        try:
            deleted = airtime_lines.delete_line(line_id)
        except Exception as exc:  # pragma: no cover - defensive
            return _error(f"Could not delete airtime line: {exc}", 500)
        if not deleted:
            return _error("Airtime line not found.", 404)
        return {"message": "Airtime line removed."}


class AirtimeRecharge(Resource):
    """
    POST /api/airtime/lines/<id>/recharge

    Starts a recharge attempt and returns the exact USSD string + a `tel:`
    URI for the frontend to hand to the Android dialer.

    IMPORTANT: this endpoint does NOT confirm the recharge. The attempt stays
    'pending_confirmation' until a human confirms it on the handset, so a
    failed or abandoned dial never advances the schedule.
    """

    @api_auth.require_api_key
    def post(self, line_id):
        data = request.get_json(silent=True) or {}

        try:
            line = airtime_lines.get_line(line_id, reveal_phone=True)
            if line is None:
                return _error("Airtime line not found.", 404)

            amount = data.get("amount")
            if amount in (None, ""):
                amount = line["amount"]

            number_format = (data.get("numberFormat") or "local").strip().lower()
            built = airtime_ussd.build_ussd(
                amount, line["phoneNumber"], number_format=number_format
            )

            attempt = airtime_lines.start_attempt(
                line_id,
                ussd_string=built["ussd"],
                amount=built["amount"],
                note=data.get("note"),
            )
        except (airtime_lines.ValidationError, airtime_lines.NotFoundError) as exc:
            return _handle_domain_error(exc)
        except airtime_ussd.UssdError as exc:
            return _handle_domain_error(exc)
        except Exception as exc:  # pragma: no cover - defensive
            return _error(f"Could not start recharge: {exc}", 500)

        return {
            "message": (
                "Recharge started. Dial the command and confirm once the handset "
                "shows the recharge succeeded."
            ),
            "attempt": attempt,
            "ussd": built["ussd"],
            "uri": built["uri"],
            "amount": built["amount"],
            "phone": built["phone"],
            "numberFormat": built["numberFormat"],
            "confirmationRequired": True,
        }, 201


class AirtimeExpiryReport(Resource):
    """
    POST /api/airtime/lines/<id>/expiry

    Records an explicit human report that a SIM has expired (or clears it).
    """

    @api_auth.require_api_key
    def post(self, line_id):
        data = request.get_json(silent=True) or {}
        reported = data.get("reported", True)
        reported = True if reported is None else bool(reported)

        try:
            line = airtime_lines.report_expiry(
                line_id,
                reported=reported,
                source=data.get("source"),
                note=data.get("note"),
            )
        except airtime_lines.NotFoundError as exc:
            return _handle_domain_error(exc)
        except Exception as exc:  # pragma: no cover - defensive
            return _error(f"Could not update expiry status: {exc}", 500)

        return {
            "message": (
                "SIM expiry reported." if reported else "SIM expiry report cleared."
            ),
            "line": line,
        }


def build_attempt_resolver(action):
    """Builds a Resource class that resolves an attempt with the given action."""

    class _AttemptResolver(Resource):
        @api_auth.require_api_key
        def post(self, attempt_id):
            data = request.get_json(silent=True) or {}
            try:
                if action == "confirm":
                    attempt = airtime_lines.confirm_attempt(
                        attempt_id,
                        transaction_ref=data.get("transactionRef"),
                        note=data.get("note"),
                        outcome=data.get("outcome"),
                    )
                    message = "Recharge confirmed."
                elif action == "fail":
                    attempt = airtime_lines.fail_attempt(
                        attempt_id,
                        note=data.get("note"),
                        outcome=data.get("outcome"),
                    )
                    message = "Recharge marked as failed. The reminder schedule is unchanged."
                else:
                    attempt = airtime_lines.cancel_attempt(
                        attempt_id, note=data.get("note")
                    )
                    message = "Recharge cancelled. The reminder schedule is unchanged."
            except (airtime_lines.NotFoundError, airtime_lines.ConflictError,
                    airtime_lines.ValidationError) as exc:
                return _handle_domain_error(exc)
            except Exception as exc:  # pragma: no cover - defensive
                return _error(f"Could not resolve recharge attempt: {exc}", 500)

            # Include the affected line so the client can show the updated
            # next-reminder date without a second request. A confirmation
            # advances the schedule, so this is genuinely useful.
            line = None
            try:
                line = airtime_lines.get_line(attempt["lineId"])
            except airtime_lines.NotFoundError:
                pass

            payload = {"message": message, "attempt": attempt}
            if line is not None:
                payload["line"] = line
            return payload

    _AttemptResolver.__name__ = f"AirtimeAttempt{action.capitalize()}"
    return _AttemptResolver


class AirtimeAttempts(Resource):
    """GET /api/airtime/attempts - recent recharge attempts."""

    @api_auth.require_api_key
    def get(self):
        line_id = request.args.get("lineId")
        try:
            limit = int(request.args.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 500))

        parsed_line_id = None
        if line_id not in (None, ""):
            try:
                parsed_line_id = int(line_id)
            except (TypeError, ValueError):
                return _error("Invalid lineId.", 400, "lineId")

        try:
            attempts = airtime_lines.list_attempts(parsed_line_id, limit=limit)
        except Exception as exc:  # pragma: no cover - defensive
            return _error(f"Could not load recharge attempts: {exc}", 500)

        return {"attempts": attempts}


class AirtimeRemindersDue(Resource):
    """GET /api/airtime/reminders/due - lines whose reminder is due."""

    @api_auth.require_api_key
    def get(self):
        try:
            due = airtime_lines.due_reminders()
        except Exception as exc:  # pragma: no cover - defensive
            return _error(f"Could not load due reminders: {exc}", 500)

        return {
            "reminders": due,
            "count": len(due),
            "note": (
                "A browser cannot reliably run in the background, so these "
                "reminders are surfaced when the Expired tab is opened. Use a "
                "server-side scheduler for unattended reminders."
            ),
        }


class AirtimeUssdPreview(Resource):
    """
    GET /api/airtime/ussd/preview?phoneNumber=...&amount=10

    Builds a USSD string without creating an attempt. Useful for testing the
    format and for showing the exact command before dialling.
    """

    @api_auth.require_api_key
    def get(self):
        phone = request.args.get("phoneNumber") or request.args.get("phone")
        amount = request.args.get("amount")
        number_format = (request.args.get("numberFormat") or "local").strip().lower()

        try:
            normalized = airtime_lines.normalize_phone(phone)
            built = airtime_ussd.build_ussd(
                amount, normalized, number_format=number_format
            )
        except airtime_lines.ValidationError as exc:
            return _handle_domain_error(exc)
        except airtime_ussd.UssdError as exc:
            return _handle_domain_error(exc)

        return {
            "ussd": built["ussd"],
            "uri": built["uri"],
            "amount": built["amount"],
            "phone": built["phone"],
            "numberFormat": built["numberFormat"],
            "disclaimer": (
                "Opening the dialer does not confirm the recharge. Confirm "
                "manually on the handset after the recharge completes."
            ),
        }
