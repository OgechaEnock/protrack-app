// Point this at wherever the Flask-RESTful backend is running. The
// frontend (this Node/Express app) and backend are now two separate
// processes/origins, so this needs to be a full URL, not a relative path.
const API_BASE = "http://localhost:5000/api";

const STATUS_LABELS = {
  overdue: "Overdue",
  due_soon: "Due soon",
  ok: "OK",
  no_data: "No data",
};

function debounce(fn, delay) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), delay);
  };
}

// ---------- Tabs ----------

const tabButtons = document.querySelectorAll(".tab-btn");
const TAB_PANELS = {
  cardHistory: document.getElementById("cardHistoryTab"),
  rechargeSchedule: document.getElementById("rechargeScheduleTab"),
};

// Populated further down once createProgramSection() instances exist.
// Referencing this inside the click handler is safe: the handler only runs
// on a user click, which always happens after the whole script has run.
const PROGRAM_SECTIONS = {};

tabButtons.forEach(btn => {
  btn.addEventListener("click", () => {
    tabButtons.forEach(b => b.classList.remove("active"));
    btn.classList.add("active");

    const target = btn.dataset.tab;
    Object.entries(TAB_PANELS).forEach(([name, panel]) => {
      panel.classList.toggle("hidden", name !== target);
    });

    if (target === "cardHistory" && allCardHistory.length === 0) {
      fetchCardHistory();
    }
    // Recharge Schedule holds three sub-tabs (Protrack/Mettax/Pilot);
    // load whichever one is currently active the first time this tab opens.
    if (target === "rechargeSchedule" && PROGRAM_SECTIONS[activeSubtab] && !PROGRAM_SECTIONS[activeSubtab].isLoaded()) {
      PROGRAM_SECTIONS[activeSubtab].fetchData();
    }
  });
});

// ---------- Card History ----------

let allCardHistory = [];
const cardHistoryTbody = document.getElementById("cardHistoryTbody");
const chSummaryCards = document.getElementById("chSummaryCards");
const chImeiFilter = document.getElementById("chImeiFilter");
const chTypeFilter = document.getElementById("chTypeFilter");
const chUploadInput = document.getElementById("chUploadInput");

async function fetchCardHistory() {
  cardHistoryTbody.innerHTML = `<tr><td colspan="9" class="loading-row">Loading card history…</td></tr>`;
  try {
    const params = new URLSearchParams();
    if (chImeiFilter.value.trim()) params.set("imei", chImeiFilter.value.trim());
    if (chTypeFilter.value) params.set("type", chTypeFilter.value);

    const res = await fetch(`${API_BASE}/card-history?${params.toString()}`);
    const data = await res.json();

    if (!res.ok) {
      cardHistoryTbody.innerHTML = `<tr><td colspan="9" class="empty-row">${data.error || "Failed to load card history."}</td></tr>`;
      return;
    }

    allCardHistory = data.transactions || [];
    renderCardHistorySummary(data.summary);
    renderCardHistoryTable(allCardHistory);
  } catch (err) {
    cardHistoryTbody.innerHTML = `<tr><td colspan="9" class="empty-row">Network error loading card history.</td></tr>`;
    console.error(err);
  }
}

function renderCardHistorySummary(summary) {
  if (!summary) { chSummaryCards.innerHTML = ""; return; }
  chSummaryCards.innerHTML = `
    <div class="card"><div class="num">${summary.totalTransactions}</div><div class="label">Transactions</div></div>
    <div class="card"><div class="num">${summary.uniqueDevices}</div><div class="label">Devices Billed</div></div>
    <div class="card"><div class="num">${summary.newDevicesAdded}</div><div class="label">New Devices Added</div></div>
    <div class="card"><div class="num">${summary.totalExpenditure.toFixed(2)}</div><div class="label">Total Expenditure</div></div>
  `;
}

function renderCardHistoryTable(rows) {
  if (!rows.length) {
    cardHistoryTbody.innerHTML = `<tr><td colspan="9" class="empty-row">No card history found. Upload a ProTrack365 export to get started.</td></tr>`;
    return;
  }

  cardHistoryTbody.innerHTML = rows.map(r => `
    <tr>
      <td>${r.seq ?? "—"}</td>
      <td>${r.datetime ?? "—"}</td>
      <td>${r.type ?? "—"}</td>
      <td>${r.operation ?? "—"}</td>
      <td class="amount-in">${r.income != null ? r.income.toFixed(2) : "—"}</td>
      <td class="amount-out">${r.expenditure != null ? r.expenditure.toFixed(2) : "—"}</td>
      <td>${r.balance != null ? r.balance.toFixed(2) : "—"}</td>
      <td>${r.imei ?? "—"}</td>
      <td>${r.remark ?? "—"}</td>
    </tr>
  `).join("");
}

