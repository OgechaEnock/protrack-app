# ProTrack365 Device Dashboard

A Flask (RESTful backend) + vanilla JavaScript frontend for managing GPS device
card billing history, recharge schedules (Protrack/Mettax/Pilot), manually
tracked Pilot Fuel devices, and Safaricom airtime lines (the **Expired**
sub-tab).

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
  - `GET/POST/DELETE /api/pilot-fuel` — manually managed Pilot Fuel devices
    (plate + last recharge date, fixed 70MB recharge every 2 months).
  - `/api/airtime/*` — Safaricom airtime lines, weekly reminders and recharge
    attempts (see [Airtime / Expired tab](#airtime--expired-tab) below).
  - Admin endpoints under `/api/admin/*` for file management, configuration,
    and audit logs.
- **Frontend (`public/js/app.js`)** calls those endpoints and renders:
  - A **Card History** tab with summary cards, filters, and a transaction table.
  - A **Recharge Schedule** tab with Protrack/Mettax/Pilot sub-tabs, each
    showing per-device due dates and the raw recharge messages with
    matched/unmatched plate indicators.
  - A **Pilot Fuel** sub-tab where the admin manually adds devices (plate
    number + last recharge date). The system computes the next due date
    2 months out and shows status/summary cards.
  - An **Expired** sub-tab (`public/js/airtime.js`) for Safaricom airtime
    lines that need attention.

Your data never leaves your machine — everything is parsed from files you
upload through the UI.

## Setup

```bash
cd protrack-app/backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Run the backend:

```bash
cd backend
python app.py
```

Run the frontend (separate terminal):

```bash
cd frontend
npm install
npm start
```

Visit **http://localhost:3000** for the dashboard and
**http://localhost:3000/admin** for the admin panel.

### Running the tests

```bash
cd backend
pip install pytest
python -m pytest tests -v
```

The test-suite runs against a throwaway SQLite database (via the
`AIRTIME_DB_PATH` environment variable), so your real `data/protrack.db` is
never modified by the tests.

---

## Admin panel

The admin panel at **http://localhost:3000/admin** is protected by an API key
that is sent in the `X-Admin-API-Key` header.

Set the key on the backend before starting it:

```bash
export ADMIN_API_KEY="your-secret-key"
cd backend && python app.py
```

If `ADMIN_API_KEY` is not set, the backend falls back to the development
default `admin123` and prints a warning in the log - set it before exposing
the panel to anyone else.

The panel remembers the key in the browser, so you only type it once. You can
change the backend address and key at any time under **Connection settings**
on the login screen. To point the panel at another backend without using the
UI, set the values before the script loads:

```html
<script>window.ADMIN_API_KEY = "your-secret-key";</script>
<script>window.ADMIN_API_BASE = "http://192.168.1.20:5000/api";</script>
```

The same values can be stored directly in `localStorage`:

```js
localStorage.setItem("protrack.adminApiKey", "your-secret-key");
localStorage.setItem("protrack.adminApiBase", "http://192.168.1.20:5000/api");
```

Both are stored in that browser only - nothing is sent anywhere else.

---

## Airtime / Expired tab

The **Expired** sub-tab lives inside **Recharge Schedule** and tracks
Safaricom airtime lines: which ones are due a top-up, which are overdue, and
which ones somebody has reported as expired.

### The important distinction

| What you see | What it actually means |
| --- | --- |
| **Overdue reminder** | Our weekly reminder date has passed. This says **nothing** about the real SIM state. The row says *"Overdue reminder — SIM expiry unverified"*. |
| **SIM expiry reported** | A human explicitly reported that the SIM has expired. The reporter, date and note are recorded. |
| **Reminder due** | The reminder falls on today. |
| **Recharge awaiting confirmation** | A recharge was started but nobody has confirmed it yet. |
| **Recharge failed** | The last attempt was marked as failed. The reminder schedule is unchanged. |

Nothing in this codebase talks to Safaricom's systems, so the dashboard never
claims that Safaricom verified a SIM's expiry. Only a person can report it.

### The recharge state machine

```
reminder due  ->  start recharge  ->  pending_confirmation  ->  confirmed   (schedule advances)
                                     |                 \
                                     |                  -> failed        (schedule unchanged)
                                     |                  -> cancelled     (schedule unchanged)
```

- **Only a confirmed recharge** sets `lastConfirmedRechargeDate` and moves
  `nextReminderDate` forward by the line's reminder frequency.
- **Failed and cancelled** attempts leave the schedule exactly where it was,
  so the line stays due and can be retried.
- Confirming the same attempt twice returns **409 Conflict** and does not
  advance the schedule twice.

### USSD command

The command is built in one place (`backend/airtime_ussd.py`) and used by the
API, the UI and the tests, so it can never drift between surfaces:

```
*140*AMOUNT*PHONE_NUMBER#          e.g.  *140*10*0712345678#
```

The browser hands this to the Android dialer as a `tel:` URI:

```
tel:*140*10*0712345678%23
```

> ⚠️ **This USSD code has not been verified against Safaricom's current
> airtime menu.** `*140*` may only work for certain accounts or products.
> Always confirm the real behaviour manually on the SIM before relying on it.
> The tool never touches M-PESA PINs, OTPs or any payment credential — it
> only formats a string that you then dial yourself.

### Manual recharge steps (Android Chrome)

1. Open the dashboard → **Recharge Schedule** → **Expired**.
2. Find the line and press **Recharge now**.
3. The dialog shows the exact USSD command and a **📞 Open dialer** button.
   Pressing it opens the Android dialer with the command pre-filled (press
   call).
4. Follow the prompts on the handset until Safaricom confirms the airtime was
   sent.
5. Come back to the dashboard and press **✅ Yes, it worked**. Only now is the
   recharge recorded as confirmed and the next reminder date moved forward.
   If the recharge failed, press **❌ No / it failed** instead — the reminder
   schedule stays untouched so you can retry.

### Known limitations (please read)

- **A browser cannot run in the background reliably.** Closed tabs, phone
  screen locks and browser process suspension all stop JavaScript from
  running. The "reminders" are therefore surfaced on-screen when you open the
  Expired tab — they are **not** push notifications. For unattended reminders,
  point a server-side scheduler at `GET /api/airtime/reminders/due` and send
  your own notification (SMS, email, etc.).
- **Opening a `tel:` URI does not prove the recharge succeeded.** A USSD
  session can be cancelled, time out, or be rejected by the network. That is
  exactly why every attempt stays *pending* until a human confirms it.
- **The dashboard cannot detect SIM expiry on its own.** An overdue reminder is
  only a missed top-up, so it is labelled *"Overdue reminder — SIM expiry
  unverified"*. Use **Report expiry** when a field technician confirms the SIM
  has expired.
- Phone numbers are **masked by default** in the UI (`0712 *** 678`). The
  full number is only sent when `?revealPhone=true` is requested. Tick
  **"Show full numbers"** in the Expired toolbar to load them; untick it to go
  back to masked numbers. The toggle is per-session and does not persist.

### Using the dashboard from a phone over Wi-Fi

`localhost` on a phone means *the phone itself*, so the default API address
will not work from a phone. Two ways to fix it:

1. **In the UI (recommended):** open the **Expired** tab →
   **Connection settings (API address & key)** and enter:
   - Backend API address: `http://<your-computer-LAN-IP>:5000/api`
     (find the IP with `hostname -I` on Linux, `ipconfig` on Windows)
   - API key: the same value as the backend's `AIRTIME_API_KEY`

   Both values are stored in that browser only (`localStorage`).

2. **In the page URL:** append `?apiBase=http://192.168.1.20:5000/api` to the
   dashboard URL — handy for a one-off bookmark.

Make sure the backend is bound to your LAN interface (it already listens on
`0.0.0.0:5000` when run via `python app.py`) and that your firewall allows
port 5000.

### Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `AIRTIME_API_KEY` | `protrack-airtime-dev` | Key required in the `X-API-Key` header for every `/api/airtime/*` route. **Set this before exposing the API.** |
| `AIRTIME_DB_PATH` | `backend/data/protrack.db` | SQLite file for the airtime tables. |

The airtime tables (`airtime_lines`, `recharge_attempts`) are created
automatically in the existing SQLite database on first start — no separate
migration step, and no new migration framework. The schema creation is
idempotent, so it is safe to run on every boot.

### API reference

All `/api/airtime/*` routes require the `X-API-Key` header
(`?api_key=...` also works, for testing from a phone browser).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/airtime/lines` | List lines + summary. Query: `status`, `attention=true`, `includeArchived=true`, `revealPhone=true`. The returned `summary` is always **global** (counting every non-archived line) while `lines` honours the `status`/`attention` filters; `summary.filteredLines` says how many rows came back. |
| `POST` | `/api/airtime/lines` | Register a line. `409` if the number is already registered. |
| `GET` | `/api/airtime/lines/<id>` | One line. Query: `revealPhone=true`. |
| `PATCH` | `/api/airtime/lines/<id>` | Update a line. Only the keys you send are changed. Send `expiryReported: false` to clear a SIM expiry report (and `expiryNote: null` to drop the note); omit the key entirely to leave the report as it is. |
| `DELETE` | `/api/airtime/lines/<id>` | Archive a line. |
| `POST` | `/api/airtime/lines/<id>/recharge` | Start a recharge attempt; returns the USSD string + `tel:` URI. Does **not** confirm it. |
| `POST` | `/api/airtime/lines/<id>/expiry` | Report (`reported: true`) or clear (`reported: false`) a SIM expiry. |
| `GET` | `/api/airtime/attempts` | Recent attempts. Query: `lineId`, `limit`. |
| `POST` | `/api/airtime/attempts/<id>/confirm` | Confirm a recharge (advances the schedule). `409` if already confirmed. Returns the updated `attempt` **and** the updated `line` (new next-reminder date). |
| `POST` | `/api/airtime/attempts/<id>/fail` | Mark a recharge as failed (schedule unchanged). |
| `POST` | `/api/airtime/attempts/<id>/cancel` | Cancel a pending attempt (schedule unchanged). |
| `GET` | `/api/airtime/reminders/due` | Lines whose reminder is due — poll this from a scheduler. |
| `GET` | `/api/airtime/ussd/preview` | Build a USSD string without creating an attempt. |

Example:

```bash
curl -H "X-API-Key: $AIRTIME_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"label":"Truck 1","phoneNumber":"0712345678","amount":10,"reminderFrequencyDays":7}' \
     http://localhost:5000/api/airtime/lines
```

## Notes / gotchas

- Card-history exports are ProTrack365's HTML-table format saved with a `.xls`
  extension — the parser handles that automatically.
- The device registry sheet may have a title row above the real header; the
  parser scans the first few rows for the one containing "NUMBERPLATE".
- WhatsApp exports from both iOS and Android layouts are supported.
- Recharge events are categorized into Protrack (base), Mettax, and Pilot
  programs based on remark tags in the chat messages.
- Airtime phone numbers are stored in E.164 form (`254712345678`) and are
  displayed masked (`0712 *** 678`) unless you explicitly ask for the full
  number.
- The `*140*` USSD airtime command is a project assumption that has **not**
  been verified against Safaricom's live menu. Verify it on a test SIM first.
- The summary cards in the Expired tab always describe **every** registered
  line, even when the table below is filtered (for example to "Needs
  attention"). The "Showing N of M" line makes the difference explicit.
- An **overdue reminder** only means our top-up date has passed. It is never
  treated as proof that the SIM expired — a line is only marked "SIM expiry
  reported" when somebody reports it explicitly, and that report has to be
  cleared explicitly too.

## Production notes

This is a solid starting point, not a hardened deployment. Before shipping:
- Run behind gunicorn/uwsgi instead of the Flask dev server.
- Add rate limiting / auth on your own `/api/*` routes if this will be
  exposed beyond your local network.
- **Set `AIRTIME_API_KEY`** to a strong secret. The default
  (`protrack-airtime-dev`) is only for local development, and the backend
  prints a warning on startup when it is missing.
- The airtime API holds phone numbers and money amounts, so it is
  key-protected. Serve the dashboard over HTTPS (or keep it on a trusted
  LAN) so the key is not sent in clear text.
- The key is stored in the browser's `localStorage` when set through the
  Expired tab's connection settings. On a shared device, clear it
  afterwards.