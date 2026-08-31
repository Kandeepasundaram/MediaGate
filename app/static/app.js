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
      if (btn.dataset.tab === "notifications") { loadNotifications(); loadTrackedList(); loadNotificationHistory(); }
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

function wireWatchedToggles(container, items) {
  container.querySelectorAll(".watched-toggle input").forEach((input) => {
    input.addEventListener("click", (e) => e.stopPropagation());
    input.addEventListener("change", async () => {
      try {
        await toggleWatched(Number(input.dataset.id), input.checked);
        if (items) {
          const item = items.find((i) => i.id === Number(input.dataset.id));
          if (item) item.watched = input.checked;
        }
        setWatchedBadge(input.closest(".gallery-card"), input.checked);
      } catch (e) {
        input.checked = !input.checked;
      }
    });
  });
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
  const { query, sortMode, titleKey, filterMode, genreFilter, resolutionFilter, watchFilter, yearFilter, ratingFilter, addedFilter } = opts;
  let out = items;
  if (filterMode === "unmatched") out = out.filter((i) => i.tmdb_id == null);
  if (genreFilter) out = out.filter((i) => (i.genres || []).includes(genreFilter));
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
  const savedTv = restoreFilterState("tv", TV_FILTER_IDS);
  state.pendingGenreRestore.tv = savedTv["tv-genre"] || null;
  state.pendingYearRestore.tv = savedTv["tv-year"] || null;

  const movieIds = [...MOVIE_FILTER_IDS, "movies-genre", "movies-year"];
  movieIds.forEach((id) => {
    const el = document.getElementById(id);
    el.addEventListener(id.endsWith("-search") ? "input" : "change", () => saveFilterState("movies", movieIds));
  });
  const tvIds = [...TV_FILTER_IDS, "tv-genre", "tv-year"];
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

function distinctYears(items) {
  return Array.from(new Set(items.map((i) => i.year).filter((y) => y != null))).sort((a, b) => b - a);
}

function populateYearOptions(selectEl, items, previousValue) {
  const years = distinctYears(items);
  selectEl.innerHTML = `<option value="">All years</option>` + years.map((y) => `<option value="${y}">${y}</option>`).join("");
  if (previousValue && years.includes(Number(previousValue))) selectEl.value = previousValue;
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
  const resolutionFilter = $("#movies-resolution").value;
  const watchFilter = $("#movies-watch").value;
  const yearFilter = $("#movies-year").value;
  const ratingFilter = $("#movies-rating").value;
  const addedFilter = $("#movies-added").value;
  const items = filterAndSort(state.movieItems, { query, sortMode, titleKey: "title", filterMode, genreFilter, resolutionFilter, watchFilter, yearFilter, ratingFilter, addedFilter });
  const signature = JSON.stringify([query, sortMode, filterMode, genreFilter, resolutionFilter, watchFilter, yearFilter, ratingFilter, addedFilter]);
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
      <div class="gallery-badges" data-movie-badges="${item.tmdb_id ?? ""}">
        ${(item.tmdb_id == null && !item.manual_override) ? `<span class="badge badge-warn" title="Unidentified — no TMDB match yet">⚠</span>` : ""}
        ${item.watched ? `<span class="badge badge-ok" title="Watched">✓</span>` : ""}
      </div>
      ${posterMarkup(item.title, item.poster_path)}
      <div class="gallery-info">
        <div class="gallery-title" title="${item.title}">${item.title}</div>
        <div class="gallery-meta">
          <span>${item.year || ""}${item.vote_average ? ` · ★ ${item.vote_average.toFixed(1)}` : ""}</span>
          <label class="watched-toggle">
            <input type="checkbox" data-id="${item.id}" ${item.watched ? "checked" : ""}>
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
    const data = await api("/api/library/movies");
    state.movieItems = data.items;
    const previousGenre = state.pendingGenreRestore.movies ?? $("#movies-genre").value;
    state.pendingGenreRestore.movies = null;
    populateGenreOptions($("#movies-genre"), state.movieItems, previousGenre);
    const previousYear = state.pendingYearRestore.movies ?? $("#movies-year").value;
    state.pendingYearRestore.movies = null;
    populateYearOptions($("#movies-year"), state.movieItems, previousYear);
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
      shows.set(key, {
        title: item.title, poster_path: item.poster_path, tmdb_id: item.tmdb_id, overview: item.overview,
        manual_override: item.manual_override, vote_average: item.vote_average, genres: item.genres, year: item.year, episodes: [],
      });
    }
    shows.get(key).episodes.push(item);
  }
  for (const show of shows.values()) {
    show.episodes.sort((a, b) => (a.season_number - b.season_number) || (a.episode_number - b.episode_number));
    show.watched = show.episodes.every((e) => e.watched);
    show.archived_at = show.episodes.reduce((min, e) => (e.archived_at && (!min || e.archived_at < min)) ? e.archived_at : min, null);
  }
  return Array.from(shows.values());
}