chImeiFilter.addEventListener("input", debounce(fetchCardHistory, 300));
chTypeFilter.addEventListener("change", fetchCardHistory);

chUploadInput.addEventListener("change", async () => {
  const file = chUploadInput.files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch(`${API_BASE}/card-history/upload`, { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) {
      alert(data.error || "Upload failed.");
      return;
    }
    alert(`Uploaded ${data.filename} — parsed ${data.rowsParsed} rows.`);
    fetchCardHistory();
  } catch (err) {
    alert("Network error during upload.");
    console.error(err);
  } finally {
    chUploadInput.value = "";
  }
});

// ---------- Recharge Schedule sub-tabs (Protrack / Mettax / Pilot) ----------
// The upload buttons live once at the top of the Recharge Schedule tab and
// apply to whichever sub-tab(s) have data loaded.

const registryUploadInput = document.getElementById("registryUploadInput");
const chatUploadInput = document.getElementById("chatUploadInput");

const subtabButtons = document.querySelectorAll(".subtab-btn");
const SUBTAB_PANELS = {
  protrack: document.getElementById("protrackSubtab"),
  mettax: document.getElementById("mettaxSubtab"),
  pilot: document.getElementById("pilotSubtab"),
};
let activeSubtab = "protrack";

subtabButtons.forEach(btn => {
  btn.addEventListener("click", () => {
    subtabButtons.forEach(b => b.classList.remove("active"));
    btn.classList.add("active");

    activeSubtab = btn.dataset.subtab;
    Object.entries(SUBTAB_PANELS).forEach(([name, panel]) => {
      panel.classList.toggle("hidden", name !== activeSubtab);
    });

    if (PROGRAM_SECTIONS[activeSubtab] && !PROGRAM_SECTIONS[activeSubtab].isLoaded()) {
      PROGRAM_SECTIONS[activeSubtab].fetchData();
    }
  });
});

async function handleUpload(inputEl, endpoint, successMessageFn, onDone) {
  const file = inputEl.files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch(`${API_BASE}${endpoint}`, { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) {
      alert(data.error || "Upload failed.");
      return;
    }
    alert(successMessageFn(data));
    if (onDone) onDone();
  } catch (err) {
    alert("Network error during upload.");
    console.error(err);
  } finally {
    inputEl.value = "";
  }
}

function refreshAllScheduleViews() {
  Object.values(PROGRAM_SECTIONS).forEach(section => section.fetchData());
}

registryUploadInput.addEventListener("change", () =>
  handleUpload(registryUploadInput, "/device-registry/upload",
    d => `Uploaded ${d.filename} — parsed ${d.rowsParsed} devices.`,
    refreshAllScheduleViews)
);

chatUploadInput.addEventListener("change", () =>
  handleUpload(chatUploadInput, "/recharge-log/upload",
    d => `Uploaded ${d.filename} — parsed ${d.eventsParsed} recharge events (${d.unparsedLines} lines skipped).`,
    refreshAllScheduleViews)
);

// ---------- Protrack / Mettax / Pilot sections ----------
// All three are the SAME reused backend logic (recharge_schedule.py),
// just filtered server-side to a different category. This factory avoids
// duplicating the fetch/render/filter logic three times. Each section also
// pulls the raw recharge messages for its category and shows a small
// ✅/❌ icon per message indicating whether its plate matched a device in
// the registry (recharge_schedule.annotate_events on the backend).

