"""
ProTrack365 Device Dashboard - Flask backend
---------------------------------------------
Wraps the ProTrack365 Open API (https://www.protrack365.com/api.html)
and exposes a small REST API that the JS frontend consumes.

Env vars required:
    PROTRACK_ACCOUNT   - your ProTrack365 account name
    PROTRACK_PASSWORD  - your ProTrack365 account password
    PROTRACK_BASE_URL  - optional, defaults to http://api.protrack365.com
"""

import hashlib
import os
import time
import threading

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.utils import secure_filename

import card_history
import device_registry
import whatsapp_parser
import recharge_schedule

app = Flask(__name__)
CORS(app)

CARD_HISTORY_DIR = card_history.CARD_HISTORY_DIR
REGISTRY_DIR = device_registry.REGISTRY_DIR
RECHARGE_LOG_DIR = whatsapp_parser.RECHARGE_LOG_DIR
os.makedirs(CARD_HISTORY_DIR, exist_ok=True)
os.makedirs(REGISTRY_DIR, exist_ok=True)
os.makedirs(RECHARGE_LOG_DIR, exist_ok=True)
ALLOWED_UPLOAD_EXT = {".xls", ".xlsx", ".html", ".htm"}
ALLOWED_REGISTRY_EXT = {".xls", ".xlsx"}
ALLOWED_CHAT_EXT = {".txt"}

PROTRACK_ACCOUNT = os.environ.get("PROTRACK_ACCOUNT", "")
PROTRACK_PASSWORD = os.environ.get("PROTRACK_PASSWORD", "")
BASE_URL = os.environ.get("PROTRACK_BASE_URL", "http://api.protrack365.com")

REQUEST_TIMEOUT = 15  # seconds


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

    # ---------- auth ----------

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
        # refresh 2 minutes early to be safe
        self._token_expires_at = time.time() + record.get("expires_in", 7200) - 120
        return self._token

    def get_token(self):
        with self._lock:
            if self._token and time.time() < self._token_expires_at:
                return self._token
            return self._fetch_new_token()

    # ---------- api calls ----------

    def _get(self, path, params, retry_on_auth_error=True):
        params = dict(params)
        params["access_token"] = self.get_token()
        resp = requests.get(f"{self.base_url}{path}", params=params, timeout=REQUEST_TIMEOUT)
        data = resp.json()

        # token expired/invalid mid-flight -> force refresh once and retry
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


def api_error(message, status=502):
    return jsonify({"error": message}), status


@app.route("/api/health")
def health():
    configured = bool(PROTRACK_ACCOUNT and PROTRACK_PASSWORD)
    return jsonify({"status": "ok", "configured": configured})


@app.route("/api/devices")
def list_devices():
    """
    Returns every device on the account (basic info from /api/device/list),
    merged with live status/position from /api/track when available.
    """
    if not (PROTRACK_ACCOUNT and PROTRACK_PASSWORD):
        return api_error(
            "Server isn't configured with PROTRACK_ACCOUNT / PROTRACK_PASSWORD.", status=500
        )

    try:
        list_resp = client.device_list()
    except TokenError as e:
        return api_error(f"Authentication failed: {e}", status=401)
    except requests.RequestException as e:
        return api_error(f"Could not reach ProTrack365: {e}", status=502)

    if list_resp.get("code") != 0:
        return api_error(list_resp.get("message") or f"ProTrack365 error {list_resp.get('code')}")

    devices = list_resp.get("record") or []
    imeis = [d["imei"] for d in devices]

    # Enrich with live tracking data (position/status), best-effort.
    track_by_imei = {}
    if imeis:
        try:
            # /api/track accepts max 100 IMEIs per request
            for i in range(0, len(imeis), 100):
                chunk = imeis[i:i + 100]
                track_resp = client.track(chunk)
                if track_resp.get("code") == 0:
                    for t in track_resp.get("record") or []:
                        track_by_imei[t["imei"]] = t
        except requests.RequestException:
            pass  # devices list still useful without live data

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

    return jsonify({"count": len(merged), "devices": merged})


