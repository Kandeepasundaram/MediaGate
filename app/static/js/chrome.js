/**
 * Page chrome: tab switching, the global search box, the light/dark theme toggle, and the top-bar status pill. Tab switching calls into every feature module's own load function, so this module has the widest fan-out of imports.
 */

import { escapeAttr } from "./archive-tab.js";
import { loadBrowse } from "./browse-tab.js";
import { $, $all, api } from "./core.js";
import { loadMoviesGallery, loadTvGallery } from "./gallery.js";
import { loadHistory } from "./history-tab.js";
import { loadNotificationHistory, loadNotifications, loadTrackerTab, loadUpcomingReleases } from "./notifications-tab.js";
import { loadApiTokensList, loadConfigHistory, loadSettings, loadViewers } from "./settings-tab.js";
import { loadBackgroundTaskStatus, loadInsights, loadStats, loadStorageStatus } from "./stats-tab.js";

// ---- Tabs ----
function activateTab(tabName) {
  $all(".tab-btn").forEach((b) => b.classList.remove("active"));
  $all(".tab-panel").forEach((p) => p.classList.remove("active"));
  $(`.tab-btn[data-tab="${tabName}"]`).classList.add("active");
  $(`#tab-${tabName}`).classList.add("active");
  if (tabName === "movies") loadMoviesGallery();
  if (tabName === "tv") loadTvGallery();
  if (tabName === "browse") loadBrowse();
  if (tabName === "notifications") { loadNotifications(); loadUpcomingReleases(); loadNotificationHistory(); }
  if (tabName === "tracker") loadTrackerTab();
  if (tabName === "history") loadHistory();
  if (tabName === "settings") { loadStats(); loadInsights(); loadSettings(); loadBackgroundTaskStatus(); loadStorageStatus(); loadApiTokensList(); loadConfigHistory(); loadViewers(); }
}

export function setupTabs() {
  $all(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => activateTab(btn.dataset.tab));
  });
}

// ---- Global search ----
let globalSearchDebounce = null;

export function setupGlobalSearch() {
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

export function setupTheme() {
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
export async function loadStatus() {
  try {
    const status = await api("/api/status");
    $("#tmdb-mode").textContent = `TMDB: ${status.tmdb_mode}`;
  } catch (e) {
    $("#tmdb-mode").textContent = "TMDB: offline";
  }
}
