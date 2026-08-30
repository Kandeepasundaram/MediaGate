const state = {
  previewItems: [],
  sizeByPath: {},
};

function $(sel) { return document.querySelector(sel); }
function $all(sel) { return Array.from(document.querySelectorAll(sel)); }

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(1)} ${units[i]}`;
}

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status}: ${text}`);
  }
  return resp.json();
}

// ---- Tabs ----
function setupTabs() {
  $all(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      $all(".tab-btn").forEach((b) => b.classList.remove("active"));
      $all(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $(`#tab-${btn.dataset.tab}`).classList.add("active");
      if (btn.dataset.tab === "notifications") loadNotifications();
      if (btn.dataset.tab === "history") loadHistory();
      if (btn.dataset.tab === "settings") loadStats();
    });
  });
}

// ---- Status badge ----
async function loadStatus() {
  try {
    const status = await api("/api/status");
    $("#tmdb-mode").textContent = `TMDB: ${status.tmdb_mode}`;
  } catch (e) {
    $("#tmdb-mode").textContent = "TMDB: offline";
  }
}

// ---- Archive tab ----
async function scanAndPreview() {
  $("#scan-status").textContent = "Scanning...";
  const tbody = $("#archive-table tbody");
  tbody.innerHTML = "";
  state.previewItems = [];

  try {
    const scan = await api("/api/scan");
    state.sizeByPath = Object.fromEntries(scan.files.map((f) => [f.path, f.size_bytes]));

    if (scan.files.length === 0) {
      $("#scan-status").textContent = `No media files found in ${scan.directory}`;
      return;
    }

    $("#scan-status").textContent = "Fetching metadata...";
    const preview = await api("/api/archive/preview", {
      method: "POST",
      body: JSON.stringify({ paths: scan.files.map((f) => f.path) }),
    });

    state.previewItems = preview.items;
    renderArchiveTable(preview.items);
    $("#scan-status").textContent = `${preview.items.length} file(s) ready` +
      (preview.errors.length ? `, ${preview.errors.length} error(s)` : "");
  } catch (e) {
    $("#scan-status").textContent = `Error: ${e.message}`;
  }
}

function renderArchiveTable(items) {
  const tbody = $("#archive-table tbody");
  tbody.innerHTML = items.map((item, i) => `
    <tr>
      <td><input type="checkbox" class="row-check" data-index="${i}" checked></td>
      <td title="${item.source_path}">${item.source_path.split(/[\\/]/).pop()}</td>
      <td title="${item.dest_path}">${item.dest_path.split(/[\\/]/).pop()}</td>
      <td>${item.media_type}</td>
      <td>${formatBytes(state.sizeByPath[item.source_path])}</td>
      <td title="${item.overview}">${item.overview.slice(0, 80)}</td>
    </tr>
  `).join("");
}

function selectedItems() {
  return $all(".row-check:checked").map((cb) => state.previewItems[Number(cb.dataset.index)]);
}

function showConfirm(text) {
  return new Promise((resolve) => {
    $("#confirm-text").textContent = text;
    $("#confirm-modal").classList.remove("hidden");
    const cleanup = (result) => {
      $("#confirm-modal").classList.add("hidden");
      $("#confirm-yes").onclick = null;
      $("#confirm-no").onclick = null;
      resolve(result);
    };
    $("#confirm-yes").onclick = () => cleanup(true);
    $("#confirm-no").onclick = () => cleanup(false);
  });
}

async function approveAndArchive() {
  const items = selectedItems();
  if (items.length === 0) return;

  const ok = await showConfirm(`Archive ${items.length} file(s)? This copies them to the archive location.`);
  if (!ok) return;

  $("#scan-status").textContent = "Archiving...";
  try {
    const result = await api("/api/archive/confirm", {
      method: "POST",
      body: JSON.stringify({ items, purge_subtitles: true }),
    });
    const failures = result.results.filter((r) => r.status === "failed");
    $("#scan-status").textContent = failures.length
      ? `Done with ${failures.length} failure(s)`
      : "Archived successfully";
    scanAndPreview();
  } catch (e) {
    $("#scan-status").textContent = `Error: ${e.message}`;
  }
}

