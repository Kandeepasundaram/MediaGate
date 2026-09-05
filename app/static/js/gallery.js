/**
 * Movies/TV gallery: rendering, filtering/sorting, saved filter presets, badges, CSV export, and per-item actions (watched toggle, notes, viewer switch).
 */

import { escapeAttr } from "./archive-tab.js";
import { $, api, getStoredApiToken, state } from "./core.js";
import { computeMovieStatusInfo, computeTvStatusInfo, getMovieStatus, getTvStatus, openDetailPane, renderTvBody } from "./detail-pane.js";

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

// ---- Movies / TV galleries ----
const TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w342";

export function posterUrl(posterPath) {
  if (!posterPath) return null;
  return posterPath.startsWith("http") ? posterPath : `${TMDB_IMAGE_BASE}${posterPath}`;
}

export function posterMarkup(title, posterPath) {
  const url = posterUrl(posterPath);
  return url
    ? `<img class="gallery-poster" src="${url}" alt="${title}" loading="lazy">`
    : `<div class="gallery-poster-placeholder">${title}</div>`;
}

// Hover preview: reuses overview/rating/genres already in state from the
// gallery's own load call, so this costs nothing extra over the network --
// just a CSS opacity reveal on an overlay div that ships hidden in the card.
function posterWithOverlayMarkup(title, posterPath, overview, voteAverage, genres) {
  const meta = [voteAverage ? `★ ${voteAverage.toFixed(1)}` : null, (genres || []).slice(0, 2).join(", ") || null]
    .filter(Boolean).join(" · ");
  return `
    <div class="gallery-poster-wrap">
      ${posterMarkup(title, posterPath)}
      <div class="gallery-poster-overlay">
        ${meta ? `<div class="gallery-poster-overlay-meta">${meta}</div>` : ""}
        <div class="gallery-poster-overlay-text">${overview || "No overview available."}</div>
      </div>
    </div>
  `;
}

// Per-viewer watch state: state.activeViewerId (null = "All viewers", the
// original single-flag behavior every other feature -- filters, Continue
// Watching, CSV export -- still reads). When a viewer is active,
// toggleWatched writes to that viewer's own record instead of the shared
// media_items.watched flag, and effectiveWatched() reads it back the same
// way. See viewers/viewer_watched_items in database.py.
const ACTIVE_VIEWER_KEY = "media-manager:active-viewer-id";

export function getActiveViewerId() {
  if (state.activeViewerId !== undefined) return state.activeViewerId;
  try {
    const stored = localStorage.getItem(ACTIVE_VIEWER_KEY);
    state.activeViewerId = stored ? Number(stored) : null;
  } catch (e) {
    state.activeViewerId = null;
  }
  return state.activeViewerId;
}

