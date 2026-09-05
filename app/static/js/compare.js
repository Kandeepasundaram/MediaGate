/**
 * Compare Titles modal: search two titles (movie or TV, via the same
 * /api/library/search the topbar global search uses) and render their
 * fields side by side. Client-side only -- no new backend endpoint.
 */

import { escapeAttr } from "./archive-tab.js";
import { $, api } from "./core.js";
import { posterMarkup } from "./gallery.js";

let debounce = { a: null, b: null };
let picked = { a: null, b: null };

export function openCompareModal() {
  $("#compare-modal").classList.remove("hidden");
}

export function closeCompareModal() {
  $("#compare-modal").classList.add("hidden");
  picked = { a: null, b: null };
  ["a", "b"].forEach((side) => {
    $(`#compare-search-${side}`).value = "";
    $(`#compare-results-${side}`).innerHTML = "";
    $(`#compare-results-${side}`).classList.add("hidden");
  });
  $("#compare-output").innerHTML = "";
}

// A TV search hit is one row per episode -- picking the first is the same
// "representative row stands in for the show" convention gallery.js's
// groupEpisodesByShow already uses for show-level display fields.
async function searchSide(side, query) {
  const results = $(`#compare-results-${side}`);
  if (query.length < 2) {
    results.classList.add("hidden");
    results.innerHTML = "";
    return;
  }
  try {
    const data = await api(`/api/library/search?q=${encodeURIComponent(query)}`);
    const seen = new Set();
    const items = (data.items || []).filter((item) => {
      const key = `${item.media_type}:${item.title}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, 8);
    results.innerHTML = items.length
      ? items.map((item, i) => `
        <div class="global-search-result" data-side="${side}" data-index="${i}">
          <span class="gsr-type">${item.media_type === "movie" ? "Movie" : "TV"}</span>
          <span class="gsr-title">${escapeAttr(item.title)}</span>
          <span class="gsr-year">${item.year || ""}</span>
        </div>`).join("")
      : `<div class="global-search-empty">No matches</div>`;
    results.classList.remove("hidden");
    results.querySelectorAll(".global-search-result").forEach((row) => {
      row.addEventListener("click", () => selectSide(side, items[Number(row.dataset.index)]));
    });
  } catch (e) {
    results.innerHTML = `<div class="global-search-empty">Error: ${e.message}</div>`;
    results.classList.remove("hidden");
  }
}

function selectSide(side, item) {
  picked[side] = item;
  $(`#compare-search-${side}`).value = item.title;
  $(`#compare-results-${side}`).classList.add("hidden");
  renderComparison();
}

function winnerClass(a, b, higherWins = true) {
  if (a == null || b == null || a === b) return ["", ""];
  const aWins = higherWins ? a > b : a < b;
  return aWins ? ["delta-up", "delta-down"] : ["delta-down", "delta-up"];
}

function numRow(label, a, b, format = (v) => v ?? "—", higherWins = true) {
  const [clsA, clsB] = winnerClass(a, b, higherWins);
  return `<tr><th>${label}</th><td class="${clsA}">${format(a)}</td><td class="${clsB}">${format(b)}</td></tr>`;
}

function listRow(label, a, b) {
  const fmt = (v) => (v && v.length ? escapeAttr(v.join(", ")) : "—");
  return `<tr><th>${label}</th><td>${fmt(a)}</td><td>${fmt(b)}</td></tr>`;
}

function renderComparison() {
  const el = $("#compare-output");
  if (!picked.a || !picked.b) {
    el.innerHTML = "";
    return;
  }
  const a = picked.a;
  const b = picked.b;
  el.innerHTML = `
    <table class="compare-table">
      <thead>
        <tr>
          <th></th>
          <th>${posterMarkup(a.title, a.poster_path)}<div>${escapeAttr(a.title)}</div></th>
          <th>${posterMarkup(b.title, b.poster_path)}<div>${escapeAttr(b.title)}</div></th>
        </tr>
      </thead>
      <tbody>
        <tr><th>Type</th><td>${a.media_type === "movie" ? "Movie" : "TV"}</td><td>${b.media_type === "movie" ? "Movie" : "TV"}</td></tr>
        ${numRow("Year", a.year, b.year)}
        ${numRow("TMDB Rating", a.vote_average, b.vote_average, (v) => (v != null ? v.toFixed(1) : "—"))}
        ${numRow("Personal Rating", a.personal_rating, b.personal_rating, (v) => (v != null ? "★".repeat(v) : "—"))}
        ${listRow("Genres", a.genres, b.genres)}
        ${listRow("Tags", a.tags, b.tags)}
        <tr><th>Resolution</th><td>${a.resolution || "—"}</td><td>${b.resolution || "—"}</td></tr>
        <tr><th>Watched</th><td>${a.watched ? "Yes" : "No"}</td><td>${b.watched ? "Yes" : "No"}</td></tr>
      </tbody>
    </table>
  `;
}

export function setupCompareModal() {
  ["a", "b"].forEach((side) => {
    $(`#compare-search-${side}`).addEventListener("input", (e) => {
      clearTimeout(debounce[side]);
      debounce[side] = setTimeout(() => searchSide(side, e.target.value.trim()), 250);
    });
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".compare-picker")) {
      $("#compare-results-a").classList.add("hidden");
      $("#compare-results-b").classList.add("hidden");
    }
  });
}
