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
  let attempts = [];
  // The backend summary is always GLOBAL (it counts every registered line);
  // `lines` is the filtered slice. We keep both so the table can say
  // "showing N of M".
  let summary = null;
  let loadedOnce = false;

  const el = {
    tbody: document.getElementById("airtimeTbody"),
    summary: document.getElementById("airtimeSummaryCards"),
    filterInfo: document.getElementById("airtimeFilterInfo"),
    search: document.getElementById("airtimeSearchInput"),
    statusFilter: document.getElementById("airtimeStatusFilter"),
    scopeFilter: document.getElementById("airtimeScopeFilter"),
    revealPhone: document.getElementById("airtimeRevealPhone"),
    addBtn: document.getElementById("airtimeAddBtn"),
    attemptsTbody: document.getElementById("airtimeAttemptsTbody"),
    attemptsSummary: document.getElementById("airtimeAttemptsSummary"),
    apiBaseInput: document.getElementById("airtimeApiBaseInput"),
    apiKeyInput: document.getElementById("airtimeApiKeyInput"),
    saveSettingsBtn: document.getElementById("airtimeSaveSettingsBtn"),
    settingsStatus: document.getElementById("airtimeSettingsStatus"),
  };

  const modal = {
    root: document.getElementById("airtimeModal"),
    title: document.getElementById("airtimeModalTitle"),
    label: document.getElementById("airtimeFormLabel"),
    phone: document.getElementById("airtimeFormPhone"),
    amount: document.getElementById("airtimeFormAmount"),
    frequency: document.getElementById("airtimeFormFrequency"),
    nextDate: document.getElementById("airtimeFormNextDate"),
    expiryReported: document.getElementById("airtimeFormExpiryReported"),
    expiryNoteWrap: document.getElementById("airtimeFormExpiryNoteWrap"),
    expiryNote: document.getElementById("airtimeFormExpiryNote"),
    cancel: document.getElementById("airtimeFormCancel"),
    save: document.getElementById("airtimeFormSave"),
  };

  const rechargeModal = {
    root: document.getElementById("airtimeRechargeModal"),
    ussdText: document.getElementById("airtimeUssdText"),
    ussdPhone: document.getElementById("airtimeUssdPhone"),
    ussdAmount: document.getElementById("airtimeUssdAmount"),
    dialBtn: document.getElementById("airtimeDialBtn"),
    copyBtn: document.getElementById("airtimeCopyBtn"),
    cancel: document.getElementById("airtimeRechargeCancel"),
    confirmOk: document.getElementById("airtimeConfirmOk"),
    confirmFail: document.getElementById("airtimeConfirmFail"),
  };

  // The line currently being recharged, and its pending attempt.
  let activeAttemptId = null;
  let editingLineId = null;

  // ---------- Data loading ----------

  async function fetchData() {
    el.tbody.innerHTML =
      `<tr><td colspan="8" class="loading-row">Loading airtime lines…</td></tr>`;

    const scope = el.scopeFilter ? el.scopeFilter.value : "attention";
    // Phone numbers are masked by default. The reveal flag is only sent when
    // the operator explicitly asks for full numbers.
    const params = [];
    if (scope === "attention") params.push("attention=true");
    if (el.revealPhone && el.revealPhone.checked) params.push("revealPhone=true");
    const query = params.length ? `?${params.join("&")}` : "";

    try {
      const [linesData, attemptsData] = await Promise.all([
        airtimeFetch(`/airtime/lines${query}`),
        airtimeFetch("/airtime/attempts?limit=25"),
      ]);

      lines = linesData.lines || [];
      attempts = attemptsData.attempts || [];
      summary = linesData.summary || null;
      loadedOnce = true;

      renderSummary(summary);
      renderTable();
      renderAttempts();
    } catch (err) {
      el.tbody.innerHTML =
        `<tr><td colspan="8" class="empty-row">${escapeHtml(
          err.message || "Failed to load airtime lines."
        )}</td></tr>`;
      el.summary.innerHTML = "";
      summary = null;
      renderFilterInfo(0);
      console.error(err);
    }
  }

  // ---------- Rendering ----------

  function renderSummary(summary) {
    if (!summary) {
      el.summary.innerHTML = "";
      return;
    }
    el.summary.innerHTML = `
      <div class="card"><div class="num">${summary.needsAttention ?? 0}</div><div class="label">Need attention</div></div>
      <div class="card"><div class="num">${summary.overdue ?? 0}</div><div class="label">Overdue reminder</div></div>
      <div class="card"><div class="num">${summary.due ?? 0}</div><div class="label">Due today</div></div>
      <div class="card"><div class="num">${summary.expiryReported ?? 0}</div><div class="label">SIM expiry reported</div></div>
      <div class="card"><div class="num">${summary.rechargePending ?? 0}</div><div class="label">Awaiting confirmation</div></div>
      <div class="card"><div class="num">${summary.totalLines ?? 0}</div><div class="label">Total lines</div></div>
    `;
  }

  // The cards above always describe EVERY registered line. This line spells
  // out how many of them the current filter actually shows, so the two are
  // never confused with each other.
  function renderFilterInfo(visibleCount) {
    if (!el.filterInfo) return;
    const total = summary ? (summary.totalLines ?? 0) : 0;
    if (!summary) {
      el.filterInfo.textContent = "";
      return;
    }
    if (visibleCount === total) {
      el.filterInfo.textContent = `Showing all ${total} line${total === 1 ? "" : "s"}.`;
    } else {
      el.filterInfo.textContent = `Showing ${visibleCount} of ${total} line${
        total === 1 ? "" : "s"
      }. The cards above always show the totals for all lines.`;
    }
  }

  function filteredLines() {
    const term = (el.search.value || "").trim().toLowerCase();
    const status = el.statusFilter.value;

    return lines.filter(line => {
      if (status && line.status !== status) return false;
      if (!term) return true;
      return (
        (line.label || "").toLowerCase().includes(term) ||
        (line.phoneMasked || "").toLowerCase().includes(term) ||
        (line.phoneDisplay || "").toLowerCase().includes(term) ||
        (line.phoneNumber || "").toLowerCase().includes(term)
      );
    });
  }

  function renderTable() {
    const rows = filteredLines();
    renderFilterInfo(rows.length);

    if (!rows.length) {
      el.tbody.innerHTML = `<tr><td colspan="8" class="empty-row">No airtime lines to show. Use "Add line" to register one.</td></tr>`;
      return;
    }

    el.tbody.innerHTML = rows
      .map(line => {
        const label = AIRTIME_STATUS_LABELS[line.status] || line.statusLabel || line.status;
        const days =
          line.daysUntilDue === null || line.daysUntilDue === undefined
            ? "—"
            : line.daysUntilDue;
        const expiryButton = line.expiryReported
          ? `<button class="btn-action" data-action="clear-expiry" data-id="${line.id}">Clear expiry</button>`
          : `<button class="btn-action" data-action="report-expiry" data-id="${line.id}">Report expiry</button>`;

        // Show the unmasked number only when the operator asked for it.
        const shownPhone =
          line.phoneDisplay || line.phoneMasked || "";
        const phoneTitle = line.phoneDisplay
          ? escapeHtml(line.phoneMasked || "")
          : escapeHtml(line.carrierHint || "");

        return `
          <tr>
            <td><span class="status-pill status-${escapeHtml(line.status)}">${escapeHtml(label)}</span></td>
            <td>${escapeHtml(line.label)}</td>
            <td title="${phoneTitle}">${escapeHtml(shownPhone)}</td>
            <td>${escapeHtml(line.currency || "KES")} ${escapeHtml(line.amount)}</td>
            <td>${escapeHtml(line.nextReminderDate || "—")}</td>
            <td>${escapeHtml(days)}</td>
            <td class="reason-cell">${escapeHtml(line.reason || "")}</td>
            <td class="action-cell">
              <button class="btn-action btn-primary" data-action="recharge" data-id="${line.id}">Recharge now</button>
              <button class="btn-action" data-action="edit" data-id="${line.id}">Edit</button>
              ${expiryButton}
              <button class="btn-delete" data-action="delete" data-id="${line.id}">Delete</button>
            </td>
          </tr>
        `;
      })
      .join("");
  }

  function renderAttempts() {
    if (!attempts.length) {
      el.attemptsTbody.innerHTML =
        `<tr><td colspan="7" class="empty-row">No recharge attempts yet.</td></tr>`;
      if (el.attemptsSummary) {
        el.attemptsSummary.textContent = "Recharge attempts (history) — none yet";
      }
      return;
    }

    const lineLabels = {};
    lines.forEach(l => {
      lineLabels[l.id] = l.label;
    });

    el.attemptsTbody.innerHTML = attempts
      .map(a => {
        const status = AIRTIME_ATTEMPT_LABELS[a.status] || a.status;
        const label = lineLabels[a.lineId] || `Line ${a.lineId}`;
        return `
          <tr>
            <td><span class="status-pill status-${escapeHtml(a.status)}">${escapeHtml(status)}</span></td>
            <td>${escapeHtml(label)}</td>
            <td>${escapeHtml(a.amount)}</td>
            <td><code>${escapeHtml(a.ussdString || "—")}</code></td>
            <td>${escapeHtml(a.transactionRef || "—")}</td>
            <td>${escapeHtml(a.initiatedAt || "—")}</td>
            <td>${escapeHtml(a.resolvedAt || "—")}</td>
          </tr>
        `;
      })
      .join("");

    if (el.attemptsSummary) {
      el.attemptsSummary.textContent = `Recharge attempts (history) — ${attempts.length} recent`;
    }
  }

  // ---------- Modal helpers ----------

  function openModal(node) {
    node.classList.remove("hidden");
  }

  function closeModal(node) {
    node.classList.add("hidden");
  }

  function openAddModal() {
    editingLineId = null;
    modal.title.textContent = "Add airtime line";
    modal.label.value = "";
    modal.phone.value = "";
    modal.amount.value = 10;
    modal.frequency.value = 7;
    modal.nextDate.value = "";
    modal.expiryReported.checked = false;
    modal.expiryNote.value = "";
    modal.expiryNoteWrap.classList.add("hidden");
    openModal(modal.root);
    modal.label.focus();
  }

  // The list endpoint masks phone numbers by default, so the edit form needs
  // the unmasked number. It is fetched on demand (revealPhone=true) instead
  // of shipping every number to the browser on every list request.
  async function openEditModal(line) {
    editingLineId = line.id;
    modal.title.textContent = "Edit airtime line";
    modal.label.value = line.label || "";
    modal.phone.value = line.phoneDisplay || "";
    modal.amount.value = line.amount || 10;
    modal.frequency.value = line.reminderFrequencyDays || 7;
    modal.nextDate.value = line.nextReminderDate || "";
    modal.expiryReported.checked = !!line.expiryReported;
    modal.expiryNote.value = line.expiryNote || "";
    modal.expiryNoteWrap.classList.toggle("hidden", !line.expiryReported);
    openModal(modal.root);
    modal.label.focus();

    if (!line.phoneDisplay) {
      try {
        const data = await airtimeFetch(
          `/airtime/lines/${line.id}?revealPhone=true`
        );
        if (editingLineId === line.id && data.line) {
          modal.phone.value = data.line.phoneDisplay || "";
        }
      } catch (err) {
        // Non-fatal: the operator can still type the number manually.
        console.error(err);
      }
    }
  }

  async function saveLine() {
    const payload = {
      label: modal.label.value.trim(),
      phoneNumber: modal.phone.value.trim(),
      amount: Number(modal.amount.value),
      reminderFrequencyDays: Number(modal.frequency.value),
    };
    if (modal.nextDate.value) {
      payload.nextReminderDate = modal.nextDate.value;
    }
    // The expiry flag is ALWAYS sent - including an explicit `false`. If it
    // were omitted when the box is unchecked, the backend would keep the old
    // expiry report and the line would stay stuck on "SIM expiry reported".
    payload.expiryReported = modal.expiryReported.checked;
    if (modal.expiryReported.checked) {
      payload.expiryNote = modal.expiryNote.value.trim() || null;
    } else {
      // Clear any note left over from a previous report.
      payload.expiryNote = null;
    }

    if (!payload.label) {
      alert("Please enter a label.");
      return;
    }
    if (!payload.phoneNumber) {
      alert("Please enter a phone number.");
      return;
    }

    try {
      if (editingLineId) {
        await airtimeFetch(`/airtime/lines/${editingLineId}`, {
          method: "PATCH",
          body: payload,
        });
      } else {
        await airtimeFetch("/airtime/lines", { method: "POST", body: payload });
      }
      closeModal(modal.root);
      await fetchData();
    } catch (err) {
      alert(err.message || "Failed to save the line.");
      console.error(err);
    }
  }

  // ---------- Recharge flow ----------
  //
  // The browser CANNOT perform the USSD transaction itself - USSD codes are
  // not callable from a web page. What we CAN do is hand the exact command
  // to the Android dialer via a `tel:` URI and then ASK the user whether it
  // worked. The attempt stays "pending_confirmation" until they answer, so
  // an abandoned dial never advances the schedule.

  async function startRecharge(line) {
    try {
      const data = await airtimeFetch(`/airtime/lines/${line.id}/recharge`, {
        method: "POST",
        body: { amount: line.amount },
      });

      activeAttemptId = data.attempt.id;
      rechargeModal.ussdText.textContent = data.ussd;
      rechargeModal.ussdPhone.textContent = data.phone;
      rechargeModal.ussdAmount.textContent = `${data.amount} KES`;
      rechargeModal.dialBtn.href = data.uri;
      openModal(rechargeModal.root);
    } catch (err) {
      alert(err.message || "Failed to start the recharge.");
      console.error(err);
    }
  }

  async function resolveAttempt(action, note) {
    if (!activeAttemptId) {
      closeModal(rechargeModal.root);
      return;
    }
    try {
      const payload = await airtimeFetch(
        `/airtime/attempts/${activeAttemptId}/${action}`,
        {
          method: "POST",
          body: note ? { note } : {},
        }
      );
      closeModal(rechargeModal.root);

      // The API returns the updated line so the new next-reminder date is
      // known immediately. Patch the local copy first so the row never shows
      // a stale date, then refresh everything (summary, attempts, filters).
      // The returned line is masked, so the already-loaded phone fields are
      // carried over — otherwise a revealed number would revert to masked.
      if (payload && payload.line) {
        const index = lines.findIndex(l => l.id === payload.line.id);
        if (index !== -1) {
          const previous = lines[index];
          lines[index] = {
            ...payload.line,
            phoneDisplay: previous.phoneDisplay,
            phoneNumber: previous.phoneNumber,
          };
          renderTable();
        }
      }
      await fetchData();
    } catch (err) {
      alert(err.message || "Failed to update the recharge attempt.");
      console.error(err);
    }
  }

  async function copyUssd() {
    const text = rechargeModal.ussdText.textContent;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        // Fallback for plain-HTTP dev servers, where the Clipboard API is
        // unavailable.
        const helper = document.createElement("textarea");
        helper.value = text;
        helper.setAttribute("readonly", "");
        helper.style.position = "absolute";
        helper.style.left = "-9999px";
        document.body.appendChild(helper);
        helper.select();
        document.execCommand("copy");
        document.body.removeChild(helper);
      }
      rechargeModal.copyBtn.textContent = "✅ Copied";
      setTimeout(() => {
        rechargeModal.copyBtn.textContent = "📋 Copy";
      }, 2000);
    } catch (err) {
      alert("Could not copy automatically. The command is: " + text);
    }
  }

  // ---------- Other actions ----------

  async function reportExpiry(line, reported) {
    const note = reported
      ? prompt(
          "Who reported that this SIM expired?\n\nThis is recorded so the dashboard never claims Safaricom verified the expiry."
        )
      : null;
    if (reported && note === null) {
      return; // user cancelled
    }

    try {
      await airtimeFetch(`/airtime/lines/${line.id}/expiry`, {
        method: "POST",
        body: { reported, source: reported ? "Dashboard user" : null, note: note || null },
      });
      await fetchData();
    } catch (err) {
      alert(err.message || "Failed to update the expiry status.");
      console.error(err);
    }
  }

  async function deleteLine(line) {
    if (!confirm(`Remove "${line.label}" from the airtime list?`)) return;
    try {
      await airtimeFetch(`/airtime/lines/${line.id}`, { method: "DELETE" });
      await fetchData();
    } catch (err) {
      alert(err.message || "Failed to delete the line.");
      console.error(err);
    }
  }

  // ---------- Connection settings ----------

  function saveSettings() {
    const base = (el.apiBaseInput.value || "").trim().replace(/\/$/, "");
    const key = (el.apiKeyInput.value || "").trim();

    try {
      if (base) {
        // Store under the shared keys too, so the main dashboard and the
        // admin panel pick up the same address without extra setup.
        window.localStorage.setItem("protrack.airtimeApiBase", base);
        window.localStorage.setItem("protrack.apiBase", base);
        window.localStorage.setItem("protrack.adminApiBase", base);
      } else {
        window.localStorage.removeItem("protrack.airtimeApiBase");
        window.localStorage.removeItem("protrack.apiBase");
        window.localStorage.removeItem("protrack.adminApiBase");
      }
      if (key) {
        window.localStorage.setItem("protrack.airtimeApiKey", key);
      } else {
        window.localStorage.removeItem("protrack.airtimeApiKey");
      }
    } catch (err) {
      el.settingsStatus.textContent = "Could not save (storage blocked).";
      return;
    }

    AIRTIME_API_BASE = base || resolveAirtimeApiBase();
    AIRTIME_API_KEY = key || resolveAirtimeApiKey();

    el.settingsStatus.textContent = "Saved. Reloading…";
    fetchData();
  }

  // ---------- Event wiring ----------

  el.addBtn.addEventListener("click", openAddModal);
  modal.cancel.addEventListener("click", () => closeModal(modal.root));
  modal.save.addEventListener("click", saveLine);

  modal.expiryReported.addEventListener("change", () => {
    modal.expiryNoteWrap.classList.toggle("hidden", !modal.expiryReported.checked);
  });

  el.search.addEventListener("input", renderTable);
  el.statusFilter.addEventListener("change", renderTable);
  el.scopeFilter.addEventListener("change", fetchData);
  // Toggling "Show full numbers" re-fetches the list with revealPhone=true.
  if (el.revealPhone) {
    el.revealPhone.addEventListener("change", fetchData);
  }

  // Prefill the connection settings with the values actually in use.
  el.apiBaseInput.value = AIRTIME_API_BASE;
  el.apiKeyInput.value = AIRTIME_API_KEY;
  el.saveSettingsBtn.addEventListener("click", saveSettings);

  // Delegated click handling for the table's action buttons.
  el.tbody.addEventListener("click", event => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;

    const line = lines.find(l => l.id === Number(button.dataset.id));
    if (!line) return;

    switch (button.dataset.action) {
      case "recharge":
        startRecharge(line);
        break;
      case "edit":
        openEditModal(line);
        break;
      case "report-expiry":
        reportExpiry(line, true);
        break;
      case "clear-expiry":
        reportExpiry(line, false);
        break;
      case "delete":
        deleteLine(line);
        break;
      default:
        break;
    }
  });

  rechargeModal.dialBtn.addEventListener("click", () => {
    // The dialer is opening - remind the user that the recharge is NOT
    // confirmed until they say so.
    rechargeModal.confirmOk.focus();
  });
  rechargeModal.copyBtn.addEventListener("click", copyUssd);
  rechargeModal.cancel.addEventListener("click", () => {
    // Cancelling keeps the reminder schedule intact - the line stays due.
    activeAttemptId = null;
    closeModal(rechargeModal.root);
    fetchData();
  });
  rechargeModal.confirmOk.addEventListener("click", () =>
    resolveAttempt("confirm", "Confirmed from the dashboard by the operator")
  );
  rechargeModal.confirmFail.addEventListener("click", () =>
    resolveAttempt("fail", "Marked as failed from the dashboard")
  );

  // Expose a few helpers for debugging from the browser console.
  window.airtimeRecharge = startRecharge;

  return { fetchData, isLoaded: () => loadedOnce };
})();