@app.route("/api/devices/<imei>")
def device_detail(imei):
    try:
        detail_resp = client.device_detail([imei])
        track_resp = client.track([imei])
    except TokenError as e:
        return api_error(f"Authentication failed: {e}", status=401)
    except requests.RequestException as e:
        return api_error(f"Could not reach ProTrack365: {e}", status=502)

    if detail_resp.get("code") != 0:
        return api_error(detail_resp.get("message") or f"ProTrack365 error {detail_resp.get('code')}")

    records = detail_resp.get("record") or []
    if not records:
        return api_error("Device not found", status=404)

    d = records[0]
    t = (track_resp.get("record") or [{}])[0] if track_resp.get("code") == 0 else {}
    datastatus = t.get("datastatus")

    return jsonify({
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


@app.route("/api/card-history")
def get_card_history():
    """
    Returns parsed transactions from every ProTrack365 card-history export
    (.xls/.html) found in the data/ folder, newest first, plus summary
    stats. Supports optional ?imei=... and ?type=... filters.
    """
    try:
        records = card_history.load_all()
    except Exception as e:
        return api_error(f"Could not parse card history exports: {e}", status=500)

    imei_filter = request.args.get("imei", "").strip()
    type_filter = request.args.get("type", "").strip()

    if imei_filter:
        records = [r for r in records if r["imei"] == imei_filter]
    if type_filter:
        records = [r for r in records if r["type"] == type_filter]

    return jsonify({
        "summary": card_history.summarize(records),
        "transactions": records,
    })


@app.route("/api/card-history/upload", methods=["POST"])
def upload_card_history():
    """
    Accepts a new ProTrack365 card-history export (multipart/form-data,
    field name 'file') and stores it in the data/ folder so it's picked up
    by /api/card-history going forward.
    """
    if "file" not in request.files:
        return api_error("No file part in the request.", status=400)

    f = request.files["file"]
    if not f.filename:
        return api_error("No file selected.", status=400)

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        return api_error(
            f"Unsupported file type '{ext}'. Expected one of {sorted(ALLOWED_UPLOAD_EXT)}.",
            status=400,
        )

    filename = secure_filename(f.filename)
    dest = os.path.join(CARD_HISTORY_DIR, filename)
    f.save(dest)

    # Validate it actually parses as a ProTrack365 card-history export
    try:
        records = card_history.parse_card_history_file(dest)
    except Exception as e:
        os.remove(dest)
        return api_error(f"File uploaded but couldn't be parsed: {e}", status=400)

    return jsonify({"message": "Uploaded.", "filename": filename, "rowsParsed": len(records)})


@app.route("/api/device-registry")
def get_device_registry():
    try:
        registry = device_registry.load_registry()
    except Exception as e:
        return api_error(f"Could not parse device registry: {e}", status=500)
    return jsonify({"count": len(registry), "devices": registry})


@app.route("/api/device-registry/upload", methods=["POST"])
def upload_device_registry():
    if "file" not in request.files:
        return api_error("No file part in the request.", status=400)

    f = request.files["file"]
    if not f.filename:
        return api_error("No file selected.", status=400)

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_REGISTRY_EXT:
        return api_error(
            f"Unsupported file type '{ext}'. Expected one of {sorted(ALLOWED_REGISTRY_EXT)}.",
            status=400,
        )

    filename = secure_filename(f.filename)
    dest = os.path.join(REGISTRY_DIR, filename)
    f.save(dest)

    try:
        records = device_registry.parse_registry_file(dest)
    except Exception as e:
        os.remove(dest)
        return api_error(f"File uploaded but couldn't be parsed: {e}", status=400)

    if not records:
        os.remove(dest)
        return api_error("No device rows found - check the sheet has a NUMBERPLATE column.", status=400)

    return jsonify({"message": "Uploaded.", "filename": filename, "rowsParsed": len(records)})


@app.route("/api/recharge-log")
def get_recharge_log():
    try:
        events, unparsed = whatsapp_parser.load_all()
    except Exception as e:
        return api_error(f"Could not parse WhatsApp chat log: {e}", status=500)
    return jsonify({
        "count": len(events),
        "events": events,
        "unparsedCount": len(unparsed),
        "unparsed": unparsed,
    })


@app.route("/api/recharge-log/upload", methods=["POST"])
def upload_recharge_log():
    if "file" not in request.files:
        return api_error("No file part in the request.", status=400)

    f = request.files["file"]
    if not f.filename:
        return api_error("No file selected.", status=400)

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_CHAT_EXT:
        return api_error(
            f"Unsupported file type '{ext}'. Expected {sorted(ALLOWED_CHAT_EXT)}.",
            status=400,
        )

    filename = secure_filename(f.filename)
    dest = os.path.join(RECHARGE_LOG_DIR, filename)
    f.save(dest)

    try:
        events, unparsed = whatsapp_parser.parse_chat_file(dest)
    except Exception as e:
        os.remove(dest)
        return api_error(f"File uploaded but couldn't be parsed: {e}", status=400)

    return jsonify({
        "message": "Uploaded.",
        "filename": filename,
        "eventsParsed": len(events),
        "unparsedLines": len(unparsed),
    })


@app.route("/api/recharge-schedule")
def get_recharge_schedule():
    """
    Matches the device registry against parsed WhatsApp recharge events and
    returns, per device: last recharge date/amount, and the suggested next
    recharge due date (last recharge + 3 months by default).
    Optional ?months=N overrides the recharge interval.
    """
    try:
        registry = device_registry.load_registry()
        events, _unparsed = whatsapp_parser.load_all()
    except Exception as e:
        return api_error(f"Could not build recharge schedule: {e}", status=500)

    if not registry:
        return api_error(
            "No device registry uploaded yet. Upload your devices spreadsheet first.", status=400
        )

    try:
        months = int(request.args.get("months", 3))
    except ValueError:
        months = 3

    schedule, unmatched = recharge_schedule.build_schedule(registry, events, recharge_interval_months=months)

    return jsonify({
        "summary": recharge_schedule.summarize(schedule, unmatched),
        "schedule": schedule,
        "unmatchedEvents": unmatched,
    })


@app.route("/")
def index():
    from flask import render_template
    return render_template("index.html")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)