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
      if (btn.dataset.tab === "movies") loadMoviesGallery();
      if (btn.dataset.tab === "tv") loadTvGallery();
      if (btn.dataset.tab === "browse") loadBrowse();
      if (btn.dataset.tab === "notifications") loadNotifications();
      if (btn.dataset.tab === "history") loadHistory();
      if (btn.dataset.tab === "settings") { loadStats(); loadSettings(); }
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

// ---- Movies / TV galleries ----
const TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w342";

function posterUrl(posterPath) {
  if (!posterPath) return null;
  return posterPath.startsWith("http") ? posterPath : `${TMDB_IMAGE_BASE}${posterPath}`;
}

function posterMarkup(title, posterPath) {
  const url = posterUrl(posterPath);
  return url
    ? `<img class="gallery-poster" src="${url}" alt="${title}" loading="lazy">`
    : `<div class="gallery-poster-placeholder">${title}</div>`;
}

async function toggleWatched(itemId, watched) {
  return api(`/api/library/${itemId}/watched`, {
    method: "POST",
    body: JSON.stringify({ watched }),
  });
}

function wireWatchedToggles(container) {
  container.querySelectorAll(".watched-toggle input").forEach((input) => {
    input.addEventListener("click", (e) => e.stopPropagation());
    input.addEventListener("change", async () => {
      try {
        await toggleWatched(Number(input.dataset.id), input.checked);
      } catch (e) {
        input.checked = !input.checked;
      }
    });
  });
}

async function loadMoviesGallery() {
  const gallery = $("#movies-gallery");
  gallery.innerHTML = "Loading...";
  try {
    const data = await api("/api/library/movies");
    $("#movies-count").textContent = `${data.items.length} movie(s) archived`;
    if (data.items.length === 0) {
      gallery.innerHTML = `<p class="gallery-empty">No movies archived yet — approve some from "Ready to Archive".</p>`;
      return;
    }
    gallery.innerHTML = data.items.map((item) => `
      <div class="gallery-card">
        ${posterMarkup(item.title, item.poster_path)}
        <div class="gallery-info">
          <div class="gallery-title" title="${item.title}">${item.title}</div>
          <div class="gallery-meta">
            <span>${item.year || ""}</span>
            <label class="watched-toggle">
              <input type="checkbox" data-id="${item.id}" ${item.watched ? "checked" : ""}>
              Watched
            </label>
          </div>
        </div>
      </div>
    `).join("");
    wireWatchedToggles(gallery);
  } catch (e) {
    gallery.innerHTML = `<p class="gallery-empty">Error: ${e.message}</p>`;
  }
}

function groupEpisodesByShow(items) {
  const shows = new Map();
  for (const item of items) {
    const key = item.title;
    if (!shows.has(key)) shows.set(key, { title: item.title, poster_path: item.poster_path, episodes: [] });
    shows.get(key).episodes.push(item);
  }
  for (const show of shows.values()) {
    show.episodes.sort((a, b) => (a.season_number - b.season_number) || (a.episode_number - b.episode_number));
  }
  return Array.from(shows.values());
}

async function loadTvGallery() {
  const gallery = $("#tv-gallery");
  gallery.innerHTML = "Loading...";
  try {
    const data = await api("/api/library/tv");
    $("#tv-count").textContent = `${data.items.length} episode(s) across ${new Set(data.items.map((i) => i.title)).size} show(s)`;
    if (data.items.length === 0) {
      gallery.innerHTML = `<p class="gallery-empty">No TV episodes archived yet — approve some from "Ready to Archive".</p>`;
      return;
    }
    const shows = groupEpisodesByShow(data.items);
    gallery.innerHTML = shows.map((show, i) => `
      <div class="gallery-card" data-show-index="${i}">
        ${posterMarkup(show.title, show.poster_path)}
        <div class="gallery-info">
          <div class="gallery-title" title="${show.title}">${show.title}</div>
          <div class="gallery-meta">
            <span>${show.episodes.length} episode(s)</span>
          </div>
        </div>
        <div class="tv-episodes">
          ${show.episodes.map((ep) => `
            <div class="tv-episode-row">
              <span>S${String(ep.season_number).padStart(2, "0")}E${String(ep.episode_number).padStart(2, "0")}</span>
              <label class="watched-toggle">
                <input type="checkbox" data-id="${ep.id}" ${ep.watched ? "checked" : ""}>
                Watched
              </label>
            </div>
          `).join("")}
        </div>
      </div>
    `).join("");
    wireWatchedToggles(gallery);
    gallery.querySelectorAll(".gallery-card").forEach((card) => {
      card.addEventListener("click", () => {
        card.querySelector(".tv-episodes").classList.toggle("expanded");
      });
    });
  } catch (e) {
    gallery.innerHTML = `<p class="gallery-empty">Error: ${e.message}</p>`;
  }
}

