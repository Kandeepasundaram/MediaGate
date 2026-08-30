const state = {
  previewItems: [],
  sizeByPath: {},
  previewMode: "archive", // "archive" (copy, from Ready to Archive) or "organize" (move in place, from Browse)
  movieItems: [],
  tvItems: [],
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
      if (btn.dataset.tab === "notifications") { loadNotifications(); loadTrackedList(); }
      if (btn.dataset.tab === "history") loadHistory();
      if (btn.dataset.tab === "settings") { loadStats(); loadSettings(); }
    });
  });
}

// ---- Theme ----
const THEME_KEY = "media-manager:theme";

function applyTheme(theme) {
  document.body.dataset.theme = theme;
  $("#theme-toggle-btn").textContent = theme === "light" ? "☀️" : "🌙";
}

function setupTheme() {
  let saved = "dark";
  try {
    saved = localStorage.getItem(THEME_KEY) || "dark";
  } catch (e) { /* localStorage unavailable, default to dark */ }
  applyTheme(saved);

  $("#theme-toggle-btn").addEventListener("click", () => {
    const next = document.body.dataset.theme === "light" ? "dark" : "light";
    applyTheme(next);
    try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* ignore */ }
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

async function checkBackfillProgress(mediaType, tabKey, reloadFn) {
  try {
    const status = await api(`/api/library/metadata-status?media_type=${mediaType}`);
    const stillTrying = status.pending - status.failed;
    let msg = "";
    if (stillTrying > 0) msg += ` — fetching metadata for ${stillTrying} more...`;
    if (status.failed > 0) msg += ` (${status.failed} could not be matched automatically, retrying periodically)`;
    if (msg) $(`#${tabKey}-count`).textContent += msg;

    // Only keep auto-reloading while something is actively in flight --
    // items stuck in the failed-match retry cooldown would otherwise keep
    // status.pending > 0 indefinitely, re-rendering (and silently discarding
    // any in-progress gallery selection) every 8s forever for no reason.
    if (stillTrying > 0) {
      setTimeout(() => {
        if ($(`#tab-${tabKey}`).classList.contains("active")) reloadFn();
      }, 8000);
    }
  } catch (e) { /* next manual reload will retry */ }
}

function filterAndSort(items, query, sortMode, titleKey) {
  let out = items;
  if (query) {
    const q = query.toLowerCase();
    out = out.filter((i) => i[titleKey].toLowerCase().includes(q));
  }
  out = out.slice();
  if (sortMode === "title") out.sort((a, b) => a[titleKey].localeCompare(b[titleKey]));
  else if (sortMode === "year") out.sort((a, b) => (b.year || 0) - (a.year || 0));
  return out;
}

async function markWatchedBatch(ids, watched) {
  if (ids.length === 0) return;
  await api("/api/library/watched-batch", { method: "POST", body: JSON.stringify({ ids, watched }) });
}

function renderMoviesGallery() {
  const gallery = $("#movies-gallery");
  const query = $("#movies-search").value.trim();
  const sortMode = $("#movies-sort").value;
  const items = filterAndSort(state.movieItems, query, sortMode, "title");

  $("#movies-count").textContent = `${state.movieItems.length} movie(s) archived` +
    (items.length !== state.movieItems.length ? ` (${items.length} shown)` : "");

  if (state.movieItems.length === 0) {
    gallery.innerHTML = `<p class="gallery-empty">No movies found — approve some from "Ready to Archive", or drop files into your movies archive folder and reload this tab.</p>`;
    return;
  }
  if (items.length === 0) {
    gallery.innerHTML = `<p class="gallery-empty">No movies match "${query}".</p>`;
    return;
  }
  gallery.innerHTML = items.map((item) => `
    <div class="gallery-card">
      <input type="checkbox" class="gallery-select" data-select-id="${item.id}">
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
  gallery.querySelectorAll(".gallery-select").forEach((cb) => {
    cb.addEventListener("click", (e) => e.stopPropagation());
  });
}

async function loadMoviesGallery() {
  const gallery = $("#movies-gallery");
  gallery.innerHTML = "Loading...";
  try {
    const data = await api("/api/library/movies");
    state.movieItems = data.items;
    checkBackfillProgress("movie", "movies", loadMoviesGallery);
    renderMoviesGallery();
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

function renderTvGallery() {
  const gallery = $("#tv-gallery");
  const query = $("#tv-search").value.trim();
  const sortMode = $("#tv-sort").value;
  const allShows = groupEpisodesByShow(state.tvItems);
  const shows = filterAndSort(allShows, query, sortMode, "title");

  $("#tv-count").textContent = `${state.tvItems.length} episode(s) across ${allShows.length} show(s)` +
    (shows.length !== allShows.length ? ` (${shows.length} shown)` : "");

  if (state.tvItems.length === 0) {
    gallery.innerHTML = `<p class="gallery-empty">No TV episodes found — approve some from "Ready to Archive", or drop files into your TV archive folder and reload this tab.</p>`;
    return;
  }
  if (shows.length === 0) {
    gallery.innerHTML = `<p class="gallery-empty">No shows match "${query}".</p>`;
    return;
  }
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
            <input type="checkbox" class="tv-episode-select" data-select-id="${ep.id}">
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
  gallery.querySelectorAll(".tv-episode-select").forEach((cb) => {
    cb.addEventListener("click", (e) => e.stopPropagation());
  });
  gallery.querySelectorAll(".gallery-card").forEach((card) => {
    card.addEventListener("click", () => {
      card.querySelector(".tv-episodes").classList.toggle("expanded");
    });
  });
}

async function loadTvGallery() {
  const gallery = $("#tv-gallery");
  gallery.innerHTML = "Loading...";
  try {
    const data = await api("/api/library/tv");
    state.tvItems = data.items;
    checkBackfillProgress("tv", "tv", loadTvGallery);
    renderTvGallery();
  } catch (e) {
    gallery.innerHTML = `<p class="gallery-empty">Error: ${e.message}</p>`;
  }
}

// ---- Archive tab ----
function setPreviewMode(mode) {
  state.previewMode = mode;
  $("#approve-btn").textContent = mode === "organize" ? "Approve & Organize (Move)" : "Approve & Archive";
}

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
  setPreviewMode("archive");
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
  const checkedBefore = $all(".row-check").map((cb) => cb.checked);
  tbody.innerHTML = items.map((item, i) => `
    <tr>
      <td><input type="checkbox" class="row-check" data-index="${i}" ${checkedBefore[i] === false ? "" : "checked"}></td>
      <td>${item.duplicate ? `<span class="duplicate-badge" title="A matching title already exists in the library">⚠</span>` : ""}</td>
      <td title="${item.source_path}">${item.source_path.split(/[\\/]/).pop()}</td>
      <td title="${item.dest_path}">${item.dest_path.split(/[\\/]/).pop()}</td>
      <td>${item.media_type}</td>
      <td>${formatBytes(state.sizeByPath[item.source_path])}</td>
      <td title="${item.overview}">${item.overview.slice(0, 80)}</td>
      <td><button class="change-match-btn" data-index="${i}">Change Match</button></td>
    </tr>
  `).join("");
  $all(".change-match-btn").forEach((btn) => {
    btn.addEventListener("click", () => openMatchPicker(Number(btn.dataset.index)));
  });
}

// ---- Manual TMDB match picker ----
async function openMatchPicker(index) {
  const item = state.previewItems[index];
  const modal = $("#match-modal");
  const results = $("#match-results");
  results.innerHTML = "Searching...";
  modal.classList.remove("hidden");

  try {
    const data = await api(`/api/archive/search?title=${encodeURIComponent(item.title)}&media_type=${item.media_type}`);
    if (data.results.length === 0) {
      results.innerHTML = "<p>No candidates found.</p>";
      return;
    }
    results.innerHTML = data.results.map((r, i) => `
      <div class="match-result-row">
        <span>${r.title}${r.year ? ` (${r.year})` : ""}</span>
        <button class="primary use-match-btn" data-result-index="${i}">Use</button>
      </div>
    `).join("");
    results.querySelectorAll(".use-match-btn").forEach((btn) => {
      btn.addEventListener("click", () => applyMatchOverride(index, data.results[Number(btn.dataset.resultIndex)]));
    });
  } catch (e) {
    results.innerHTML = `<p>Error: ${e.message}</p>`;
  }
}

async function applyMatchOverride(index, candidate) {
  const item = state.previewItems[index];
  $("#match-modal").classList.add("hidden");
  $("#scan-status").textContent = "Applying match...";
  try {
    const preview = await api("/api/archive/preview", {
      method: "POST",
      body: JSON.stringify({ paths: [item.source_path], tmdb_overrides: { [item.source_path]: candidate.tmdb_id } }),
    });
    if (preview.items.length > 0) {
      state.previewItems[index] = preview.items[0];
      renderArchiveTable(state.previewItems);
    }
    $("#scan-status").textContent = `${state.previewItems.length} file(s) ready`;
  } catch (e) {
    $("#scan-status").textContent = `Error: ${e.message}`;
  }
}

function closeMatchPicker() {
  $("#match-modal").classList.add("hidden");
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

async function approveSelected() {
  const items = selectedItems();
  if (items.length === 0) return;

  const isOrganize = state.previewMode === "organize";
  const confirmMsg = isOrganize
    ? `Organize ${items.length} file(s)? This moves them to their correct name/folder in place — no duplicate is created.`
    : `Archive ${items.length} file(s)? This copies them to the archive location.`;
  const ok = await showConfirm(confirmMsg);
  if (!ok) return;

  $("#scan-status").textContent = isOrganize ? "Organizing..." : "Archiving...";
  try {
    const endpoint = isOrganize ? "/api/library/organize" : "/api/archive/confirm";
    const body = isOrganize ? { items } : { items, purge_subtitles: true };
    const result = await api(endpoint, { method: "POST", body: JSON.stringify(body) });
    const failures = result.results.filter((r) => r.status === "failed");
    $("#scan-status").textContent = failures.length
      ? `Done with ${failures.length} failure(s)`
      : isOrganize ? "Organized successfully" : "Archived successfully";

    if (isOrganize) {
      $("#archive-table tbody").innerHTML = "";
      state.previewItems = [];
    } else {
      scanAndPreview();
    }
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

async function organizeSelected() {
  const selected = $all(".browse-check:checked").map((cb) => state.browseItems[Number(cb.dataset.index)]);
  if (selected.length === 0) return;

  $all(".tab-btn").forEach((b) => b.classList.remove("active"));
  $all(".tab-panel").forEach((p) => p.classList.remove("active"));
  $('.tab-btn[data-tab="archive"]').classList.add("active");
  $("#tab-archive").classList.add("active");

  setPreviewMode("organize");
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

async function loadTrackedList() {
  const container = $("#tracked-list");
  container.innerHTML = "Loading...";
  try {
    const data = await api("/api/tracker/list");
    if (data.tracked.length === 0) {
      container.innerHTML = "<p>No tracked titles yet.</p>";
      return;
    }
    container.innerHTML = data.tracked.map((t) => `
      <div class="tracked-item">
        <div>
          <strong>${t.title}</strong>
          <span class="hint">(${t.media_type}${t.last_checked ? `, last checked ${new Date(t.last_checked).toLocaleString()}` : ""})</span>
        </div>
        <div class="tracked-item-actions">
          <label class="watched-toggle">
            <input type="checkbox" class="mute-toggle" data-id="${t.id}" ${t.muted ? "checked" : ""}>
            Muted
          </label>
          <button class="check-now-btn" data-id="${t.id}">Check Now</button>
        </div>
      </div>
    `).join("");
    $all(".mute-toggle").forEach((cb) => {
      cb.addEventListener("change", async () => {
        await api(`/api/tracker/${cb.dataset.id}/mute`, { method: "POST", body: JSON.stringify({ muted: cb.checked }) });
        loadNotifications();
      });
    });
    $all(".check-now-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "Checking...";
        try {
          await api(`/api/tracker/${btn.dataset.id}/check-now`, { method: "POST" });
          loadNotifications();
          loadTrackedList();
        } catch (e) {
          btn.disabled = false;
          btn.textContent = "Check Now";
        }
      });
    });
  } catch (e) {
    container.innerHTML = `<p>Error loading tracked titles: ${e.message}</p>`;
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
  tbody.innerHTML = "<tr><td colspan=5>Loading...</td></tr>";
  const type = $("#history-type-filter").value;
  try {
    const data = await api(`/api/archive/history${type ? `?operation_type=${type}` : ""}`);
    tbody.innerHTML = data.operations.map((op) => `
      <tr>
        <td>${new Date(op.created_at).toLocaleString()}</td>
        <td>${op.operation_type}</td>
        <td class="status-${op.status}">${op.status}</td>
        <td>${op.error_message || (op.details ? JSON.stringify(op.details) : "")}</td>
        <td>${op.status === "success" && (op.operation_type === "archive" || op.operation_type === "rename")
          ? `<button class="danger undo-btn" data-id="${op.id}">Undo</button>` : ""}</td>
      </tr>
    `).join("") || "<tr><td colspan=5>No history yet.</td></tr>";
    $all(".undo-btn").forEach((btn) => {
      btn.addEventListener("click", () => undoOperation(Number(btn.dataset.id)));
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan=5>Error: ${e.message}</td></tr>`;
  }
}

async function undoOperation(opId) {
  const ok = await showConfirm("Undo this operation? This moves/deletes the file on disk.");
  if (!ok) return;
  $("#history-status").textContent = "Undoing...";
  try {
    await api(`/api/archive/history/${opId}/undo`, { method: "POST" });
    $("#history-status").textContent = "";
    loadHistory();
  } catch (e) {
    $("#history-status").textContent = `Undo failed: ${e.message}`;
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
    $("#setting-webhook-url").value = s.webhook_url || "";
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
    webhook_url: $("#setting-webhook-url").value.trim(),
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
        <span class="${p.low_space ? 'status-warning' : 'hint'}">
          ${p.free_bytes != null ? `${formatBytes(p.free_bytes)} free${p.low_space ? " ⚠ low space" : ""}` : ""}
        </span>
      </div>
      ${p.chown_hint ? `<div class="hint">Fix: <code class="chown-hint" title="Click to select">${p.chown_hint}</code></div>` : ""}
    `).join("");
    result.querySelectorAll(".chown-hint").forEach((el) => {
      el.addEventListener("click", () => {
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      });
    });
  } catch (e) {
    result.textContent = `Error: ${e.message}`;
  }
}

// ---- Wiring ----
const TAB_KEYS = ["movies", "tv", "browse", "archive", "notifications", "history", "settings"];

function switchToTab(tabName) {
  const btn = $(`.tab-btn[data-tab="${tabName}"]`);
  if (btn) btn.click();
}

function setupKeyboardShortcuts() {
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      approveSelected();
      return;
    }
    if (e.key === "Escape") {
      $("#confirm-modal").classList.add("hidden");
      closeMatchPicker();
      return;
    }

    const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
    if (typing) return;

    if (e.key >= "1" && e.key <= "7") {
      switchToTab(TAB_KEYS[Number(e.key) - 1]);
      return;
    }
    if (e.key === "/") {
      const activeTab = $(".tab-panel.active").id.replace("tab-", "");
      const searchBox = $(`#${activeTab}-search`);
      if (searchBox) {
        e.preventDefault();
        searchBox.focus();
      }
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupTabs();
  setupTheme();
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
  $("#approve-btn").addEventListener("click", approveSelected);
  $("#match-cancel-btn").addEventListener("click", closeMatchPicker);
  $("#settings-form").addEventListener("submit", saveSettings);
  $("#check-permissions-btn").addEventListener("click", checkPermissions);
  $("#browse-refresh-btn").addEventListener("click", loadBrowse);
  $("#browse-type").addEventListener("change", loadBrowse);
  $("#browse-organize-btn").addEventListener("click", organizeSelected);
  $("#browse-select-all-btn").addEventListener("click", () => {
    const boxes = $all(".browse-check");
    const allChecked = boxes.every((b) => b.checked);
    boxes.forEach((b) => { b.checked = !allChecked; });
  });

  $("#movies-search").addEventListener("input", renderMoviesGallery);
  $("#movies-sort").addEventListener("change", renderMoviesGallery);
  $("#movies-select-all-btn").addEventListener("click", () => {
    const boxes = $all(".gallery-select");
    const allChecked = boxes.every((b) => b.checked);
    boxes.forEach((b) => { b.checked = !allChecked; });
  });
  $("#movies-mark-watched-btn").addEventListener("click", async () => {
    const ids = $all(".gallery-select:checked").map((b) => Number(b.dataset.selectId));
    await markWatchedBatch(ids, true);
    loadMoviesGallery();
  });
  $("#movies-mark-unwatched-btn").addEventListener("click", async () => {
    const ids = $all(".gallery-select:checked").map((b) => Number(b.dataset.selectId));
    await markWatchedBatch(ids, false);
    loadMoviesGallery();
  });

  $("#tv-search").addEventListener("input", renderTvGallery);
  $("#tv-sort").addEventListener("change", renderTvGallery);
  $("#tv-mark-watched-btn").addEventListener("click", async () => {
    const ids = $all(".tv-episode-select:checked").map((b) => Number(b.dataset.selectId));
    await markWatchedBatch(ids, true);
    loadTvGallery();
  });
  $("#tv-mark-unwatched-btn").addEventListener("click", async () => {
    const ids = $all(".tv-episode-select:checked").map((b) => Number(b.dataset.selectId));
    await markWatchedBatch(ids, false);
    loadTvGallery();
  });

  $("#history-type-filter").addEventListener("change", loadHistory);
});
