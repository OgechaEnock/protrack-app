const API_BASE = "/api";
const AUTO_REFRESH_MS = 30000; // re-poll live positions every 30s

let allDevices = [];
let autoRefreshTimer = null;

const tbody = document.getElementById("devicesTbody");
const searchInput = document.getElementById("searchInput");
const refreshBtn = document.getElementById("refreshBtn");
const lastUpdatedEl = document.getElementById("lastUpdated");
const banner = document.getElementById("statusBanner");
const summaryCards = document.getElementById("summaryCards");
const modal = document.getElementById("deviceModal");
const modalBody = document.getElementById("modalBody");
const closeModalBtn = document.getElementById("closeModal");

function showBanner(message) {
  banner.textContent = message;
  banner.classList.remove("hidden");
}

function hideBanner() {
  banner.classList.add("hidden");
}

function statusClass(statusCode) {
  switch (statusCode) {
    case 1: return "status-never-online";
    case 2: return "status-online";
    case 3: return "status-expired";
    case 4: return "status-offline";
    case 5: return "status-blocked";
    default: return "status-unknown";
  }
}

function formatTimestamp(unixSeconds) {
  if (!unixSeconds) return "—";
  const d = new Date(unixSeconds * 1000);
  return d.toLocaleString();
}

async function fetchDevices() {
  refreshBtn.disabled = true;
  refreshBtn.textContent = "Refreshing…";
  try {
    const res = await fetch(`${API_BASE}/devices`);
    const data = await res.json();

    if (!res.ok) {
      showBanner(data.error || "Failed to load devices.");
      tbody.innerHTML = `<tr><td colspan="8" class="empty-row">Could not load devices.</td></tr>`;
      return;
    }

    hideBanner();
    allDevices = data.devices || [];
    renderSummary(allDevices);
    renderTable(allDevices);
    lastUpdatedEl.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    showBanner("Network error contacting the server.");
    console.error(err);
  } finally {
    refreshBtn.disabled = false;
    refreshBtn.textContent = "Refresh";
  }
}

function renderSummary(devices) {
  const total = devices.length;
  const online = devices.filter(d => d.statusCode === 2).length;
  const offline = devices.filter(d => d.statusCode === 4).length;
  const expired = devices.filter(d => d.statusCode === 3 || d.statusCode === 5).length;

  summaryCards.innerHTML = `
    <div class="card"><div class="num">${total}</div><div class="label">Total Devices</div></div>
    <div class="card"><div class="num">${online}</div><div class="label">Online</div></div>
    <div class="card"><div class="num">${offline}</div><div class="label">Offline</div></div>
    <div class="card"><div class="num">${expired}</div><div class="label">Expired / Blocked</div></div>
  `;
}

function renderTable(devices) {
  if (!devices.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty-row">No devices found on this account.</td></tr>`;
    return;
  }

  tbody.innerHTML = devices.map(d => `
    <tr data-imei="${d.imei}">
      <td><span class="status-pill ${statusClass(d.statusCode)}">${d.status}</span></td>
      <td>${d.name || "—"}</td>
      <td>${d.imei}</td>
      <td>${d.model || "—"}</td>
      <td>${d.plate || "—"}</td>
      <td>${d.speed ?? "—"}</td>
      <td>${d.battery != null ? d.battery + "%" : "—"}</td>
      <td>${formatTimestamp(d.gpstime)}</td>
    </tr>
  `).join("");

  tbody.querySelectorAll("tr[data-imei]").forEach(row => {
    row.addEventListener("click", () => openModal(row.dataset.imei));
  });
}

function applySearch() {
  const q = searchInput.value.trim().toLowerCase();
  if (!q) {
    renderTable(allDevices);
    return;
  }
  const filtered = allDevices.filter(d =>
    (d.name || "").toLowerCase().includes(q) ||
    (d.imei || "").toLowerCase().includes(q) ||
    (d.plate || "").toLowerCase().includes(q)
  );
  renderTable(filtered);
}

function openModal(imei) {
  const d = allDevices.find(x => x.imei === imei);
  if (!d) return;

  modalBody.innerHTML = `
    <h2>${d.name || d.imei}</h2>
    <div class="detail-row"><span>Status</span><span>${d.status}</span></div>
    <div class="detail-row"><span>IMEI</span><span>${d.imei}</span></div>
    <div class="detail-row"><span>Model</span><span>${d.model || "—"}</span></div>
    <div class="detail-row"><span>Plate</span><span>${d.plate || "—"}</span></div>
    <div class="detail-row"><span>Speed</span><span>${d.speed ?? "—"} km/h</span></div>
    <div class="detail-row"><span>Battery</span><span>${d.battery != null ? d.battery + "%" : "—"}</span></div>
    <div class="detail-row"><span>ACC Status</span><span>${d.accStatus === 1 ? "On" : d.accStatus === 0 ? "Off" : "—"}</span></div>
    <div class="detail-row"><span>Latitude</span><span>${d.latitude ?? "—"}</span></div>
    <div class="detail-row"><span>Longitude</span><span>${d.longitude ?? "—"}</span></div>
    <div class="detail-row"><span>Last GPS Update</span><span>${formatTimestamp(d.gpstime)}</span></div>
    <div class="detail-row"><span>First Online</span><span>${formatTimestamp(d.onlinetime)}</span></div>
    <div class="detail-row"><span>Platform Expires</span><span>${formatTimestamp(d.platformduetime)}</span></div>
  `;
  modal.classList.remove("hidden");
}

closeModalBtn.addEventListener("click", () => modal.classList.add("hidden"));
modal.addEventListener("click", (e) => {
  if (e.target === modal) modal.classList.add("hidden");
});

refreshBtn.addEventListener("click", fetchDevices);
searchInput.addEventListener("input", applySearch);

