// ProTrack365 Admin Dashboard - Frontend Logic

const API_BASE = "http://localhost:5000/api";
const ADMIN_API_KEY = "admin123"; // Must match Backend/admin_auth.py default or env var

let isAuthenticated = false;
let currentConfig = {};

// DOM Elements
const loginScreen = document.getElementById("loginScreen");
const adminDashboard = document.getElementById("adminDashboard");
const apiKeyInput = document.getElementById("apiKeyInput");
const loginBtn = document.getElementById("loginBtn");
const loginError = document.getElementById("loginError");
const logoutBtn = document.getElementById("logoutBtn");
const statusBanner = document.getElementById("statusBanner");

// Tab elements
const tabButtons = document.querySelectorAll(".tab-btn");
const TAB_PANELS = {
  overview: document.getElementById("overviewTab"),
  files: document.getElementById("filesTab"),
  config: document.getElementById("configTab"),
  logs: document.getElementById("logsTab"),
};

// ==================== Authentication ====================

function showBanner(message, type = "info") {
  statusBanner.textContent = message;
  statusBanner.className = `banner ${type}`;
  statusBanner.classList.remove("hidden");
  setTimeout(hideBanner, 5000);
}

function hideBanner() {
  statusBanner.classList.add("hidden");
}

async function login(apiKey) {
  try {
    const res = await fetch(`${API_BASE}/admin/health`, {
      headers: {
        "X-Admin-API-Key": apiKey
      }
    });
    
    if (!res.ok) {
      const data = await res.json();
      throw new Error(data.error || "Invalid API key");
    }
    
    isAuthenticated = true;
    sessionStorage.setItem("adminAuthenticated", "true");
    sessionStorage.setItem("adminApiKey", apiKey);
    
    showDashboard();
    await loadOverview();
  } catch (err) {
    loginError.textContent = err.message;
    loginError.classList.remove("hidden");
  }
}

function logout() {
  isAuthenticated = false;
  sessionStorage.removeItem("adminAuthenticated");
  sessionStorage.removeItem("adminApiKey");
  loginScreen.classList.remove("hidden");
  adminDashboard.classList.add("hidden");
}

function showDashboard() {
  loginScreen.classList.add("hidden");
  adminDashboard.classList.remove("hidden");
}

// ==================== API Helper ====================

async function adminFetch(endpoint, options = {}) {
  const apiKey = sessionStorage.getItem("adminApiKey") || ADMIN_API_KEY;
  
  const res = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Admin-API-Key": apiKey,
      ...options.headers
    }
  });
  
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  
  return res.json();
}

// ==================== Overview Tab ====================

async function loadOverview() {
  await Promise.all([
    loadHealth(),
    loadStats()
  ]);
}

async function loadHealth() {
  try {
    const health = await adminFetch("/admin/health");
    
    const healthCards = document.getElementById("healthCards");
    const healthDetails = document.getElementById("healthDetails");
    
    healthCards.innerHTML = `
      <div class="card">
        <div class="num">${health.files.cardHistory}</div>
        <div class="label">Card History Files</div>
      </div>
      <div class="card">
        <div class="num">${health.files.deviceRegistry}</div>
        <div class="label">Registry Files</div>
      </div>
      <div class="card">
        <div class="num">${health.files.chatLogs}</div>
        <div class="label">Chat Logs</div>
      </div>
      <div class="card">
        <div class="num">${health.pilotFuelDevices || 0}</div>
        <div class="label">Pilot Fuel Devices</div>
      </div>
    `;
    
    healthDetails.innerHTML = `
      <div class="health-item">
        <span class="health-label">Card History Files</span>
        <span class="health-value">${health.files.cardHistory}</span>
      </div>
      <div class="health-item">
        <span class="health-label">Device Registry Files</span>
        <span class="health-value">${health.files.deviceRegistry}</span>
      </div>
      <div class="health-item">
        <span class="health-label">Chat Log Files</span>
        <span class="health-value">${health.files.chatLogs}</span>
      </div>
      <div class="health-item">
        <span class="health-label">Pilot Fuel Devices</span>
        <span class="health-value">${health.pilotFuelDevices || 0}</span>
      </div>
      <div class="health-item">
        <span class="health-label">Last Check</span>
        <span class="health-value">${new Date(health.timestamp).toLocaleString()}</span>
      </div>
    `;
  } catch (err) {
    showBanner(`Failed to load health: ${err.message}`, "error");
  }
}

