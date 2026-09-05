/**
 * Entry point: wires up every tab's event listeners and kicks off the initial load. Imports from every other module below -- see app/static/js/.
 */

import { approveSelected, closeBulkTrackModal, closeMatchPicker, confirmBulkTrack, openBulkMatchPicker, openBulkTrackModal, openTrackAddModal, previewBulkTrack, runMatchSearch, scanAndPreview, showConfirm, useMatchById } from "./js/archive-tab.js";
import { cleanupOrphanedArtwork, cleanupOrphans, deleteSelectedBrowseItems, loadBrowse, openDuplicatesModal, organizePaths, organizeSelected, renderBrowseTable } from "./js/browse-tab.js";
import { loadStatus, restoreLastTab, restoreScrollPosition, setupGlobalSearch, setupScrollPersistence, setupTabs, setupTheme } from "./js/chrome.js";
import { closeCommandPalette, filterCommands, openCommandPalette, renderPalette, runPaletteCommand } from "./js/command-palette.js";
import { $, $all, api, showToast, state } from "./js/core.js";
import { closeDetailPane, closePersonModal } from "./js/detail-pane.js";
import { activateGalleryFocus, activeGalleryContext, applyFilterPreset, applyTagBatch, deleteFilterPreset, exportMoviesView, exportTvView, loadMoviesGallery, loadTvGallery, markWatchedBatch, MOVIE_PRESET_IDS, moveGalleryFocus, refreshMetadataBatch, renderMoviesGallery, renderTvGallery, saveFilterPreset, setActiveViewerId, setupFilterPersistence, TV_PRESET_IDS, wireRecommendationsToggle } from "./js/gallery.js";
import { exportHistoryView, loadHistory } from "./js/history-tab.js";
import { createUniverseAction, pollNewFiles, pollNotifications, requestNotificationPermission, setupTrackerCategoryTabs, setupUniverseTypeTabs } from "./js/notifications-tab.js";
import { setupReportsTab } from "./js/reports-tab.js";
import { checkPermissions, createApiToken, createViewerAction, disableApiToken, exportLibrary, importLibrary, loadViewers, saveMediaServerSettings, saveNamingTemplates, saveSettings, saveWebdavBackupSettings, syncWatchedFromMediaServers } from "./js/settings-tab.js";

