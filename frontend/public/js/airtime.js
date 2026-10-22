// Safaricom airtime - the "Expired" sub-tab under Recharge Schedule.
//
// This is the vanilla-JS equivalent of the React component that was supplied
// for this feature. It talks to the Flask API under /api/airtime/* and is
// protected by the X-API-Key header.
//
// IMPORTANT LIMITATIONS (repeated in the UI so users are never misled):
//   * A browser tab cannot reliably run in the background, so "reminders" are
//     surfaced on-screen when the tab is opened - they are not push
//     notifications. For unattended reminders, run a server-side scheduler
//     against GET /api/airtime/reminders/due.
//   * Opening a `tel:` URI only opens the Android dialer. It does NOT prove
//     the recharge succeeded, so every attempt stays "pending" until a human
//     explicitly confirms it.
//   * An overdue reminder is NOT evidence that a SIM expired.

// The backend API base URL.
//
// The default assumes the Flask backend runs on the same machine, on port
// 5000. To use the dashboard from a PHONE over Wi-Fi you must point this at
// the computer's LAN address (e.g. "http://192.168.1.20:5000/api"), because
// "localhost" on the phone means the phone itself.
//
// Resolution order:
//   1. ?apiBase=... in the page URL   (handy for a one-off bookmark)
//   2. localStorage (set once from the console, survives reloads)
//   3. window.AIRTIME_API_BASE        (set before this script loads)
//   4. the default below
const AIRTIME_DEFAULT_API_BASE = "http://localhost:5000/api";

// Mutable at runtime: the "Connection settings" panel lets the operator
// repoint the dashboard at another backend (e.g. the LAN address when the
// dashboard is opened on a phone).
let AIRTIME_API_BASE = resolveAirtimeApiBase();

function resolveAirtimeApiBase() {
  try {
    const fromQuery = new URLSearchParams(window.location.search).get("apiBase");
    if (fromQuery) return fromQuery.replace(/\/$/, "");
  } catch (err) {
    // Malformed URL - fall through to the other sources.
  }
  try {
    // The other panels store the address under their own keys, so a value
    // saved from any of them is picked up here too.
    const storedKeys = ["protrack.airtimeApiBase", "protrack.adminApiBase", "protrack.apiBase"];
    for (const key of storedKeys) {
      const stored = window.localStorage.getItem(key);
      if (stored) return stored.replace(/\/+$/, "");
    }
  } catch (err) {
    // localStorage can be blocked (private mode) - not fatal.
  }
  if (typeof window !== "undefined" && window.AIRTIME_API_BASE) {
    return String(window.AIRTIME_API_BASE).replace(/\/$/, "");
  }
  return AIRTIME_DEFAULT_API_BASE;
}

// The airtime API is key-protected. The default matches the backend's
// development default (AIRTIME_API_KEY). To point the dashboard at a
// different backend key, set it before this script loads:
//
//   <script>window.AIRTIME_API_KEY = "your-key";</script>
//   <script src="/js/airtime.js"></script>
//
// or store it once with:
//   localStorage.setItem("protrack.airtimeApiKey", "your-key")
function resolveAirtimeApiKey() {
  try {
    const stored = window.localStorage.getItem("protrack.airtimeApiKey");
    if (stored) return stored;
  } catch (err) {
    // localStorage can be blocked (private mode) - not fatal.
  }
  if (typeof window !== "undefined" && window.AIRTIME_API_KEY) {
    return window.AIRTIME_API_KEY;
  }
  return "protrack-airtime-dev";
}

let AIRTIME_API_KEY = resolveAirtimeApiKey();

const AIRTIME_STATUS_LABELS = {
  active: "Active",
  due: "Reminder due",
  overdue: "Overdue reminder",
  expiry_reported: "SIM expiry reported",
  recharge_pending: "Awaiting confirmation",
  recharge_confirmed: "Recharge confirmed",
  recharge_failed: "Recharge failed",
  paused: "Paused",
};

const AIRTIME_ATTEMPT_LABELS = {
  attempt_started: "Started",
  pending_confirmation: "Awaiting confirmation",
  confirmed: "Confirmed",
  failed: "Failed",
  cancelled: "Cancelled",
};

