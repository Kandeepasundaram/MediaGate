const state = {
  previewItems: [],
  sizeByPath: {},
  previewMode: "archive", // "archive" (copy, from Ready to Archive) or "organize" (move in place, from Browse)
  movieItems: [],
  tvItems: [],
  matchPicker: null, // { mediaType, onApply } for the current match-modal search
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

function filterAndSort(items, query, sortMode, titleKey, filterMode) {
  let out = items;
  if (filterMode === "unmatched") out = out.filter((i) => i.tmdb_id == null);
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
  const filterMode = $("#movies-filter").value;
  const items = filterAndSort(state.movieItems, query, sortMode, "title", filterMode);

  $("#movies-count").textContent = `${state.movieItems.length} movie(s) archived` +
    (items.length !== state.movieItems.length ? ` (${items.length} shown)` : "");

  if (state.movieItems.length === 0) {
    gallery.innerHTML = `<p class="gallery-empty">No movies found — approve some from "Ready to Archive", or drop files into your movies archive folder and reload this tab.</p>`;
    return;
  }
  if (items.length === 0) {
    gallery.innerHTML = `<p class="gallery-empty">No movies match${query ? ` "${query}"` : ""}${filterMode === "unmatched" ? " (unmatched filter)" : ""}.</p>`;
    return;
  }
  gallery.innerHTML = items.map((item, i) => `
    <div class="gallery-card" data-item-index="${i}">
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
  gallery.querySelectorAll(".gallery-card").forEach((card, i) => {
    card.addEventListener("click", () => openDetailPane("movie", items[i]));
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
    if (!shows.has(key)) {
      shows.set(key, { title: item.title, poster_path: item.poster_path, tmdb_id: item.tmdb_id, overview: item.overview, episodes: [] });
    }
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
  const filterMode = $("#tv-filter").value;
  const allShows = groupEpisodesByShow(state.tvItems);
  const shows = filterAndSort(allShows, query, sortMode, "title", filterMode);

  $("#tv-count").textContent = `${state.tvItems.length} episode(s) across ${allShows.length} show(s)` +
    (shows.length !== allShows.length ? ` (${shows.length} shown)` : "");

  if (state.tvItems.length === 0) {
    gallery.innerHTML = `<p class="gallery-empty">No TV episodes found — approve some from "Ready to Archive", or drop files into your TV archive folder and reload this tab.</p>`;
    return;
  }
  if (shows.length === 0) {
    gallery.innerHTML = `<p class="gallery-empty">No shows match${query ? ` "${query}"` : ""}${filterMode === "unmatched" ? " (unmatched filter)" : ""}.</p>`;
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
    </div>
  `).join("");
  gallery.querySelectorAll(".gallery-card").forEach((card, i) => {
    card.addEventListener("click", () => openDetailPane("tv", shows[i]));
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

// ---- Detail pane ----
state.detailPane = null; // { kind: "movie"|"tv", data }

function renderDetailPane() {
  const pane = state.detailPane;
  const content = $("#detail-pane-content");
  if (!pane) return;

  if (pane.kind === "movie") {
    const item = pane.data;
    content.innerHTML = `
      ${item.tmdb_id == null ? `<p class="unidentified-badge">⚠ Unidentified — no TMDB match yet</p>` : ""}
      ${posterMarkupLarge(item.title, item.poster_path)}
      <div class="detail-title">${item.title}</div>
      <div class="detail-year">${item.year || ""}</div>
      <label class="watched-toggle">
        <input type="checkbox" id="detail-watched-toggle" data-id="${item.id}" ${item.watched ? "checked" : ""}>
        Watched
      </label>
      <div id="detail-ratings" class="detail-ratings"></div>
      <p class="detail-overview">${item.overview || "No overview available."}</p>
      <div class="detail-file-info">
        <div class="detail-file-row"><span>File</span><span class="detail-file-value" title="${item.file_name || ""}">${item.file_name || "—"}</span></div>
        <div class="detail-file-row"><span>Size</span><span>${item.size_bytes != null ? formatBytes(item.size_bytes) : "—"}</span></div>
        <div class="detail-file-row"><span>Path</span><span class="detail-file-value" title="${item.final_path || ""}">${item.final_path || "—"}</span></div>
        <div id="detail-file-extra" class="hint">Loading file details…</div>
      </div>
      ${detailFixMarkup()}
    `;
    $("#detail-watched-toggle").addEventListener("change", async (e) => {
      try {
        await toggleWatched(item.id, e.target.checked);
        item.watched = e.target.checked;
        renderMoviesGallery(); // syncs the same checkbox shown on the gallery card
      } catch (err) {
        e.target.checked = !e.target.checked;
      }
    });
    loadRatings(item.id);
    loadFileInfo(item.id, "#detail-file-extra");
  } else {
    const show = pane.data;
    content.innerHTML = `
      <div id="detail-tv-status"></div>
      ${show.tmdb_id == null ? `<p class="unidentified-badge">⚠ Unidentified — no TMDB match yet</p>` : ""}
      ${posterMarkupLarge(show.title, show.poster_path)}
      <div class="detail-title">${show.title}</div>
      <div class="detail-year">${show.episodes.length} episode(s)</div>
      <div id="detail-ratings" class="detail-ratings"></div>
      <p class="detail-overview">${show.overview || "No overview available."}</p>
      <div id="detail-tv-body"></div>
      ${detailFixMarkup()}
    `;
    renderTvBody();
    loadRatings(show.episodes[0].id); // ratings are show-level; episodes are pre-sorted, so [0] is stable
    if (show.tmdb_id != null) loadTvStatus(show.tmdb_id, show.episodes);
  }

  wireDetailFix();
}

// Redraws just the season tabs + episode list (not the outer shell), so
// switching seasons or toggling name/watched state doesn't re-trigger the
// show-level ratings/status fetches (loadRatings especially -- OMDb isn't
// cached server-side, unlike TMDBClient).
function renderTvBody() {
  const pane = state.detailPane;
  if (!pane || pane.kind !== "tv") return;
  const show = pane.data;
  const container = $("#detail-tv-body");
  if (!container) return;

  const seasons = Array.from(new Set(show.episodes.map((e) => e.season_number))).sort((a, b) => a - b);
  if (pane.selectedSeason == null || !seasons.includes(pane.selectedSeason)) {
    pane.selectedSeason = seasons[seasons.length - 1];
  }
  if (pane.nameMode == null) pane.nameMode = "episode";

  const seasonEpisodes = show.episodes.filter((e) => e.season_number === pane.selectedSeason);
  const allWatched = seasonEpisodes.length > 0 && seasonEpisodes.every((e) => e.watched);
  const hasEpisodeNames = show.episodes.some((e) => e.episode_title);

  container.innerHTML = `
    <div class="season-tabs">
      ${seasons.map((s) => `<button class="season-tab-btn ${s === pane.selectedSeason ? "active" : ""}" data-season="${s}">Season ${s}</button>`).join("")}
    </div>
    <div class="episode-toolbar">
      ${hasEpisodeNames ? `
        <label class="name-mode-toggle">
          <input type="checkbox" id="detail-name-mode-toggle" ${pane.nameMode === "episode" ? "checked" : ""}>
          Show episode names
        </label>
      ` : "<span></span>"}
      <button id="detail-season-watched-btn">${allWatched ? "Mark Season Unwatched" : "Mark Season Watched"}</button>
    </div>
    <div class="detail-episodes">
      ${seasonEpisodes.map((ep) => `
        <div class="detail-episode-row">
          <span>S${String(ep.season_number).padStart(2, "0")}E${String(ep.episode_number).padStart(2, "0")}</span>
          <span class="detail-ep-file hint" title="${ep.file_name || ""}">${(pane.nameMode === "episode" && ep.episode_title) ? ep.episode_title : (ep.file_name || "")}${ep.size_bytes != null ? ` · ${formatBytes(ep.size_bytes)}` : ""}</span>
          <label class="watched-toggle">
            <input type="checkbox" class="detail-ep-watched" data-id="${ep.id}" ${ep.watched ? "checked" : ""}>
            Watched
          </label>
          <button class="ep-details-btn" data-id="${ep.id}">Details</button>
        </div>
        <div class="detail-ep-extra hint" id="detail-ep-extra-${ep.id}" hidden></div>
      `).join("")}
    </div>
  `;

  container.querySelectorAll(".season-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      pane.selectedSeason = Number(btn.dataset.season);
      renderTvBody();
    });
  });

  const nameModeToggle = $("#detail-name-mode-toggle");
  if (nameModeToggle) {
    nameModeToggle.addEventListener("change", (e) => {
      pane.nameMode = e.target.checked ? "episode" : "file";
      renderTvBody();
    });
  }

  $("#detail-season-watched-btn").addEventListener("click", async (e) => {
    const btn = e.target;
    const newWatched = !allWatched;
    btn.disabled = true;
    try {
      await api("/api/library/watched-batch", {
        method: "POST",
        body: JSON.stringify({ ids: seasonEpisodes.map((ep) => ep.id), watched: newWatched }),
      });
      seasonEpisodes.forEach((ep) => { ep.watched = newWatched; });
      renderTvBody();
    } catch (err) {
      btn.disabled = false;
    }
  });

  container.querySelectorAll(".detail-ep-watched").forEach((input) => {
    input.addEventListener("change", async () => {
      try {
        await toggleWatched(Number(input.dataset.id), input.checked);
        const ep = show.episodes.find((e) => e.id === Number(input.dataset.id));
        if (ep) ep.watched = input.checked;
        renderTvBody(); // keeps the "Mark Season Watched" button label in sync
      } catch (err) {
        input.checked = !input.checked;
      }
    });
  });

  container.querySelectorAll(".ep-details-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const el = document.getElementById(`detail-ep-extra-${btn.dataset.id}`);
      if (!el) return;
      if (!el.hidden) { el.hidden = true; return; }
      el.hidden = false;
      loadFileInfo(Number(btn.dataset.id), `#detail-ep-extra-${btn.dataset.id}`);
    });
  });
}

