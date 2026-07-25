# ProTrack365 Device Dashboard

A Flask (RESTful backend) + vanilla JavaScript frontend that lists every GPS
device on your ProTrack365 account, with live status, position, speed and
battery, pulled from the [ProTrack365 Open API](https://www.protrack365.com/api.html).

## How it works

- **Backend (`app.py`)** authenticates against ProTrack365's
  `/api/authorization` endpoint using the required
  `md5(md5(password) + time)` signature, caches the access token in memory
  (tokens last 2 hours) and auto-refreshes it when it's about to expire or
  the API reports it as invalid.
- It then exposes two simple JSON endpoints:
  - `GET /api/devices` — merges `/api/device/list` (basic info) with
    `/api/track` (live position/status) for every device on the account.
  - `GET /api/devices/<imei>` — detail for a single device.
- **Frontend (`static/js/app.js`)** calls those endpoints, renders a
  searchable, sortable-by-status table, summary cards, and a detail modal.
  It polls every 30 seconds to keep positions fresh.

Your ProTrack365 account/password never touch the browser — only the Flask
server talks to ProTrack365, using credentials from environment variables.

## Setup

```bash
cd protrack-app
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Set your credentials (don't hardcode them in the source):

```bash
export PROTRACK_ACCOUNT="your_account"
export PROTRACK_PASSWORD="your_password"
# Optional, only if your vendor gave you a different API host:
# export PROTRACK_BASE_URL="http://api.protrack365.com"
```

Run it:

```bash
python app.py
```

Visit **http://localhost:5000**.

## Notes / gotchas

- `/api/device/list` returns at most 500 devices per account (a ProTrack365
  API limit) — fine for virtually all accounts.
- `/api/track` accepts at most 100 IMEIs per call, so the backend
  automatically chunks requests for larger fleets.
- If a device has never reported live data, its position/speed/battery
  fields will simply show as "—" in the UI — that's the API returning no
  track record for that IMEI, not a bug.
- Error code translations (invalid token, blocked account, etc.) come
  straight from ProTrack365's documented error table and are surfaced in
  the banner at the top of the page.

## Production notes

This is a solid starting point, not a hardened deployment. Before shipping:
- Run behind gunicorn/uwsgi instead of the Flask dev server.
- Add rate limiting / auth on your own `/api/*` routes if this will be
  exposed beyond your local network.
- Consider persisting the access token (e.g. Redis) if you run multiple
  backend workers, since each process currently caches its own token.