// ---------- Tabs ----------

const tabButtons = document.querySelectorAll(".tab-btn");
const devicesTab = document.getElementById("devicesTab");
const cardHistoryTab = document.getElementById("cardHistoryTab");
const rechargeScheduleTab = document.getElementById("rechargeScheduleTab");

const TAB_PANELS = {
  devices: devicesTab,
  cardHistory: cardHistoryTab,
  rechargeSchedule: rechargeScheduleTab,
};

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
    if (target === "rechargeSchedule" && !scheduleLoadedOnce) {
      fetchRechargeSchedule();
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

function debounce(fn, delay) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), delay);
  };
}

document.addEventListener("DOMContentLoaded", () => {
  fetchDevices();
  autoRefreshTimer = setInterval(fetchDevices, AUTO_REFRESH_MS);
});

// ---------- Recharge Schedule ----------

let scheduleLoadedOnce = false;
let allScheduleRows = [];

const scheduleTbody = document.getElementById("scheduleTbody");
const scheduleSummaryCards = document.getElementById("scheduleSummaryCards");
const scheduleSearchInput = document.getElementById("scheduleSearchInput");
const scheduleStatusFilter = document.getElementById("scheduleStatusFilter");
const registryUploadInput = document.getElementById("registryUploadInput");
const chatUploadInput = document.getElementById("chatUploadInput");
const unmatchedTbody = document.getElementById("unmatchedTbody");
const unmatchedSummary = document.getElementById("unmatchedSummary");

const STATUS_LABELS = {
  overdue: "Overdue",
  due_soon: "Due soon",
  ok: "OK",
  no_data: "No data",
};

async function fetchRechargeSchedule() {
  scheduleLoadedOnce = true;
  scheduleTbody.innerHTML = `<tr><td colspan="8" class="loading-row">Loading recharge schedule…</td></tr>`;
  try {
    const res = await fetch(`${API_BASE}/recharge-schedule`);
    const data = await res.json();

    if (!res.ok) {
      scheduleTbody.innerHTML = `<tr><td colspan="8" class="empty-row">${data.error || "Failed to load recharge schedule."}</td></tr>`;
      scheduleSummaryCards.innerHTML = "";
      return;
    }

    allScheduleRows = data.schedule || [];
    renderScheduleSummary(data.summary);
    renderScheduleTable(allScheduleRows);
    renderUnmatchedEvents(data.unmatchedEvents || []);
  } catch (err) {
    scheduleTbody.innerHTML = `<tr><td colspan="8" class="empty-row">Network error loading recharge schedule.</td></tr>`;
    console.error(err);
  }
}

function renderScheduleSummary(summary) {
  if (!summary) { scheduleSummaryCards.innerHTML = ""; return; }
  scheduleSummaryCards.innerHTML = `
    <div class="card"><div class="num">${summary.totalDevices}</div><div class="label">Devices</div></div>
    <div class="card"><div class="num">${summary.overdue}</div><div class="label">Overdue</div></div>
    <div class="card"><div class="num">${summary.dueSoon}</div><div class="label">Due Soon</div></div>
    <div class="card"><div class="num">${summary.ok}</div><div class="label">OK</div></div>
    <div class="card"><div class="num">${summary.noData}</div><div class="label">No Recharge Data</div></div>
  `;
}

function renderScheduleTable(rows) {
  if (!rows.length) {
    scheduleTbody.innerHTML = `<tr><td colspan="8" class="empty-row">No devices in the registry yet. Upload your devices spreadsheet above.</td></tr>`;
    return;
  }

  scheduleTbody.innerHTML = rows.map(r => `
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

function renderUnmatchedEvents(events) {
  unmatchedSummary.textContent = `Unmatched recharge messages (${events.length})`;
  if (!events.length) {
    unmatchedTbody.innerHTML = `<tr><td colspan="6" class="empty-row">Everything matched a device in the registry.</td></tr>`;
    return;
  }
  unmatchedTbody.innerHTML = events.map(e => `
    <tr>
      <td>${e.date || "—"}</td>
      <td>${e.time || "—"}</td>
      <td>${e.sender || "—"}</td>
      <td>${e.device || "—"}</td>
      <td>${e.amount != null ? e.amount : "—"}</td>
      <td>${e.remark || "—"}</td>
    </tr>
  `).join("");
}

function applyScheduleFilters() {
  const q = scheduleSearchInput.value.trim().toLowerCase();
  const statusFilter = scheduleStatusFilter.value;

  let filtered = allScheduleRows;
  if (q) {
    filtered = filtered.filter(r =>
      (r.plate || "").toLowerCase().includes(q) ||
      (r.accountName || "").toLowerCase().includes(q)
    );
  }
  if (statusFilter) {
    filtered = filtered.filter(r => r.status === statusFilter);
  }
  renderScheduleTable(filtered);
}

scheduleSearchInput.addEventListener("input", debounce(applyScheduleFilters, 200));
scheduleStatusFilter.addEventListener("change", applyScheduleFilters);

async function handleUpload(inputEl, endpoint, successMessageFn) {
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
    fetchRechargeSchedule();
  } catch (err) {
    alert("Network error during upload.");
    console.error(err);
  } finally {
    inputEl.value = "";
  }
}

registryUploadInput.addEventListener("change", () =>
  handleUpload(registryUploadInput, "/device-registry/upload",
    d => `Uploaded ${d.filename} — parsed ${d.rowsParsed} devices.`)
);

chatUploadInput.addEventListener("change", () =>
  handleUpload(chatUploadInput, "/recharge-log/upload",
    d => `Uploaded ${d.filename} — parsed ${d.eventsParsed} recharge events (${d.unparsedLines} lines skipped).`)
);