export function setActiveViewerId(id) {
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
export function effectiveWatched(item) {
  return getActiveViewerId() != null ? !!item.viewer_watched : !!item.watched;
}

export async function downloadMovieNote(itemId) {
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

export async function saveMovieNote(itemId) {
  const status = $("#detail-note-status");
  status.textContent = "Saving…";
  try {
    const data = await api(`/api/library/${itemId}/note/save`, { method: "POST" });
    status.textContent = `Saved to ${data.path}`;
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
}

export async function downloadTvNote(tmdbId) {
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

export async function saveTvNote(tmdbId) {
  const status = $("#detail-note-status");
  status.textContent = "Saving…";
  try {
    const data = await api(`/api/library/tv-shows/${tmdbId}/note/save`, { method: "POST" });
    status.textContent = `Saved to ${data.path}`;
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
}

export async function toggleWatched(itemId, watched) {
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

// ---- Pinning ----
// Reuses the existing user-tags column (no schema/API change) -- a plain
// tag named PIN_TAG, added/removed via the same single-item tags endpoint
// the detail pane's tag editor already uses. A movie is one media_items
// row (one id); a TV show card represents many episode rows, so pinning a
// show loops the same per-item call across every episode id.
export const PIN_TAG = "★ Pinned";

export function isPinned(item) {
  return (item.tags || []).includes(PIN_TAG);
}

async function setTagsForId(id, tags) {
  return api(`/api/library/${id}/tags`, { method: "POST", body: JSON.stringify({ tags }) });
}

export async function togglePin(item, ids) {
  const pinning = !isPinned(item);
  await Promise.all(ids.map((id) => {
    const owner = item.episodes ? item.episodes.find((e) => e.id === id) : item;
    const current = (owner?.tags || []).slice();
    const next = pinning ? Array.from(new Set([...current, PIN_TAG])) : current.filter((t) => t !== PIN_TAG);
    return setTagsForId(id, next);
  }));
  const targets = item.episodes ? item.episodes : [item];
  targets.forEach((t) => {
    t.tags = pinning ? Array.from(new Set([...(t.tags || []), PIN_TAG])) : (t.tags || []).filter((tg) => tg !== PIN_TAG);
  });
}

function pinButtonMarkup(pinned, index) {
  return `<button class="pin-toggle-btn${pinned ? " pinned" : ""}" data-pin-index="${index}" title="${pinned ? "Unpin" : "Pin to top"}">${pinned ? "★" : "☆"}</button>`;
}

// Indexed by data-pin-index rather than DOM position -- some cards (orphan
// TV shows with no episode rows left to tag) render no pin button at all,
// which would desync a plain positional NodeList-to-items mapping.
function wirePinToggles(container, items, idsForItem, rerender) {
  container.querySelectorAll(".pin-toggle-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const item = items[Number(btn.dataset.pinIndex)];
      btn.disabled = true;
      try {
        await togglePin(item, idsForItem(item));
        rerender();
      } catch (err) {
        btn.disabled = false;
      }
    });
  });
}

// Stable secondary sort: pinned items float to the top of whatever the
// active sort/filter produced, without disturbing relative order within
// each group (pinned vs. not) -- Array.prototype.sort is stable in every
// engine this app targets, so a single comparator on the pinned flag is
// enough on top of the already-sorted array from filterAndSort.
function floatPinnedToTop(items) {
  return items.slice().sort((a, b) => (isPinned(b) ? 1 : 0) - (isPinned(a) ? 1 : 0));
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
export const MOVIE_PRESET_IDS = [...MOVIE_FILTER_IDS, "movies-genre", "movies-year", "movies-tag"];
export const TV_PRESET_IDS = [...TV_FILTER_IDS, "tv-genre", "tv-year", "tv-tag"];

// ---- Named saved filter presets (per-tab, localStorage) ----
// Distinct from the auto-persisted "last used filters" above (which always
// restores the *most recent* combo silently): this lets a user save several
// named combos ("Unwatched 4K", "Kids' shows") and jump between them on
// purpose via the preset dropdown.
const FILTER_PRESET_KEY_PREFIX = "media-manager:filter-presets:";

function loadPresets(prefix) {
  try {
    return JSON.parse(localStorage.getItem(FILTER_PRESET_KEY_PREFIX + prefix) || "{}");
  } catch (e) {
    return {};
  }
}

function persistPresets(prefix, presets) {
  try {
    localStorage.setItem(FILTER_PRESET_KEY_PREFIX + prefix, JSON.stringify(presets));
  } catch (e) { /* private browsing / storage disabled -- presets just won't persist */ }
}

export function saveFilterPreset(prefix, name, controlIds) {
  name = name.trim();
  if (!name) return;
  const presets = loadPresets(prefix);
  const values = {};
  controlIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el) values[id] = el.value;
  });
  presets[name] = values;
  persistPresets(prefix, presets);
  populatePresetSelect(prefix, name);
}

export function applyFilterPreset(prefix, name, controlIds) {
  const presets = loadPresets(prefix);
  const values = presets[name];
  if (!values) return false;
  controlIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el && values[id] !== undefined) el.value = values[id];
  });
  return true;
}

export function deleteFilterPreset(prefix, name) {
  const presets = loadPresets(prefix);
  delete presets[name];
  persistPresets(prefix, presets);
  populatePresetSelect(prefix);
}

export function populatePresetSelect(prefix, selectValue) {
  const select = document.getElementById(`${prefix}-preset-select`);
  if (!select) return;
  const presets = loadPresets(prefix);
  const names = Object.keys(presets).sort();
  select.innerHTML = `<option value="">Saved views…</option>` + names.map((n) => `<option value="${escapeAttr(n)}">${escapeAttr(n)}</option>`).join("");
  if (selectValue && names.includes(selectValue)) select.value = selectValue;
}