async function loadTvStatus(tmdbId, episodes) {
  const el = $("#detail-tv-status");
  if (!el) return;
  try {
    const status = await api(`/api/library/tv-status?tmdb_id=${tmdbId}`);
    if (!status.data_available || status.latest_known_season == null) {
      el.innerHTML = "";
      return;
    }
    const localMaxSeason = Math.max(...episodes.map((e) => e.season_number));
    const localEpisodeCountInMaxSeason = episodes.filter((e) => e.season_number === localMaxSeason).length;

    let message = null;
    if (status.latest_known_season > localMaxSeason) {
      message = `Season ${status.latest_known_season} is out — you have up to season ${localMaxSeason}.`;
    } else if (
      status.latest_known_season === localMaxSeason &&
      status.latest_season_episode_count != null &&
      status.latest_season_episode_count > localEpisodeCountInMaxSeason
    ) {
      message = `Season ${localMaxSeason} has ${status.latest_season_episode_count} episode(s) — you have ${localEpisodeCountInMaxSeason}.`;
    }
    el.innerHTML = message
      ? `<div class="tv-status-banner">📺 ${message}${status.status ? ` <span class="hint">(${status.status})</span>` : ""}</div>`
      : "";
  } catch (e) {
    el.innerHTML = "";
  }
}

