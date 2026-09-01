const state = {
  previewItems: [],
  sizeByPath: {},
  previewMode: "archive", // "archive" (copy, from Ready to Archive) or "organize" (move in place, from Browse)
  movieItems: [],
  tvItems: [],
  matchPicker: null, // { mediaType, onApply } for the current match-modal search
  tvStatusCache: {}, // tmdb_id -> TvStatusOut (or null on failure) -- shared by gallery badges and the detail pane banner
  movieStatusCache: {}, // tmdb_id -> MovieStatusOut (or null on failure) -- shared by gallery badges and the detail pane banner
  pendingGenreRestore: { movies: null, tv: null }, // saved genre filter value applied on the first gallery load only
  pendingYearRestore: { movies: null, tv: null }, // saved year filter value applied on the first gallery load only
  pendingTagRestore: { movies: null, tv: null }, // saved tag filter value applied on the first gallery load only
  moviesRenderLimit: 60,
  moviesFilterSignature: "",
  tvRenderLimit: 60,
  tvFilterSignature: "",
};

const GALLERY_PAGE_SIZE = 60;

// Caps how many cards get rendered/badge-fetched at once -- a library with
// thousands of items would otherwise build thousands of DOM nodes and fire
// a TMDB status lookup per card on every filter/sort change. Resets to the
// first page only when the filter/sort/query signature actually changes,
// not on every re-render (e.g. toggling one item's watched state), so
// "Load More" progress survives incidental re-renders.
function paginateGallery(stateKeyPrefix, items, signature) {
  const sigKey = `${stateKeyPrefix}FilterSignature`;
  const limitKey = `${stateKeyPrefix}RenderLimit`;
  if (state[sigKey] !== signature) {
    state[sigKey] = signature;
    state[limitKey] = GALLERY_PAGE_SIZE;
  }
  return { visible: items.slice(0, state[limitKey]), total: items.length, limitKey };
}

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

// ---- Optional API token (see Settings > API token) ----
const API_TOKEN_KEY = "media-manager:api-token";

function getStoredApiToken() {
  try { return localStorage.getItem(API_TOKEN_KEY) || ""; } catch (e) { return ""; }
}

function setStoredApiToken(token) {
  try {
    if (token) localStorage.setItem(API_TOKEN_KEY, token);
    else localStorage.removeItem(API_TOKEN_KEY);
  } catch (e) { /* private browsing / storage disabled -- token won't persist across reloads */ }
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getStoredApiToken();
  if (token) headers["X-API-Token"] = token;

  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401) {
    const entered = window.prompt("This dashboard requires an API token to continue. Enter it:");
    if (!entered) throw new Error("401: API token required");
    setStoredApiToken(entered);
    return api(path, options);
  }
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status}: ${text}`);
  }
  return resp.json();
}

// ---- Tabs ----
function activateTab(tabName) {
  $all(".tab-btn").forEach((b) => b.classList.remove("active"));
  $all(".tab-panel").forEach((p) => p.classList.remove("active"));
  $(`.tab-btn[data-tab="${tabName}"]`).classList.add("active");
  $(`#tab-${tabName}`).classList.add("active");
  if (tabName === "movies") loadMoviesGallery();
  if (tabName === "tv") loadTvGallery();
  if (tabName === "browse") loadBrowse();
  if (tabName === "notifications") { loadNotifications(); loadUpcomingReleases(); loadTrackedList(); loadNotificationHistory(); }
  if (tabName === "history") loadHistory();
  if (tabName === "settings") { loadStats(); loadInsights(); loadSettings(); loadBackgroundTaskStatus(); loadStorageStatus(); loadApiTokensList(); loadConfigHistory(); loadViewers(); }
}

function setupTabs() {
  $all(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => activateTab(btn.dataset.tab));
  });
}

// ---- Global search ----
let globalSearchDebounce = null;

function setupGlobalSearch() {
  const input = $("#global-search");
  const results = $("#global-search-results");
  if (!input || !results) return;

  input.addEventListener("input", () => {
    const query = input.value.trim();
    clearTimeout(globalSearchDebounce);
    if (query.length < 2) {
      results.classList.add("hidden");
      results.innerHTML = "";
      return;
    }
    globalSearchDebounce = setTimeout(() => runGlobalSearch(query), 250);
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".topbar-search")) results.classList.add("hidden");
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { results.classList.add("hidden"); input.blur(); }
  });
}

async function runGlobalSearch(query) {
  const results = $("#global-search-results");
  try {
    const data = await api(`/api/library/search?q=${encodeURIComponent(query)}`);
    renderGlobalSearchResults(data.items || []);
  } catch (e) {
    results.innerHTML = `<div class="global-search-empty">Search failed: ${e.message}</div>`;
    results.classList.remove("hidden");
  }
}

function renderGlobalSearchResults(rawItems) {
  const results = $("#global-search-results");
  // Collapse to one row per title -- a TV show search hit returns one
  // media_items row per episode, and the jump target is the show as a
  // whole (jumpToGlobalSearchResult filters the TV tab by title), so
  // showing every episode row separately would just repeat the same title.
  const seen = new Set();
  const items = rawItems.filter((item) => {
    const key = `${item.media_type}:${item.title}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (items.length === 0) {
    results.innerHTML = `<div class="global-search-empty">No matches</div>`;
    results.classList.remove("hidden");
    return;
  }
  results.innerHTML = items
    .slice(0, 20)
    .map(
      (item) => `
      <div class="global-search-result" data-id="${item.id}" data-type="${item.media_type}" data-title="${escapeAttr(item.title)}">
        <span class="gsr-type">${item.media_type === "movie" ? "Movie" : "TV"}</span>
        <span class="gsr-title">${escapeAttr(item.title)}</span>
        <span class="gsr-year">${item.year || ""}</span>
      </div>`
    )
    .join("");
  results.classList.remove("hidden");

  results.querySelectorAll(".global-search-result").forEach((row) => {
    row.addEventListener("click", () => jumpToGlobalSearchResult(row.dataset.type, row.dataset.title));
  });
}

function jumpToGlobalSearchResult(mediaType, title) {
  const tab = mediaType === "movie" ? "movies" : "tv";
  activateTab(tab);
  const searchInput = $(`#${tab}-search`);
  if (searchInput) {
    searchInput.value = title;
    searchInput.dispatchEvent(new Event("input"));
  }
  $("#global-search-results").classList.add("hidden");
  $("#global-search").value = "";
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

// Per-viewer watch state: state.activeViewerId (null = "All viewers", the
// original single-flag behavior every other feature -- filters, Continue
// Watching, CSV export -- still reads). When a viewer is active,
// toggleWatched writes to that viewer's own record instead of the shared
// media_items.watched flag, and effectiveWatched() reads it back the same
// way. See viewers/viewer_watched_items in database.py.
const ACTIVE_VIEWER_KEY = "media-manager:active-viewer-id";

function getActiveViewerId() {
  if (state.activeViewerId !== undefined) return state.activeViewerId;
  try {
    const stored = localStorage.getItem(ACTIVE_VIEWER_KEY);
    state.activeViewerId = stored ? Number(stored) : null;
  } catch (e) {
    state.activeViewerId = null;
  }
  return state.activeViewerId;
}

function setActiveViewerId(id) {
  state.activeViewerId = id;
  try {
    if (id == null) localStorage.removeItem(ACTIVE_VIEWER_KEY);
    else localStorage.setItem(ACTIVE_VIEWER_KEY, String(id));
  } catch (e) { /* private browsing / storage disabled -- selection just won't persist */ }
}

// Per-item watched state honoring the active viewer -- movies and
// individual TV episode rows carry an accurate per-viewer viewer_watched
// field from the API (see library.py::_to_out); aggregate "whole show/
// season watched" badges are deliberately left reading the global flag
// (see renderTvBody/groupEpisodesByShow), not recomputed per viewer.
function effectiveWatched(item) {
  return getActiveViewerId() != null ? !!item.viewer_watched : !!item.watched;
}

async function downloadMovieNote(itemId) {
  const status = $("#detail-note-status");
  status.textContent = "Generating…";
  try {
    const headers = {};
    const token = getStoredApiToken();
    if (token) headers["X-API-Token"] = token;
    const resp = await fetch(`/api/library/${itemId}/note`, { headers });
    if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text().catch(() => resp.statusText)}`);
    const filename = parseDownloadFilename(resp.headers.get("Content-Disposition"), "movie-note.md");
    const text = await resp.text();
    downloadTextFile(filename, text, "text/markdown;charset=utf-8;");
    status.textContent = "";
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
}

async function saveMovieNote(itemId) {
  const status = $("#detail-note-status");
  status.textContent = "Saving…";
  try {
    const data = await api(`/api/library/${itemId}/note/save`, { method: "POST" });
    status.textContent = `Saved to ${data.path}`;
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
}

async function downloadTvNote(tmdbId) {
  const status = $("#detail-note-status");
  status.textContent = "Generating…";
  try {
    const headers = {};
    const token = getStoredApiToken();
    if (token) headers["X-API-Token"] = token;
    const resp = await fetch(`/api/library/tv-shows/${tmdbId}/note`, { headers });
    if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text().catch(() => resp.statusText)}`);
    const filename = parseDownloadFilename(resp.headers.get("Content-Disposition"), "show-note.md");
    const text = await resp.text();
    downloadTextFile(filename, text, "text/markdown;charset=utf-8;");
    status.textContent = "";
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
}

async function saveTvNote(tmdbId) {
  const status = $("#detail-note-status");
  status.textContent = "Saving…";
  try {
    const data = await api(`/api/library/tv-shows/${tmdbId}/note/save`, { method: "POST" });
    status.textContent = `Saved to ${data.path}`;
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
}

async function toggleWatched(itemId, watched) {
  const viewerId = getActiveViewerId();
  if (viewerId != null) {
    return api(`/api/library/${itemId}/watched-by/${viewerId}`, {
      method: "POST",
      body: JSON.stringify({ watched }),
    });
  }
  return api(`/api/library/${itemId}/watched`, {
    method: "POST",
    body: JSON.stringify({ watched }),
  });
}

function wireWatchedToggles(container, items) {
  container.querySelectorAll(".watched-toggle input").forEach((input) => {
    input.addEventListener("click", (e) => e.stopPropagation());
    input.addEventListener("change", async () => {
      try {
        await toggleWatched(Number(input.dataset.id), input.checked);
        if (items) {
          const item = items.find((i) => i.id === Number(input.dataset.id));
          if (item) {
            if (getActiveViewerId() != null) item.viewer_watched = input.checked;
            else item.watched = input.checked;
          }
        }
        setWatchedBadge(input.closest(".gallery-card"), input.checked);
      } catch (e) {
        input.checked = !input.checked;
      }
    });
  });
}

const AUDIO_CHANNEL_LABELS = { 1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1" };

function mediaBadges(item) {
  // item is either a movie/episode row (hdr/audio_channels directly) or a
  // TV show grouping (aggregate across episodes -- season-to-season HDR
  // masters and audio mixes are common, so "any episode has it" is the
  // useful signal for a single show-level badge).
  const hdr = item.episodes ? item.episodes.some((e) => e.hdr) : item.hdr;
  const channels = item.episodes
    ? item.episodes.map((e) => e.audio_channels).find((c) => c)
    : item.audio_channels;
  let html = "";
  if (hdr) html += `<span class="badge badge-hdr" title="HDR video">HDR</span>`;
  if (channels) html += `<span class="badge badge-audio" title="${channels}-channel audio">${AUDIO_CHANNEL_LABELS[channels] || `${channels}ch`}</span>`;
  return html;
}

function setWatchedBadge(card, watched) {
  const badges = card ? card.querySelector(".gallery-badges") : null;
  if (!badges) return;
  const existing = badges.querySelector(".badge-ok");
  if (watched && !existing) {
    badges.insertAdjacentHTML("beforeend", `<span class="badge badge-ok" title="Watched">✓</span>`);
  } else if (!watched && existing) {
    existing.remove();
  }
}

async function checkBackfillProgress(mediaType, tabKey, reloadFn) {
  try {
    const status = await api(`/api/library/metadata-status?media_type=${mediaType}`);
    const stillTrying = status.pending - status.failed;
    let msg = "";
    if (stillTrying > 0) msg += ` — fetching metadata for ${stillTrying} more...`;
    if (status.failed > 0) msg += ` (${status.failed} could not be matched automatically, retrying periodically)`;
    if (msg) {
      const countEl = $(`#${tabKey}-count`);
      countEl.textContent += msg;
      if (status.failed > 0) {
        const btn = document.createElement("button");
        btn.textContent = "Retry Now";
        btn.className = "retry-failed-matches-btn";
        btn.addEventListener("click", async () => {
          await api(`/api/library/retry-failed-matches?media_type=${mediaType}`, { method: "POST" });
          reloadFn();
        });
        countEl.appendChild(btn);
      }
    }

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

function matchesResolution(item, resolutionFilter) {
  if (!resolutionFilter) return true;
  const wanted = resolutionFilter === "unknown" ? null : resolutionFilter;
  if (item.episodes) return item.episodes.some((e) => (e.resolution || null) === wanted);
  return (item.resolution || null) === wanted;
}

function matchesWatch(item, watchFilter) {
  if (!watchFilter) return true;
  return watchFilter === "watched" ? !!item.watched : !item.watched;
}

function matchesRating(item, ratingFilter) {
  if (!ratingFilter) return true;
  return (item.vote_average || 0) >= Number(ratingFilter);
}

function matchesAddedWithin(item, addedFilter) {
  if (!addedFilter) return true;
  if (!item.archived_at) return false;
  const cutoff = Date.now() - Number(addedFilter) * 86400000;
  return new Date(item.archived_at).getTime() >= cutoff;
}

function filterAndSort(items, opts) {
  const { query, sortMode, titleKey, filterMode, genreFilter, tagFilter, resolutionFilter, watchFilter, yearFilter, ratingFilter, addedFilter } = opts;
  let out = items;
  if (filterMode === "unmatched") out = out.filter((i) => i.tmdb_id == null);
  if (genreFilter) out = out.filter((i) => (i.genres || []).includes(genreFilter));
  if (tagFilter) out = out.filter((i) => (i.tags || []).includes(tagFilter));
  if (resolutionFilter) out = out.filter((i) => matchesResolution(i, resolutionFilter));
  if (watchFilter) out = out.filter((i) => matchesWatch(i, watchFilter));
  if (yearFilter) out = out.filter((i) => String(i.year) === yearFilter);
  if (ratingFilter) out = out.filter((i) => matchesRating(i, ratingFilter));
  if (addedFilter) out = out.filter((i) => matchesAddedWithin(i, addedFilter));
  if (query) {
    const q = query.toLowerCase();
    out = out.filter((i) => i[titleKey].toLowerCase().includes(q));
  }
  out = out.slice();
  if (sortMode === "title") out.sort((a, b) => a[titleKey].localeCompare(b[titleKey]));
  else if (sortMode === "year") out.sort((a, b) => (b.year || 0) - (a.year || 0));
  else if (sortMode === "rating") out.sort((a, b) => (b.vote_average || 0) - (a.vote_average || 0));
  return out;
}

// ---- Saved filter/sort presets (per-viewer, localStorage) ----
const FILTER_STATE_KEY_PREFIX = "media-manager:filters:";

function saveFilterState(prefix, controlIds) {
  const values = {};
  controlIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el) values[id] = el.value;
  });
  try {
    localStorage.setItem(FILTER_STATE_KEY_PREFIX + prefix, JSON.stringify(values));
  } catch (e) { /* private browsing / storage disabled -- filters just won't persist */ }
}

function restoreFilterState(prefix, controlIds) {
  let values = {};
  try {
    values = JSON.parse(localStorage.getItem(FILTER_STATE_KEY_PREFIX + prefix) || "{}");
  } catch (e) { /* corrupt or inaccessible storage -- fall back to defaults */ }
  controlIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el && values[id] !== undefined) el.value = values[id];
  });
  return values;
}

