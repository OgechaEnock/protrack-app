"""
ProTrack365 Dashboard - Flask-RESTful backend
-----------------------------------------------
A pure JSON API (no template rendering - the frontend is a separate
Node/Express app under ../frontend). Exposes card billing history, and a
device-registry <-> WhatsApp recharge-log matcher with dedicated
Mettax/Pilot program sections.
"""

import os

from flask import Flask, request
from flask_restful import Api, Resource
from flask_cors import CORS
from werkzeug.utils import secure_filename

import card_history
import device_registry
import whatsapp_parser
import recharge_schedule
import pilot_fuel
import admin_auth
import admin_routes
import airtime_lines
import airtime_routes

app = Flask(__name__)
CORS(app)
api = Api(app)

CARD_HISTORY_DIR = card_history.CARD_HISTORY_DIR
REGISTRY_DIR = device_registry.REGISTRY_DIR
RECHARGE_LOG_DIR = whatsapp_parser.RECHARGE_LOG_DIR
PILOT_FUEL_DIR = pilot_fuel.PILOT_FUEL_DIR
os.makedirs(CARD_HISTORY_DIR, exist_ok=True)
os.makedirs(REGISTRY_DIR, exist_ok=True)
os.makedirs(RECHARGE_LOG_DIR, exist_ok=True)
os.makedirs(PILOT_FUEL_DIR, exist_ok=True)
ALLOWED_CARD_HISTORY_EXT = {".xls", ".xlsx", ".html", ".htm"}
ALLOWED_REGISTRY_EXT = {".xls", ".xlsx"}
ALLOWED_CHAT_EXT = {".txt"}

VALID_CATEGORIES = {"protrack", "mettax", "pilot"}


def error(message, status=502):
    return {"error": message}, status


def save_upload(file_storage, allowed_ext, dest_dir):
    """Shared upload-validation helper. Returns (dest_path, filename) or (None, error_tuple)."""
    if not file_storage.filename:
        return None, error("No file selected.", status=400)

    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in allowed_ext:
        return None, error(
            f"Unsupported file type '{ext}'. Expected one of {sorted(allowed_ext)}.", status=400
        )

    filename = secure_filename(file_storage.filename)
    dest = os.path.join(dest_dir, filename)
    file_storage.save(dest)
    return (dest, filename), None


# ---------------------------------------------------------------------------
# Card / billing history
# ---------------------------------------------------------------------------

class CardHistory(Resource):
    def get(self):
        try:
            records = card_history.load_all()
        except Exception as e:
            return error(f"Could not parse card history exports: {e}", status=500)

        imei_filter = request.args.get("imei", "").strip()
        type_filter = request.args.get("type", "").strip()

        if imei_filter:
            records = [r for r in records if r["imei"] == imei_filter]
        if type_filter:
            records = [r for r in records if r["type"] == type_filter]

        return {"summary": card_history.summarize(records), "transactions": records}


class CardHistoryUpload(Resource):
    def post(self):
        if "file" not in request.files:
            return error("No file part in the request.", status=400)

        result, err = save_upload(request.files["file"], ALLOWED_CARD_HISTORY_EXT, CARD_HISTORY_DIR)
        if err:
            return err
        dest, filename = result

        try:
            records = card_history.parse_card_history_file(dest)
        except Exception as e:
            os.remove(dest)
            return error(f"File uploaded but couldn't be parsed: {e}", status=400)

        return {"message": "Uploaded.", "filename": filename, "rowsParsed": len(records)}


# ---------------------------------------------------------------------------
# Device registry
# ---------------------------------------------------------------------------

class DeviceRegistry(Resource):
    def get(self):
        try:
            registry = device_registry.load_registry()
        except Exception as e:
            return error(f"Could not parse device registry: {e}", status=500)
        return {"count": len(registry), "devices": registry}