function renderTvGallery() {
  const gallery = $("#tv-gallery");
  const query = $("#tv-search").value.trim();
  const sortMode = $("#tv-sort").value;
  const filterMode = $("#tv-filter").value;
  const genreFilter = $("#tv-genre").value;
  const resolutionFilter = $("#tv-resolution").value;
  const watchFilter = $("#tv-watch").value;
  const yearFilter = $("#tv-year").value;
  const ratingFilter = $("#tv-rating").value;
  const addedFilter = $("#tv-added").value;
  const allShows = groupEpisodesByShow(state.tvItems);
  const shows = filterAndSort(allShows, { query, sortMode, titleKey: "title", filterMode, genreFilter, resolutionFilter, watchFilter, yearFilter, ratingFilter, addedFilter });
  const signature = JSON.stringify([query, sortMode, filterMode, genreFilter, resolutionFilter, watchFilter, yearFilter, ratingFilter, addedFilter]);
  const { visible, total, limitKey } = paginateGallery("tv", shows, signature);

  $("#tv-count").textContent = `${state.tvItems.length} episode(s) across ${allShows.length} show(s)` +
    (total !== allShows.length ? ` (${total} shown)` : "");

  if (state.tvItems.length === 0) {
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
      <div class="gallery-badges" data-tv-badges="${show.tmdb_id ?? ""}">
        ${(show.tmdb_id == null && !show.manual_override) ? `<span class="badge badge-warn" title="Unidentified — no TMDB match yet">⚠</span>` : ""}
        ${show.watched ? `<span class="badge badge-ok" title="All episodes watched">✓</span>` : ""}
      </div>
      ${posterMarkup(show.title, show.poster_path)}
      <div class="gallery-info">
        <div class="gallery-title" title="${show.title}">${show.title}</div>
        <div class="gallery-meta">
          <span>${show.episodes.length} episode(s)</span>
        </div>
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
    const data = await api("/api/library/tv");
    state.tvItems = data.items;
    const previousGenre = state.pendingGenreRestore.tv ?? $("#tv-genre").value;
    state.pendingGenreRestore.tv = null;
    populateGenreOptions($("#tv-genre"), state.tvItems, previousGenre);
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
    if (item.tmdb_id != null) loadMovieStatus(item.tmdb_id);
  } else {
    const show = pane.data;
    content.innerHTML = `
      <div id="detail-tv-status"></div>
      ${(show.tmdb_id == null && !show.manual_override) ? `<p class="unidentified-badge">⚠ Unidentified — no TMDB match yet</p>` : ""}
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
      renderTvGallery(); // keeps the gallery card's "all watched" badge in sync
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
  if (!status || !status.data_available || status.latest_known_season == null) return null;
  const localMaxSeason = Math.max(...episodes.map((e) => e.season_number));
  const localEpisodeCountInMaxSeason = episodes.filter((e) => e.season_number === localMaxSeason).length;

  let gapMessage = null;
  if (status.latest_known_season > localMaxSeason) {
    gapMessage = `Season ${status.latest_known_season} is out — you have up to season ${localMaxSeason}.`;
  } else if (
    status.latest_known_season === localMaxSeason &&
    status.latest_season_episode_count != null &&
    status.latest_season_episode_count > localEpisodeCountInMaxSeason
  ) {
    gapMessage = `Season ${localMaxSeason} has ${status.latest_season_episode_count} episode(s) — you have ${localEpisodeCountInMaxSeason}.`;
  }

  return {
    hasGap: gapMessage != null,
    gapMessage,
    totalArchived: episodes.length,
    totalEpisodes: status.total_episodes,
    statusLabel: status.status,
  };
}

async function loadTvStatus(tmdbId, episodes) {
  const el = $("#detail-tv-status");
  if (!el) return;
  const status = await getTvStatus(tmdbId);
  const info = computeTvStatusInfo(status, episodes);
  if (!info) {
    el.innerHTML = "";
    return;
  }
  const fraction = info.totalEpisodes != null
    ? `${info.totalArchived} of ${info.totalEpisodes} episodes archived`
    : `${info.totalArchived} episode(s) archived`;
  const parts = info.hasGap ? [info.gapMessage, fraction] : [fraction];
  const cls = info.hasGap ? "status-banner" : "status-banner status-banner-ok";
  const icon = info.hasGap ? "📺" : "✅";
  el.innerHTML = `<div class="${cls}">${icon} ${parts.join(" · ")}${info.statusLabel ? ` <span class="hint">(${info.statusLabel})</span>` : ""}</div>`;
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
  el.innerHTML = `<div class="${cls}">${icon} ${parts.join(" · ")}</div>`;
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
      <td>${item.media_type === "movie" ? renderMovieNameCell(item, i) : renderTvNameCell(item, i)}</td>
      <td>${item.media_type}</td>
      <td>${formatBytes(state.sizeByPath[item.source_path])}</td>
      <td title="${item.overview}">${item.overview.slice(0, 80)}</td>
      <td><button class="change-match-btn" data-index="${i}">Change Match</button></td>
    </tr>
  `).join("");
  $all(".change-match-btn").forEach((btn) => {
    btn.addEventListener("click", () => openMatchPicker(Number(btn.dataset.index)));
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
  loadLibraryHealth();
}

async function loadLibraryHealth() {
  const card = $("#library-health-card");
  const summary = $("#library-health-summary");
  const cleanupBtn = $("#cleanup-orphans-btn");
  try {
    const data = await api("/api/library/health");
    const orphanCount = data.orphans.length;
    const duplicateCount = data.duplicates.length;
    if (orphanCount === 0 && duplicateCount === 0) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");
    const parts = [];
    if (orphanCount > 0) parts.push(`${orphanCount} orphaned record(s) (file missing on disk)`);
    if (duplicateCount > 0) parts.push(`${duplicateCount} duplicate group(s)`);
    summary.textContent = parts.join(" — ");
    cleanupBtn.classList.toggle("hidden", orphanCount === 0);
  } catch (e) {
    card.classList.add("hidden");
  }
}

async function cleanupOrphans() {
  const ok = await showConfirm("Remove database records for archived files that no longer exist on disk? This does not touch any files.");
  if (!ok) return;
  try {
    const data = await api("/api/library/orphans/cleanup", { method: "POST" });
    $("#browse-status").textContent = `Removed ${data.removed} orphaned record(s).`;
    loadLibraryHealth();
  } catch (e) {
    $("#browse-status").textContent = `Error: ${e.message}`;
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

async function deleteSelectedBrowseItems() {
  const selected = $all(".browse-check:checked").map((cb) => state.browseFiltered[Number(cb.dataset.index)]);
  if (selected.length === 0) return;
  const ok = await showConfirm(`Permanently delete ${selected.length} file(s)? This cannot be undone.`);
  if (!ok) return;

  $("#browse-status").textContent = "Deleting...";
  try {
    const data = await api("/api/library/delete-batch", {
      method: "POST",
      body: JSON.stringify({ paths: selected.map((item) => item.path) }),
    });
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
    $("#setting-auto-track-new").checked = !!s.auto_track_new;
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
    auto_track_new: $("#setting-auto-track-new").checked,
  };
  const keyValue = $("#setting-tmdb-key").value;
  if (keyValue) payload.tmdb_api_key = keyValue;
  const omdbKeyValue = $("#setting-omdb-key").value;
  if (omdbKeyValue) payload.omdb_api_key = omdbKeyValue;
  const apiTokenValue = $("#setting-api-token").value;
  if (apiTokenValue) payload.api_token = apiTokenValue;

  $("#settings-status").textContent = "Saving...";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    $("#setting-tmdb-key").value = "";
    $("#setting-omdb-key").value = "";
    $("#setting-api-token").value = "";
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

async function saveMediaServerSettings(e) {
  e.preventDefault();
  const payload = {
    plex_url: $("#setting-plex-url").value.trim(),
    jellyfin_url: $("#setting-jellyfin-url").value.trim(),
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
  setupTheme();
  setupKeyboardShortcuts();
  setupFilterPersistence();
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
  $("#disable-api-token-btn").addEventListener("click", disableApiToken);
  $("#media-server-form").addEventListener("submit", saveMediaServerSettings);
  $("#export-library-btn").addEventListener("click", exportLibrary);
  $("#import-library-input").addEventListener("change", (e) => {
    importLibrary(e.target.files[0]);
    e.target.value = "";
  });
  $("#browse-refresh-btn").addEventListener("click", loadBrowse);
  $("#cleanup-orphans-btn").addEventListener("click", cleanupOrphans);
  $("#browse-type").addEventListener("change", loadBrowse);
  $("#browse-filter").addEventListener("change", renderBrowseTable);
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

  $("#tv-search").addEventListener("input", renderTvGallery);
  $("#tv-sort").addEventListener("change", renderTvGallery);
  $("#tv-filter").addEventListener("change", renderTvGallery);
  $("#tv-genre").addEventListener("change", renderTvGallery);
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
  $("#tv-rename-selected-btn").addEventListener("click", async () => {
    const titles = new Set($all("#tv-gallery .gallery-select:checked").map((b) => b.dataset.selectTitle));
    const items = state.tvItems.filter((i) => titles.has(i.title) && i.final_path);
    if (items.length === 0) return;
    const sizeByPath = Object.fromEntries(items.map((i) => [i.final_path, i.size_bytes]));
    await organizePaths(items.map((i) => i.final_path), sizeByPath);
  });

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
