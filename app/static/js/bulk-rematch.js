/**
 * "Fix Match (Selected)" checklist modal for the Movies/TV galleries.
 * Unlike archive-tab.js's own bulk-change-match (every selected row IS the
 * same title, e.g. duplicate copies awaiting archive) or a TV rematch (one
 * tmdb_id applied to every episode of one show), rows selected here are
 * typically *different* titles each needing their *own* match -- so this
 * is a per-row checklist reusing the existing match-modal, not a single
 * one-tmdb-id-for-all call.
 */

import { escapeAttr, openMatchModal } from "./archive-tab.js";
import { $, api } from "./core.js";
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
      // Whatever closes match-modal -- Cancel, Escape, backdrop click, or a
      // successful apply below -- brings this list back. Without this, only
      // the success path used to bring it back, so cancelling out of a row's
      // search left BOTH modals hidden with no way back short of a reload.
      const matchModal = $("#match-modal");
      const reopenOnClose = new MutationObserver(() => {
        if (matchModal.classList.contains("hidden")) {
          reopenOnClose.disconnect();
          $("#bulk-rematch-modal").classList.remove("hidden");
        }
      });
      reopenOnClose.observe(matchModal, { attributes: true, attributeFilter: ["class"] });
      openMatchModal(row.mediaType, row.title, (candidate) => applyRowMatch(row, candidate));
    });
  });
}

// Left to throw on failure -- archive-tab.js's applyPickerChoice (every
// openMatchModal onApply goes through it) is what keeps the match popup
// open with the error shown inline instead of closing it first and
// reporting the failure elsewhere. On success it closes the popup once
// this returns, which is what the MutationObserver above is watching for.
async function applyRowMatch(row, candidate) {
  await api("/api/library/rematch-tmdb", {
    method: "POST",
    body: JSON.stringify({ ids: row.ids, tmdb_id: candidate.tmdb_id, media_type: row.mediaType }),
  });
  row.matched = true;
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