// ---- Notifications tab ----
async function loadNotifications() {
  const container = $("#notifications-list");
  container.innerHTML = "Loading...";
  try {
    const data = await api("/api/tracker/notifications");
    if (data.notifications.length === 0) {
      container.innerHTML = "<p>No pending notifications.</p>";
      return;
    }
    container.innerHTML = data.notifications.map((n) => `
      <div class="notification-item">
        <div>
          <strong>${n.title}</strong>
          <div>${n.media_type === "tv"
            ? `Season ${n.latest_known_season} now available`
            : (n.movie_release_status || "New release detected")}</div>
        </div>
        <button data-id="${n.id}" class="ack-btn">Mark Downloaded</button>
      </div>
    `).join("");
    $all(".ack-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await api("/api/tracker/acknowledge", {
          method: "POST",
          body: JSON.stringify({ tracker_id: Number(btn.dataset.id) }),
        });
        loadNotifications();
      });
    });
  } catch (e) {
    container.innerHTML = `<p>Error loading notifications: ${e.message}</p>`;
  }
}

// ---- Browser notifications ----
const NOTIFIED_IDS_KEY = "media-manager:notified-ids";

function loadNotifiedIds() {
  try {
    return new Set(JSON.parse(localStorage.getItem(NOTIFIED_IDS_KEY) || "[]"));
  } catch (e) {
    return new Set();
  }
}

function saveNotifiedIds(ids) {
  try {
    localStorage.setItem(NOTIFIED_IDS_KEY, JSON.stringify(Array.from(ids)));
  } catch (e) { /* localStorage unavailable, skip persistence */ }
}

function requestNotificationPermission() {
  if (!("Notification" in window)) return;
  if (Notification.permission === "default") Notification.requestPermission();
}

function notificationBody(n) {
  return n.media_type === "tv"
    ? `Season ${n.latest_known_season} of ${n.title} is out!`
    : `${n.title}: ${n.movie_release_status || "new release detected"}`;
}

function firePendingBrowserNotifications(notifications, notifiedIds) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  for (const n of notifications) {
    if (notifiedIds.has(n.id)) continue;
    new Notification("New Media Available", { body: notificationBody(n) });
    notifiedIds.add(n.id);
  }
  saveNotifiedIds(notifiedIds);
}

function pollNotifications() {
  const notifiedIds = loadNotifiedIds();
  const tick = async () => {
    try {
      const data = await api("/api/tracker/notifications");
      firePendingBrowserNotifications(data.notifications, notifiedIds);
      if ($("#tab-notifications").classList.contains("active")) loadNotifications();
    } catch (e) { /* offline or server restarting, retry on next tick */ }
  };
  tick();
  setInterval(tick, 30000);
}

// ---- History tab ----
async function loadHistory() {
  const tbody = $("#history-table tbody");
  tbody.innerHTML = "<tr><td colspan=4>Loading...</td></tr>";
  try {
    const data = await api("/api/archive/history");
    tbody.innerHTML = data.operations.map((op) => `
      <tr>
        <td>${new Date(op.created_at).toLocaleString()}</td>
        <td>${op.operation_type}</td>
        <td class="status-${op.status}">${op.status}</td>
        <td>${op.error_message || (op.details ? JSON.stringify(op.details) : "")}</td>
      </tr>
    `).join("") || "<tr><td colspan=4>No history yet.</td></tr>";
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan=4>Error: ${e.message}</td></tr>`;
  }
}

// ---- Settings/stats tab ----
async function loadStats() {
  const card = $("#stats-card");
  card.textContent = "Loading...";
  try {
    const stats = await api("/api/stats");
    card.innerHTML = `
      <p>Total media items: <strong>${stats.total_media_items}</strong></p>
      <p>Movies: <strong>${stats.total_movies}</strong></p>
      <p>TV episodes: <strong>${stats.total_tv_episodes}</strong></p>
      <p>Total archived size: <strong>${formatBytes(stats.total_size_bytes)}</strong></p>
    `;
  } catch (e) {
    card.textContent = `Error: ${e.message}`;
  }
}

// ---- Wiring ----
function setupKeyboardShortcuts() {
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      approveAndArchive();
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupTabs();
  setupKeyboardShortcuts();
  loadStatus();
  requestNotificationPermission();
  pollNotifications();

  $("#scan-btn").addEventListener("click", scanAndPreview);
  $("#select-all-btn").addEventListener("click", () => {
    const boxes = $all(".row-check");
    const allChecked = boxes.every((b) => b.checked);
    boxes.forEach((b) => { b.checked = !allChecked; });
  });
  $("#approve-btn").addEventListener("click", approveAndArchive);
});
