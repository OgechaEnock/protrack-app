"""
ProTrack365 Dashboard - Flask-RESTful backend
-----------------------------------------------
A pure JSON API (no template rendering - the frontend is a separate
Node/Express app under ../frontend). Wraps the ProTrack365 Open API
(https://www.protrack365.com/api.html) and exposes device data, card
billing history, and a device-registry <-> WhatsApp recharge-log matcher
with dedicated Mettax/Pilot program sections.

Env vars required for the live ProTrack365 device endpoints:
    PROTRACK_ACCOUNT   - your ProTrack365 account name
    PROTRACK_PASSWORD  - your ProTrack365 account password
    PROTRACK_BASE_URL  - optional, defaults to http://api.protrack365.com
"""

import hashlib
import os
import time
import threading

import requests
from flask import Flask, request
from flask_restful import Api, Resource
from flask_cors import CORS
from werkzeug.utils import secure_filename

import card_history
import device_registry
import whatsapp_parser
import recharge_schedule

app = Flask(__name__)
CORS(app)
api = Api(app)

CARD_HISTORY_DIR = card_history.CARD_HISTORY_DIR
REGISTRY_DIR = device_registry.REGISTRY_DIR
RECHARGE_LOG_DIR = whatsapp_parser.RECHARGE_LOG_DIR
os.makedirs(CARD_HISTORY_DIR, exist_ok=True)
os.makedirs(REGISTRY_DIR, exist_ok=True)
os.makedirs(RECHARGE_LOG_DIR, exist_ok=True)
ALLOWED_CARD_HISTORY_EXT = {".xls", ".xlsx", ".html", ".htm"}
ALLOWED_REGISTRY_EXT = {".xls", ".xlsx"}
ALLOWED_CHAT_EXT = {".txt"}

PROTRACK_ACCOUNT = os.environ.get("PROTRACK_ACCOUNT", "")
PROTRACK_PASSWORD = os.environ.get("PROTRACK_PASSWORD", "")
BASE_URL = os.environ.get("PROTRACK_BASE_URL", "http://api.protrack365.com")

REQUEST_TIMEOUT = 15  # seconds

VALID_CATEGORIES = {"protrack", "mettax", "pilot"}


class TokenError(Exception):
    """Raised when we can't obtain/refresh an access token."""