const MOVIE_FILTER_IDS = ["movies-search", "movies-sort", "movies-filter", "movies-resolution", "movies-watch", "movies-rating", "movies-added"];
const TV_FILTER_IDS = ["tv-search", "tv-sort", "tv-filter", "tv-resolution", "tv-watch", "tv-rating", "tv-added"];

function setupFilterPersistence() {
  const savedMovies = restoreFilterState("movies", MOVIE_FILTER_IDS);
  state.pendingGenreRestore.movies = savedMovies["movies-genre"] || null;
  state.pendingYearRestore.movies = savedMovies["movies-year"] || null;
  state.pendingTagRestore.movies = savedMovies["movies-tag"] || null;
  const savedTv = restoreFilterState("tv", TV_FILTER_IDS);
  state.pendingGenreRestore.tv = savedTv["tv-genre"] || null;
  state.pendingYearRestore.tv = savedTv["tv-year"] || null;
  state.pendingTagRestore.tv = savedTv["tv-tag"] || null;

  const movieIds = [...MOVIE_FILTER_IDS, "movies-genre", "movies-year", "movies-tag"];
  movieIds.forEach((id) => {
    const el = document.getElementById(id);
    el.addEventListener(id.endsWith("-search") ? "input" : "change", () => saveFilterState("movies", movieIds));
  });
  const tvIds = [...TV_FILTER_IDS, "tv-genre", "tv-year", "tv-tag"];
  tvIds.forEach((id) => {
    const el = document.getElementById(id);
    el.addEventListener(id.endsWith("-search") ? "input" : "change", () => saveFilterState("tv", tvIds));
  });
}

function distinctGenres(items) {
  return Array.from(new Set(items.flatMap((i) => i.genres || []))).sort();
}

function populateGenreOptions(selectEl, items, previousValue) {
  const genres = distinctGenres(items);
  selectEl.innerHTML = `<option value="">All genres</option>` + genres.map((g) => `<option value="${g}">${g}</option>`).join("");
  if (previousValue && genres.includes(previousValue)) selectEl.value = previousValue;
}

function distinctTags(items) {
  return Array.from(new Set(items.flatMap((i) => i.tags || []))).sort();
}

function populateTagOptions(selectEl, items, previousValue) {
  const tags = distinctTags(items);
  selectEl.innerHTML = `<option value="">All tags</option>` + tags.map((t) => `<option value="${escapeAttr(t)}">${escapeAttr(t)}</option>`).join("");
  if (previousValue && tags.includes(previousValue)) selectEl.value = previousValue;
}

function distinctYears(items) {
  return Array.from(new Set(items.map((i) => i.year).filter((y) => y != null))).sort((a, b) => b - a);
}

function populateYearOptions(selectEl, items, previousValue) {
  const years = distinctYears(items);
  selectEl.innerHTML = `<option value="">All years</option>` + years.map((y) => `<option value="${y}">${y}</option>`).join("");
  if (previousValue && years.includes(Number(previousValue))) selectEl.value = previousValue;
}

async function refreshMetadataBatch(ids, statusEl) {
  if (ids.length === 0) return;
  if (statusEl) statusEl.textContent = "Refreshing metadata…";
  try {
    const data = await api("/api/library/refresh-metadata", { method: "POST", body: JSON.stringify({ ids }) });
    if (statusEl) {
      statusEl.textContent = data.failed > 0
        ? `Refreshed ${data.updated}, ${data.failed} failed (unmatched or TMDB lookup failed).`
        : `Refreshed ${data.updated} item(s).`;
    }
  } catch (e) {
    if (statusEl) statusEl.textContent = `Error: ${e.message}`;
  }
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
  const genreFilter = $("#movies-genre").value;
  const tagFilter = $("#movies-tag").value;
  const resolutionFilter = $("#movies-resolution").value;
  const watchFilter = $("#movies-watch").value;
  const yearFilter = $("#movies-year").value;
  const ratingFilter = $("#movies-rating").value;
  const addedFilter = $("#movies-added").value;
  const items = filterAndSort(state.movieItems, { query, sortMode, titleKey: "title", filterMode, genreFilter, tagFilter, resolutionFilter, watchFilter, yearFilter, ratingFilter, addedFilter });
  const signature = JSON.stringify([query, sortMode, filterMode, genreFilter, tagFilter, resolutionFilter, watchFilter, yearFilter, ratingFilter, addedFilter]);
  const { visible, total, limitKey } = paginateGallery("movies", items, signature);

  $("#movies-count").textContent = `${state.movieItems.length} movie(s) archived` +
    (total !== state.movieItems.length ? ` (${total} shown)` : "");

  if (state.movieItems.length === 0) {
    gallery.innerHTML = `<p class="gallery-empty">No movies found — approve some from "Ready to Archive", or drop files into your movies archive folder and reload this tab.</p>`;
    return;
  }
  if (total === 0) {
    gallery.innerHTML = `<p class="gallery-empty">No movies match${query ? ` "${query}"` : ""}${filterMode === "unmatched" ? " (unmatched filter)" : ""}.</p>`;
    return;
  }
  gallery.innerHTML = visible.map((item, i) => `
    <div class="gallery-card" data-item-index="${i}">
      <input type="checkbox" class="gallery-select" data-select-id="${item.id}">
      <span class="gallery-index" title="Position ${i + 1} of ${total} in the current sort/filter">${i + 1}</span>
      <div class="gallery-badges" data-movie-badges="${item.tmdb_id ?? ""}">
        ${(item.tmdb_id == null && !item.manual_override) ? `<span class="badge badge-warn" title="Unidentified — no TMDB match yet">⚠</span>` : ""}
        ${effectiveWatched(item) ? `<span class="badge badge-ok" title="Watched">✓</span>` : ""}
        ${mediaBadges(item)}
      </div>
      ${posterMarkup(item.title, item.poster_path)}
      <div class="gallery-info">
        <div class="gallery-title" title="${item.title}">${item.title}</div>
        <div class="gallery-meta">
          <span>${item.year || ""}${item.vote_average ? ` · ★ ${item.vote_average.toFixed(1)}` : ""}</span>
          <label class="watched-toggle">
            <input type="checkbox" data-id="${item.id}" ${effectiveWatched(item) ? "checked" : ""}>
            Watched
          </label>
        </div>
      </div>
    </div>
  `).join("") + galleryLoadMoreMarkup(visible.length, total);
  wireWatchedToggles(gallery, visible);
  gallery.querySelectorAll(".gallery-select").forEach((cb) => {
    cb.addEventListener("click", (e) => e.stopPropagation());
  });
  gallery.querySelectorAll(".gallery-card").forEach((card, i) => {
    card.addEventListener("click", () => openDetailPane("movie", visible[i]));
  });
  wireGalleryLoadMore(gallery, limitKey, renderMoviesGallery);
  loadMovieGalleryBadges(visible);
}

// ---- CSV export of the currently filtered/sorted gallery view ----
function csvEscape(value) {
  const str = value === null || value === undefined ? "" : String(value);
  return /[",\n]/.test(str) ? `"${str.replace(/"/g, '""')}"` : str;
}

function rowsToCsv(header, rows) {
  return [header, ...rows].map((row) => row.map(csvEscape).join(",")).join("\r\n");
}

function downloadTextFile(filename, text, mimeType) {
  const blob = new Blob([text], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function downloadCsv(filename, csvText) {
  downloadTextFile(filename, csvText, "text/csv;charset=utf-8;");
}

// Prefers the RFC 5987 filename*=UTF-8''... part of a Content-Disposition
// header (the real name, e.g. with an en dash or accented character) over
// the plain filename="..." part (an ASCII-safe fallback the server sends
// alongside it -- see library.py::_content_disposition) -- otherwise a
// title with any non-ASCII character would download named with literal
// "?" placeholders instead of the real characters.
function parseDownloadFilename(disposition, fallback) {
  if (!disposition) return fallback;
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) {
    try { return decodeURIComponent(utf8Match[1]); } catch (e) { /* fall through to the plain name */ }
  }
  const plainMatch = disposition.match(/filename="([^"]+)"/);
  return plainMatch ? plainMatch[1] : fallback;
}

function currentMovieFilters() {
  return {
    query: $("#movies-search").value.trim(),
    sortMode: $("#movies-sort").value,
    titleKey: "title",
    filterMode: $("#movies-filter").value,
    genreFilter: $("#movies-genre").value,
    tagFilter: $("#movies-tag").value,
    resolutionFilter: $("#movies-resolution").value,
    watchFilter: $("#movies-watch").value,
    yearFilter: $("#movies-year").value,
    ratingFilter: $("#movies-rating").value,
    addedFilter: $("#movies-added").value,
  };
}

function currentTvFilters() {
  return {
    query: $("#tv-search").value.trim(),
    sortMode: $("#tv-sort").value,
    titleKey: "title",
    filterMode: $("#tv-filter").value,
    genreFilter: $("#tv-genre").value,
    tagFilter: $("#tv-tag").value,
    resolutionFilter: $("#tv-resolution").value,
    watchFilter: $("#tv-watch").value,
    yearFilter: $("#tv-year").value,
    ratingFilter: $("#tv-rating").value,
    addedFilter: $("#tv-added").value,
  };
}

function exportMoviesView() {
  const items = filterAndSort(state.movieItems, currentMovieFilters());
  const header = ["Title", "Year", "Rating", "Resolution", "Watched", "Genres", "Tags", "TMDB ID", "Path"];
  const rows = items.map((i) => [
    i.title, i.year ?? "", i.vote_average ?? "", i.resolution ?? "",
    i.watched ? "yes" : "no", (i.genres || []).join("; "), (i.tags || []).join("; "), i.tmdb_id ?? "", i.final_path ?? "",
  ]);
  downloadCsv(`movies-export-${new Date().toISOString().slice(0, 10)}.csv`, rowsToCsv(header, rows));
}

function exportTvView() {
  const shows = filterAndSort(groupEpisodesByShow(state.tvItems), currentTvFilters());
  const header = ["Show", "Year", "Rating", "Season", "Episode", "Episode Title", "Air Date", "Resolution", "Watched", "Genres", "Tags", "TMDB ID", "Path"];
  const rows = shows.flatMap((show) => show.episodes.map((ep) => [
    show.title, show.year ?? "", show.vote_average ?? "", ep.season_number ?? "", ep.episode_number ?? "",
    ep.episode_title ?? "", ep.air_date ?? "", ep.resolution ?? "", ep.watched ? "yes" : "no",
    (show.genres || []).join("; "), (show.tags || []).join("; "), show.tmdb_id ?? "", ep.final_path ?? "",
  ]));
  downloadCsv(`tv-export-${new Date().toISOString().slice(0, 10)}.csv`, rowsToCsv(header, rows));
}

function activeGalleryContext() {
  if ($("#tab-movies").classList.contains("active")) return { container: $("#movies-gallery") };
  if ($("#tab-tv").classList.contains("active")) return { container: $("#tv-gallery") };
  return null;
}

// Linear (not row/column-aware) card-to-card nav -- simple, and the number
// of columns per row changes with window width anyway, so a strictly
// grid-aware up/down wouldn't stay correct without recomputing layout.
function moveGalleryFocus(delta) {
  const ctx = activeGalleryContext();
  if (!ctx) return;
  const cards = Array.from(ctx.container.querySelectorAll(".gallery-card"));
  if (cards.length === 0) return;
  const current = cards.findIndex((c) => c.classList.contains("gallery-focused"));
  const next = Math.max(0, Math.min(cards.length - 1, current === -1 ? 0 : current + delta));
  cards.forEach((c) => c.classList.remove("gallery-focused"));
  cards[next].classList.add("gallery-focused");
  cards[next].scrollIntoView({ block: "nearest" });
}

function activateGalleryFocus() {
  const ctx = activeGalleryContext();
  if (!ctx) return false;
  const focused = ctx.container.querySelector(".gallery-focused");
  if (!focused) return false;
  focused.click();
  return true;
}

function galleryLoadMoreMarkup(shownCount, total) {
  return shownCount < total
    ? `<button class="gallery-load-more-btn">Load ${Math.min(GALLERY_PAGE_SIZE, total - shownCount)} More (${shownCount} of ${total} shown)</button>`
    : "";
}

function wireGalleryLoadMore(gallery, limitKey, rerender) {
  const btn = gallery.querySelector(".gallery-load-more-btn");
  if (!btn) return;
  btn.addEventListener("click", () => {
    state[limitKey] += GALLERY_PAGE_SIZE;
    rerender();
  });
}

// Best-effort "related title available" badge per movie card (sequel/prequel
// in the same TMDB collection that isn't in the library yet) -- mirrors
// loadTvGalleryBadges. One TMDB lookup per movie, cached in
// state.movieStatusCache so repeat renders (search/sort/filter) don't refetch.
function loadMovieGalleryBadges(items) {
  const archivedTmdbIds = new Set(state.movieItems.map((i) => i.tmdb_id).filter((id) => id != null));
  items.forEach((item) => {
    if (item.tmdb_id == null) return;
    getMovieStatus(item.tmdb_id).then((status) => {
      const info = computeMovieStatusInfo(status, archivedTmdbIds);
      if (!info || !info.hasGap) return;
      const container = $(`.gallery-badges[data-movie-badges="${item.tmdb_id}"]`);
      if (!container || container.querySelector(".badge-new")) return;
      container.insertAdjacentHTML("beforeend", `<span class="badge badge-new" title="${info.gapMessage}">🆕</span>`);
    });
  });
}

async function loadMoviesGallery() {
  const gallery = $("#movies-gallery");
  gallery.innerHTML = "Loading...";
  try {
    const viewerId = getActiveViewerId();
    const data = await api(`/api/library/movies${viewerId != null ? `?viewer_id=${viewerId}` : ""}`);
    state.movieItems = data.items;
    const previousGenre = state.pendingGenreRestore.movies ?? $("#movies-genre").value;
    state.pendingGenreRestore.movies = null;
    populateGenreOptions($("#movies-genre"), state.movieItems, previousGenre);
    const previousTag = state.pendingTagRestore.movies ?? $("#movies-tag").value;
    state.pendingTagRestore.movies = null;
    populateTagOptions($("#movies-tag"), state.movieItems, previousTag);
    const previousYear = state.pendingYearRestore.movies ?? $("#movies-year").value;
    state.pendingYearRestore.movies = null;
    populateYearOptions($("#movies-year"), state.movieItems, previousYear);
    checkBackfillProgress("movie", "movies", loadMoviesGallery);
    renderMoviesGallery();
  } catch (e) {
    gallery.innerHTML = `<p class="gallery-empty">Error: ${e.message}</p>`;
  }
}

const TV_SHOW_STATUSES = [
  { value: "watching", label: "Watching" },
  { value: "running", label: "Running" },
  { value: "season_done", label: "Season Done" },
  { value: "cancelled", label: "Cancelled" },
  { value: "ended", label: "Ended" },
];

function tvShowStatusLabel(status) {
  return TV_SHOW_STATUSES.find((s) => s.value === status)?.label || status;
}

// Real broadcast status from TMDB ("Ended", "Canceled", "Returning Series", ...)
// or TVmaze ("Ended", "Running", "To Be Determined", ...) mapped onto our own
// manual TvShowStatus vocabulary -- only the two terminal states have an
// unambiguous match; "Returning Series"/"Running"/etc don't map to "watching"
// or "running" since those are about *your* progress, not the show's.
function mapApiStatusToManual(apiStatusLabel) {
  if (!apiStatusLabel) return null;
  const lower = apiStatusLabel.toLowerCase();
  if (lower.includes("cancel")) return "cancelled";
  if (lower.includes("end")) return "ended";
  return null;
}

async function setTvShowStatus(tmdbId, status) {
  return api(`/api/library/tv-shows/${tmdbId}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
}

// orphanShows: tracked shows with zero episode files left on disk (see
// TvShowSummaryOut / GET /api/library/tv's orphaned_shows) -- rendered as
// show cards with an empty episodes array so a show stays visible (with its
// user-set status) even after every episode was deleted from disk.
function groupEpisodesByShow(items, orphanShows = state.tvOrphanShows || []) {
  const shows = new Map();
  for (const item of items) {
    const key = item.title;
    if (!shows.has(key)) {
      shows.set(key, {
        title: item.title, poster_path: item.poster_path, tmdb_id: item.tmdb_id, overview: item.overview,
        manual_override: item.manual_override, vote_average: item.vote_average, genres: item.genres, tags: item.tags, year: item.year, episodes: [],
        show_status: item.show_status,
      });
    }
    shows.get(key).episodes.push(item);
  }
  for (const show of shows.values()) {
    show.episodes.sort((a, b) => (a.season_number - b.season_number) || (a.episode_number - b.episode_number));
    show.watched = show.episodes.length > 0 && show.episodes.every((e) => e.watched);
    show.archived_at = show.episodes.reduce((min, e) => (e.archived_at && (!min || e.archived_at < min)) ? e.archived_at : min, null);
  }
  for (const orphan of orphanShows) {
    if (shows.has(orphan.title)) continue;
    shows.set(orphan.title, {
      title: orphan.title, poster_path: orphan.poster_path, tmdb_id: orphan.tmdb_id, overview: orphan.overview,
      manual_override: false, vote_average: null, genres: orphan.genres, tags: [], year: null, episodes: [],
      show_status: orphan.status, watched: false, archived_at: null, noFilesOnDisk: true,
    });
  }
  return Array.from(shows.values());
}

// A show is "in progress" (not "not started", not "finished") when its
// sorted episode list has at least one watched episode before the first
// unwatched one -- episodes are pre-sorted by season/episode in
// groupEpisodesByShow, so the first unwatched entry is the true "next up"
// regardless of watch order the user actually clicked them in.
function computeUpNext(shows) {
  return shows
    .map((show) => {
      const nextIndex = show.episodes.findIndex((e) => !e.watched);
      if (nextIndex <= 0) return null; // -1: fully watched; 0: not started yet
      return { show, nextEpisode: show.episodes[nextIndex], watchedCount: nextIndex, totalCount: show.episodes.length };
    })
    .filter(Boolean);
}

function renderContinueWatching(allShows) {
  const row = $("#continue-watching-row");
  const cards = $("#continue-watching-cards");
  if (!row || !cards) return;

  const upNext = computeUpNext(allShows);
  if (upNext.length === 0) {
    row.classList.add("hidden");
    cards.innerHTML = "";
    return;
  }
  row.classList.remove("hidden");
  cards.innerHTML = upNext.map(({ show, nextEpisode, watchedCount, totalCount }, i) => `
    <div class="continue-watching-card" data-up-next-index="${i}">
      ${posterMarkup(show.title, show.poster_path)}
      <div class="continue-watching-info">
        <div class="gallery-title" title="${show.title}">${show.title}</div>
        <div class="gallery-meta">S${nextEpisode.season_number}E${nextEpisode.episode_number}${nextEpisode.episode_title ? ` — ${nextEpisode.episode_title}` : ""}</div>
        <div class="hint">${watchedCount} of ${totalCount} watched${nextEpisode.air_date ? ` · aired ${nextEpisode.air_date}` : ""}</div>
      </div>
    </div>
  `).join("");
  cards.querySelectorAll(".continue-watching-card").forEach((card, i) => {
    card.addEventListener("click", () => {
      const { show, nextEpisode } = upNext[i];
      openDetailPane("tv", show);
      state.detailPane.selectedSeason = nextEpisode.season_number;
      renderTvBody();
    });
  });
}

// "Recommended for You" row shared by Movies and TV tabs -- a <details>
// collapsed by default, since GET /api/library/recommendations does a TMDB
// "similar" lookup per recently-archived title (pooled + deduped server
// side, but still N requests) and most sessions never open the row, so
// fetching it eagerly on every gallery load wasted TMDB calls for nothing.
// Wired once at startup (see DOMContentLoaded); fetches on first expand
// and again on each subsequent expand, since the underlying library
// (and therefore the pool of "recently archived" seed titles) can have
// changed since the last time it was open.
function wireRecommendationsToggle(mediaType, rowId, cardsId) {
  const row = $(`#${rowId}`);
  if (!row) return;
  row.addEventListener("toggle", () => {
    if (row.open) loadRecommendations(mediaType, rowId, cardsId);
  });
}

async function loadRecommendations(mediaType, rowId, cardsId) {
  const row = $(`#${rowId}`);
  const cards = $(`#${cardsId}`);
  if (!row || !cards || row.dataset.loading === "1") return;
  row.dataset.loading = "1";
  cards.innerHTML = `<p class="hint">Loading…</p>`;
  try {
    const data = await api(`/api/library/recommendations?media_type=${mediaType}`);
    if (!data.tmdb_configured) {
      cards.innerHTML = `<p class="hint">Recommendations need a TMDB key -- configure one in Settings.</p>`;
      return;
    }
    if (data.items.length === 0) {
      cards.innerHTML = `<p class="hint">No recommendations right now.</p>`;
      return;
    }
    cards.innerHTML = data.items.map((item) => `
      <div class="continue-watching-card">
        ${posterMarkup(item.title, item.poster_path)}
        <div class="continue-watching-info">
          <div class="gallery-title" title="${escapeAttr(item.title)}">${escapeAttr(item.title)}</div>
          <div class="hint">${item.year || ""}</div>
          <button class="recommendation-track-btn" data-tmdb-id="${item.tmdb_id}" data-title="${escapeAttr(item.title)}">+ Track</button>
        </div>
      </div>
    `).join("");
    cards.querySelectorAll(".recommendation-track-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "Tracking…";
        try {
          await api("/api/tracker/add", {
            method: "POST",
            body: JSON.stringify({ tmdb_id: Number(btn.dataset.tmdbId), media_type: mediaType, title: btn.dataset.title }),
          });
          btn.textContent = "Tracked ✓";
        } catch (e) {
          btn.disabled = false;
          btn.textContent = "+ Track";
        }
      });
    });
  } catch (e) {
    cards.innerHTML = `<p class="hint">Error: ${e.message}</p>`;
  } finally {
    row.dataset.loading = "0";
  }
}