class DeviceRegistryUpload(Resource):
    def post(self):
        if "file" not in request.files:
            return error("No file part in the request.", status=400)

        result, err = save_upload(request.files["file"], ALLOWED_REGISTRY_EXT, REGISTRY_DIR)
        if err:
            return err
        dest, filename = result

        try:
            records = device_registry.parse_registry_file(dest)
        except Exception as e:
            os.remove(dest)
            return error(f"File uploaded but couldn't be parsed: {e}", status=400)

        if not records:
            os.remove(dest)
            return error("No device rows found - check the sheet has a NUMBERPLATE column.", status=400)

        return {"message": "Uploaded.", "filename": filename, "rowsParsed": len(records)}


# ---------------------------------------------------------------------------
# WhatsApp recharge log
# ---------------------------------------------------------------------------

class RechargeLog(Resource):
    def get(self):
        """
        Optional ?category=protrack|mettax|pilot filters the returned events.
        Each event is annotated with "matched" (bool) and "matchedPlate"
        (the registry plate it matched, or null) so the UI can show a
        small ✅/❌ icon per message - the actual matching logic lives in
        recharge_schedule.annotate_events(), reused from build_schedule().
        """
        try:
            events, unparsed = whatsapp_parser.load_all()
            registry = device_registry.load_registry()
        except Exception as e:
            return error(f"Could not parse WhatsApp chat log: {e}", status=500)

        events = recharge_schedule.annotate_events(events, registry)

        category = request.args.get("category", "").strip().lower()
        if category:
            if category not in VALID_CATEGORIES:
                return error(f"Invalid category '{category}'. Expected one of {sorted(VALID_CATEGORIES)}.", status=400)
            events = [e for e in events if e.get("category") == category]

        matched_count = sum(1 for e in events if e["matched"])

        return {
            "count": len(events),
            "matchedCount": matched_count,
            "unmatchedCount": len(events) - matched_count,
            "events": events,
            "unparsedCount": len(unparsed),
            "unparsed": unparsed,
        }


class RechargeLogUpload(Resource):
    def post(self):
        if "file" not in request.files:
            return error("No file part in the request.", status=400)

        result, err = save_upload(request.files["file"], ALLOWED_CHAT_EXT, RECHARGE_LOG_DIR)
        if err:
            return err
        dest, filename = result

        try:
            events, unparsed = whatsapp_parser.parse_chat_file(dest)
        except Exception as e:
            os.remove(dest)
            return error(f"File uploaded but couldn't be parsed: {e}", status=400)

        return {
            "message": "Uploaded.",
            "filename": filename,
            "eventsParsed": len(events),
            "unparsedLines": len(unparsed),
        }


# ---------------------------------------------------------------------------
# Recharge schedule (device registry <-> WhatsApp log matcher)
# ---------------------------------------------------------------------------

class RechargeSchedule(Resource):
    def get(self):
        """
        Matches the device registry against parsed WhatsApp recharge events
        and returns, per device: last recharge date/amount, and the
        suggested next recharge due date (last recharge + 3 months by
        default).

        Query params:
            months=N            override the recharge interval (default 3)
            category=protrack|mettax|pilot
                                 restrict to one program's recharges; when
                                 set, devices with no history in that
                                 category are omitted (dedicated section
                                 view) rather than shown as "no data".
        """
        try:
            registry = device_registry.load_registry()
            events, _unparsed = whatsapp_parser.load_all()
        except Exception as e:
            return error(f"Could not build recharge schedule: {e}", status=500)

        if not registry:
            return error("No device registry uploaded yet. Upload your devices spreadsheet first.", status=400)

        try:
            months = int(request.args.get("months", 3))
        except ValueError:
            months = 3

        category = request.args.get("category", "").strip().lower() or None
        if category and category not in VALID_CATEGORIES:
            return error(f"Invalid category '{category}'. Expected one of {sorted(VALID_CATEGORIES)}.", status=400)

        schedule, unmatched = recharge_schedule.build_schedule(
            registry, events, recharge_interval_months=months, category=category
        )

        response = {
            "summary": recharge_schedule.summarize(schedule, unmatched),
            "schedule": schedule,
            "unmatchedEvents": unmatched,
        }

        if category is None:
            # Give the "all categories" view a quick breakdown so the UI
            # can surface counts/links to the dedicated Mettax/Pilot tabs.
            breakdown = {"protrack": 0, "mettax": 0, "pilot": 0}
            for row in schedule:
                cat = row.get("lastRechargeCategory")
                if cat in breakdown:
                    breakdown[cat] += 1
            response["categoryBreakdown"] = breakdown

        return response


