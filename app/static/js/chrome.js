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
import { loadWatchlist } from "./watchlist-tab.js";

// ---- Tabs ----
const LAST_TAB_KEY = "media-manager:last-tab";
const SCROLL_KEY_PREFIX = "media-manager:scroll:";

function saveScrollPosition(tabName) {
  if (!tabName) return;
  try { localStorage.setItem(SCROLL_KEY_PREFIX + tabName, String(window.scrollY)); } catch (e) { /* private browsing / storage disabled */ }
}

// Content for the newly-active tab loads asynchronously (a gallery fetch,
// a table render, ...), so scrolling once immediately would just land at
// 0 on a page that isn't tall enough yet -- retry a handful of times as
// the content renders in instead of trying to await every tab's own load
// function (each has a different shape/signature).
export function restoreScrollPosition(tabName) {
  let y = null;
  try { y = localStorage.getItem(SCROLL_KEY_PREFIX + tabName); } catch (e) { /* private browsing / storage disabled */ }
  if (y == null) return;
  const target = Number(y);
  let attempts = 0;
  const tryScroll = () => {
    attempts++;
    window.scrollTo(0, target);
    if (attempts < 6) setTimeout(tryScroll, 150);
  };
  tryScroll();
}

let scrollSaveDebounce = null;
export function setupScrollPersistence() {
  window.addEventListener("scroll", () => {
    clearTimeout(scrollSaveDebounce);
    scrollSaveDebounce = setTimeout(() => {
      const active = $(".tab-btn.active");
      if (active) saveScrollPosition(active.dataset.tab);
    }, 200);
  });
}

function activateTab(tabName) {
  const previous = $(".tab-btn.active");
  if (previous) saveScrollPosition(previous.dataset.tab);
  $all(".tab-btn").forEach((b) => b.classList.remove("active"));
  $all(".tab-panel").forEach((p) => p.classList.remove("active"));
  $(`.tab-btn[data-tab="${tabName}"]`).classList.add("active");
  $(`#tab-${tabName}`).classList.add("active");
  try { localStorage.setItem(LAST_TAB_KEY, tabName); } catch (e) { /* private browsing / storage disabled -- just won't be remembered */ }
  restoreScrollPosition(tabName);
  if (tabName === "movies") loadMoviesGallery();
  if (tabName === "tv") loadTvGallery();
  if (tabName === "browse") loadBrowse();
  if (tabName === "notifications") { loadNotifications(); loadUpcomingReleases(); loadNotificationHistory(); }
  if (tabName === "watchlist") loadWatchlist();
  if (tabName === "tracker") loadTrackerTab();
  if (tabName === "history") loadHistory();
  if (tabName === "settings") { loadStats(); loadInsights(); loadSettings(); loadBackgroundTaskStatus(); loadStorageStatus(); loadApiTokensList(); loadConfigHistory(); loadViewers(); }
}

export function setupTabs() {
  $all(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => activateTab(btn.dataset.tab));
  });
}

// Restores whichever tab was open on the last visit, run once at startup
// instead of always defaulting to Movies -- a no-op (stays on Movies, the
// markup's default-active tab) if nothing was saved yet or the saved name
// no longer matches a real tab button.
export function restoreLastTab() {
  let tabName = null;
  try { tabName = localStorage.getItem(LAST_TAB_KEY); } catch (e) { /* private browsing / storage disabled */ }
  if (tabName && tabName !== "movies" && $(`.tab-btn[data-tab="${tabName}"]`)) {
    activateTab(tabName);
    return true;
  }
  return false;
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
const THEMES = ["dark", "light", "neumorphism", "claymorphism", "glassmorphism"];

function applyTheme(theme) {
  document.body.dataset.theme = theme;
  $("#theme-select").value = theme;
}

export function setTheme(theme) {
  applyTheme(theme);
  try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* ignore */ }
}

export function setupTheme() {
  let saved = "dark";
  try {
    const stored = localStorage.getItem(THEME_KEY);
    saved = THEMES.includes(stored) ? stored : "dark";
  } catch (e) { /* localStorage unavailable, default to dark */ }
  applyTheme(saved);

  $("#theme-select").addEventListener("change", (e) => setTheme(e.target.value));
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