export function setupFilterPersistence() {
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

  populatePresetSelect("movies");
  populatePresetSelect("tv");
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

export async function refreshMetadataBatch(ids, statusEl) {
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

export async function markWatchedBatch(ids, watched) {
  if (ids.length === 0) return;
  await api("/api/library/watched-batch", { method: "POST", body: JSON.stringify({ ids, watched }) });
}

export async function applyTagBatch(ids, tag) {
  if (ids.length === 0 || !tag.trim()) return;
  await api("/api/library/tags-batch", { method: "POST", body: JSON.stringify({ ids, tag: tag.trim() }) });
}

export function renderMoviesGallery() {
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
  const items = floatPinnedToTop(filterAndSort(state.movieItems, { query, sortMode, titleKey: "title", filterMode, genreFilter, tagFilter, resolutionFilter, watchFilter, yearFilter, ratingFilter, addedFilter }));
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
      ${pinButtonMarkup(isPinned(item), i)}
      <div class="gallery-badges" data-movie-badges="${item.tmdb_id ?? ""}">
        ${(item.tmdb_id == null && !item.manual_override) ? `<span class="badge badge-warn" title="Unidentified — no TMDB match yet">⚠</span>` : ""}
        ${effectiveWatched(item) ? `<span class="badge badge-ok" title="Watched">✓</span>` : ""}
        ${mediaBadges(item)}
      </div>
      ${posterWithOverlayMarkup(item.title, item.poster_path, item.overview, item.vote_average, item.genres)}
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
  wirePinToggles(gallery, visible, (item) => [item.id], renderMoviesGallery);
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

export function rowsToCsv(header, rows) {
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

export function downloadCsv(filename, csvText) {
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

export function exportMoviesView() {
  const items = filterAndSort(state.movieItems, currentMovieFilters());
  const header = ["Title", "Year", "Rating", "Resolution", "Watched", "Genres", "Tags", "TMDB ID", "Path"];
  const rows = items.map((i) => [
    i.title, i.year ?? "", i.vote_average ?? "", i.resolution ?? "",
    i.watched ? "yes" : "no", (i.genres || []).join("; "), (i.tags || []).join("; "), i.tmdb_id ?? "", i.final_path ?? "",
  ]);
  downloadCsv(`movies-export-${new Date().toISOString().slice(0, 10)}.csv`, rowsToCsv(header, rows));
}

export function exportTvView() {
  const shows = filterAndSort(groupEpisodesByShow(state.tvItems), currentTvFilters());
  const header = ["Show", "Year", "Rating", "Season", "Episode", "Episode Title", "Air Date", "Resolution", "Watched", "Genres", "Tags", "TMDB ID", "Path"];
  const rows = shows.flatMap((show) => show.episodes.map((ep) => [
    show.title, show.year ?? "", show.vote_average ?? "", ep.season_number ?? "", ep.episode_number ?? "",
    ep.episode_title ?? "", ep.air_date ?? "", ep.resolution ?? "", ep.watched ? "yes" : "no",
    (show.genres || []).join("; "), (show.tags || []).join("; "), show.tmdb_id ?? "", ep.final_path ?? "",
  ]));
  downloadCsv(`tv-export-${new Date().toISOString().slice(0, 10)}.csv`, rowsToCsv(header, rows));
}

export function activeGalleryContext() {
  if ($("#tab-movies").classList.contains("active")) return { container: $("#movies-gallery") };
  if ($("#tab-tv").classList.contains("active")) return { container: $("#tv-gallery") };
  return null;
}

// Linear (not row/column-aware) card-to-card nav -- simple, and the number
// of columns per row changes with window width anyway, so a strictly
// grid-aware up/down wouldn't stay correct without recomputing layout.
export function moveGalleryFocus(delta) {
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

export function activateGalleryFocus() {
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

// ---- Best-effort offline read cache (localStorage) ----
// Separate from sw.js's shell caching (which deliberately never caches
// /api/* responses -- see that file's own comment on why "always live
// data when online" matters more than instant paint here). This is a
// browser-side fallback only: on a *failed* fetch (offline, LAN
// unreachable from a phone that's roamed off the home network, server
// down) the last-successful gallery response is shown read-only instead
// of an empty error screen. Never consulted on a successful fetch.
const GALLERY_CACHE_KEY_PREFIX = "media-manager:gallery-cache:";

function cacheGalleryResponse(key, data) {
  try {
    localStorage.setItem(GALLERY_CACHE_KEY_PREFIX + key, JSON.stringify({ data, cachedAt: Date.now() }));
  } catch (e) { /* private browsing / storage disabled / quota exceeded -- offline fallback just won't be available */ }
}

function readCachedGalleryResponse(key) {
  try {
    const raw = localStorage.getItem(GALLERY_CACHE_KEY_PREFIX + key);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function skeletonGalleryMarkup(count = 12) {
  return Array.from({ length: count }, () => `
    <div class="gallery-skeleton-card">
      <div class="gallery-skeleton-poster"></div>
      <div class="gallery-skeleton-info">
        <div class="gallery-skeleton-line"></div>
        <div class="gallery-skeleton-line short"></div>
      </div>
    </div>
  `).join("");
}

function offlineBannerMarkup(cachedAt) {
  const when = new Date(cachedAt).toLocaleString();
  return `<p class="gallery-offline-banner">⚠ Showing cached data from ${when} — couldn't reach the server (offline, or it's down). Reload once you're back online.</p>`;
}

export async function loadMoviesGallery() {
  const gallery = $("#movies-gallery");
  gallery.innerHTML = skeletonGalleryMarkup();
  const viewerId = getActiveViewerId();
  const cacheKey = `movies${viewerId != null ? `:${viewerId}` : ""}`;
  try {
    const data = await api(`/api/library/movies${viewerId != null ? `?viewer_id=${viewerId}` : ""}`);
    cacheGalleryResponse(cacheKey, data);
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
    const cached = readCachedGalleryResponse(cacheKey);
    if (cached) {
      state.movieItems = cached.data.items;
      renderMoviesGallery();
      gallery.insertAdjacentHTML("afterbegin", offlineBannerMarkup(cached.cachedAt));
    } else {
      gallery.innerHTML = `<p class="gallery-empty">Error: ${e.message}</p>`;
    }
  }
}

export const TV_SHOW_STATUSES = [
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
export function mapApiStatusToManual(apiStatusLabel) {
  if (!apiStatusLabel) return null;
  const lower = apiStatusLabel.toLowerCase();
  if (lower.includes("cancel")) return "cancelled";
  if (lower.includes("end")) return "ended";
  return null;
}

export async function setTvShowStatus(tmdbId, status) {
  return api(`/api/library/tv-shows/${tmdbId}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
}

// orphanShows: tracked shows with zero episode files left on disk (see
// TvShowSummaryOut / GET /api/library/tv's orphaned_shows) -- rendered as
// show cards with an empty episodes array so a show stays visible (with its
// user-set status) even after every episode was deleted from disk.
export function groupEpisodesByShow(items, orphanShows = state.tvOrphanShows || []) {
  const shows = new Map();
  for (const item of items) {
    const key = item.title;
    if (!shows.has(key)) {
      shows.set(key, {
        title: item.title, poster_path: item.poster_path, tmdb_id: item.tmdb_id, overview: item.overview,
        manual_override: item.manual_override, vote_average: item.vote_average, genres: item.genres, tags: item.tags, year: item.year, episodes: [],
        show_status: item.show_status, personal_rating: item.personal_rating, personal_note: item.personal_note,
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
      personal_rating: orphan.personal_rating, personal_note: orphan.personal_note,
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
export function wireRecommendationsToggle(mediaType, rowId, cardsId) {
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
          <div class="hint">${item.year || ""}${item.because_of ? ` · because you have ${escapeAttr(item.because_of)}` : ""}</div>
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

export function renderTvGallery() {
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
  const shows = floatPinnedToTop(filterAndSort(allShows, { query, sortMode, titleKey: "title", filterMode, genreFilter, tagFilter, resolutionFilter, watchFilter, yearFilter, ratingFilter, addedFilter }));
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
      ${show.episodes.length > 0 ? pinButtonMarkup(isPinned(show), i) : ""}
      <div class="gallery-badges" data-tv-badges="${show.tmdb_id ?? ""}">
        ${(show.tmdb_id == null && !show.manual_override) ? `<span class="badge badge-warn" title="Unidentified — no TMDB match yet">⚠</span>` : ""}
        ${show.watched ? `<span class="badge badge-ok" title="All episodes watched">✓</span>` : ""}
        ${mediaBadges(show)}
      </div>
      ${posterWithOverlayMarkup(show.title, show.poster_path, show.overview, show.vote_average, show.genres)}
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
  wirePinToggles(gallery, visible, (show) => show.episodes.map((e) => e.id), renderTvGallery);
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

export async function loadTvGallery() {
  const gallery = $("#tv-gallery");
  gallery.innerHTML = skeletonGalleryMarkup();
  const viewerId = getActiveViewerId();
  const cacheKey = `tv${viewerId != null ? `:${viewerId}` : ""}`;
  try {
    const data = await api(`/api/library/tv${viewerId != null ? `?viewer_id=${viewerId}` : ""}`);
    cacheGalleryResponse(cacheKey, data);
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
    const cached = readCachedGalleryResponse(cacheKey);
    if (cached) {
      state.tvItems = cached.data.items;
      state.tvOrphanShows = cached.data.orphaned_shows || [];
      renderTvGallery();
      gallery.insertAdjacentHTML("afterbegin", offlineBannerMarkup(cached.cachedAt));
    } else {
      gallery.innerHTML = `<p class="gallery-empty">Error: ${e.message}</p>`;
    }
  }
}