// ---- Wiring ----
const TAB_KEYS = ["movies", "tv", "browse", "archive", "notifications", "watchlist", "tracker", "history", "reports", "settings"];

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
      $("#shortcuts-modal").classList.add("hidden");
      closeMatchPicker();
      closePersonModal();
      closeDetailPane();
      return;
    }

    const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
    if (typing) return;

    if (e.key === "?") {
      e.preventDefault();
      $("#shortcuts-modal").classList.remove("hidden");
      return;
    }
    if (e.key === "s" || e.key === "S") {
      e.preventDefault();
      $("#global-search").focus();
      return;
    }

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

    const digit = Number(e.key);
    if (digit >= 1 && digit <= Math.min(9, TAB_KEYS.length)) {
      switchToTab(TAB_KEYS[digit - 1]);
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
  setupScrollPersistence();
  setupUniverseTypeTabs();
  setupTrackerCategoryTabs();
  setupGlobalSearch();
  setupTheme();
  setupKeyboardShortcuts();
  setupFilterPersistence();
  setupReportsTab();
  loadStatus();
  if (!restoreLastTab()) { loadMoviesGallery(); restoreScrollPosition("movies"); }
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
  $("#track-add-recommend-movie-btn").addEventListener("click", () => openTrackAddModal("movie", "interested"));
  $("#track-add-recommend-tv-btn").addEventListener("click", () => openTrackAddModal("tv", "interested"));
  $("#track-add-bulk-btn").addEventListener("click", openBulkTrackModal);
  $("#bulk-track-preview-btn").addEventListener("click", previewBulkTrack);
  $("#bulk-track-confirm-btn").addEventListener("click", confirmBulkTrack);
  $("#bulk-track-cancel-btn").addEventListener("click", closeBulkTrackModal);
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
    if (ids.length === 0) return;
    await markWatchedBatch(ids, true);
    showToast(`Marked ${ids.length} movie(s) watched.`, "success");
    loadMoviesGallery();
  });
  $("#movies-mark-unwatched-btn").addEventListener("click", async () => {
    const ids = $all("#movies-gallery .gallery-select:checked").map((b) => Number(b.dataset.selectId));
    if (ids.length === 0) return;
    await markWatchedBatch(ids, false);
    showToast(`Marked ${ids.length} movie(s) unwatched.`, "success");
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
      if (data.errors.length) {
        $("#movies-count").textContent = `${data.errors.length} deletion(s) failed: ${data.errors.join("; ")}`;
        showToast(`${data.errors.length} deletion(s) failed.`, "error");
      } else {
        showToast(`Deleted ${items.length} movie file(s).`, "success");
      }
      loadMoviesGallery();
    } catch (e) {
      $("#movies-count").textContent = `Error: ${e.message}`;
      showToast(`Delete failed: ${e.message}`, "error");
    }
  });
  $("#movies-refresh-metadata-btn").addEventListener("click", async () => {
    const ids = $all("#movies-gallery .gallery-select:checked").map((b) => Number(b.dataset.selectId));
    await refreshMetadataBatch(ids, $("#movies-count"));
    loadMoviesGallery();
  });
  $("#movies-tag-apply-btn").addEventListener("click", async () => {
    const ids = $all("#movies-gallery .gallery-select:checked").map((b) => Number(b.dataset.selectId));
    const tagInput = $("#movies-tag-input");
    if (ids.length === 0 || !tagInput.value.trim()) return;
    await applyTagBatch(ids, tagInput.value);
    showToast(`Tagged ${ids.length} movie(s) "${tagInput.value.trim()}".`, "success");
    tagInput.value = "";
    loadMoviesGallery();
  });
  $("#movies-preset-select").addEventListener("change", (e) => {
    if (!e.target.value) return;
    applyFilterPreset("movies", e.target.value, MOVIE_PRESET_IDS);
    renderMoviesGallery();
  });
  $("#movies-preset-save-btn").addEventListener("click", () => {
    const name = window.prompt("Save current filters/sort as:");
    if (name) {
      saveFilterPreset("movies", name, MOVIE_PRESET_IDS);
      showToast(`Saved view "${name}".`, "success");
    }
  });
  $("#movies-preset-delete-btn").addEventListener("click", async () => {
    const name = $("#movies-preset-select").value;
    if (!name) return;
    const ok = await showConfirm(`Delete saved view "${name}"?`);
    if (ok) {
      deleteFilterPreset("movies", name);
      showToast(`Deleted saved view "${name}".`, "info");
    }
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
    if (titles.size === 0) return;
    const ids = state.tvItems.filter((i) => titles.has(i.title)).map((i) => i.id);
    await markWatchedBatch(ids, true);
    showToast(`Marked ${titles.size} show(s) watched.`, "success");
    loadTvGallery();
  });
  $("#tv-mark-unwatched-btn").addEventListener("click", async () => {
    const titles = new Set($all("#tv-gallery .gallery-select:checked").map((b) => b.dataset.selectTitle));
    if (titles.size === 0) return;
    const ids = state.tvItems.filter((i) => titles.has(i.title)).map((i) => i.id);
    await markWatchedBatch(ids, false);
    showToast(`Marked ${titles.size} show(s) unwatched.`, "success");
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
  $("#tv-tag-apply-btn").addEventListener("click", async () => {
    const titles = new Set($all("#tv-gallery .gallery-select:checked").map((b) => b.dataset.selectTitle));
    const ids = state.tvItems.filter((i) => titles.has(i.title)).map((i) => i.id);
    const tagInput = $("#tv-tag-input");
    if (ids.length === 0 || !tagInput.value.trim()) return;
    await applyTagBatch(ids, tagInput.value);
    showToast(`Tagged ${titles.size} show(s) "${tagInput.value.trim()}".`, "success");
    tagInput.value = "";
    loadTvGallery();
  });
  $("#tv-delete-selected-btn").addEventListener("click", async () => {
    const titles = new Set($all("#tv-gallery .gallery-select:checked").map((b) => b.dataset.selectTitle));
    const items = state.tvItems.filter((i) => titles.has(i.title) && i.final_path);
    if (items.length === 0) return;
    const ok = await showConfirm(`Permanently delete ${items.length} episode file(s) from disk? This cannot be undone.`);
    if (!ok) return;
    try {
      const data = await api("/api/library/delete-batch", {
        method: "POST",
        body: JSON.stringify({ paths: items.map((i) => i.final_path) }),
      });
      if (data.errors.length) {
        $("#tv-count").textContent = `${data.errors.length} deletion(s) failed: ${data.errors.join("; ")}`;
        showToast(`${data.errors.length} deletion(s) failed.`, "error");
      } else {
        showToast(`Deleted ${items.length} episode file(s).`, "success");
      }
      loadTvGallery();
    } catch (e) {
      $("#tv-count").textContent = `Error: ${e.message}`;
      showToast(`Delete failed: ${e.message}`, "error");
    }
  });
  $("#tv-preset-select").addEventListener("change", (e) => {
    if (!e.target.value) return;
    applyFilterPreset("tv", e.target.value, TV_PRESET_IDS);
    renderTvGallery();
  });
  $("#tv-preset-save-btn").addEventListener("click", () => {
    const name = window.prompt("Save current filters/sort as:");
    if (name) {
      saveFilterPreset("tv", name, TV_PRESET_IDS);
      showToast(`Saved view "${name}".`, "success");
    }
  });
  $("#tv-preset-delete-btn").addEventListener("click", async () => {
    const name = $("#tv-preset-select").value;
    if (!name) return;
    const ok = await showConfirm(`Delete saved view "${name}"?`);
    if (ok) {
      deleteFilterPreset("tv", name);
      showToast(`Deleted saved view "${name}".`, "info");
    }
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
  $("#person-modal-close-btn").addEventListener("click", closePersonModal);
  $("#person-modal").addEventListener("click", (e) => {
    if (e.target.id === "person-modal") closePersonModal();
  });
  $("#shortcuts-close-btn").addEventListener("click", () => $("#shortcuts-modal").classList.add("hidden"));
  $("#shortcuts-modal").addEventListener("click", (e) => {
    if (e.target.id === "shortcuts-modal") $("#shortcuts-modal").classList.add("hidden");
  });
});