// ---------- Small helpers ----------

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function airtimeFetch(endpoint, options = {}) {
  const headers = Object.assign(
    { "X-API-Key": AIRTIME_API_KEY },
    options.headers || {}
  );
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(`${AIRTIME_API_BASE}${endpoint}`, {
    method: options.method || "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  let data = {};
  try {
    data = await res.json();
  } catch (err) {
    data = { error: `Unexpected response from the server (HTTP ${res.status}).` };
  }

  if (!res.ok) {
    const message = data.error || `Request failed (HTTP ${res.status}).`;
    const error = new Error(message);
    error.status = res.status;
    error.field = data.field;
    throw error;
  }
  return data;
}

// ---------- The Expired sub-tab section ----------

window.AIRTIME_SECTION = (() => {
  let lines = [];
  let loadedOnce = false;

  const el = {
    tbody: document.getElementById("airtimeTbody"),
    emptyState: document.getElementById("emptyState"),
    addLabel: document.getElementById("airtimeAddLabel"),
    addPhone: document.getElementById("airtimeAddPhone"),
    addAmount: document.getElementById("airtimeAddAmount"),
    addBtn: document.getElementById("airtimeAddLineBtn"),
    addStatus: document.getElementById("airtimeAddStatus"),
  };

  // ---------- Data loading ----------

  async function fetchData() {
    el.tbody.innerHTML =
      `<tr><td colspan="5" class="loading-row">Loading expired lines…</td></tr>`;

    try {
      // Fetch lines that need attention (overdue reminders)
      const data = await airtimeFetch(`/airtime/lines?attention=true`);
      lines = data.lines || [];
      loadedOnce = true;
      renderTable();
    } catch (err) {
      el.tbody.innerHTML =
        `<tr><td colspan="5" class="empty-row">${escapeHtml(err.message || "Failed to load expired lines.")}</td></tr>`;
      console.error(err);
    }
  }

  // ---------- Add line ----------

  async function addLine() {
    const label = (el.addLabel.value || "").trim();
    const phone = (el.addPhone.value || "").trim();
    const amount = (el.addAmount.value || "").trim() || 10;

    if (!label) {
      el.addStatus.textContent = "Please enter a label.";
      return;
    }
    if (!phone) {
      el.addStatus.textContent = "Please enter a phone number.";
      return;
    }

    el.addStatus.textContent = "Adding…";

    try {
      await airtimeFetch("/airtime/lines", {
        method: "POST",
        body: {
          label,
          phoneNumber: phone,
          amount: Number(amount),
        },
      });

      el.addLabel.value = "";
      el.addPhone.value = "";
      el.addAmount.value = "10";
      el.addStatus.textContent = "Line added.";
      setTimeout(() => (el.addStatus.textContent = ""), 3000);

      await fetchData();
    } catch (err) {
      el.addStatus.textContent = err.message || "Failed to add line.";
      console.error(err);
    }
  }

  // ---------- Rendering ----------

  function renderTable() {
    if (!lines.length) {
      el.tbody.innerHTML = `<tr><td colspan="5" class="empty-row">No expired lines.</td></tr>`;
      if (el.emptyState) el.emptyState.classList.add("hidden");
      return;
    }

    if (el.emptyState) el.emptyState.classList.add("hidden");

    el.tbody.innerHTML = lines
      .map(line => {
        const phone = line.phoneMasked || line.phoneDisplay || "—";
        const daysOverdue = line.daysOverdue ?? line.daysUntilDue ?? "—";
        const amount = line.amount ?? 10;
        const ussd = `*140*${amount}*${line.phoneNumber || line.phoneMasked || ""}#`;

        return `
          <tr>
            <td>${escapeHtml(line.label || "—")}</td>
            <td>${escapeHtml(phone)}</td>
            <td>${escapeHtml(daysOverdue)}</td>
            <td><code style="font-size: 12px;">${escapeHtml(ussd)}</code></td>
            <td>
              <button class="upload-btn btn-primary recharge-btn"
                      data-ussd="${escapeHtml(ussd)}">
                📞 Recharge
              </button>
            </td>
          </tr>
        `;
      })
      .join("");
  }

  // ---------- USSD dialer ----------

  function openDialer(ussd) {
    // Opens the Android dialer with the USSD code pre-filled.
    // Works via Phone Link on Windows and directly on Android.
    window.location.href = `tel:${ussd}`;
  }

  // ---------- Event wiring ----------

  el.tbody.addEventListener("click", event => {
    const btn = event.target.closest(".recharge-btn");
    if (!btn) return;
    openDialer(btn.dataset.ussd);
  });

  el.addBtn.addEventListener("click", addLine);

  return { fetchData, isLoaded: () => loadedOnce };
})();