function renderTvGallery() {
  const gallery = $("#tv-gallery");
  const query = $("#tv-search").value.trim();
  const sortMode = $("#tv-sort").value;
  const filterMode = $("#tv-filter").value;
  const genreFilter = $("#tv-genre").value;
  const tagFilter = $("#tv-tag").value;
  const resolutionFilter = $("#tv-resolution").value;
  const watchFilter = $("#tv-watch").value;
  const yearFilter = $("#tv-year").value;
  const ratingFilter = $("#tv-rating").value;
  const addedFilter = $("#tv-added").value;
  const allShows = groupEpisodesByShow(state.tvItems);
  renderContinueWatching(allShows);
  const shows = filterAndSort(allShows, { query, sortMode, titleKey: "title", filterMode, genreFilter, tagFilter, resolutionFilter, watchFilter, yearFilter, ratingFilter, addedFilter });
  const signature = JSON.stringify([query, sortMode, filterMode, genreFilter, tagFilter, resolutionFilter, watchFilter, yearFilter, ratingFilter, addedFilter]);
  const { visible, total, limitKey } = paginateGallery("tv", shows, signature);

  $("#tv-count").textContent = `${state.tvItems.length} episode(s) across ${allShows.length} show(s)` +
    (total !== allShows.length ? ` (${total} shown)` : "");

  if (state.tvItems.length === 0 && allShows.length === 0) {
    gallery.innerHTML = `<p class="gallery-empty">No TV episodes found — approve some from "Ready to Archive", or drop files into your TV archive folder and reload this tab.</p>`;
    return;
  }
  if (total === 0) {
    gallery.innerHTML = `<p class="gallery-empty">No shows match${query ? ` "${query}"` : ""}${filterMode === "unmatched" ? " (unmatched filter)" : ""}.</p>`;
    return;
  }
  gallery.innerHTML = visible.map((show, i) => `
    <div class="gallery-card" data-show-index="${i}">
      <input type="checkbox" class="gallery-select" data-select-title="${show.title}">
      <span class="gallery-index" title="Position ${i + 1} of ${total} in the current sort/filter">${i + 1}</span>
      <div class="gallery-badges" data-tv-badges="${show.tmdb_id ?? ""}">
        ${(show.tmdb_id == null && !show.manual_override) ? `<span class="badge badge-warn" title="Unidentified — no TMDB match yet">⚠</span>` : ""}
        ${show.watched ? `<span class="badge badge-ok" title="All episodes watched">✓</span>` : ""}
        ${mediaBadges(show)}
      </div>
      ${posterMarkup(show.title, show.poster_path)}
      <div class="gallery-info">
        <div class="gallery-title" title="${show.title}">${show.title}</div>
        <div class="gallery-meta">
          <span>${show.episodes.length} episode(s)</span>
          ${show.show_status ? `<span class="show-status-pill show-status-${show.show_status}">${tvShowStatusLabel(show.show_status)}</span>` : ""}
        </div>
        ${show.noFilesOnDisk ? `<div class="hint">Removed from disk -- still tracked</div>` : ""}
      </div>
    </div>
  `).join("") + galleryLoadMoreMarkup(visible.length, total);
  gallery.querySelectorAll(".gallery-select").forEach((cb) => {
    cb.addEventListener("click", (e) => e.stopPropagation());
  });
  gallery.querySelectorAll(".gallery-card").forEach((card, i) => {
    card.addEventListener("click", () => openDetailPane("tv", visible[i]));
  });
  wireGalleryLoadMore(gallery, limitKey, renderTvGallery);
  loadTvGalleryBadges(visible);
}

// Best-effort "new season/episodes available" badge per show card, filled in
// after the initial paint -- one TMDB lookup per show, cached in
// state.tvStatusCache so repeat renders (search/sort/filter) don't refetch.
function loadTvGalleryBadges(shows) {
  shows.forEach((show) => {
    if (show.tmdb_id == null) return;
    getTvStatus(show.tmdb_id).then((status) => {
      const info = computeTvStatusInfo(status, show.episodes);
      if (!info || !info.hasGap) return;
      const container = $(`.gallery-badges[data-tv-badges="${show.tmdb_id}"]`);
      if (!container || container.querySelector(".badge-new")) return;
      container.insertAdjacentHTML("beforeend", `<span class="badge badge-new" title="${info.gapMessage}">🆕</span>`);
    });
  });
}