function createProgramSection(category, ids) {
  let rows = [];
  let loadedOnce = false;

  const el = {
    tbody: document.getElementById(ids.tbody),
    summary: document.getElementById(ids.summary),
    search: document.getElementById(ids.search),
    statusFilter: document.getElementById(ids.statusFilter),
    messagesTbody: document.getElementById(ids.messagesTbody),
    messagesSummary: document.getElementById(ids.messagesSummary),
  };

  async function fetchData() {
    loadedOnce = true;
    el.tbody.innerHTML = `<tr><td colspan="8" class="loading-row">Loading ${category} devices…</td></tr>`;
    el.messagesTbody.innerHTML = `<tr><td colspan="7" class="loading-row">Loading recharge messages…</td></tr>`;

    const schedulePromise = fetch(`${API_BASE}/recharge-schedule?category=${category}`).then(r => r.json().then(d => [r, d]));
    const logPromise = fetch(`${API_BASE}/recharge-log?category=${category}`).then(r => r.json().then(d => [r, d]));

    try {
      const [[schedRes, schedData], [logRes, logData]] = await Promise.all([schedulePromise, logPromise]);

      if (!schedRes.ok) {
        el.tbody.innerHTML = `<tr><td colspan="8" class="empty-row">${schedData.error || "Failed to load."}</td></tr>`;
        el.summary.innerHTML = "";
      } else {
        rows = schedData.schedule || [];
        renderSummary(schedData.summary);
        renderTable(rows);
      }

      if (!logRes.ok) {
        el.messagesTbody.innerHTML = `<tr><td colspan="7" class="empty-row">${logData.error || "Failed to load messages."}</td></tr>`;
      } else {
        renderMessages(logData);
      }
    } catch (err) {
      el.tbody.innerHTML = `<tr><td colspan="8" class="empty-row">Network error loading ${category} devices.</td></tr>`;
      el.messagesTbody.innerHTML = `<tr><td colspan="7" class="empty-row">Network error loading messages.</td></tr>`;
      console.error(err);
    }
  }

  function renderSummary(summary) {
    if (!summary) { el.summary.innerHTML = ""; return; }
    el.summary.innerHTML = `
      <div class="card"><div class="num">${summary.totalDevices}</div><div class="label">Devices</div></div>
      <div class="card"><div class="num">${summary.overdue}</div><div class="label">Overdue</div></div>
      <div class="card"><div class="num">${summary.dueSoon}</div><div class="label">Due Soon</div></div>
      <div class="card"><div class="num">${summary.ok}</div><div class="label">OK</div></div>
    `;
  }

  function renderTable(list) {
    if (!list.length) {
      el.tbody.innerHTML = `<tr><td colspan="8" class="empty-row">No devices matched to the ${category} program yet.</td></tr>`;
      return;
    }
    el.tbody.innerHTML = list.map(r => `
      <tr>
        <td><span class="status-pill status-${r.status}">${STATUS_LABELS[r.status] || r.status}</span></td>
        <td>${r.plate}</td>
        <td>${r.accountName || "—"}</td>
        <td>${r.sim || "—"}</td>
        <td>${r.lastRechargeDate || "—"}</td>
        <td>${r.lastRechargeAmount != null ? r.lastRechargeAmount : "—"}</td>
        <td>${r.nextDueDate || "—"}</td>
        <td>${r.daysUntilDue != null ? r.daysUntilDue : "—"}</td>
      </tr>
    `).join("");
  }

  function renderMessages(logData) {
    const events = logData.events || [];
    el.messagesSummary.textContent =
      `Recharge messages (plate match status) — ${logData.matchedCount ?? 0} matched, ${logData.unmatchedCount ?? 0} unmatched`;

    if (!events.length) {
      el.messagesTbody.innerHTML = `<tr><td colspan="7" class="empty-row">No recharge messages found for ${category}.</td></tr>`;
      return;
    }

    el.messagesTbody.innerHTML = events.map(e => `
      <tr>
        <td><span class="match-icon ${e.matched ? "matched" : "unmatched"}" title="${e.matched ? "Matched to registry" : "No matching plate in registry"}">${e.matched ? "✓" : "✕"}</span></td>
        <td>${e.date || "—"}</td>
        <td>${e.time || "—"}</td>
        <td>${e.device || "—"}</td>
        <td>${e.matchedPlate || "—"}</td>
        <td>${e.amount != null ? e.amount : "—"}</td>
        <td>${e.remark || "—"}</td>
      </tr>
    `).join("");
  }

  function applyFilters() {
    const q = el.search.value.trim().toLowerCase();
    const status = el.statusFilter.value;
    let filtered = rows;
    if (q) {
      filtered = filtered.filter(r =>
        (r.plate || "").toLowerCase().includes(q) ||
        (r.accountName || "").toLowerCase().includes(q)
      );
    }
    if (status) filtered = filtered.filter(r => r.status === status);
    renderTable(filtered);
  }

  el.search.addEventListener("input", debounce(applyFilters, 200));
  el.statusFilter.addEventListener("change", applyFilters);

  return { fetchData, isLoaded: () => loadedOnce };
}

PROGRAM_SECTIONS.protrack = createProgramSection("protrack", {
  tbody: "protrackTbody", summary: "protrackSummaryCards",
  search: "protrackSearchInput", statusFilter: "protrackStatusFilter",
  messagesTbody: "protrackMessagesTbody", messagesSummary: "protrackMessagesSummary",
});

PROGRAM_SECTIONS.mettax = createProgramSection("mettax", {
  tbody: "mettaxTbody", summary: "mettaxSummaryCards",
  search: "mettaxSearchInput", statusFilter: "mettaxStatusFilter",
  messagesTbody: "mettaxMessagesTbody", messagesSummary: "mettaxMessagesSummary",
});

PROGRAM_SECTIONS.pilot = createProgramSection("pilot", {
  tbody: "pilotTbody", summary: "pilotSummaryCards",
  search: "pilotSearchInput", statusFilter: "pilotStatusFilter",
  messagesTbody: "pilotMessagesTbody", messagesSummary: "pilotMessagesSummary",
});

document.addEventListener("DOMContentLoaded", () => {
  fetchCardHistory();
});