// ---- Archive tab ----
async function previewPaths(paths, sizeByPath = {}) {
  const tbody = $("#archive-table tbody");
  tbody.innerHTML = "";
  state.previewItems = [];
  state.sizeByPath = sizeByPath;

  if (paths.length === 0) {
    $("#scan-status").textContent = "No files selected";
    return;
  }

  $("#scan-status").textContent = "Fetching metadata...";
  try {
    const preview = await api("/api/archive/preview", {
      method: "POST",
      body: JSON.stringify({ paths }),
    });

    state.previewItems = preview.items;
    renderArchiveTable(preview.items);
    $("#scan-status").textContent = `${preview.items.length} file(s) ready` +
      (preview.errors.length ? `, ${preview.errors.length} error(s)` : "");
  } catch (e) {
    $("#scan-status").textContent = `Error: ${e.message}`;
  }
}

async function scanAndPreview() {
  $("#scan-status").textContent = "Scanning...";
  try {
    const scan = await api("/api/scan");
    if (scan.files.length === 0) {
      $("#scan-status").textContent = `No new media files found in ${scan.directories.join(", ")}`;
      $("#archive-table tbody").innerHTML = "";
      state.previewItems = [];
      return;
    }
    const sizeByPath = Object.fromEntries(scan.files.map((f) => [f.path, f.size_bytes]));
    await previewPaths(scan.files.map((f) => f.path), sizeByPath);
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

// ---- Browse & Clean Up tab ----
state.browseItems = [];

async function loadBrowse() {
  const tbody = $("#browse-table tbody");
  const mediaType = $("#browse-type").value;
  tbody.innerHTML = `<tr><td colspan=6>Loading...</td></tr>`;
  $("#browse-status").textContent = "";

  try {
    const data = await api(`/api/library/browse?media_type=${mediaType}`);
    state.browseItems = data.items;
    if (data.items.length === 0) {
      tbody.innerHTML = `<tr><td colspan=6>No files found in ${data.directory}</td></tr>`;
      return;
    }
    tbody.innerHTML = data.items.map((item, i) => `
      <tr>
        <td><input type="checkbox" class="browse-check" data-index="${i}"></td>
        <td title="${item.path}">${item.path.split(/[\\/]/).pop()}</td>
        <td>${item.parsed_title}${item.year ? ` (${item.year})` : ""}${
          item.season != null ? ` S${String(item.season).padStart(2, "0")}E${String(item.episode).padStart(2, "0")}` : ""
        }</td>
        <td>${formatBytes(item.size_bytes)}</td>
        <td class="${item.tracked ? "tracked-yes" : "tracked-no"}">${item.tracked ? "tracked" : "untracked"}</td>
        <td><button class="danger browse-delete-btn" data-index="${i}">Delete</button></td>
      </tr>
    `).join("");
    $all(".browse-delete-btn").forEach((btn) => {
      btn.addEventListener("click", () => deleteBrowseItem(Number(btn.dataset.index)));
    });
    $("#browse-status").textContent = `${data.items.length} file(s) in ${data.directory}`;
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan=6>Error: ${e.message}</td></tr>`;
  }
}

async function deleteBrowseItem(index) {
  const item = state.browseItems[index];
  const ok = await showConfirm(`Permanently delete "${item.path}"? This cannot be undone.`);
  if (!ok) return;

  $("#browse-status").textContent = "Deleting...";
  try {
    await api("/api/library/delete-file", { method: "POST", body: JSON.stringify({ path: item.path }) });
    loadBrowse();
  } catch (e) {
    $("#browse-status").textContent = `Error: ${e.message}`;
  }
}

async function rerunArchiveMatch() {
  const selected = $all(".browse-check:checked").map((cb) => state.browseItems[Number(cb.dataset.index)]);
  if (selected.length === 0) return;

  $all(".tab-btn").forEach((b) => b.classList.remove("active"));
  $all(".tab-panel").forEach((p) => p.classList.remove("active"));
  $('.tab-btn[data-tab="archive"]').classList.add("active");
  $("#tab-archive").classList.add("active");

  const sizeByPath = Object.fromEntries(selected.map((item) => [item.path, item.size_bytes]));
  await previewPaths(selected.map((item) => item.path), sizeByPath);
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

// ---- Settings tab ----
async function loadSettings() {
  try {
    const s = await api("/api/settings");
    $("#setting-incoming-movies").value = s.incoming_movies;
    $("#setting-incoming-tv").value = s.incoming_tv;
    $("#setting-archive-movies").value = s.archive_movies;
    $("#setting-archive-tv").value = s.archive_tv;
    const keyInput = $("#setting-tmdb-key");
    keyInput.disabled = s.tmdb_api_key_locked_by_env;
    $("#tmdb-key-note").textContent = s.tmdb_api_key_locked_by_env
      ? "Locked by the TMDB_API_KEY environment variable; edit it there instead."
      : s.tmdb_api_key_set
        ? "A key is currently set. Leave blank to keep it."
        : "No key set — running in TMDB scraper fallback mode.";
  } catch (e) {
    $("#settings-status").textContent = `Error loading settings: ${e.message}`;
  }
}

async function saveSettings(e) {
  e.preventDefault();
  const payload = {
    incoming_movies: $("#setting-incoming-movies").value.trim(),
    incoming_tv: $("#setting-incoming-tv").value.trim(),
    archive_movies: $("#setting-archive-movies").value.trim(),
    archive_tv: $("#setting-archive-tv").value.trim(),
  };
  const keyValue = $("#setting-tmdb-key").value;
  if (keyValue) payload.tmdb_api_key = keyValue;

  $("#settings-status").textContent = "Saving...";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    $("#setting-tmdb-key").value = "";
    $("#settings-status").textContent = "Saved.";
    loadSettings();
    loadStatus();
  } catch (e) {
    $("#settings-status").textContent = `Error: ${e.message}`;
  }
}

async function checkPermissions() {
  const result = $("#permissions-result");
  result.textContent = "Checking...";
  try {
    const data = await api("/api/settings/permissions-check");
    const uidLine = data.running_uid !== null
      ? `<p class="hint">Container running as uid=${data.running_uid} gid=${data.running_gid}</p>`
      : "";
    result.innerHTML = uidLine + data.paths.map((p) => `
      <div class="path-check-row">
        <span title="${p.path}">${p.path}</span>
        <span class="${p.writable ? 'status-success' : 'status-failed'}">
          ${p.writable ? "writable" : (p.error || "not writable")}
        </span>
      </div>
    `).join("");
  } catch (e) {
    result.textContent = `Error: ${e.message}`;
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
  loadMoviesGallery();
  requestNotificationPermission();
  pollNotifications();

  $("#scan-btn").addEventListener("click", scanAndPreview);
  $("#select-all-btn").addEventListener("click", () => {
    const boxes = $all(".row-check");
    const allChecked = boxes.every((b) => b.checked);
    boxes.forEach((b) => { b.checked = !allChecked; });
  });
  $("#approve-btn").addEventListener("click", approveAndArchive);
  $("#settings-form").addEventListener("submit", saveSettings);
  $("#check-permissions-btn").addEventListener("click", checkPermissions);
  $("#browse-refresh-btn").addEventListener("click", loadBrowse);
  $("#browse-type").addEventListener("change", loadBrowse);
  $("#browse-rematch-btn").addEventListener("click", rerunArchiveMatch);
});
