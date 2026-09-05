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

// Stays open on failure (showing the error right where the user is looking,
// with their search/candidate list still intact to just try again) instead
// of closing immediately and reporting the error via a toast elsewhere --
// closing unconditionally before knowing the result is what made a bad ID
// look like it "disappeared" with no explanation.
async function applyRowMatch(row, candidate) {
  const results = $("#match-results");
  results.innerHTML = "<p>Applying match…</p>";
  try {
    await api("/api/library/rematch-tmdb", {
      method: "POST",
      body: JSON.stringify({ ids: row.ids, tmdb_id: candidate.tmdb_id, media_type: row.mediaType }),
    });
    row.matched = true;
    closeMatchPicker();
    renderBulkRematchModal();
  } catch (e) {
    results.innerHTML = `<p>Error: ${e.message}</p><p class="hint">Pick a different candidate, or enter a different TMDB ID above.</p>`;
  }
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