async function loadRatings(itemId) {
  const el = $("#detail-ratings");
  if (!el) return;
  el.textContent = "Loading ratings...";
  try {
    const r = await api(`/api/library/${itemId}/ratings`);
    if (!r.omdb_configured) {
      el.innerHTML = `<span class="hint">Ratings unavailable — no OMDb API key configured in Settings.</span>`;
      return;
    }
    if (r.imdb_rating == null && !r.rotten_tomatoes) {
      el.innerHTML = `<span class="hint">No ratings found${r.imdb_id ? "" : " (no IMDb match yet)"}.</span>`;
      return;
    }
    const parts = [];
    if (r.imdb_rating != null) parts.push(`IMDb ${r.imdb_rating}/10${r.imdb_votes ? ` (${r.imdb_votes} votes)` : ""}`);
    if (r.rotten_tomatoes) parts.push(`🍅 ${r.rotten_tomatoes}`);
    if (r.metacritic) parts.push(`Metacritic ${r.metacritic}`);
    el.innerHTML = parts.map((p) => `<span class="rating-badge">${p}</span>`).join("");
  } catch (e) {
    el.innerHTML = `<span class="hint">Ratings error: ${e.message}</span>`;
  }
}

function formatDuration(seconds) {
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

async function loadFileInfo(itemId, selector) {
  const el = $(selector);
  if (!el) return;
  el.textContent = "Loading file details…";
  try {
    const info = await api(`/api/library/${itemId}/file-info`);
    if (!info.probe_available) {
      el.innerHTML = `<span class="hint">Duration/codec info unavailable — ffprobe not installed on the server.</span>`;
      return;
    }
    const rows = [];
    if (info.duration_seconds != null) rows.push(["Duration", formatDuration(info.duration_seconds)]);
    if (info.width && info.height) rows.push(["Resolution", `${info.width}×${info.height}`]);
    if (info.video_codec) rows.push(["Video codec", info.video_codec]);
    if (info.audio_codec) rows.push(["Audio codec", info.audio_codec]);
    if (info.bitrate) rows.push(["Bitrate", `${Math.round(info.bitrate / 1000)} kbps`]);
    if (info.container) rows.push(["Container", info.container]);
    el.innerHTML = rows.length
      ? rows.map(([k, v]) => `<div class="detail-file-row"><span>${k}</span><span>${v}</span></div>`).join("")
      : `<span class="hint">No additional details available.</span>`;
  } catch (e) {
    el.innerHTML = `<span class="hint">File info error: ${e.message}</span>`;
  }
}

function posterMarkupLarge(title, posterPath) {
  const url = posterUrl(posterPath);
  return url
    ? `<img class="detail-poster" src="${url}" alt="${title}">`
    : `<div class="detail-poster-placeholder">${title}</div>`;
}

function detailFixMarkup() {
  return `
    <div class="detail-fix">
      <button id="detail-change-match-btn">Change Match (search TMDB)</button>
      <label>IMDb ID
        <input type="text" id="detail-imdb-input" placeholder="tt1234567">
      </label>
      <button id="detail-fetch-btn">Fetch Metadata from IMDb ID</button>
      <span id="detail-fetch-status" class="hint"></span>
    </div>
  `;
}

async function openPaneMatchPicker() {
  const pane = state.detailPane;
  if (!pane) return;
  const mediaType = pane.kind === "movie" ? "movie" : "tv";
  openMatchModal(mediaType, pane.data.title, applyPaneMatchOverride);
}

async function applyPaneMatchOverride(candidate) {
  const pane = state.detailPane;
  closeMatchPicker();
  if (!pane || candidate.tmdb_id == null) return;

  const ids = pane.kind === "movie" ? [pane.data.id] : pane.data.episodes.map((e) => e.id);
  const mediaType = pane.kind === "movie" ? "movie" : "tv";

  $("#detail-fetch-status").textContent = "Applying match...";
  try {
    await api("/api/library/rematch-tmdb", {
      method: "POST",
      body: JSON.stringify({ ids, tmdb_id: candidate.tmdb_id, media_type: mediaType }),
    });
    await reopenDetailPaneAfterRematch(pane, ids);
  } catch (e) {
    $("#detail-fetch-status").textContent = `Error: ${e.message}`;
  }
}

async function reopenDetailPaneAfterRematch(pane, ids) {
  if (pane.kind === "movie") {
    await loadMoviesGallery();
    const updated = state.movieItems.find((i) => i.id === pane.data.id);
    if (updated) openDetailPane("movie", updated);
  } else {
    await loadTvGallery();
    const updated = groupEpisodesByShow(state.tvItems).find((s) => s.episodes.some((e) => ids.includes(e.id)));
    if (updated) openDetailPane("tv", updated);
  }
}

function wireDetailFix() {
  $("#detail-change-match-btn").addEventListener("click", openPaneMatchPicker);
  $("#detail-fetch-btn").addEventListener("click", async () => {
    const pane = state.detailPane;
    if (!pane) return;
    const imdbId = $("#detail-imdb-input").value.trim();
    if (!imdbId) return;

    const ids = pane.kind === "movie" ? [pane.data.id] : pane.data.episodes.map((e) => e.id);
    const mediaType = pane.kind === "movie" ? "movie" : "tv";

    $("#detail-fetch-status").textContent = "Fetching...";
    try {
      await api("/api/library/rematch-imdb", {
        method: "POST",
        body: JSON.stringify({ ids, imdb_id: imdbId, media_type: mediaType }),
      });
      $("#detail-fetch-status").textContent = "Updated.";
      await reopenDetailPaneAfterRematch(pane, ids);
    } catch (e) {
      $("#detail-fetch-status").textContent = `Error: ${e.message}`;
    }
  });
}

function openDetailPane(kind, data) {
  state.detailPane = { kind, data };
  renderDetailPane();
  $("#detail-pane").classList.remove("hidden");
}

function closeDetailPane() {
  state.detailPane = null;
  $("#detail-pane").classList.add("hidden");
}

// ---- Command palette ----
const COMMANDS = [
  { category: "Navigate", label: "Go to Movies", run: () => switchToTab("movies") },
  { category: "Navigate", label: "Go to TV", run: () => switchToTab("tv") },
  { category: "Navigate", label: "Go to Browse & Clean Up", run: () => switchToTab("browse") },
  { category: "Navigate", label: "Go to Ready to Archive", run: () => switchToTab("archive") },
  { category: "Navigate", label: "Go to Notifications", run: () => switchToTab("notifications") },
  { category: "Navigate", label: "Go to History", run: () => switchToTab("history") },
  { category: "Navigate", label: "Go to Settings", run: () => switchToTab("settings") },
  { category: "Library", label: "Scan Library", run: () => { switchToTab("archive"); scanAndPreview(); } },
  { category: "Library", label: "Refresh Browse & Clean Up", run: () => { switchToTab("browse"); loadBrowse(); } },
  { category: "Library", label: "Check Storage Permissions", run: () => { switchToTab("settings"); checkPermissions(); } },
  { category: "View", label: "Toggle Light / Dark Theme", run: () => $("#theme-toggle-btn").click() },
];
const PALETTE_CATEGORY_ORDER = ["Navigate", "Library", "View"];

state.paletteVisible = [];
state.paletteIndex = 0;

function filterCommands(query) {
  const q = query.trim().toLowerCase();
  const matches = q ? COMMANDS.filter((c) => c.label.toLowerCase().includes(q)) : COMMANDS;
  return matches.slice().sort((a, b) => PALETTE_CATEGORY_ORDER.indexOf(a.category) - PALETTE_CATEGORY_ORDER.indexOf(b.category));
}

function renderPalette() {
  const results = $("#palette-results");
  if (state.paletteVisible.length === 0) {
    results.innerHTML = `<p class="palette-empty">No matching commands.</p>`;
    return;
  }
  let html = "";
  let lastCategory = null;
  state.paletteVisible.forEach((cmd, i) => {
    if (cmd.category !== lastCategory) {
      html += `<div class="palette-category">${cmd.category}</div>`;
      lastCategory = cmd.category;
    }
    html += `<div class="palette-item${i === state.paletteIndex ? " active" : ""}" data-index="${i}">${cmd.label}</div>`;
  });
  results.innerHTML = html;
  results.querySelectorAll(".palette-item").forEach((el) => {
    el.addEventListener("click", () => runPaletteCommand(Number(el.dataset.index)));
    el.addEventListener("mouseenter", () => {
      state.paletteIndex = Number(el.dataset.index);
      results.querySelectorAll(".palette-item").forEach((e2) => e2.classList.remove("active"));
      el.classList.add("active");
    });
  });
}

function runPaletteCommand(index) {
  const cmd = state.paletteVisible[index];
  if (!cmd) return;
  closeCommandPalette();
  cmd.run();
}

function openCommandPalette() {
  $("#command-palette").classList.remove("hidden");
  const input = $("#palette-input");
  input.value = "";
  state.paletteVisible = filterCommands("");
  state.paletteIndex = 0;
  renderPalette();
  input.focus();
}

function closeCommandPalette() {
  $("#command-palette").classList.add("hidden");
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
  openMatchModal(item.media_type, item.title, (candidate) => applyMatchOverride(index, candidate));
}

function openMatchModal(mediaType, initialQuery, onApply) {
  state.matchPicker = { mediaType, onApply };
  $("#match-modal").classList.remove("hidden");
  $("#match-search-input").value = initialQuery;
  $("#match-id-input").value = "";
  runMatchSearch(initialQuery);
}

async function runMatchSearch(query) {
  const picker = state.matchPicker;
  const results = $("#match-results");
  if (!picker || !query.trim()) return;
  results.innerHTML = "Searching...";
  try {
    const data = await api(`/api/archive/search?title=${encodeURIComponent(query.trim())}&media_type=${picker.mediaType}`);
    if (data.results.length === 0) {
      results.innerHTML = "<p>No candidates found.</p>";
      return;
    }
    results.innerHTML = data.results.map((r, i) => `
      <div class="match-result-row">
        <span>${r.title}${r.year ? ` (${r.year})` : ""} <span class="hint">#${r.tmdb_id}</span></span>
        <button class="primary use-match-btn" data-result-index="${i}">Use</button>
      </div>
    `).join("");
    results.querySelectorAll(".use-match-btn").forEach((btn) => {
      btn.addEventListener("click", () => picker.onApply(data.results[Number(btn.dataset.resultIndex)]));
    });
  } catch (e) {
    results.innerHTML = `<p>Error: ${e.message}</p>`;
  }
}

function useMatchById() {
  const picker = state.matchPicker;
  if (!picker) return;
  const raw = $("#match-id-input").value.trim();
  const id = Number(raw);
  if (!raw || !Number.isInteger(id) || id <= 0) {
    $("#match-results").innerHTML = "<p>Enter a valid numeric TMDB ID.</p>";
    return;
  }
  picker.onApply({ tmdb_id: id });
}

async function applyMatchOverride(index, candidate) {
  const item = state.previewItems[index];
  closeMatchPicker();
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
  state.matchPicker = null;
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
state.browseFiltered = [];

function renderBrowseTable() {
  const tbody = $("#browse-table tbody");
  const filterMode = $("#browse-filter").value;
  const items = filterMode === "unmatched" ? state.browseItems.filter((i) => i.tmdb_id == null) : state.browseItems;
  state.browseFiltered = items;

  if (state.browseDirectory) {
    $("#browse-status").textContent = `${state.browseItems.length} file(s) in ${state.browseDirectory}` +
      (items.length !== state.browseItems.length ? ` (${items.length} shown)` : "");
  }

  if (state.browseItems.length === 0) {
    tbody.innerHTML = `<tr><td colspan=6>No files found.</td></tr>`;
    return;
  }
  if (items.length === 0) {
    tbody.innerHTML = `<tr><td colspan=6>No files match the unmatched filter.</td></tr>`;
    return;
  }
  tbody.innerHTML = items.map((item, i) => `
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
}

async function loadBrowse() {
  const tbody = $("#browse-table tbody");
  const mediaType = $("#browse-type").value;
  tbody.innerHTML = `<tr><td colspan=6>Loading...</td></tr>`;
  $("#browse-status").textContent = "";

  try {
    const data = await api(`/api/library/browse?media_type=${mediaType}`);
    state.browseItems = data.items;
    state.browseDirectory = data.directory;
    renderBrowseTable();
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan=6>Error: ${e.message}</td></tr>`;
  }
}

async function deleteBrowseItem(index) {
  const item = state.browseFiltered[index];
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
  const selected = $all(".browse-check:checked").map((cb) => state.browseFiltered[Number(cb.dataset.index)]);
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
    $("#omdb-key-note").textContent = s.omdb_api_key_set
      ? "A key is currently set. Leave blank to keep it. Powers IMDb/Rotten Tomatoes ratings in the detail pane."
      : "Powers IMDb/Rotten Tomatoes ratings in the detail pane. Free key at omdbapi.com/apikey.aspx.";
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
  const omdbKeyValue = $("#setting-omdb-key").value;
  if (omdbKeyValue) payload.omdb_api_key = omdbKeyValue;

  $("#settings-status").textContent = "Saving...";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    $("#setting-tmdb-key").value = "";
    $("#setting-omdb-key").value = "";
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

function movePaletteSelection(delta) {
  if (state.paletteVisible.length === 0) return;
  state.paletteIndex = (state.paletteIndex + delta + state.paletteVisible.length) % state.paletteVisible.length;
  renderPalette();
}

function setupKeyboardShortcuts() {
  document.addEventListener("keydown", (e) => {
    const paletteOpen = !$("#command-palette").classList.contains("hidden");
    if (paletteOpen) {
      if (e.key === "Escape") { e.preventDefault(); closeCommandPalette(); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); movePaletteSelection(1); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); movePaletteSelection(-1); return; }
      if (e.key === "Enter") { e.preventDefault(); runPaletteCommand(state.paletteIndex); return; }
      return; // any other key types normally into the palette input
    }

    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      approveSelected();
      return;
    }
    if (e.key === "Escape") {
      $("#confirm-modal").classList.add("hidden");
      closeMatchPicker();
      closeDetailPane();
      return;
    }

    const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
    if (typing) return;

    if (e.key >= "1" && e.key <= "7") {
      switchToTab(TAB_KEYS[Number(e.key) - 1]);
      return;
    }
    if (e.key === "/") {
      e.preventDefault();
      openCommandPalette();
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
  $("#match-search-btn").addEventListener("click", () => runMatchSearch($("#match-search-input").value));
  $("#match-search-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); runMatchSearch($("#match-search-input").value); }
  });
  $("#match-id-btn").addEventListener("click", useMatchById);
  $("#match-id-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); useMatchById(); }
  });
  $("#settings-form").addEventListener("submit", saveSettings);
  $("#check-permissions-btn").addEventListener("click", checkPermissions);
  $("#browse-refresh-btn").addEventListener("click", loadBrowse);
  $("#browse-type").addEventListener("change", loadBrowse);
  $("#browse-filter").addEventListener("change", renderBrowseTable);
  $("#browse-organize-btn").addEventListener("click", organizeSelected);
  $("#browse-select-all-btn").addEventListener("click", () => {
    const boxes = $all(".browse-check");
    const allChecked = boxes.every((b) => b.checked);
    boxes.forEach((b) => { b.checked = !allChecked; });
  });

  $("#movies-search").addEventListener("input", renderMoviesGallery);
  $("#movies-sort").addEventListener("change", renderMoviesGallery);
  $("#movies-filter").addEventListener("change", renderMoviesGallery);
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
  $("#tv-filter").addEventListener("change", renderTvGallery);

  $("#history-type-filter").addEventListener("change", loadHistory);

  $("#detail-close-btn").addEventListener("click", closeDetailPane);
  $("#palette-input").addEventListener("input", () => {
    state.paletteVisible = filterCommands($("#palette-input").value);
    state.paletteIndex = 0;
    renderPalette();
  });
  $("#command-palette").addEventListener("click", (e) => {
    if (e.target.id === "command-palette") closeCommandPalette();
  });
});