async function loadTvGallery() {
  const gallery = $("#tv-gallery");
  gallery.innerHTML = "Loading...";
  try {
    const viewerId = getActiveViewerId();
    const data = await api(`/api/library/tv${viewerId != null ? `?viewer_id=${viewerId}` : ""}`);
    state.tvItems = data.items;
    state.tvOrphanShows = data.orphaned_shows || [];
    const previousGenre = state.pendingGenreRestore.tv ?? $("#tv-genre").value;
    state.pendingGenreRestore.tv = null;
    populateGenreOptions($("#tv-genre"), state.tvItems, previousGenre);
    const previousTag = state.pendingTagRestore.tv ?? $("#tv-tag").value;
    state.pendingTagRestore.tv = null;
    populateTagOptions($("#tv-tag"), state.tvItems, previousTag);
    const previousYear = state.pendingYearRestore.tv ?? $("#tv-year").value;
    state.pendingYearRestore.tv = null;
    populateYearOptions($("#tv-year"), state.tvItems, previousYear);
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
      <div id="detail-movie-status"></div>
      ${(item.tmdb_id == null && !item.manual_override) ? `<p class="unidentified-badge">⚠ Unidentified — no TMDB match yet</p>` : ""}
      ${posterMarkupLarge(item.title, item.poster_path)}
      <div class="detail-title">${item.title}</div>
      <div class="detail-year">${item.year || ""}</div>
      <label class="watched-toggle">
        <input type="checkbox" id="detail-watched-toggle" data-id="${item.id}" ${effectiveWatched(item) ? "checked" : ""}>
        Watched${getActiveViewerId() != null ? ` (${escapeAttr(state.viewers?.find((v) => v.id === getActiveViewerId())?.name || "viewer")})` : ""}
      </label>
      ${detailTagsMarkup(item.tags)}
      <div id="detail-ratings" class="detail-ratings"></div>
      <div id="detail-trailer" class="detail-trailer"></div>
      <p class="detail-overview">${item.overview || "No overview available."}</p>
      <div id="detail-more-info"></div>
      <div class="detail-file-info">
        <div class="detail-file-row"><span>File</span><span class="detail-file-value" title="${item.file_name || ""}">${item.file_name || "—"}</span></div>
        <div class="detail-file-row"><span>Size</span><span>${item.size_bytes != null ? formatBytes(item.size_bytes) : "—"}</span></div>
        <div class="detail-file-row"><span>Path</span><span class="detail-file-value" title="${item.final_path || ""}">${item.final_path || "—"}</span></div>
        <div id="detail-file-extra" class="hint">Loading file details…</div>
      </div>
      <div class="detail-note-actions">
        <button id="detail-note-download-btn">Download Note (.md)</button>
        <button id="detail-note-save-btn">Save Note to Movie Folder</button>
        <span id="detail-note-status" class="hint"></span>
      </div>
      ${detailFixMarkup()}
    `;
    $("#detail-note-download-btn").addEventListener("click", () => downloadMovieNote(item.id));
    $("#detail-note-save-btn").addEventListener("click", () => saveMovieNote(item.id));
    $("#detail-watched-toggle").addEventListener("change", async (e) => {
      try {
        await toggleWatched(item.id, e.target.checked);
        if (getActiveViewerId() != null) item.viewer_watched = e.target.checked;
        else item.watched = e.target.checked;
        renderMoviesGallery(); // syncs the same checkbox shown on the gallery card
      } catch (err) {
        e.target.checked = !e.target.checked;
      }
    });
    loadRatings(item.id);
    loadTrailer(item.id);
    loadMoreInfo(item.id);
    loadFileInfo(item.id, "#detail-file-extra");
    if (item.tmdb_id != null) loadMovieStatus(item.tmdb_id);
    wireDetailTags([item.id], (tags) => { item.tags = tags; renderMoviesGallery(); });
  } else {
    const show = pane.data;
    const hasEpisodes = show.episodes.length > 0;
    content.innerHTML = `
      <div id="detail-tv-status"></div>
      ${(show.tmdb_id == null && !show.manual_override) ? `<p class="unidentified-badge">⚠ Unidentified — no TMDB match yet</p>` : ""}
      ${show.noFilesOnDisk ? `<p class="unidentified-badge">🗑 Removed from disk — still tracked</p>` : ""}
      ${posterMarkupLarge(show.title, show.poster_path)}
      <div class="detail-title">${show.title}</div>
      <div class="detail-year">${show.episodes.length} episode(s)</div>
      ${show.tmdb_id != null ? `
        <label class="show-status-select-label">
          Status
          <select id="detail-show-status-select">
            ${TV_SHOW_STATUSES.map((s) => `<option value="${s.value}" ${s.value === show.show_status ? "selected" : ""}>${s.label}</option>`).join("")}
          </select>
          <span id="detail-api-status-pill"></span>
        </label>
      ` : ""}
      ${hasEpisodes ? detailTagsMarkup(show.tags) : ""}
      <div id="detail-ratings" class="detail-ratings"></div>
      <div id="detail-trailer" class="detail-trailer"></div>
      <p class="detail-overview">${show.overview || "No overview available."}</p>
      <div id="detail-more-info"></div>
      <div id="detail-tv-body"></div>
      ${show.tmdb_id != null ? `
        <div class="detail-note-actions">
          <button id="detail-note-download-btn">Download Note (.md)</button>
          <button id="detail-note-save-btn">Save Note to Show Folder</button>
          <span id="detail-note-status" class="hint"></span>
        </div>
      ` : `<p class="hint">Notes need a TMDB match first.</p>`}
      ${detailFixMarkup()}
    `;
    if (show.tmdb_id != null) {
      $("#detail-note-download-btn").addEventListener("click", () => downloadTvNote(show.tmdb_id));
      $("#detail-note-save-btn").addEventListener("click", () => saveTvNote(show.tmdb_id));
      $("#detail-show-status-select").addEventListener("change", async (e) => {
        const select = e.target;
        const previous = show.show_status;
        select.disabled = true;
        try {
          await setTvShowStatus(show.tmdb_id, select.value);
          show.show_status = select.value;
          renderTvGallery();
        } catch (err) {
          select.value = previous;
        } finally {
          select.disabled = false;
        }
      });
    }
    renderTvBody();
    if (hasEpisodes) {
      loadRatings(show.episodes[0].id); // ratings are show-level; episodes are pre-sorted, so [0] is stable
      loadTrailer(show.episodes[0].id);
      loadMoreInfo(show.episodes[0].id);
      wireDetailTags(show.episodes.map((e) => e.id), (tags) => {
        show.tags = tags;
        show.episodes.forEach((e) => { e.tags = tags; });
        renderTvGallery();
      });
    }
    if (show.tmdb_id != null) loadTvStatus(show);
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

  if (show.episodes.length === 0) {
    container.innerHTML = `<p class="hint">No files on disk for this show -- status is still tracked above.</p>`;
    return;
  }

  const seasons = Array.from(new Set(show.episodes.map((e) => e.season_number))).sort((a, b) => a - b);
  if (pane.selectedSeason == null || !seasons.includes(pane.selectedSeason)) {
    pane.selectedSeason = seasons[seasons.length - 1];
  }
  if (pane.nameMode == null) pane.nameMode = "episode";

  const seasonEpisodes = show.episodes.filter((e) => e.season_number === pane.selectedSeason);
  const allWatched = seasonEpisodes.length > 0 && seasonEpisodes.every((e) => e.watched);
  const hasEpisodeNames = show.episodes.some((e) => e.episode_title);
  // Every episode in a season strictly before the one currently selected --
  // lets someone who's picking up a show mid-way mark everything they're
  // already past as watched without also touching the season they're on.
  const earlierEpisodes = show.episodes.filter((e) => e.season_number < pane.selectedSeason);
  const earlierAllWatched = earlierEpisodes.length > 0 && earlierEpisodes.every((e) => e.watched);
  // Recomputed on every render (not just at groupEpisodesByShow time) so it
  // stays correct after a season- or episode-level toggle changes it.
  show.watched = show.episodes.every((e) => e.watched);

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
      ${earlierEpisodes.length > 0 ? `
        <button id="detail-earlier-watched-btn">${earlierAllWatched ? "Mark Earlier Seasons Unwatched" : "Mark Earlier Seasons Watched"}</button>
      ` : ""}
      <button id="detail-show-watched-btn">${show.watched ? "Mark Show Unwatched" : "Mark Show Watched"}</button>
    </div>
    <div class="detail-episodes">
      ${seasonEpisodes.map((ep) => `
        <div class="detail-episode-row">
          <span>S${String(ep.season_number).padStart(2, "0")}E${String(ep.episode_number).padStart(2, "0")}</span>
          <span class="detail-ep-file hint" title="${ep.file_name || ""}">${(pane.nameMode === "episode" && ep.episode_title) ? ep.episode_title : (ep.file_name || "")}${ep.air_date ? ` · ${ep.air_date}` : ""}${ep.size_bytes != null ? ` · ${formatBytes(ep.size_bytes)}` : ""}</span>
          <label class="watched-toggle">
            <input type="checkbox" class="detail-ep-watched" data-id="${ep.id}" ${effectiveWatched(ep) ? "checked" : ""}>
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
      renderTvGallery(); // keeps the gallery card's "all watched" badge in sync
    } catch (err) {
      btn.disabled = false;
    }
  });

  const earlierBtn = $("#detail-earlier-watched-btn");
  if (earlierBtn) {
    earlierBtn.addEventListener("click", async (e) => {
      const btn = e.target;
      const newWatched = !earlierAllWatched;
      btn.disabled = true;
      try {
        await api("/api/library/watched-batch", {
          method: "POST",
          body: JSON.stringify({ ids: earlierEpisodes.map((ep) => ep.id), watched: newWatched }),
        });
        earlierEpisodes.forEach((ep) => { ep.watched = newWatched; });
        renderTvBody();
        renderTvGallery();
      } catch (err) {
        btn.disabled = false;
      }
    });
  }

  $("#detail-show-watched-btn").addEventListener("click", async (e) => {
    const btn = e.target;
    const newWatched = !show.watched;
    btn.disabled = true;
    try {
      await api("/api/library/watched-batch", {
        method: "POST",
        body: JSON.stringify({ ids: show.episodes.map((ep) => ep.id), watched: newWatched }),
      });
      show.episodes.forEach((ep) => { ep.watched = newWatched; });
      renderTvBody();
      renderTvGallery();
    } catch (err) {
      btn.disabled = false;
    }
  });

  container.querySelectorAll(".detail-ep-watched").forEach((input) => {
    input.addEventListener("change", async () => {
      try {
        await toggleWatched(Number(input.dataset.id), input.checked);
        const ep = show.episodes.find((e) => e.id === Number(input.dataset.id));
        if (ep) {
          if (getActiveViewerId() != null) ep.viewer_watched = input.checked;
          else ep.watched = input.checked;
        }
        renderTvBody(); // keeps the "Mark Season Watched" button label in sync
        renderTvGallery(); // keeps the gallery card's "all watched" badge in sync
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

// GET /api/library/tv-status, memoized in state.tvStatusCache so the gallery
// badges and the detail pane banner (and repeat renders of either) don't
// keep re-issuing the same TMDB lookup for a show already checked this session.
async function getTvStatus(tmdbId) {
  if (tmdbId in state.tvStatusCache) return state.tvStatusCache[tmdbId];
  try {
    const status = await api(`/api/library/tv-status?tmdb_id=${tmdbId}`);
    state.tvStatusCache[tmdbId] = status;
    return status;
  } catch (e) {
    state.tvStatusCache[tmdbId] = null;
    return null;
  }
}

// Pure comparison of a TvStatusOut against what's actually archived --
// shared by the gallery "new season" badge and the detail pane banner so
// the two never disagree about what counts as "behind".
function computeTvStatusInfo(status, episodes) {
  if (!status || !status.data_available) return null;
  let gapMessage = null;
  if (status.latest_known_season != null) {
    const localMaxSeason = Math.max(...episodes.map((e) => e.season_number));
    const localEpisodeCountInMaxSeason = episodes.filter((e) => e.season_number === localMaxSeason).length;
    if (status.latest_known_season > localMaxSeason) {
      gapMessage = `Season ${status.latest_known_season} is out — you have up to season ${localMaxSeason}.`;
    } else if (
      status.latest_known_season === localMaxSeason &&
      status.latest_season_episode_count != null &&
      status.latest_season_episode_count > localEpisodeCountInMaxSeason
    ) {
      gapMessage = `Season ${localMaxSeason} has ${status.latest_season_episode_count} episode(s) — you have ${localEpisodeCountInMaxSeason}.`;
    }
  }

  return {
    hasGap: gapMessage != null,
    gapMessage,
    totalArchived: episodes.length,
    totalEpisodes: status.total_episodes,
    statusLabel: status.status,
    network: status.network,
    nextEpisodeAirDate: status.next_episode_air_date,
    nextEpisodeCode: status.next_episode_code,
  };
}

// Per-season gap detector: unlike computeTvStatusInfo (which only flags the
// latest season), this diffs every season TMDB knows about against what's
// actually archived -- catches a hole left in an already-"complete" earlier
// season (e.g. S1E1-E2,E4 archived, E3 never downloaded), which a
// max-season/count-only check would never surface.
function computeMissingEpisodes(status, episodes) {
  if (!status || !status.data_available || !status.seasons || status.seasons.length === 0) return [];
  const ownedBySeason = new Map();
  episodes.forEach((e) => ownedBySeason.set(e.season_number, (ownedBySeason.get(e.season_number) || 0) + 1));

  return status.seasons
    .map((s) => ({
      season: s.season_number,
      owned: ownedBySeason.get(s.season_number) || 0,
      expected: s.episode_count,
    }))
    .filter((s) => s.owned > 0 && s.expected > s.owned) // only seasons already started -- an unstarted season is "not archived yet", not a gap
    .map((s) => ({ ...s, missing: s.expected - s.owned }));
}

async function loadTvStatus(show) {
  const el = $("#detail-tv-status");
  if (!el) return;
  const status = await getTvStatus(show.tmdb_id);
  const info = computeTvStatusInfo(status, show.episodes);
  const missingEpisodes = computeMissingEpisodes(status, show.episodes);
  renderApiStatusPill(show, info);
  if (!info && missingEpisodes.length === 0) {
    el.innerHTML = "";
    return;
  }
  let bannerHtml = "";
  if (info) {
    const fraction = info.totalEpisodes != null
      ? `${info.totalArchived} of ${info.totalEpisodes} episodes archived`
      : `${info.totalArchived} episode(s) archived`;
    const parts = info.hasGap ? [info.gapMessage, fraction] : [fraction];
    const cls = info.hasGap ? "status-banner" : "status-banner status-banner-ok";
    const icon = info.hasGap ? "📺" : "✅";
    const statusBits = [info.statusLabel, info.network].filter(Boolean).join(" · ");
    bannerHtml = `<div class="${cls}">${icon} ${parts.join(" · ")}${statusBits ? ` <span class="hint">(${statusBits})</span>` : ""}</div>`;
    if (info.nextEpisodeAirDate) {
      bannerHtml += `<div class="status-banner hint">📅 Next: ${info.nextEpisodeCode ? `${info.nextEpisodeCode} — ` : ""}${info.nextEpisodeAirDate}</div>`;
    }
  }
  const missingHtml = missingEpisodes.length > 0
    ? `<div class="status-banner missing-episodes">🕳️ Missing episodes: ${missingEpisodes.map((s) => `Season ${s.season} (${s.missing} missing)`).join(", ")}</div>`
    : "";
  el.innerHTML = bannerHtml + missingHtml;
}

// Pill next to the manual Status dropdown showing the real broadcast status
// (TMDB/TVmaze), separate from and never silently overwriting the manual
// TvShowStatus dropdown -- clickable only when it maps to a terminal state
// (ended/cancelled) that the dropdown doesn't already reflect, so setting it
// is always one deliberate click, never automatic.
function renderApiStatusPill(show, info) {
  const pill = $("#detail-api-status-pill");
  if (!pill) return;
  if (!info || !info.statusLabel) {
    pill.innerHTML = "";
    return;
  }
  const mapped = mapApiStatusToManual(info.statusLabel);
  if (mapped && mapped !== show.show_status) {
    pill.innerHTML = `<button type="button" id="detail-api-status-apply" class="show-status-pill show-status-${mapped}" title="Set show status to match">${info.statusLabel} — set?</button>`;
    $("#detail-api-status-apply").addEventListener("click", async (e) => {
      const btn = e.target;
      btn.disabled = true;
      try {
        await setTvShowStatus(show.tmdb_id, mapped);
        show.show_status = mapped;
        const select = $("#detail-show-status-select");
        if (select) select.value = mapped;
        renderApiStatusPill(show, info);
        renderTvGallery();
      } catch (err) {
        btn.disabled = false;
      }
    });
  } else {
    pill.innerHTML = `<span class="show-status-pill">${info.statusLabel}</span>`;
  }
}

// GET /api/library/movie-status, memoized like getTvStatus.
async function getMovieStatus(tmdbId) {
  if (tmdbId in state.movieStatusCache) return state.movieStatusCache[tmdbId];
  try {
    const status = await api(`/api/library/movie-status?tmdb_id=${tmdbId}`);
    state.movieStatusCache[tmdbId] = status;
    return status;
  } catch (e) {
    state.movieStatusCache[tmdbId] = null;
    return null;
  }
}

// Movie counterpart of computeTvStatusInfo: "gap" here means another movie
// in the same TMDB collection that isn't archived yet. No collection at all
// (the common case) returns null, same as a TV show with no TMDB match --
// no banner rather than a misleadingly empty one.
function computeMovieStatusInfo(status, archivedTmdbIds) {
  if (!status || !status.data_available || status.collection_id == null) return null;
  const missing = status.related.filter((r) => r.tmdb_id != null && !archivedTmdbIds.has(r.tmdb_id));
  const hasGap = missing.length > 0;
  const gapMessage = hasGap
    ? (missing.length === 1
        ? `Related title available: ${missing[0].title}${missing[0].year ? ` (${missing[0].year})` : ""}.`
        : `${missing.length} related titles available: ${missing.map((m) => m.title).join(", ")}.`)
    : null;
  return {
    hasGap,
    gapMessage,
    collectionSize: status.related.length + 1, // + the movie itself
    archivedInCollection: status.related.length + 1 - missing.length,
  };
}

async function loadMovieStatus(tmdbId) {
  const el = $("#detail-movie-status");
  if (!el) return;
  const archivedTmdbIds = new Set(state.movieItems.map((i) => i.tmdb_id).filter((id) => id != null));
  const status = await getMovieStatus(tmdbId);
  const info = computeMovieStatusInfo(status, archivedTmdbIds);
  if (!info) {
    el.innerHTML = "";
    return;
  }
  const fraction = `${info.archivedInCollection} of ${info.collectionSize} collection titles archived`;
  const parts = info.hasGap ? [info.gapMessage, fraction] : [fraction];
  const cls = info.hasGap ? "status-banner" : "status-banner status-banner-ok";
  const icon = info.hasGap ? "🎬" : "✅";
  const trackBtn = info.hasGap ? `<button id="detail-track-missing-btn">Track Missing Titles</button>` : "";
  el.innerHTML = `<div class="${cls}">${icon} ${parts.join(" · ")}</div>${trackBtn}`;
  if (info.hasGap) {
    $("#detail-track-missing-btn").addEventListener("click", async (e) => {
      const btn = e.target;
      btn.disabled = true;
      btn.textContent = "Tracking...";
      const missing = status.related.filter((r) => r.tmdb_id != null && !archivedTmdbIds.has(r.tmdb_id));
      for (const m of missing) {
        try {
          await api("/api/tracker/add", {
            method: "POST",
            body: JSON.stringify({ tmdb_id: m.tmdb_id, media_type: "movie", title: m.title }),
          });
        } catch (err) { /* best-effort -- one failure shouldn't block the rest */ }
      }
      btn.textContent = `Tracking ${missing.length} title(s)`;
    });
  }
}

async function loadTrailer(itemId) {
  const el = $("#detail-trailer");
  if (!el) return;
  try {
    const t = await api(`/api/library/${itemId}/trailer`);
    if (t.youtube_key) {
      el.innerHTML = `<a href="https://www.youtube.com/watch?v=${encodeURIComponent(t.youtube_key)}" target="_blank" rel="noopener">▶ Watch Trailer</a>`;
    } else if (!t.tmdb_configured) {
      el.innerHTML = "";
    } else {
      el.innerHTML = `<span class="hint">No trailer found.</span>`;
    }
  } catch (e) {
    el.innerHTML = "";
  }
}

async function loadMoreInfo(itemId) {
  const el = $("#detail-more-info");
  if (!el) return;
  try {
    const info = await api(`/api/library/${itemId}/more-info`);
    if (!info.tmdb_configured || (info.cast.length === 0 && info.similar.length === 0)) {
      el.innerHTML = "";
      return;
    }
    const castHtml = info.cast.length ? `
      <div class="detail-section">
        <h4>Cast</h4>
        <div class="cast-row">
          ${info.cast.map((c) => `
            <div class="cast-card" title="${c.name || ""}${c.character ? ` as ${c.character}` : ""}">
              ${c.profile_path
                ? `<img src="https://image.tmdb.org/t/p/w185${c.profile_path}" alt="${c.name || ""}">`
                : `<div class="cast-card-placeholder"></div>`}
              <span class="cast-name">${c.name || ""}</span>
              ${c.character ? `<span class="cast-character hint">${c.character}</span>` : ""}
            </div>
          `).join("")}
        </div>
      </div>
    ` : "";
    const similarHtml = info.similar.length ? `
      <div class="detail-section">
        <h4>Similar Titles</h4>
        <div class="cast-row">
          ${info.similar.map((s) => `
            <div class="cast-card" title="${s.title}${s.year ? ` (${s.year})` : ""}">
              ${posterMarkup(s.title, s.poster_path)}
              <span class="cast-name">${s.title}</span>
            </div>
          `).join("")}
        </div>
      </div>
    ` : "";
    el.innerHTML = castHtml + similarHtml;
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

function detailTagsMarkup(tags) {
  return `
    <div class="detail-tags">
      <div class="detail-tags-chips">
        ${(tags || []).map((t) => `<span class="tag-chip">${escapeAttr(t)}</span>`).join("") || `<span class="hint">No tags</span>`}
      </div>
      <div class="detail-tags-edit">
        <input type="text" id="detail-tags-input" placeholder="comma, separated, tags" value="${escapeAttr((tags || []).join(", "))}">
        <button id="detail-tags-save-btn">Save Tags</button>
      </div>
      <span id="detail-tags-error" class="hint"></span>
    </div>
  `;
}

function wireDetailTags(ids, onSaved) {
  const btn = $("#detail-tags-save-btn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const raw = $("#detail-tags-input").value;
    const tags = Array.from(new Set(raw.split(",").map((t) => t.trim()).filter(Boolean)));
    const errorEl = $("#detail-tags-error");
    btn.disabled = true;
    errorEl.textContent = "";
    try {
      for (const id of ids) {
        await api(`/api/library/${id}/tags`, { method: "POST", body: JSON.stringify({ tags }) });
      }
      const chips = $(".detail-tags-chips");
      if (chips) chips.innerHTML = tags.map((t) => `<span class="tag-chip">${escapeAttr(t)}</span>`).join("") || `<span class="hint">No tags</span>`;
      onSaved(tags);
    } catch (e) {
      errorEl.textContent = `Failed to save tags: ${e.message}`;
    } finally {
      btn.disabled = false;
    }
  });
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
      <details class="detail-custom-title">
        <summary>Not on TMDB? Set a custom title</summary>
        <label>Title
          <input type="text" id="detail-custom-title-input" placeholder="Family Vacation 2019">
        </label>
        <label>Year (optional)
          <input type="number" id="detail-custom-year-input" placeholder="2019">
        </label>
        <button id="detail-custom-title-btn">Set Custom Title</button>
      </details>
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
  $("#detail-custom-title-btn").addEventListener("click", async () => {
    const pane = state.detailPane;
    if (!pane) return;
    const title = $("#detail-custom-title-input").value.trim();
    if (!title) return;
    const yearRaw = $("#detail-custom-year-input").value.trim();
    const year = yearRaw ? Number(yearRaw) : null;

    const ids = pane.kind === "movie" ? [pane.data.id] : pane.data.episodes.map((e) => e.id);
    $("#detail-fetch-status").textContent = "Setting custom title...";
    try {
      for (const id of ids) {
        await api(`/api/library/${id}/override`, { method: "POST", body: JSON.stringify({ title, year }) });
      }
      $("#detail-fetch-status").textContent = "Updated.";
      await reopenDetailPaneAfterRematch(pane, ids);
    } catch (e) {
      $("#detail-fetch-status").textContent = `Error: ${e.message}`;
    }
  });
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
    refreshNewFilesBadge(); // the scan itself just cleared the watcher's queue server-side
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

// Season-pack grouping: episodes of the same show+season that landed
// *contiguously* in the list (the common case -- files from one extracted
// pack folder sort together) get a shared header row with a "select whole
// pack" checkbox, instead of looking like N unrelated rows. Not a full
// re-sort/re-group across the whole table: interleaved episodes from
// different imports stay as plain rows rather than being silently
// reordered out from under their original index.
function contiguousPreviewGroups(items) {
  const groups = [];
  let current = null;
  items.forEach((item, i) => {
    const key = item.media_type === "tv" && item.season != null ? `${item.title}::${item.season}` : null;
    if (key && current && current.key === key) {
      current.indices.push(i);
    } else {
      current = { key, indices: [i] };
      groups.push(current);
    }
  });
  return groups;
}

function renderArchiveTable(items) {
  const tbody = $("#archive-table tbody");
  const checkedBefore = $all(".row-check").map((cb) => cb.checked);
  const rowHtml = (item, i) => `
    <tr>
      <td><input type="checkbox" class="row-check" data-index="${i}" ${checkedBefore[i] === false ? "" : "checked"}></td>
      <td>${item.duplicate ? `<span class="duplicate-badge" title="A matching title already exists in the library">⚠</span>` : ""}</td>
      <td title="${item.source_path}">${item.source_path.split(/[\\/]/).pop()}</td>
      <td>${item.media_type === "movie" ? renderMovieNameCell(item, i) : renderTvNameCell(item, i)}</td>
      <td>${item.media_type}</td>
      <td>${formatBytes(state.sizeByPath[item.source_path])}</td>
      <td title="${item.overview}">${item.overview.slice(0, 80)}</td>
      <td><button class="change-match-btn" data-index="${i}">Change Match</button></td>
    </tr>
  `;
  tbody.innerHTML = contiguousPreviewGroups(items).map((group) => {
    const rows = group.indices.map((i) => rowHtml(items[i], i)).join("");
    if (!group.key || group.indices.length < 2) return rows;
    const first = items[group.indices[0]];
    return `
      <tr class="season-pack-header-row">
        <td><input type="checkbox" class="season-pack-select" data-indices="${group.indices.join(",")}" checked></td>
        <td colspan="7">Season Pack: ${escapeAttr(first.title)} — Season ${first.season} (${group.indices.length} episodes)</td>
      </tr>
    ` + rows;
  }).join("");
  $all(".change-match-btn").forEach((btn) => {
    btn.addEventListener("click", () => openMatchPicker(Number(btn.dataset.index)));
  });
  $all(".season-pack-select").forEach((groupCb) => {
    groupCb.addEventListener("change", () => {
      const indices = groupCb.dataset.indices.split(",");
      indices.forEach((i) => {
        const rowCb = $(`.row-check[data-index="${i}"]`);
        if (rowCb) rowCb.checked = groupCb.checked;
      });
    });
  });

  // Movies: the folder shares the file's base name by convention
  // (renamer.py::plan_movie_rename), but the "also rename file" checkbox
  // lets a folder-name fix be made without touching the video's own
  // filename -- unchecked, only the folder segment of dest_path changes.
  $all(".dest-stem-input, .dest-rename-file-checkbox").forEach((el) => {
    el.addEventListener("change", () => {
      const idx = Number(el.dataset.index);
      const item = state.previewItems[idx];
      const row = el.closest("td");
      const stemInput = row.querySelector(".dest-stem-input");
      const renameFileBox = row.querySelector(".dest-rename-file-checkbox");
      const stem = stemInput.value.trim();
      if (!stem) {
        stemInput.value = extBaseOf(item.dest_path.split(/[\\/]/).pop())[0];
        return;
      }
      const root = dirOf(dirOf(item.dest_path));
      const folder = root ? `${root}/${stem}` : stem;
      if (renameFileBox.checked) {
        const [, ext] = extBaseOf(item.dest_path.split(/[\\/]/).pop());
        item.dest_path = `${folder}/${stem}${ext}`;
      } else {
        const currentFileName = item.dest_path.split(/[\\/]/).pop();
        item.dest_path = `${folder}/${currentFileName}`;
      }
      stemInput.title = item.dest_path;
    });
  });

  // TV: the "Season NN" folder is independent of the episode's file name --
  // only the file itself is ever renamed here.
  $all(".dest-name-input").forEach((input) => {
    input.addEventListener("change", () => {
      const idx = Number(input.dataset.index);
      const item = state.previewItems[idx];
      const raw = input.value.trim();
      if (!raw) {
        input.value = item.dest_path.split(/[\\/]/).pop();
        return;
      }
      const dir = dirOf(item.dest_path);
      item.dest_path = dir ? `${dir}/${raw}` : raw;
      input.title = item.dest_path;
    });
  });
}

function renderMovieNameCell(item, i) {
  const [stem] = extBaseOf(item.dest_path.split(/[\\/]/).pop());
  return `
    <div class="dest-name-cell">
      <input type="text" class="dest-stem-input" data-index="${i}" title="${item.dest_path}" value="${escapeAttr(stem)}">
      <label class="dest-rename-file-toggle">
        <input type="checkbox" class="dest-rename-file-checkbox" data-index="${i}" checked> also rename file
      </label>
    </div>
  `;
}

function renderTvNameCell(item, i) {
  return `<input type="text" class="dest-name-input" data-index="${i}" title="${item.dest_path}" value="${escapeAttr(item.dest_path.split(/[\\/]/).pop())}">`;
}

function extBaseOf(fileName) {
  const extMatch = fileName.match(/\.[^./\\]+$/);
  const ext = extMatch ? extMatch[0] : "";
  const stem = ext ? fileName.slice(0, -ext.length) : fileName;
  return [stem, ext];
}

function dirOf(path) {
  const parts = path.split(/[\\/]/);
  parts.pop();
  return parts.join("/");
}

function escapeAttr(str) {
  return str.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

// ---- Manual TMDB match picker ----
async function openMatchPicker(index) {
  const item = state.previewItems[index];
  openMatchModal(item.media_type, item.title, (candidate) => applyMatchOverride(index, candidate));
}

function openMatchModal(mediaType, initialQuery, onApply, { hideIdEntry = false } = {}) {
  state.matchPicker = { mediaType, onApply };
  $("#match-modal").classList.remove("hidden");
  $("#match-search-input").value = initialQuery;
  $("#match-id-input").value = "";
  // "Use ID" has no title to go with the tmdb_id -- fine for archive preview
  // (which re-fetches full TMDB details anyway) but tracker.add needs a
  // title up front, so that path is search-results-only.
  $(".match-id-row").classList.toggle("hidden", hideIdEntry);
  if (initialQuery) runMatchSearch(initialQuery);
  else $("#match-results").innerHTML = "";
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

function openBulkMatchPicker() {
  const items = selectedItems();
  if (items.length === 0) {
    $("#scan-status").textContent = "Select at least one row first.";
    return;
  }
  const mediaTypes = new Set(items.map((i) => i.media_type));
  if (mediaTypes.size > 1) {
    $("#scan-status").textContent = "Select rows of only one media type (Movie or TV) to bulk-change match.";
    return;
  }
  openMatchModal(items[0].media_type, items[0].title, (candidate) => applyBulkMatchOverride(items, candidate));
}

async function applyBulkMatchOverride(items, candidate) {
  closeMatchPicker();
  $("#scan-status").textContent = `Applying match to ${items.length} file(s)...`;
  try {
    const overrides = Object.fromEntries(items.map((i) => [i.source_path, candidate.tmdb_id]));
    const preview = await api("/api/archive/preview", {
      method: "POST",
      body: JSON.stringify({ paths: items.map((i) => i.source_path), tmdb_overrides: overrides }),
    });
    const bySource = Object.fromEntries(preview.items.map((p) => [p.source_path, p]));
    state.previewItems = state.previewItems.map((existing) => bySource[existing.source_path] || existing);
    renderArchiveTable(state.previewItems);
    $("#scan-status").textContent = `${state.previewItems.length} file(s) ready`;
  } catch (e) {
    $("#scan-status").textContent = `Error: ${e.message}`;
  }
}

function closeMatchPicker() {
  $("#match-modal").classList.add("hidden");
  state.matchPicker = null;
}

// ---- Manually track a title not yet in the library (wishlist/wanted) ----
function openTrackAddModal(mediaType) {
  openMatchModal(mediaType, "", (candidate) => addTrackedTitle(mediaType, candidate), { hideIdEntry: true });
}

async function addTrackedTitle(mediaType, candidate) {
  closeMatchPicker();
  $("#track-add-status").textContent = `Tracking "${candidate.title}"...`;
  try {
    await api("/api/tracker/add", {
      method: "POST",
      body: JSON.stringify({ tmdb_id: candidate.tmdb_id, media_type: mediaType, title: candidate.title }),
    });
    $("#track-add-status").textContent = `Now tracking "${candidate.title}".`;
    loadTrackedList();
  } catch (e) {
    $("#track-add-status").textContent = `Error: ${e.message}`;
  }
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
  const dryRun = $("#dry-run-toggle").checked;
  const endpoint = isOrganize ? "/api/library/organize" : "/api/archive/confirm";
  const body = isOrganize ? { items, dry_run: dryRun } : { items, purge_subtitles: true, dry_run: dryRun };

  if (!dryRun) {
    const confirmMsg = isOrganize
      ? `Organize ${items.length} file(s)? This moves them to their correct name/folder in place — no duplicate is created.`
      : `Archive ${items.length} file(s)? This copies them to the archive location.`;
    const ok = await showConfirm(confirmMsg);
    if (!ok) return;
  }

  $("#scan-status").textContent = dryRun ? "Checking (dry run, nothing will change)..." : (isOrganize ? "Organizing..." : "Archiving...");
  try {
    const result = await api(endpoint, { method: "POST", body: JSON.stringify(body) });
    const failures = result.results.filter((r) => r.status === "failed");

    if (dryRun) {
      const okCount = result.results.length - failures.length;
      $("#scan-status").textContent = failures.length
        ? `Dry run: ${okCount} would succeed, ${failures.length} would fail (${failures.map((f) => f.error).join("; ")})`
        : `Dry run: all ${okCount} file(s) would succeed. Nothing was changed.`;
      return; // preview table untouched -- nothing on disk/DB changed
    }

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
  const query = $("#browse-search").value.trim().toLowerCase();
  const filterMode = $("#browse-filter").value;
  const trackedFilter = $("#browse-tracked").value;
  const sortMode = $("#browse-sort").value;

  let items = state.browseItems;
  if (filterMode === "unmatched") items = items.filter((i) => i.tmdb_id == null);
  if (trackedFilter === "tracked") items = items.filter((i) => i.tracked);
  if (trackedFilter === "untracked") items = items.filter((i) => !i.tracked);
  if (query) items = items.filter((i) => i.path.toLowerCase().includes(query) || i.parsed_title.toLowerCase().includes(query));
  items = [...items].sort((a, b) => {
    if (sortMode === "size") return b.size_bytes - a.size_bytes;
    if (sortMode === "title") return a.parsed_title.localeCompare(b.parsed_title);
    return a.path.localeCompare(b.path);
  });
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
    tbody.innerHTML = `<tr><td colspan=6>No files match the current filters.</td></tr>`;
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
  loadLibraryHealth();
}

async function loadLibraryHealth() {
  const card = $("#library-health-card");
  const summary = $("#library-health-summary");
  const cleanupBtn = $("#cleanup-orphans-btn");
  const reviewBtn = $("#review-duplicates-btn");
  const artworkBtn = $("#cleanup-artwork-btn");
  try {
    const data = await api("/api/library/health");
    state.libraryDuplicates = data.duplicates;
    const orphanCount = data.orphans.length;
    const duplicateCount = data.duplicates.length;
    const artworkCount = data.orphaned_artwork.length;
    if (orphanCount === 0 && duplicateCount === 0 && artworkCount === 0) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");
    const parts = [];
    if (orphanCount > 0) parts.push(`${orphanCount} orphaned record(s) (file missing on disk)`);
    if (duplicateCount > 0) parts.push(`${duplicateCount} duplicate group(s)`);
    if (artworkCount > 0) parts.push(`${artworkCount} folder(s) with leftover artwork/subtitles`);
    summary.textContent = parts.join(" — ");
    cleanupBtn.classList.toggle("hidden", orphanCount === 0);
    reviewBtn.classList.toggle("hidden", duplicateCount === 0);
    artworkBtn.classList.toggle("hidden", artworkCount === 0);
  } catch (e) {
    card.classList.add("hidden");
  }
}

function isBrowseDryRun() {
  return $("#browse-dry-run-toggle").checked;
}

async function cleanupOrphanedArtwork() {
  const dryRun = isBrowseDryRun();
  if (!dryRun) {
    const ok = await showConfirm("Delete poster/nfo/subtitle files left behind in folders whose video was renamed or moved away? This does not touch any video file.");
    if (!ok) return;
  }
  try {
    const data = await api(`/api/library/orphaned-artwork/cleanup?dry_run=${dryRun}`, { method: "POST" });
    if (dryRun) {
      const fileCount = data.groups.reduce((n, g) => n + g.files.length, 0);
      $("#browse-status").textContent = data.groups.length
        ? `Dry run: would remove ${fileCount} file(s) across ${data.groups.length} folder(s). Nothing was changed.`
        : "Dry run: nothing to clean up. Nothing was changed.";
      return;
    }
    $("#browse-status").textContent = `Removed ${data.removed} orphaned artwork file(s).`;
    loadLibraryHealth();
  } catch (e) {
    $("#browse-status").textContent = `Error: ${e.message}`;
  }
}

function openDuplicatesModal() {
  renderDuplicatesList();
  $("#duplicates-modal").classList.remove("hidden");
}

// Best in a duplicate group = highest resolution, then HDR, then more audio
// channels, then bigger file -- a reasonable "which copy would I keep"
// default the group's own "Keep Best" button can act on in one click,
// without needing a per-file ffprobe/codec fetch just to rank duplicates.
const RESOLUTION_RANK = { "4K": 4, "1080p": 3, "720p": 2, "SD": 1 };

function duplicateQualityRank(item) {
  return [
    RESOLUTION_RANK[item.resolution] || 0,
    item.hdr ? 1 : 0,
    item.audio_channels || 0,
    item.size_bytes || 0,
  ];
}

function bestDuplicateIndex(group) {
  let bestIndex = 0;
  for (let i = 1; i < group.length; i++) {
    const a = duplicateQualityRank(group[i]);
    const b = duplicateQualityRank(group[bestIndex]);
    for (let d = 0; d < a.length; d++) {
      if (a[d] !== b[d]) { if (a[d] > b[d]) bestIndex = i; break; }
    }
  }
  return bestIndex;
}

function renderDuplicatesList() {
  const groups = state.libraryDuplicates || [];
  const container = $("#duplicates-list");
  if (groups.length === 0) {
    container.innerHTML = "<p>No duplicate groups remaining.</p>";
    return;
  }
  container.innerHTML = groups.map((group, groupIndex) => {
    const first = group[0];
    const label = first.media_type === "tv" && first.season_number != null
      ? `${first.title} S${String(first.season_number).padStart(2, "0")}E${String(first.episode_number).padStart(2, "0")}`
      : `${first.title}${first.year ? ` (${first.year})` : ""}`;
    const bestIndex = bestDuplicateIndex(group);
    return `
      <div class="duplicate-group">
        <div class="duplicate-group-header">
          <h4>${label}</h4>
          <button class="duplicate-keep-best-btn" data-group-index="${groupIndex}">Keep Best, Delete Others</button>
        </div>
        ${group.map((item, i) => `
          <div class="duplicate-row ${i === bestIndex ? "duplicate-row-best" : ""}">
            ${i === bestIndex ? `<span class="badge badge-ok" title="Highest resolution/HDR/audio/size in this group">★ Best</span>` : ""}
            <span class="duplicate-row-path" title="${item.final_path || ""}">${item.final_path || "(no file)"}</span>
            <span class="hint">${formatBytes(item.size_bytes)}${item.resolution ? ` · ${item.resolution}` : ""}${item.hdr ? " · HDR" : ""}${item.audio_channels ? ` · ${item.audio_channels}ch` : ""}</span>
            <button class="danger duplicate-delete-btn" data-path="${item.final_path || ""}">Delete This Copy</button>
          </div>
        `).join("")}
      </div>
    `;
  }).join("");
  container.querySelectorAll(".duplicate-delete-btn").forEach((btn) => {
    btn.addEventListener("click", () => deleteDuplicateCopy(btn.dataset.path));
  });
  container.querySelectorAll(".duplicate-keep-best-btn").forEach((btn) => {
    btn.addEventListener("click", () => keepBestDuplicate(groups[Number(btn.dataset.groupIndex)]));
  });
}

async function keepBestDuplicate(group) {
  const bestIndex = bestDuplicateIndex(group);
  const toDelete = group.filter((_, i) => i !== bestIndex).map((item) => item.final_path).filter(Boolean);
  if (toDelete.length === 0) return;
  const ok = await showConfirm(`Keep "${group[bestIndex].final_path}" and permanently delete the other ${toDelete.length} copy/copies? This cannot be undone.`);
  if (!ok) return;
  try {
    const data = await api("/api/library/delete-batch", { method: "POST", body: JSON.stringify({ paths: toDelete }) });
    await loadLibraryHealth();
    renderDuplicatesList();
    loadMoviesGallery();
    loadTvGallery();
    if (data.errors.length) $("#duplicates-list").insertAdjacentHTML("afterbegin", `<p>${data.errors.length} deletion(s) failed: ${data.errors.join("; ")}</p>`);
  } catch (e) {
    $("#duplicates-list").insertAdjacentHTML("afterbegin", `<p>Error: ${e.message}</p>`);
  }
}

async function deleteDuplicateCopy(path) {
  if (!path) return;
  const ok = await showConfirm(`Permanently delete "${path}"? This cannot be undone.`);
  if (!ok) return;
  try {
    await api("/api/library/delete-file", { method: "POST", body: JSON.stringify({ path }) });
    await loadLibraryHealth();
    renderDuplicatesList();
    loadMoviesGallery();
    loadTvGallery();
  } catch (e) {
    $("#duplicates-list").insertAdjacentHTML("afterbegin", `<p>Error: ${e.message}</p>`);
  }
}

async function cleanupOrphans() {
  const dryRun = isBrowseDryRun();
  if (!dryRun) {
    const ok = await showConfirm("Remove database records for archived files that no longer exist on disk? This does not touch any files.");
    if (!ok) return;
  }
  try {
    const data = await api(`/api/library/orphans/cleanup?dry_run=${dryRun}`, { method: "POST" });
    $("#browse-status").textContent = dryRun
      ? `Dry run: would remove ${data.removed} orphaned record(s). Nothing was changed.`
      : `Removed ${data.removed} orphaned record(s).`;
    if (!dryRun) loadLibraryHealth();
  } catch (e) {
    $("#browse-status").textContent = `Error: ${e.message}`;
  }
}

async function deleteBrowseItem(index) {
  const item = state.browseFiltered[index];
  const dryRun = isBrowseDryRun();
  if (!dryRun) {
    const ok = await showConfirm(`Permanently delete "${item.path}"? This cannot be undone.`);
    if (!ok) return;
  }

  $("#browse-status").textContent = dryRun ? "Checking (dry run, nothing will change)..." : "Deleting...";
  try {
    const data = await api("/api/library/delete-file", { method: "POST", body: JSON.stringify({ path: item.path, dry_run: dryRun }) });
    if (dryRun) {
      const p = data.preview;
      const extra = p.sibling_files.length ? `, ${p.sibling_files.length} sibling file(s)` : "";
      $("#browse-status").textContent = `Dry run: would delete "${item.path}"${extra}${p.folder_removed ? ", and its now-empty folder" : ""}. Nothing was changed.`;
      return;
    }
    loadBrowse();
  } catch (e) {
    $("#browse-status").textContent = `Error: ${e.message}`;
  }
}

async function deleteSelectedBrowseItems() {
  const selected = $all(".browse-check:checked").map((cb) => state.browseFiltered[Number(cb.dataset.index)]);
  if (selected.length === 0) return;
  const dryRun = isBrowseDryRun();
  if (!dryRun) {
    const ok = await showConfirm(`Permanently delete ${selected.length} file(s)? This cannot be undone.`);
    if (!ok) return;
  }

  $("#browse-status").textContent = dryRun ? "Checking (dry run, nothing will change)..." : "Deleting...";
  try {
    const data = await api("/api/library/delete-batch", {
      method: "POST",
      body: JSON.stringify({ paths: selected.map((item) => item.path), dry_run: dryRun }),
    });
    if (dryRun) {
      const wouldDelete = data.previews.filter((p) => p.would_delete).length;
      const folders = data.previews.filter((p) => p.folder_removed).length;
      $("#browse-status").textContent = data.errors.length
        ? `Dry run: ${wouldDelete} would succeed (${folders} folder(s) removed too), ${data.errors.length} would fail: ${data.errors.join("; ")}`
        : `Dry run: all ${wouldDelete} file(s) would succeed (${folders} folder(s) removed too). Nothing was changed.`;
      return;
    }
    $("#browse-status").textContent = data.errors.length
      ? `Deleted ${data.deleted}, ${data.errors.length} failed: ${data.errors.join("; ")}`
      : `Deleted ${data.deleted} file(s).`;
    loadBrowse();
  } catch (e) {
    $("#browse-status").textContent = `Error: ${e.message}`;
  }
}

async function organizeSelected() {
  const selected = $all(".browse-check:checked").map((cb) => state.browseFiltered[Number(cb.dataset.index)]);
  if (selected.length === 0) return;
  const sizeByPath = Object.fromEntries(selected.map((item) => [item.path, item.size_bytes]));
  await organizePaths(selected.map((item) => item.path), sizeByPath);
}

// Switches to the Archive tab and feeds the given paths into the existing
// preview-then-organize flow (moves files in place, updates existing
// media_items rows) -- shared by Browse's "Organize Selected" and the
// Movies/TV galleries' "Rename Selected".
async function organizePaths(paths, sizeByPath) {
  if (paths.length === 0) return;

  $all(".tab-btn").forEach((b) => b.classList.remove("active"));
  $all(".tab-panel").forEach((p) => p.classList.remove("active"));
  $('.tab-btn[data-tab="archive"]').classList.add("active");
  $("#tab-archive").classList.add("active");

  setPreviewMode("organize");
  await previewPaths(paths, sizeByPath);
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
          ${n.next_episode_air_date ? `<div class="hint">📅 Next episode: ${n.next_episode_air_date}</div>` : ""}
        </div>
        <button data-id="${n.id}" class="ack-btn">Mark Downloaded</button>
        <button data-id="${n.id}" class="snooze-btn">Remind Me in 7 Days</button>
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
    $all(".snooze-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await api(`/api/tracker/${btn.dataset.id}/snooze`, {
          method: "POST",
          body: JSON.stringify({ days: 7 }),
        });
        loadNotifications();
      });
    });
  } catch (e) {
    container.innerHTML = `<p>Error loading notifications: ${e.message}</p>`;
  }
}

async function loadUpcomingReleases() {
  const container = $("#upcoming-releases-list");
  container.innerHTML = "Loading...";
  try {
    const data = await api("/api/tracker/upcoming");
    if (data.items.length === 0) {
      container.innerHTML = "<p>Nothing due in the next 90 days.</p>";
      return;
    }
    container.innerHTML = data.items.map((i) => `
      <div class="notification-item">
        <div>
          <strong>${i.title}</strong>
          <div>${i.label} — ${i.release_date}</div>
        </div>
      </div>
    `).join("");
  } catch (e) {
    container.innerHTML = `<p>Error loading upcoming releases: ${e.message}</p>`;
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

async function loadNotificationHistory() {
  const container = $("#notification-history-list");
  container.innerHTML = "Loading...";
  try {
    const data = await api("/api/tracker/history");
    if (data.history.length === 0) {
      container.innerHTML = "<p>No notifications yet.</p>";
      return;
    }
    container.innerHTML = data.history.map((h) => `
      <div class="notification-history-item hint">
        ${new Date(h.created_at).toLocaleString()} — ${h.message}
      </div>
    `).join("");
  } catch (e) {
    container.innerHTML = `<p>Error loading notification history: ${e.message}</p>`;
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

// Polls for files the filesystem watcher has seen since the last scan
// (see app/core/fs_watcher.py) -- always 0/hidden when watcher.enabled is
// off, since nothing ever feeds the tracker in that case, so this is cheap
// to poll unconditionally rather than checking Settings first.
async function refreshNewFilesBadge() {
  const badge = $("#new-files-badge");
  if (!badge) return;
  try {
    const data = await api("/api/scan/new-files");
    if (data.count > 0) {
      badge.textContent = `${data.count} new file(s) detected — Scan Library to pick them up`;
      badge.classList.remove("hidden");
    } else {
      badge.classList.add("hidden");
    }
  } catch (e) { /* offline or server restarting, retry on next tick */ }
}

function pollNewFiles() {
  refreshNewFilesBadge();
  setInterval(refreshNewFilesBadge, 20000);
}

// ---- History tab ----
function currentHistoryFilterParams() {
  const params = new URLSearchParams();
  const type = $("#history-type-filter").value;
  const status = $("#history-status-filter").value;
  const since = $("#history-since-filter").value;
  const until = $("#history-until-filter").value;
  if (type) params.set("operation_type", type);
  if (status) params.set("status", status);
  if (since) params.set("since", since);
  if (until) params.set("until", `${until}T23:59:59`); // inclusive of the whole day
  return params;
}

async function loadHistory() {
  const tbody = $("#history-table tbody");
  tbody.innerHTML = "<tr><td colspan=5>Loading...</td></tr>";
  try {
    const data = await api(`/api/archive/history?${currentHistoryFilterParams().toString()}`);
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

async function exportHistoryView() {
  const params = currentHistoryFilterParams();
  params.set("limit", "10000"); // export everything matching the filters, not just the on-screen page
  $("#history-status").textContent = "Exporting...";
  try {
    const data = await api(`/api/archive/history?${params.toString()}`);
    const header = ["Time", "Type", "Status", "Details"];
    const rows = data.operations.map((op) => [
      op.created_at, op.operation_type, op.status,
      op.error_message || (op.details ? JSON.stringify(op.details) : ""),
    ]);
    downloadCsv(`history-export-${new Date().toISOString().slice(0, 10)}.csv`, rowsToCsv(header, rows));
    $("#history-status").textContent = "";
  } catch (e) {
    $("#history-status").textContent = `Export failed: ${e.message}`;
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
function formatDuration(seconds) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

async function loadStats() {
  const card = $("#stats-card");
  card.textContent = "Loading...";
  try {
    const [stats, health] = await Promise.all([api("/api/stats"), api("/api/status")]);
    card.innerHTML = `
      <p>Total media items: <strong>${stats.total_media_items}</strong></p>
      <p>Movies: <strong>${stats.total_movies}</strong></p>
      <p>TV episodes: <strong>${stats.total_tv_episodes}</strong></p>
      <p>Total archived size: <strong>${formatBytes(stats.total_size_bytes)}</strong>
        (movies: ${formatBytes(stats.movies_size_bytes)}, TV: ${formatBytes(stats.tv_size_bytes)})</p>
      <p class="hint">
        Server uptime: ${formatDuration(health.uptime_seconds)}
        · Database: ${formatBytes(health.database_size_bytes)}
        · ffprobe: ${health.ffprobe_available ? "available" : "not installed"}
        ${health.next_tracker_check_in_seconds != null ? `· Next tracker check in ${formatDuration(health.next_tracker_check_in_seconds)}` : ""}
      </p>
    `;
  } catch (e) {
    card.textContent = `Error: ${e.message}`;
  }
}

async function loadInsights() {
  const card = $("#insights-card");
  card.textContent = "Loading...";
  try {
    const data = await api("/api/stats/insights");
    const maxGenreCount = Math.max(1, ...data.top_genres.map((g) => g.count));
    const genreRows = data.top_genres.length
      ? data.top_genres.map((g) => `
          <div class="storage-row"><span>${escapeAttr(g.genre)}</span><span class="hint">${g.count}</span></div>
          <div class="storage-bar"><div class="storage-bar-fill" style="width:${Math.round((g.count / maxGenreCount) * 100)}%"></div></div>
        `).join("")
      : `<p class="hint">No genre data yet.</p>`;

    const resolutionRows = data.resolution_breakdown.length
      ? `<table class="insights-table">
          <thead><tr><th>Resolution</th><th>Count</th><th>Avg size</th></tr></thead>
          <tbody>${data.resolution_breakdown.map((r) => `
            <tr><td>${r.resolution}</td><td>${r.count}</td><td>${formatBytes(r.avg_size_bytes)}</td></tr>
          `).join("")}</tbody>
        </table>`
      : `<p class="hint">No resolution data yet -- open a title's file details to probe it.</p>`;

    const maxGrowthCount = Math.max(1, ...data.growth_by_month.map((m) => m.count));
    const growthRows = data.growth_by_month.length
      ? data.growth_by_month.slice(-12).map((m) => `
          <div class="storage-row"><span>${m.month}</span><span class="hint">${m.count} added</span></div>
          <div class="storage-bar"><div class="storage-bar-fill" style="width:${Math.round((m.count / maxGrowthCount) * 100)}%"></div></div>
        `).join("")
      : `<p class="hint">No archive activity yet.</p>`;

    card.innerHTML = `
      <h4>Insights</h4>
      <h5>Top Genres</h5>
      ${genreRows}
      <h5>Average Size by Resolution</h5>
      ${resolutionRows}
      <h5>Library Growth (last 12 months)</h5>
      ${growthRows}
    `;
  } catch (e) {
    card.textContent = `Error loading insights: ${e.message}`;
  }
}

function taskStatusLine(label, task) {
  if (task.last_run_at == null) return `${label}: no run recorded yet since last restart`;
  const when = new Date(task.last_run_at).toLocaleString();
  return task.last_error
    ? `${label}: failed at ${when} -- ${task.last_error}`
    : `${label}: last ran ${when}`;
}

async function loadBackgroundTaskStatus() {
  const card = $("#background-tasks-card");
  card.textContent = "Loading...";
  try {
    const t = await api("/api/status/tasks");
    const lines = [
      t.tracker.last_check_at
        ? `Tracker check: last ran ${new Date(t.tracker.last_check_at).toLocaleString()} (${t.tracker.last_check_status})`
        : "Tracker check: no run recorded yet",
      `Metadata backfill: ${t.backfill.pending} pending, ${t.backfill.failed} failed to match`,
      t.backup.enabled ? taskStatusLine("Backup", t.backup) : "Backup: disabled",
      taskStatusLine("Maintenance", t.maintenance),
    ];
    card.innerHTML = `<h4>Background Tasks</h4>${lines.map((l) => `<p class="hint">${l}</p>`).join("")}`;
  } catch (e) {
    card.textContent = `Error loading task status: ${e.message}`;
  }
}

async function loadStorageStatus() {
  const card = $("#storage-card");
  card.textContent = "Loading...";
  try {
    const data = await api("/api/status/storage");
    const rows = data.paths.map((p) => {
      if (!p.exists) {
        return `<div class="storage-row"><span>${p.label}</span><span class="hint">${p.path} — does not exist</span></div>`;
      }
      const pct = p.total_bytes ? Math.round((p.used_bytes / p.total_bytes) * 100) : 0;
      const forecast = p.days_to_full != null
        ? ` · <span class="${p.days_to_full <= 30 ? 'status-warning' : ''}">~${Math.round(p.days_to_full)} day(s) to full</span>`
        : (p.history_days < 2 ? " · building forecast (needs 2+ days of history)" : "");
      return `
        <div class="storage-row">
          <span>${p.label}</span>
          <span class="hint">${formatBytes(p.used_bytes)} used of ${formatBytes(p.total_bytes)} (${formatBytes(p.free_bytes)} free)${forecast}</span>
        </div>
        <div class="storage-bar"><div class="storage-bar-fill" style="width:${pct}%"></div></div>
      `;
    });
    card.innerHTML = `<h4>Storage</h4>${rows.join("")}`;
  } catch (e) {
    card.textContent = `Error loading storage status: ${e.message}`;
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
    $("#setting-discord-webhook-url").value = s.discord_webhook_url || "";
    $("#telegram-token-note").textContent = s.telegram_bot_token_set ? "A token is currently set. Leave blank to keep it." : "";
    $("#setting-telegram-chat-id").value = s.telegram_chat_id || "";
    $("#pushover-token-note").textContent = s.pushover_api_token_set ? "A token is currently set. Leave blank to keep it." : "";
    $("#pushover-user-note").textContent = s.pushover_user_key_set ? "A key is currently set. Leave blank to keep it." : "";
    $("#setting-auto-track-new").checked = !!s.auto_track_new;
    $("#setting-digest-mode").checked = !!s.digest_mode;
    $("#setting-digest-interval-days").value = s.digest_interval_days || 1;
    $("#setting-watcher-enabled").checked = !!s.watcher_enabled;
    $("#omdb-key-note").textContent = s.omdb_api_key_set
      ? "A key is currently set. Leave blank to keep it. Powers IMDb/Rotten Tomatoes ratings in the detail pane."
      : "Powers IMDb/Rotten Tomatoes ratings in the detail pane. Free key at omdbapi.com/apikey.aspx.";
    $("#api-token-note").textContent = s.api_token_set
      ? "A token is currently set and required on every request. Leave blank to keep it."
      : "Disabled -- every request is currently allowed with no token.";
    $("#disable-api-token-btn").classList.toggle("hidden", !s.api_token_set);
    $("#setting-plex-url").value = s.plex_url || "";
    $("#plex-token-note").textContent = s.plex_token_set ? "A token is currently set. Leave blank to keep it." : "";
    $("#setting-jellyfin-url").value = s.jellyfin_url || "";
    $("#jellyfin-key-note").textContent = s.jellyfin_api_key_set ? "A key is currently set. Leave blank to keep it." : "";
    $("#setting-write-nfo-files").checked = s.write_nfo_files !== false;
    $("#setting-subtitle-languages").value = (s.subtitle_keep_languages || []).join(", ");
    $("#setting-subtitle-languages-movies").value = (s.subtitle_keep_languages_movies || []).join(", ");
    $("#setting-subtitle-languages-tv").value = (s.subtitle_keep_languages_tv || []).join(", ");
    $("#setting-movie-folder-template").value = s.movie_folder_template || "";
    $("#setting-tv-season-folder-template").value = s.tv_season_folder_template || "";
    $("#setting-tv-file-template").value = s.tv_file_template || "";
    $("#setting-collision-policy").value = s.collision_policy || "suffix";
    $("#setting-low-disk-alert-enabled").checked = !!s.low_disk_alert_enabled;
    $("#setting-low-disk-threshold-gb").value = s.low_disk_threshold_gb ?? 10;
    $("#setting-webdav-url").value = s.webdav_url || "";
    $("#setting-webdav-username").value = s.webdav_username || "";
    $("#webdav-password-note").textContent = s.webdav_password_set ? "A password is currently set. Leave blank to keep it." : "";
    $("#setting-webdav-remote-path").value = s.webdav_remote_path || "media-manager-backups";
    $("#opensubtitles-key-note").textContent = s.opensubtitles_api_key_set
      ? "A key is currently set. Leave blank to keep it. Required for auto-fetch below."
      : "Free key at opensubtitles.com/en/consumers. Required for auto-fetch below.";
    $("#setting-auto-fetch-subtitles").checked = !!s.auto_fetch_missing_subtitles;
    $("#setting-tvmaze-enabled").checked = !!s.tvmaze_enabled;
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
    discord_webhook_url: $("#setting-discord-webhook-url").value.trim(),
    telegram_chat_id: $("#setting-telegram-chat-id").value.trim(),
    auto_track_new: $("#setting-auto-track-new").checked,
    digest_mode: $("#setting-digest-mode").checked,
    digest_interval_days: Number($("#setting-digest-interval-days").value) || 1,
    watcher_enabled: $("#setting-watcher-enabled").checked,
    subtitle_keep_languages: $("#setting-subtitle-languages").value.split(",").map((s) => s.trim()).filter(Boolean),
    subtitle_keep_languages_movies: $("#setting-subtitle-languages-movies").value.split(",").map((s) => s.trim()).filter(Boolean),
    subtitle_keep_languages_tv: $("#setting-subtitle-languages-tv").value.split(",").map((s) => s.trim()).filter(Boolean),
    low_disk_alert_enabled: $("#setting-low-disk-alert-enabled").checked,
    low_disk_threshold_gb: Number($("#setting-low-disk-threshold-gb").value) || 10,
    auto_fetch_missing_subtitles: $("#setting-auto-fetch-subtitles").checked,
    tvmaze_enabled: $("#setting-tvmaze-enabled").checked,
  };
  const keyValue = $("#setting-tmdb-key").value;
  if (keyValue) payload.tmdb_api_key = keyValue;
  const omdbKeyValue = $("#setting-omdb-key").value;
  if (omdbKeyValue) payload.omdb_api_key = omdbKeyValue;
  const apiTokenValue = $("#setting-api-token").value;
  if (apiTokenValue) payload.api_token = apiTokenValue;
  const telegramTokenValue = $("#setting-telegram-bot-token").value;
  if (telegramTokenValue) payload.telegram_bot_token = telegramTokenValue;
  const pushoverTokenValue = $("#setting-pushover-api-token").value;
  if (pushoverTokenValue) payload.pushover_api_token = pushoverTokenValue;
  const pushoverUserValue = $("#setting-pushover-user-key").value;
  if (pushoverUserValue) payload.pushover_user_key = pushoverUserValue;
  const opensubtitlesKeyValue = $("#setting-opensubtitles-key").value;
  if (opensubtitlesKeyValue) payload.opensubtitles_api_key = opensubtitlesKeyValue;

  $("#settings-status").textContent = "Saving...";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    $("#setting-tmdb-key").value = "";
    $("#setting-omdb-key").value = "";
    $("#setting-api-token").value = "";
    $("#setting-telegram-bot-token").value = "";
    $("#setting-pushover-api-token").value = "";
    $("#setting-pushover-user-key").value = "";
    $("#setting-opensubtitles-key").value = "";
    // We just set this token server-side ourselves, so this browser needs it stored too, or the very next request 401s.
    if (apiTokenValue) setStoredApiToken(apiTokenValue);
    $("#settings-status").textContent = "Saved.";
    loadSettings();
    loadStatus();
    checkPermissions(); // catches a typo'd path immediately instead of waiting for a manual "Test Permissions" click
  } catch (e) {
    $("#settings-status").textContent = `Error: ${e.message}`;
  }
}

const DEFAULT_NAMING_TEMPLATES = {
  movie_folder_template: "{title}{year_suffix}",
  tv_season_folder_template: "Season {season:02d}",
  tv_file_template: "{show_name} - {code}{episode_title_suffix}",
};

async function saveNamingTemplates(e) {
  e.preventDefault();
  const payload = {
    movie_folder_template: $("#setting-movie-folder-template").value.trim() || DEFAULT_NAMING_TEMPLATES.movie_folder_template,
    tv_season_folder_template: $("#setting-tv-season-folder-template").value.trim() || DEFAULT_NAMING_TEMPLATES.tv_season_folder_template,
    tv_file_template: $("#setting-tv-file-template").value.trim() || DEFAULT_NAMING_TEMPLATES.tv_file_template,
    collision_policy: $("#setting-collision-policy").value,
  };
  $("#naming-templates-status").textContent = "Saving...";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    $("#naming-templates-status").textContent = "Saved.";
    loadSettings();
  } catch (e) {
    $("#naming-templates-status").textContent = `Error: ${e.message}`;
  }
}

async function saveMediaServerSettings(e) {
  e.preventDefault();
  const payload = {
    plex_url: $("#setting-plex-url").value.trim(),
    jellyfin_url: $("#setting-jellyfin-url").value.trim(),
    write_nfo_files: $("#setting-write-nfo-files").checked,
  };
  const plexToken = $("#setting-plex-token").value;
  if (plexToken) payload.plex_token = plexToken;
  const jellyfinKey = $("#setting-jellyfin-api-key").value;
  if (jellyfinKey) payload.jellyfin_api_key = jellyfinKey;

  $("#media-server-status").textContent = "Saving...";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    $("#setting-plex-token").value = "";
    $("#setting-jellyfin-api-key").value = "";
    $("#media-server-status").textContent = "Saved.";
    loadSettings();
  } catch (e) {
    $("#media-server-status").textContent = `Error: ${e.message}`;
  }
}

async function saveWebdavBackupSettings(e) {
  e.preventDefault();
  const payload = {
    webdav_url: $("#setting-webdav-url").value.trim(),
    webdav_username: $("#setting-webdav-username").value.trim(),
    webdav_remote_path: $("#setting-webdav-remote-path").value.trim() || "media-manager-backups",
  };
  const webdavPassword = $("#setting-webdav-password").value;
  if (webdavPassword) payload.webdav_password = webdavPassword;

  $("#webdav-backup-status").textContent = "Saving...";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    $("#setting-webdav-password").value = "";
    $("#webdav-backup-status").textContent = "Saved.";
    loadSettings();
  } catch (e) {
    $("#webdav-backup-status").textContent = `Error: ${e.message}`;
  }
}

async function loadConfigHistory() {
  const el = $("#config-history-list");
  el.textContent = "Loading...";
  try {
    const data = await api("/api/settings/history");
    if (data.versions.length === 0) {
      el.innerHTML = "<p class=\"hint\">No saved versions yet -- one is kept on every Settings save.</p>";
      return;
    }
    el.innerHTML = data.versions.map((v) => `
      <div class="config-history-row" data-version="${v.version}">
        <div class="config-history-summary">
          <span>${new Date(v.timestamp).toLocaleString()}</span>
          <span class="hint">${formatBytes(v.size_bytes)}</span>
          <button class="config-history-diff-btn" data-version="${v.version}">View Diff</button>
          <button class="danger config-history-rollback-btn" data-version="${v.version}">Rollback to This</button>
        </div>
        <pre class="config-history-diff hidden"></pre>
      </div>
    `).join("");
    el.querySelectorAll(".config-history-diff-btn").forEach((btn) => {
      btn.addEventListener("click", () => toggleConfigHistoryDiff(btn.dataset.version));
    });
    el.querySelectorAll(".config-history-rollback-btn").forEach((btn) => {
      btn.addEventListener("click", () => rollbackConfigVersion(btn.dataset.version));
    });
  } catch (e) {
    el.innerHTML = `<p>Error loading config history: ${e.message}</p>`;
  }
}

async function toggleConfigHistoryDiff(version) {
  const row = $(`.config-history-row[data-version="${version}"]`);
  const pre = row.querySelector(".config-history-diff");
  if (!pre.classList.contains("hidden")) {
    pre.classList.add("hidden");
    return;
  }
  pre.classList.remove("hidden");
  pre.textContent = "Loading diff...";
  try {
    const data = await api(`/api/settings/history/${encodeURIComponent(version)}/diff`);
    pre.textContent = data.diff.length ? data.diff.join("") : "No differences from the current config.";
  } catch (e) {
    pre.textContent = `Error loading diff: ${e.message}`;
  }
}

async function rollbackConfigVersion(version) {
  const ok = await showConfirm(`Roll back config.yaml to the version from this save? The current config is saved to history first, so this can be undone.`);
  if (!ok) return;
  try {
    await api(`/api/settings/history/${encodeURIComponent(version)}/rollback`, { method: "POST" });
    loadSettings();
    loadConfigHistory();
  } catch (e) {
    $("#config-history-list").insertAdjacentHTML("afterbegin", `<p>Rollback failed: ${e.message}</p>`);
  }
}

async function loadViewers() {
  try {
    const data = await api("/api/library/viewers");
    state.viewers = data.viewers;

    const select = $("#viewer-select");
    const previousValue = select.value || (getActiveViewerId() != null ? String(getActiveViewerId()) : "");
    select.innerHTML = `<option value="">All viewers</option>` +
      data.viewers.map((v) => `<option value="${v.id}">${escapeAttr(v.name)}</option>`).join("");
    if (previousValue && data.viewers.some((v) => String(v.id) === previousValue)) {
      select.value = previousValue;
    } else if (previousValue) {
      setActiveViewerId(null); // previously-selected viewer no longer exists (deleted elsewhere)
    }

    const list = $("#viewers-list");
    if (list) {
      list.innerHTML = data.viewers.length
        ? data.viewers.map((v) => `
            <div class="tracked-item">
              <div><strong>${escapeAttr(v.name)}</strong> <span class="hint">since ${new Date(v.created_at).toLocaleDateString()}</span></div>
              <button class="danger delete-viewer-btn" data-id="${v.id}">Delete</button>
            </div>
          `).join("")
        : `<p class="hint">No viewers yet -- add one to enable per-viewer watch state.</p>`;
      list.querySelectorAll(".delete-viewer-btn").forEach((btn) => {
        btn.addEventListener("click", () => deleteViewerAction(Number(btn.dataset.id)));
      });
    }
  } catch (e) {
    // Best-effort -- an unpopulated viewer selector just leaves the app in its original single-flag behavior.
  }
}

async function createViewerAction() {
  const input = $("#new-viewer-name");
  const name = input.value.trim();
  if (!name) return;
  try {
    await api("/api/library/viewers", { method: "POST", body: JSON.stringify({ name }) });
    input.value = "";
    loadViewers();
  } catch (e) {
    $("#viewers-list").insertAdjacentHTML("afterbegin", `<p>Error: ${e.message}</p>`);
  }
}

async function deleteViewerAction(id) {
  const ok = await showConfirm("Delete this viewer? Their per-item watched history is deleted too.");
  if (!ok) return;
  try {
    await api(`/api/library/viewers/${id}`, { method: "DELETE" });
    if (getActiveViewerId() === id) {
      setActiveViewerId(null);
      loadMoviesGallery();
      loadTvGallery();
    }
    loadViewers();
  } catch (e) {
    $("#viewers-list").insertAdjacentHTML("afterbegin", `<p>Error: ${e.message}</p>`);
  }
}

async function loadApiTokensList() {
  const el = $("#api-tokens-list");
  el.textContent = "Loading...";
  try {
    const data = await api("/api/settings/tokens");
    if (data.tokens.length === 0) {
      el.innerHTML = `<p class="hint">No named tokens yet.</p>`;
      return;
    }
    el.innerHTML = data.tokens.map((t) => `
      <div class="tracked-item">
        <div>
          <strong>${t.name}</strong>
          <span class="badge">${t.scope === "read_only" ? "read-only" : "read-write"}</span>
          <span class="hint">created ${new Date(t.created_at).toLocaleString()}${
            t.last_used_at ? ` · last used ${new Date(t.last_used_at).toLocaleString()}` : " · never used"
          }</span>
        </div>
        <button class="danger revoke-api-token-btn" data-id="${t.id}">Revoke</button>
      </div>
    `).join("");
    el.querySelectorAll(".revoke-api-token-btn").forEach((btn) => {
      btn.addEventListener("click", () => revokeApiToken(Number(btn.dataset.id)));
    });
  } catch (e) {
    el.innerHTML = `<p class="hint">Error: ${e.message}</p>`;
  }
}

async function createApiToken() {
  const nameInput = $("#new-api-token-name");
  const name = nameInput.value.trim();
  if (!name) return;
  const scope = $("#new-api-token-scope").value;
  const reveal = $("#new-api-token-reveal");
  try {
    const data = await api("/api/settings/tokens", { method: "POST", body: JSON.stringify({ name, scope }) });
    nameInput.value = "";
    reveal.classList.remove("hidden");
    reveal.innerHTML = `Token for "${data.name}" (${data.scope === "read_only" ? "read-only" : "read-write"}, copy it now — it won't be shown again): <code>${data.token}</code>`;
    // This browser needs the new token too, or its own next request 401s
    // the moment this becomes the first token ever created.
    setStoredApiToken(data.token);
    loadApiTokensList();
  } catch (e) {
    reveal.classList.remove("hidden");
    reveal.textContent = `Error: ${e.message}`;
  }
}

async function revokeApiToken(id) {
  const ok = await showConfirm("Revoke this token? Any client still using it will start getting 401 errors.");
  if (!ok) return;
  try {
    await api(`/api/settings/tokens/${id}`, { method: "DELETE" });
    loadApiTokensList();
  } catch (e) {
    $("#api-tokens-list").insertAdjacentHTML("afterbegin", `<p class="hint">Error: ${e.message}</p>`);
  }
}

async function disableApiToken() {
  const ok = await showConfirm("Disable the API token? Every /api/* request will then be allowed with no token.");
  if (!ok) return;
  $("#settings-status").textContent = "Saving...";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify({ api_token: "" }) });
    setStoredApiToken("");
    $("#settings-status").textContent = "API token disabled.";
    loadSettings();
  } catch (e) {
    $("#settings-status").textContent = `Error: ${e.message}`;
  }
}

async function exportLibrary() {
  const status = $("#backup-status");
  status.textContent = "Exporting...";
  try {
    const data = await api("/api/library/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `media-manager-library-${data.exported_at.slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    status.textContent = `Exported ${data.items.length} item(s).`;
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
}

async function importLibrary(file) {
  const status = $("#backup-status");
  if (!file) return;
  status.textContent = "Importing...";
  try {
    const text = await file.text();
    const parsed = JSON.parse(text);
    const data = await api("/api/library/import", { method: "POST", body: JSON.stringify({ items: parsed.items || [] }) });
    status.textContent = `Imported ${data.imported} item(s), skipped ${data.skipped} already-tracked item(s).`;
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
}

async function syncWatchedFromMediaServers() {
  const btn = $("#sync-watched-btn");
  const status = $("#sync-watched-status");
  btn.disabled = true;
  status.textContent = "Syncing…";
  try {
    const data = await api("/api/library/sync-watched", { method: "POST" });
    status.textContent = data.updated > 0
      ? `Marked ${data.updated} movie(s) watched.`
      : "No changes — nothing new to mark watched, or no server configured.";
    if (data.updated > 0) loadMoviesGallery(); // refetch -- state.movieItems is stale after a server-side watched change
  } catch (e) {
    status.textContent = `Sync failed: ${e.message}`;
  } finally {
    btn.disabled = false;
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

    if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(e.key)) {
      const ctx = activeGalleryContext();
      if (ctx) {
        e.preventDefault();
        moveGalleryFocus(e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : -1);
        return;
      }
    }
    if (e.key === "Enter") {
      const ctx = activeGalleryContext();
      if (ctx && activateGalleryFocus()) return;
    }

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

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => { /* PWA install just won't be offered; app still works */ });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupTabs();
  setupGlobalSearch();
  setupTheme();
  setupKeyboardShortcuts();
  setupFilterPersistence();
  loadStatus();
  loadMoviesGallery();
  loadViewers();
  wireRecommendationsToggle("movie", "movies-recommendations-row", "movies-recommendations-cards");
  wireRecommendationsToggle("tv", "tv-recommendations-row", "tv-recommendations-cards");
  requestNotificationPermission();
  pollNotifications();
  pollNewFiles();

  $("#scan-btn").addEventListener("click", scanAndPreview);
  $("#select-all-btn").addEventListener("click", () => {
    const boxes = $all(".row-check");
    const allChecked = boxes.every((b) => b.checked);
    boxes.forEach((b) => { b.checked = !allChecked; });
  });
  $("#approve-btn").addEventListener("click", approveSelected);
  $("#bulk-change-match-btn").addEventListener("click", openBulkMatchPicker);
  $("#track-add-movie-btn").addEventListener("click", () => openTrackAddModal("movie"));
  $("#track-add-tv-btn").addEventListener("click", () => openTrackAddModal("tv"));
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
  $("#disable-api-token-btn").addEventListener("click", disableApiToken);
  $("#create-api-token-btn").addEventListener("click", createApiToken);
  $("#naming-templates-form").addEventListener("submit", saveNamingTemplates);
  $("#media-server-form").addEventListener("submit", saveMediaServerSettings);
  $("#sync-watched-btn").addEventListener("click", syncWatchedFromMediaServers);
  $("#webdav-backup-form").addEventListener("submit", saveWebdavBackupSettings);
  $("#create-viewer-btn").addEventListener("click", createViewerAction);
  $("#viewer-select").addEventListener("change", (e) => {
    setActiveViewerId(e.target.value ? Number(e.target.value) : null);
    loadMoviesGallery();
    loadTvGallery();
  });
  $("#export-library-btn").addEventListener("click", exportLibrary);
  $("#import-library-input").addEventListener("change", (e) => {
    importLibrary(e.target.files[0]);
    e.target.value = "";
  });
  $("#browse-refresh-btn").addEventListener("click", loadBrowse);
  $("#cleanup-orphans-btn").addEventListener("click", cleanupOrphans);
  $("#cleanup-artwork-btn").addEventListener("click", cleanupOrphanedArtwork);
  $("#review-duplicates-btn").addEventListener("click", openDuplicatesModal);
  $("#duplicates-close-btn").addEventListener("click", () => $("#duplicates-modal").classList.add("hidden"));
  $("#browse-type").addEventListener("change", loadBrowse);
  $("#browse-filter").addEventListener("change", renderBrowseTable);
  $("#browse-search").addEventListener("input", renderBrowseTable);
  $("#browse-tracked").addEventListener("change", renderBrowseTable);
  $("#browse-sort").addEventListener("change", renderBrowseTable);
  $("#browse-organize-btn").addEventListener("click", organizeSelected);
  $("#browse-delete-selected-btn").addEventListener("click", deleteSelectedBrowseItems);
  $("#browse-select-all-btn").addEventListener("click", () => {
    const boxes = $all(".browse-check");
    const allChecked = boxes.every((b) => b.checked);
    boxes.forEach((b) => { b.checked = !allChecked; });
  });

  $("#movies-search").addEventListener("input", renderMoviesGallery);
  $("#movies-sort").addEventListener("change", renderMoviesGallery);
  $("#movies-filter").addEventListener("change", renderMoviesGallery);
  $("#movies-genre").addEventListener("change", renderMoviesGallery);
  $("#movies-tag").addEventListener("change", renderMoviesGallery);
  $("#movies-resolution").addEventListener("change", renderMoviesGallery);
  $("#movies-watch").addEventListener("change", renderMoviesGallery);
  $("#movies-year").addEventListener("change", renderMoviesGallery);
  $("#movies-rating").addEventListener("change", renderMoviesGallery);
  $("#movies-added").addEventListener("change", renderMoviesGallery);
  $("#movies-select-all-btn").addEventListener("click", () => {
    const boxes = $all("#movies-gallery .gallery-select");
    const allChecked = boxes.every((b) => b.checked);
    boxes.forEach((b) => { b.checked = !allChecked; });
  });
  $("#movies-mark-watched-btn").addEventListener("click", async () => {
    const ids = $all("#movies-gallery .gallery-select:checked").map((b) => Number(b.dataset.selectId));
    await markWatchedBatch(ids, true);
    loadMoviesGallery();
  });
  $("#movies-mark-unwatched-btn").addEventListener("click", async () => {
    const ids = $all("#movies-gallery .gallery-select:checked").map((b) => Number(b.dataset.selectId));
    await markWatchedBatch(ids, false);
    loadMoviesGallery();
  });
  $("#movies-rename-selected-btn").addEventListener("click", async () => {
    const ids = $all("#movies-gallery .gallery-select:checked").map((b) => Number(b.dataset.selectId));
    const items = state.movieItems.filter((i) => ids.includes(i.id) && i.final_path);
    if (items.length === 0) return;
    const sizeByPath = Object.fromEntries(items.map((i) => [i.final_path, i.size_bytes]));
    await organizePaths(items.map((i) => i.final_path), sizeByPath);
  });
  $("#movies-delete-selected-btn").addEventListener("click", async () => {
    const ids = $all("#movies-gallery .gallery-select:checked").map((b) => Number(b.dataset.selectId));
    const items = state.movieItems.filter((i) => ids.includes(i.id) && i.final_path);
    if (items.length === 0) return;
    const ok = await showConfirm(`Permanently delete ${items.length} movie file(s) from disk? This cannot be undone.`);
    if (!ok) return;
    try {
      const data = await api("/api/library/delete-batch", {
        method: "POST",
        body: JSON.stringify({ paths: items.map((i) => i.final_path) }),
      });
      if (data.errors.length) $("#movies-count").textContent = `${data.errors.length} deletion(s) failed: ${data.errors.join("; ")}`;
      loadMoviesGallery();
    } catch (e) {
      $("#movies-count").textContent = `Error: ${e.message}`;
    }
  });
  $("#movies-refresh-metadata-btn").addEventListener("click", async () => {
    const ids = $all("#movies-gallery .gallery-select:checked").map((b) => Number(b.dataset.selectId));
    await refreshMetadataBatch(ids, $("#movies-count"));
    loadMoviesGallery();
  });
  $("#movies-export-btn").addEventListener("click", exportMoviesView);

  $("#tv-search").addEventListener("input", renderTvGallery);
  $("#tv-sort").addEventListener("change", renderTvGallery);
  $("#tv-filter").addEventListener("change", renderTvGallery);
  $("#tv-genre").addEventListener("change", renderTvGallery);
  $("#tv-tag").addEventListener("change", renderTvGallery);
  $("#tv-resolution").addEventListener("change", renderTvGallery);
  $("#tv-watch").addEventListener("change", renderTvGallery);
  $("#tv-year").addEventListener("change", renderTvGallery);
  $("#tv-rating").addEventListener("change", renderTvGallery);
  $("#tv-added").addEventListener("change", renderTvGallery);
  $("#tv-select-all-btn").addEventListener("click", () => {
    const boxes = $all("#tv-gallery .gallery-select");
    const allChecked = boxes.every((b) => b.checked);
    boxes.forEach((b) => { b.checked = !allChecked; });
  });
  $("#tv-mark-watched-btn").addEventListener("click", async () => {
    const titles = new Set($all("#tv-gallery .gallery-select:checked").map((b) => b.dataset.selectTitle));
    const ids = state.tvItems.filter((i) => titles.has(i.title)).map((i) => i.id);
    await markWatchedBatch(ids, true);
    loadTvGallery();
  });
  $("#tv-mark-unwatched-btn").addEventListener("click", async () => {
    const titles = new Set($all("#tv-gallery .gallery-select:checked").map((b) => b.dataset.selectTitle));
    const ids = state.tvItems.filter((i) => titles.has(i.title)).map((i) => i.id);
    await markWatchedBatch(ids, false);
    loadTvGallery();
  });
  $("#tv-rename-selected-btn").addEventListener("click", async () => {
    const titles = new Set($all("#tv-gallery .gallery-select:checked").map((b) => b.dataset.selectTitle));
    const items = state.tvItems.filter((i) => titles.has(i.title) && i.final_path);
    if (items.length === 0) return;
    const sizeByPath = Object.fromEntries(items.map((i) => [i.final_path, i.size_bytes]));
    await organizePaths(items.map((i) => i.final_path), sizeByPath);
  });
  $("#tv-refresh-metadata-btn").addEventListener("click", async () => {
    const titles = new Set($all("#tv-gallery .gallery-select:checked").map((b) => b.dataset.selectTitle));
    const ids = state.tvItems.filter((i) => titles.has(i.title)).map((i) => i.id);
    await refreshMetadataBatch(ids, $("#tv-count"));
    loadTvGallery();
  });
  $("#tv-export-btn").addEventListener("click", exportTvView);

  $("#history-type-filter").addEventListener("change", loadHistory);
  $("#history-status-filter").addEventListener("change", loadHistory);
  $("#history-since-filter").addEventListener("change", loadHistory);
  $("#history-until-filter").addEventListener("change", loadHistory);
  $("#history-export-btn").addEventListener("click", exportHistoryView);

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