async function loadStats() {
  try {
    const stats = await adminFetch("/admin/stats");
    
    const statsDetails = document.getElementById("statsDetails");
    
    const schedule = stats.rechargeSchedule || {};
    const pf = stats.pilotFuel || {};
    
    statsDetails.innerHTML = `
      <div class="stat-item">
        <div class="stat-label">Card History Transactions</div>
        <div class="stat-value">${stats.files.cardHistory}</div>
      </div>
      <div class="stat-item">
        <div class="stat-label">Registered Devices</div>
        <div class="stat-value">${stats.files.deviceRegistry}</div>
      </div>
      <div class="stat-item">
        <div class="stat-label">Recharge Events</div>
        <div class="stat-value">${stats.files.chatLogs}</div>
      </div>
      <div class="stat-item">
        <div class="stat-label">Recharge Schedule Devices</div>
        <div class="stat-value">${schedule.totalDevices || 0}</div>
      </div>
      <div class="stat-item">
        <div class="stat-label">Overdue Recharges</div>
        <div class="stat-value" style="color: var(--danger)">${schedule.overdue || 0}</div>
      </div>
      <div class="stat-item">
        <div class="stat-label">Due Soon</div>
        <div class="stat-value" style="color: var(--warning)">${schedule.dueSoon || 0}</div>
      </div>
      <div class="stat-item">
        <div class="stat-label">Pilot Fuel Devices</div>
        <div class="stat-value">${pf.totalDevices || 0}</div>
      </div>
      <div class="stat-item">
        <div class="stat-label">Pilot Fuel Overdue</div>
        <div class="stat-value" style="color: var(--danger)">${pf.overdue || 0}</div>
      </div>
      <div class="stat-item">
        <div class="stat-label">Pilot Fuel Due Soon</div>
        <div class="stat-value" style="color: var(--warning)">${pf.dueSoon || 0}</div>
      </div>
    `;
  } catch (err) {
    showBanner(`Failed to load stats: ${err.message}`, "error");
  }
}

// ==================== Cache Actions ====================

async function clearCache() {
  if (!confirm("Clear all caches?")) return;
  
  try {
    await adminFetch("/admin/cache", {
      method: "POST",
      body: JSON.stringify({ action: "clear_all" })
    });
    showBanner("All caches cleared successfully", "success");
    await loadOverview();
  } catch (err) {
    showBanner(`Failed to clear cache: ${err.message}`, "error");
  }
}

// ==================== File Manager ====================