class ProtrackClient:
    """
    Thin wrapper around the ProTrack365 API.
    Handles the md5(md5(password)+time) signature and caches the
    access token in memory until it's close to expiring.
    """

    def __init__(self, account, password, base_url):
        self.account = account
        self.password = password
        self.base_url = base_url.rstrip("/")
        self._token = None
        self._token_expires_at = 0
        self._lock = threading.Lock()

    def _sign(self, ts):
        pwd_md5 = hashlib.md5(self.password.encode("utf-8")).hexdigest()
        return hashlib.md5(f"{pwd_md5}{ts}".encode("utf-8")).hexdigest()

    def _fetch_new_token(self):
        ts = int(time.time())
        signature = self._sign(ts)
        resp = requests.get(
            f"{self.base_url}/api/authorization",
            params={"time": ts, "account": self.account, "signature": signature},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise TokenError(data.get("message") or f"authorization failed (code {data.get('code')})")

        record = data["record"]
        self._token = record["access_token"]
        self._token_expires_at = time.time() + record.get("expires_in", 7200) - 120
        return self._token

    def get_token(self):
        with self._lock:
            if self._token and time.time() < self._token_expires_at:
                return self._token
            return self._fetch_new_token()

    def _get(self, path, params, retry_on_auth_error=True):
        params = dict(params)
        params["access_token"] = self.get_token()
        resp = requests.get(f"{self.base_url}{path}", params=params, timeout=REQUEST_TIMEOUT)
        data = resp.json()

        if data.get("code") in (10010, 10011, 10012) and retry_on_auth_error:
            with self._lock:
                self._token = None
            return self._get(path, params={k: v for k, v in params.items() if k != "access_token"},
                              retry_on_auth_error=False)
        return data

    def device_list(self):
        return self._get("/api/device/list", {})

    def track(self, imeis):
        return self._get("/api/track", {"imeis": ",".join(imeis)})

    def device_detail(self, imeis):
        return self._get("/api/device/detail", {"imeis": ",".join(imeis)})


client = ProtrackClient(PROTRACK_ACCOUNT, PROTRACK_PASSWORD, BASE_URL)

DATASTATUS_LABELS = {
    1: "Never online",
    2: "Online",
    3: "Expired",
    4: "Offline",
    5: "Blocked",
}


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
# Live ProTrack365 device endpoints
# ---------------------------------------------------------------------------

class Health(Resource):
    def get(self):
        configured = bool(PROTRACK_ACCOUNT and PROTRACK_PASSWORD)
        return {"status": "ok", "configured": configured}


class Devices(Resource):
    def get(self):
        """All devices on the account, merged with live status/position."""
        if not (PROTRACK_ACCOUNT and PROTRACK_PASSWORD):
            return error("Server isn't configured with PROTRACK_ACCOUNT / PROTRACK_PASSWORD.", status=500)

        try:
            list_resp = client.device_list()
        except TokenError as e:
            return error(f"Authentication failed: {e}", status=401)
        except requests.RequestException as e:
            return error(f"Could not reach ProTrack365: {e}", status=502)

        if list_resp.get("code") != 0:
            return error(list_resp.get("message") or f"ProTrack365 error {list_resp.get('code')}")

        devices = list_resp.get("record") or []
        imeis = [d["imei"] for d in devices]

        track_by_imei = {}
        if imeis:
            try:
                for i in range(0, len(imeis), 100):
                    chunk = imeis[i:i + 100]
                    track_resp = client.track(chunk)
                    if track_resp.get("code") == 0:
                        for t in track_resp.get("record") or []:
                            track_by_imei[t["imei"]] = t
            except requests.RequestException:
                pass

        merged = []
        for d in devices:
            t = track_by_imei.get(d["imei"], {})
            datastatus = t.get("datastatus")
            merged.append({
                "imei": d.get("imei"),
                "name": d.get("devicename"),
                "model": d.get("devicetype"),
                "plate": d.get("platenumber"),
                "onlinetime": d.get("onlinetime"),
                "platformduetime": d.get("platformduetime"),
                "status": DATASTATUS_LABELS.get(datastatus, "Unknown" if datastatus is None else datastatus),
                "statusCode": datastatus,
                "latitude": t.get("latitude"),
                "longitude": t.get("longitude"),
                "speed": t.get("speed"),
                "battery": t.get("battery"),
                "gpstime": t.get("gpstime"),
                "accStatus": t.get("accstatus"),
            })

        return {"count": len(merged), "devices": merged}


class DeviceDetail(Resource):
    def get(self, imei):
        try:
            detail_resp = client.device_detail([imei])
            track_resp = client.track([imei])
        except TokenError as e:
            return error(f"Authentication failed: {e}", status=401)
        except requests.RequestException as e:
            return error(f"Could not reach ProTrack365: {e}", status=502)

        if detail_resp.get("code") != 0:
            return error(detail_resp.get("message") or f"ProTrack365 error {detail_resp.get('code')}")

        records = detail_resp.get("record") or []
        if not records:
            return error("Device not found", status=404)

        d = records[0]
        t = (track_resp.get("record") or [{}])[0] if track_resp.get("code") == 0 else {}
        datastatus = t.get("datastatus")

        return {
            "imei": d.get("imei"),
            "name": d.get("devicename"),
            "model": d.get("devicetype"),
            "plate": d.get("platenumber"),
            "onlinetime": d.get("onlinetime"),
            "platformduetime": d.get("platformduetime"),
            "status": DATASTATUS_LABELS.get(datastatus, "Unknown" if datastatus is None else datastatus),
            "statusCode": datastatus,
            "latitude": t.get("latitude"),
            "longitude": t.get("longitude"),
            "speed": t.get("speed"),
            "battery": t.get("battery"),
            "gpstime": t.get("gpstime"),
            "accStatus": t.get("accstatus"),
        }


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


api.add_resource(Health, "/api/health")
api.add_resource(Devices, "/api/devices")
api.add_resource(DeviceDetail, "/api/devices/<string:imei>")
api.add_resource(CardHistory, "/api/card-history")
api.add_resource(CardHistoryUpload, "/api/card-history/upload")
api.add_resource(DeviceRegistry, "/api/device-registry")
api.add_resource(DeviceRegistryUpload, "/api/device-registry/upload")
api.add_resource(RechargeLog, "/api/recharge-log")
api.add_resource(RechargeLogUpload, "/api/recharge-log/upload")
api.add_resource(RechargeSchedule, "/api/recharge-schedule")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)