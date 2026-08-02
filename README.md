# ProTrack365 Device Dashboard

A Flask (RESTful backend) + vanilla JavaScript frontend for managing GPS device
card billing history and recharge schedules, built from uploaded ProTrack365
card-history exports, device registry spreadsheets, and WhatsApp recharge logs.

## How it works

- **Backend (`app.py`)** exposes JSON endpoints for:
  - `GET /api/card-history` — parsed ProTrack365 card billing history
    (uploaded as `.xls`/`.xlsx`/`.html` exports).
  - `GET /api/device-registry` — the device master list (plate/SIM/IMEI/account),
    uploaded as an `.xlsx` spreadsheet.
  - `GET /api/recharge-log` — parsed WhatsApp recharge events, annotated with
    whether each message's plate matched a device in the registry.
  - `GET /api/recharge-schedule` — per-device next recharge due date
    (last recharge + 3 months by default), with Protrack/Mettax/Pilot
    program breakdowns.
  - Admin endpoints under `/api/admin/*` for file management, configuration,
    and audit logs.
- **Frontend (`public/js/app.js`)** calls those endpoints and renders:
  - A **Card History** tab with summary cards, filters, and a transaction table.
  - A **Recharge Schedule** tab with Protrack/Mettax/Pilot sub-tabs, each
    showing per-device due dates and the raw recharge messages with
    matched/unmatched plate indicators.

Your data never leaves your machine — everything is parsed from files you
upload through the UI.

## Setup

```bash
cd protrack-app
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Run the backend:

```bash
python app.py
```

Run the frontend (separate terminal):

```bash
cd Frontend
npm install
npm start
```

Visit **http://localhost:3000** for the dashboard and
**http://localhost:3000/admin** for the admin panel.

## Notes / gotchas

- Card-history exports are ProTrack365's HTML-table format saved with a `.xls`
  extension — the parser handles that automatically.
- The device registry sheet may have a title row above the real header; the
  parser scans the first few rows for the one containing "NUMBERPLATE".
- WhatsApp exports from both iOS and Android layouts are supported.
- Recharge events are categorized into Protrack (base), Mettax, and Pilot
  programs based on remark tags in the chat messages.

## Production notes

This is a solid starting point, not a hardened deployment. Before shipping:
- Run behind gunicorn/uwsgi instead of the Flask dev server.
- Add rate limiting / auth on your own `/api/*` routes if this will be
  exposed beyond your local network.