async function loadFiles() {
  const fileTypeFilter = document.getElementById("fileTypeFilter").value;
  
  try {
    const data = await adminFetch(`/admin/files?type=${fileTypeFilter}`);
    const tbody = document.getElementById("filesTbody");
    
    if (!data.files.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="empty-row">No files found.</td></tr>`;
      return;
    }
    
    tbody.innerHTML = data.files.map(file => {
      const typeLabels = {
        "card-history": "Card History",
        "registry": "Device Registry",
        "chat-logs": "Chat Logs"
      };
      
      const badgeClass = {
        "card-history": "badge-card-history",
        "registry": "badge-registry",
        "chat-logs": "badge-chat-logs"
      }[file.type] || "badge";
      
      const sizeMB = (file.size / (1024 * 1024)).toFixed(2);
      
      return `
        <tr>
          <td>${file.name}</td>
          <td><span class="badge ${badgeClass}">${typeLabels[file.type] || file.type}</span></td>
          <td>${sizeMB} MB</td>
          <td>${new Date(file.modified).toLocaleString()}</td>
          <td>
            <button class="btn-delete" onclick="deleteFile('${file.type}', '${file.name}')">Delete</button>
          </td>
        </tr>
      `;
    }).join("");
  } catch (err) {
    showBanner(`Failed to load files: ${err.message}`, "error");
  }
}

async function deleteFile(fileType, filename) {
  if (!confirm(`Delete ${filename}? This action cannot be undone.`)) return;
  
  try {
    await adminFetch(`/admin/files/${fileType}/${encodeURIComponent(filename)}`, {
      method: "DELETE"
    });
    showBanner(`Deleted ${filename}`, "success");
    loadFiles();
  } catch (err) {
    showBanner(`Failed to delete file: ${err.message}`, "error");
  }
}

// ==================== Configuration ====================

async function loadConfig() {
  try {
    currentConfig = await adminFetch("/admin/config");
    
    document.getElementById("rechargeInterval").value = currentConfig.recharge_interval_months;
    document.getElementById("dueSoonDays").value = currentConfig.due_soon_days;
    document.getElementById("autoRefresh").checked = currentConfig.auto_refresh_enabled;
  } catch (err) {
    showBanner(`Failed to load config: ${err.message}`, "error");
  }
}

async function saveConfig(e) {
  e.preventDefault();
  
  try {
    const config = {
      recharge_interval_months: parseInt(document.getElementById("rechargeInterval").value),
      due_soon_days: parseInt(document.getElementById("dueSoonDays").value),
      auto_refresh_enabled: document.getElementById("autoRefresh").checked
    };
    
    const result = await adminFetch("/admin/config", {
      method: "PUT",
      body: JSON.stringify(config)
    });
    
    currentConfig = result.config;
    showBanner("Configuration saved successfully", "success");
  } catch (err) {
    showBanner(`Failed to save config: ${err.message}`, "error");
  }
}

// ==================== Audit Logs ====================

async function loadLogs() {
  const days = document.getElementById("logDaysFilter").value;
  
  try {
    const data = await adminFetch(`/admin/logs?days=${days}`);
    const logsContent = document.getElementById("logsContent");
    
    if (!data.logs.length) {
      logsContent.textContent = "No logs found.";
      return;
    }
    
    logsContent.textContent = data.logs.join("");
  } catch (err) {
    showBanner(`Failed to load logs: ${err.message}`, "error");
  }
}

// ==================== Tab Navigation ====================

tabButtons.forEach(btn => {
  btn.addEventListener("click", () => {
    tabButtons.forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    
    const target = btn.dataset.tab;
    Object.entries(TAB_PANELS).forEach(([name, panel]) => {
      panel.classList.toggle("hidden", name !== target);
    });
    
    // Load tab data
    if (target === "overview") loadOverview();
    if (target === "files") loadFiles();
    if (target === "config") loadConfig();
    if (target === "logs") loadLogs();
  });
});

// ==================== Event Listeners ====================

loginBtn.addEventListener("click", () => {
  const apiKey = apiKeyInput.value.trim();
  if (!apiKey) {
    loginError.textContent = "Please enter an API key";
    loginError.classList.remove("hidden");
    return;
  }
  login(apiKey);
});

apiKeyInput.addEventListener("keypress", (e) => {
  if (e.key === "Enter") loginBtn.click();
});

logoutBtn.addEventListener("click", logout);

document.getElementById("clearCacheBtn").addEventListener("click", clearCache);

document.getElementById("fileTypeFilter").addEventListener("change", loadFiles);
document.getElementById("refreshFilesBtn").addEventListener("click", loadFiles);

document.getElementById("configForm").addEventListener("submit", saveConfig);

document.getElementById("logDaysFilter").addEventListener("change", loadLogs);
document.getElementById("refreshLogsBtn").addEventListener("click", loadLogs);

// ==================== Initialization ====================

document.addEventListener("DOMContentLoaded", () => {
  const authenticated = sessionStorage.getItem("adminAuthenticated");
  if (authenticated) {
    isAuthenticated = true;
    showDashboard();
    loadOverview();
  }
});