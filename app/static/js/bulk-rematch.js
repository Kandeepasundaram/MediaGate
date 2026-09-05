/**
 * "Fix Match (Selected)" checklist modal for the Movies/TV galleries.
 * Unlike archive-tab.js's own bulk-change-match (every selected row IS the
 * same title, e.g. duplicate copies awaiting archive) or a TV rematch (one
 * tmdb_id applied to every episode of one show), rows selected here are
 * typically *different* titles each needing their *own* match -- so this
 * is a per-row checklist reusing the existing match-modal, not a single
 * one-tmdb-id-for-all call.
 */

import { closeMatchPicker, escapeAttr, openMatchModal } from "./archive-tab.js";
import { $, api, showToast } from "./core.js";
import { loadMoviesGallery, loadTvGallery } from "./gallery.js";

let rows = []; // { ids: number[], title: string, mediaType: "movie"|"tv", matched: boolean }

export function openBulkRematchModal(rowsIn) {
  if (rowsIn.length === 0) return;
  rows = rowsIn.map((r) => ({ ...r, matched: false }));
  renderBulkRematchModal();
  $("#bulk-rematch-modal").classList.remove("hidden");
}

function renderBulkRematchModal() {
  $("#bulk-rematch-list").innerHTML = rows.map((r, i) => `
    <div class="bulk-rematch-row">
      <span class="bulk-rematch-title">${escapeAttr(r.title)}</span>
      <span class="hint">${r.matched ? "✓ Matched" : "Pending"}</span>
      <button class="bulk-rematch-search-btn" data-index="${i}" ${r.matched ? "disabled" : ""}>Search &amp; Match</button>
    </div>
  `).join("");
  $("#bulk-rematch-list").querySelectorAll(".bulk-rematch-search-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = rows[Number(btn.dataset.index)];
      $("#bulk-rematch-modal").classList.add("hidden");
      openMatchModal(row.mediaType, row.title, (candidate) => applyRowMatch(row, candidate));
    });
  });
}

async function applyRowMatch(row, candidate) {
  closeMatchPicker();
  $("#bulk-rematch-modal").classList.remove("hidden");
  try {
    await api("/api/library/rematch-tmdb", {
      method: "POST",
      body: JSON.stringify({ ids: row.ids, tmdb_id: candidate.tmdb_id, media_type: row.mediaType }),
    });
    row.matched = true;
  } catch (e) {
    showToast(`Match failed for "${row.title}": ${e.message}`, "error");
  }
  renderBulkRematchModal();
}

export function closeBulkRematchModal() {
  $("#bulk-rematch-modal").classList.add("hidden");
  const anyMatched = rows.some((r) => r.matched);
  rows = [];
  if (anyMatched) {
    loadMoviesGallery();
    loadTvGallery();
  }
}
