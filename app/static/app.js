/**
 * Entry point: wires up every tab's event listeners and kicks off the initial load. Imports from every other module below -- see app/static/js/.
 */

import { approveSelected, closeMatchPicker, openBulkMatchPicker, openTrackAddModal, runMatchSearch, scanAndPreview, showConfirm, useMatchById } from "./js/archive-tab.js";
import { cleanupOrphanedArtwork, cleanupOrphans, deleteSelectedBrowseItems, loadBrowse, openDuplicatesModal, organizePaths, organizeSelected, renderBrowseTable } from "./js/browse-tab.js";
import { loadStatus, setupGlobalSearch, setupTabs, setupTheme } from "./js/chrome.js";
import { closeCommandPalette, filterCommands, openCommandPalette, renderPalette, runPaletteCommand } from "./js/command-palette.js";
import { $, $all, api, state } from "./js/core.js";
import { closeDetailPane } from "./js/detail-pane.js";
import { activateGalleryFocus, activeGalleryContext, exportMoviesView, exportTvView, loadMoviesGallery, loadTvGallery, markWatchedBatch, moveGalleryFocus, refreshMetadataBatch, renderMoviesGallery, renderTvGallery, setActiveViewerId, setupFilterPersistence, wireRecommendationsToggle } from "./js/gallery.js";
import { exportHistoryView, loadHistory } from "./js/history-tab.js";
import { createUniverseAction, pollNewFiles, pollNotifications, requestNotificationPermission, setupUniverseTypeTabs } from "./js/notifications-tab.js";
import { checkPermissions, createApiToken, createViewerAction, disableApiToken, exportLibrary, importLibrary, loadViewers, saveMediaServerSettings, saveNamingTemplates, saveSettings, saveWebdavBackupSettings, syncWatchedFromMediaServers } from "./js/settings-tab.js";

// ---- Wiring ----
const TAB_KEYS = ["movies", "tv", "browse", "archive", "notifications", "tracker", "history", "settings"];

export function switchToTab(tabName) {
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

    if (e.key >= "1" && e.key <= "8") {
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
  setupUniverseTypeTabs();
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
  $("#create-universe-btn").addEventListener("click", createUniverseAction);
  $("#new-universe-name").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); createUniverseAction(); }
  });
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