# ---------------------------------------------------------------------------
# Pilot Fuel (manually managed devices with fixed 70mb recharges)
# ---------------------------------------------------------------------------

class PilotFuel(Resource):
    def get(self):
        """Returns the pilot fuel schedule with due-date status per device."""
        try:
            schedule, summary = pilot_fuel.build_schedule()
        except Exception as e:
            return error(f"Could not load pilot fuel schedule: {e}", status=500)
        return {"summary": summary, "schedule": schedule}

    def post(self):
        """Adds or updates a pilot fuel device with a plate and recharge date."""
        data = request.get_json(silent=True) or {}
        plate = data.get("plate", "")
        recharge_date = data.get("date", "").strip()

        record, err = pilot_fuel.add_device(plate, recharge_date)
        if err:
            return err
        return {"message": "Device added.", "device": record}, 201


class PilotFuelDevice(Resource):
    def delete(self, plate):
        """Removes a pilot fuel device by plate."""
        if pilot_fuel.delete_device(plate):
            return {"message": f"Deleted {plate}"}
        return error("Device not found", status=404)


api.add_resource(CardHistory, "/api/card-history")
api.add_resource(CardHistoryUpload, "/api/card-history/upload")
api.add_resource(DeviceRegistry, "/api/device-registry")
api.add_resource(DeviceRegistryUpload, "/api/device-registry/upload")
api.add_resource(RechargeLog, "/api/recharge-log")
api.add_resource(RechargeLogUpload, "/api/recharge-log/upload")
api.add_resource(RechargeSchedule, "/api/recharge-schedule")
api.add_resource(PilotFuel, "/api/pilot-fuel")
api.add_resource(PilotFuelDevice, "/api/pilot-fuel/<string:plate>")

# Airtime (Safaricom) - the "Expired" sub-tab under Recharge Schedule.
# These endpoints are protected by the X-API-Key header (api_auth.py).
airtime_lines.ensure_schema()
api.add_resource(airtime_routes.AirtimeLines, "/api/airtime/lines")
api.add_resource(airtime_routes.AirtimeLineCreate, "/api/airtime/lines")
api.add_resource(airtime_routes.AirtimeLineItem, "/api/airtime/lines/<int:line_id>")
api.add_resource(airtime_routes.AirtimeRecharge, "/api/airtime/lines/<int:line_id>/recharge")
api.add_resource(airtime_routes.AirtimeExpiryReport, "/api/airtime/lines/<int:line_id>/expiry")
api.add_resource(airtime_routes.AirtimeAttempts, "/api/airtime/attempts")
api.add_resource(
    airtime_routes.build_attempt_resolver("confirm"), "/api/airtime/attempts/<int:attempt_id>/confirm"
)
api.add_resource(
    airtime_routes.build_attempt_resolver("fail"), "/api/airtime/attempts/<int:attempt_id>/fail"
)
api.add_resource(
    airtime_routes.build_attempt_resolver("cancel"), "/api/airtime/attempts/<int:attempt_id>/cancel"
)
api.add_resource(airtime_routes.AirtimeRemindersDue, "/api/airtime/reminders/due")
api.add_resource(airtime_routes.AirtimeUssdPreview, "/api/airtime/ussd/preview")

# Admin routes
api.add_resource(admin_routes.AdminHealth, "/api/admin/health")
api.add_resource(admin_routes.AdminStats, "/api/admin/stats")
api.add_resource(admin_routes.AdminFiles, "/api/admin/files")
api.add_resource(admin_routes.AdminFileDelete, "/api/admin/files/<string:file_type>/<path:filename>")
api.add_resource(admin_routes.AdminConfig, "/api/admin/config")
api.add_resource(admin_routes.AdminCache, "/api/admin/cache")
api.add_resource(admin_routes.AdminLogs, "/api/admin/logs")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)