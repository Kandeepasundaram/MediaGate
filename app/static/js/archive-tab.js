/**
 * Ready to Archive tab (scan, preview table, approve), the manual TMDB match picker modal (reused by several other tabs), and the 'manually track a title' modal.
 */

import { $, $all, api, formatBytes, state } from "./core.js";
import { loadTrackedList, refreshNewFilesBadge } from "./notifications-tab.js";

// ---- Archive tab ----
export function setPreviewMode(mode) {
  state.previewMode = mode;
  $("#approve-btn").textContent = mode === "organize" ? "Approve & Organize (Move)" : "Approve & Archive";
}

function setScanProgressVisible(visible) {
  $("#scan-progress-bar").classList.toggle("hidden", !visible);
}

export async function previewPaths(paths, sizeByPath = {}) {
  const tbody = $("#archive-table tbody");
  tbody.innerHTML = "";
  state.previewItems = [];
  state.sizeByPath = sizeByPath;

  if (paths.length === 0) {
    $("#scan-status").textContent = "No files selected";
    setScanProgressVisible(false);
    return;
  }

  $("#scan-status").textContent = "Fetching metadata...";
  setScanProgressVisible(true);
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
  } finally {
    setScanProgressVisible(false);
  }
}

export async function scanAndPreview() {
  setPreviewMode("archive");
  $("#scan-status").textContent = "Scanning...";
  setScanProgressVisible(true);
  try {
    const scan = await api("/api/scan");
    refreshNewFilesBadge(); // the scan itself just cleared the watcher's queue server-side
    if (scan.files.length === 0) {
      $("#scan-status").textContent = `No new media files found in ${scan.directories.join(", ")}`;
      $("#archive-table tbody").innerHTML = "";
      state.previewItems = [];
      setScanProgressVisible(false);
      return;
    }
    const sizeByPath = Object.fromEntries(scan.files.map((f) => [f.path, f.size_bytes]));
    await previewPaths(scan.files.map((f) => f.path), sizeByPath);
  } catch (e) {
    $("#scan-status").textContent = `Error: ${e.message}`;
    setScanProgressVisible(false);
  }
}

// Season-pack grouping: episodes of the same show+season that landed
// *contiguously* in the list (the common case -- files from one extracted
// pack folder sort together) get a shared header row with a "select whole
// pack" checkbox, instead of looking like N unrelated rows. Not a full
// re-sort/re-group across the whole table: interleaved episodes from
// different imports stay as plain rows rather than being silently
// reordered out from under their original index.
function contiguousPreviewGroups(items) {
  const groups = [];
  let current = null;
  items.forEach((item, i) => {
    const key = item.media_type === "tv" && item.season != null ? `${item.title}::${item.season}` : null;
    if (key && current && current.key === key) {
      current.indices.push(i);
    } else {
      current = { key, indices: [i] };
      groups.push(current);
    }
  });
  return groups;
}

function renderArchiveTable(items) {
  const tbody = $("#archive-table tbody");
  const checkedBefore = $all(".row-check").map((cb) => cb.checked);
  const rowHtml = (item, i) => `
    <tr>
      <td><input type="checkbox" class="row-check" data-index="${i}" aria-label="Select ${escapeAttr(item.source_path.split(/[\\/]/).pop())}" ${checkedBefore[i] === false ? "" : "checked"}></td>
      <td>${item.duplicate ? `<span class="duplicate-badge" title="A matching title already exists in the library">⚠</span>` : ""}</td>
      <td title="${item.source_path}">${item.source_path.split(/[\\/]/).pop()}</td>
      <td>${item.media_type === "movie" ? renderMovieNameCell(item, i) : renderTvNameCell(item, i)}</td>
      <td>${item.media_type}</td>
      <td>${formatBytes(state.sizeByPath[item.source_path])}</td>
      <td title="${item.overview}">${item.overview.slice(0, 80)}</td>
      <td><button class="change-match-btn" data-index="${i}">Change Match</button></td>
    </tr>
  `;
  tbody.innerHTML = contiguousPreviewGroups(items).map((group) => {
    const rows = group.indices.map((i) => rowHtml(items[i], i)).join("");
    if (!group.key || group.indices.length < 2) return rows;
    const first = items[group.indices[0]];
    return `
      <tr class="season-pack-header-row">
        <td><input type="checkbox" class="season-pack-select" data-indices="${group.indices.join(",")}" aria-label="Select season pack: ${escapeAttr(first.title)} Season ${first.season}" checked></td>
        <td colspan="7">Season Pack: ${escapeAttr(first.title)} — Season ${first.season} (${group.indices.length} episodes)</td>
      </tr>
    ` + rows;
  }).join("");
  $all(".change-match-btn").forEach((btn) => {
    btn.addEventListener("click", () => openMatchPicker(Number(btn.dataset.index)));
  });
  $all(".season-pack-select").forEach((groupCb) => {
    groupCb.addEventListener("change", () => {
      const indices = groupCb.dataset.indices.split(",");
      indices.forEach((i) => {
        const rowCb = $(`.row-check[data-index="${i}"]`);
        if (rowCb) rowCb.checked = groupCb.checked;
      });
    });
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
      updateDestDiff(idx);
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
      updateDestDiff(idx);
    });
  });
}

// Highlights the part of the computed new filename that differs from the
// original -- a longest-common-prefix/suffix diff (not a full LCS), which
// is cheap and reads well for the common case (year/season/episode
// reformatted, resolution/release tags stripped) even though it isn't a
// true minimal diff for edits scattered across the middle of the name.
function highlightDiff(oldName, newName) {
  let prefixLen = 0;
  const maxPrefix = Math.min(oldName.length, newName.length);
  while (prefixLen < maxPrefix && oldName[prefixLen] === newName[prefixLen]) prefixLen++;
  let suffixLen = 0;
  const maxSuffix = maxPrefix - prefixLen;
  while (suffixLen < maxSuffix && oldName[oldName.length - 1 - suffixLen] === newName[newName.length - 1 - suffixLen]) suffixLen++;
  const prefix = newName.slice(0, prefixLen);
  const middle = newName.slice(prefixLen, newName.length - suffixLen);
  const suffix = newName.slice(newName.length - suffixLen);
  return `${escapeAttr(prefix)}${middle ? `<mark class="diff-highlight">${escapeAttr(middle)}</mark>` : ""}${escapeAttr(suffix)}`;
}

function destDiffMarkup(item, i) {
  const oldName = item.source_path.split(/[\\/]/).pop();
  const newName = item.dest_path.split(/[\\/]/).pop();
  return `<div class="dest-diff hint" data-diff-index="${i}" title="Original: ${escapeAttr(oldName)}">→ ${highlightDiff(oldName, newName)}</div>`;
}

function updateDestDiff(index) {
  const el = $(`.dest-diff[data-diff-index="${index}"]`);
  if (el) el.outerHTML = destDiffMarkup(state.previewItems[index], index);
}

function renderMovieNameCell(item, i) {
  const [stem] = extBaseOf(item.dest_path.split(/[\\/]/).pop());
  return `
    <div class="dest-name-cell">
      <input type="text" class="dest-stem-input" data-index="${i}" title="${item.dest_path}" value="${escapeAttr(stem)}">
      <label class="dest-rename-file-toggle">
        <input type="checkbox" class="dest-rename-file-checkbox" data-index="${i}" checked> also rename file
      </label>
      ${destDiffMarkup(item, i)}
    </div>
  `;
}

function renderTvNameCell(item, i) {
  return `
    <div class="dest-name-cell">
      <input type="text" class="dest-name-input" data-index="${i}" title="${item.dest_path}" value="${escapeAttr(item.dest_path.split(/[\\/]/).pop())}">
      ${destDiffMarkup(item, i)}
    </div>
  `;
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

export function escapeAttr(str) {
  return str.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

// ---- Manual TMDB match picker ----
async function openMatchPicker(index) {
  const item = state.previewItems[index];
  openMatchModal(item.media_type, item.title, (candidate) => applyMatchOverride(index, candidate));
}

export function openMatchModal(mediaType, initialQuery, onApply, { hideIdEntry = false } = {}) {
  state.matchPicker = { mediaType, onApply };
  $("#match-modal").classList.remove("hidden");
  $("#match-search-input").value = initialQuery;
  $("#match-id-input").value = "";
  // "Use ID" has no title to go with the tmdb_id -- fine for archive preview
  // (which re-fetches full TMDB details anyway) but tracker.add needs a
  // title up front, so that path is search-results-only.
  $(".match-id-row").classList.toggle("hidden", hideIdEntry);
  if (initialQuery) runMatchSearch(initialQuery);
  else $("#match-results").innerHTML = "";
}

export async function runMatchSearch(query) {
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
      btn.addEventListener("click", () => applyPickerChoice(data.results[Number(btn.dataset.resultIndex)]));
    });
  } catch (e) {
    results.innerHTML = `<p>Error: ${e.message}</p>`;
  }
}

export async function useMatchById() {
  const picker = state.matchPicker;
  if (!picker) return;
  const raw = $("#match-id-input").value.trim();
  const id = Number(raw);
  if (!raw || !Number.isInteger(id) || id <= 0) {
    $("#match-results").innerHTML = "<p>Enter a valid numeric TMDB ID.</p>";
    return;
  }
  await applyPickerChoice({ tmdb_id: id });
}

// The single choke point every "Use"/"Use ID" click goes through: keeps the
// popup open (with the error shown right here, candidate list and typed ID
// still intact) until onApply actually succeeds, instead of each onApply
// closing the popup unconditionally before knowing whether its own API call
// even worked -- that's what made a bad match/ID look like it "vanished"
// with the real error reported somewhere else on the page.
async function applyPickerChoice(candidate) {
  const picker = state.matchPicker;
  if (!picker) return;
  const results = $("#match-results");
  results.innerHTML = "<p>Applying match…</p>";
  try {
    await picker.onApply(candidate);
    closeMatchPicker();
  } catch (e) {
    results.innerHTML = `<p>Error: ${e.message}</p><p class="hint">Pick a different candidate, or try a different TMDB ID.</p>`;
  }
}

async function applyMatchOverride(index, candidate) {
  const item = state.previewItems[index];
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
    throw e; // keeps the match popup open on this error instead of closing -- see applyPickerChoice
  }
}

export function openBulkMatchPicker() {
  const items = selectedItems();
  if (items.length === 0) {
    $("#scan-status").textContent = "Select at least one row first.";
    return;
  }
  const mediaTypes = new Set(items.map((i) => i.media_type));
  if (mediaTypes.size > 1) {
    $("#scan-status").textContent = "Select rows of only one media type (Movie or TV) to bulk-change match.";
    return;
  }
  openMatchModal(items[0].media_type, items[0].title, (candidate) => applyBulkMatchOverride(items, candidate));
}

async function applyBulkMatchOverride(items, candidate) {
  $("#scan-status").textContent = `Applying match to ${items.length} file(s)...`;
  try {
    const overrides = Object.fromEntries(items.map((i) => [i.source_path, candidate.tmdb_id]));
    const preview = await api("/api/archive/preview", {
      method: "POST",
      body: JSON.stringify({ paths: items.map((i) => i.source_path), tmdb_overrides: overrides }),
    });
    const bySource = Object.fromEntries(preview.items.map((p) => [p.source_path, p]));
    state.previewItems = state.previewItems.map((existing) => bySource[existing.source_path] || existing);
    renderArchiveTable(state.previewItems);
    $("#scan-status").textContent = `${state.previewItems.length} file(s) ready`;
  } catch (e) {
    $("#scan-status").textContent = `Error: ${e.message}`;
    throw e; // keeps the match popup open on this error instead of closing -- see applyPickerChoice
  }
}

export function closeMatchPicker() {
  $("#match-modal").classList.add("hidden");
  state.matchPicker = null;
}

// ---- Manually track a title not yet in the library (wishlist/wanted) ----
// category="watching" (default) is the existing "own it, alert me on a new
// season/sequel" flow; category="interested" is the same search-and-pick UX
// used to add a recommendation you don't own yet -- see the Tracker tab's
// "+ Add Recommendation" buttons.
export function openTrackAddModal(mediaType, category = "watching") {
  openMatchModal(mediaType, "", (candidate) => addTrackedTitle(mediaType, candidate, category), { hideIdEntry: true });
}

async function addTrackedTitle(mediaType, candidate, category = "watching") {
  $("#track-add-status").textContent = `${category === "interested" ? "Adding" : "Tracking"} "${candidate.title}"...`;
  try {
    await api("/api/tracker/add", {
      method: "POST",
      body: JSON.stringify({
        tmdb_id: candidate.tmdb_id, media_type: mediaType, title: candidate.title,
        poster_path: candidate.poster_path || null, overview: candidate.overview || null,
        category,
      }),
    });
    $("#track-add-status").textContent = category === "interested" ? `Added "${candidate.title}" to Interested.` : `Now tracking "${candidate.title}".`;
    loadTrackedList();
  } catch (e) {
    $("#track-add-status").textContent = `Error: ${e.message}`;
    throw e; // keeps the match popup open on this error instead of closing -- see applyPickerChoice
  }
}

// ---- Bulk-add tracking: paste titles -> preview TMDB matches -> confirm ----
let bulkTrackPreview = [];

export function openBulkTrackModal() {
  bulkTrackPreview = [];
  $("#bulk-track-titles").value = "";
  $("#bulk-track-table").classList.add("hidden");
  $("#bulk-track-confirm-btn").classList.add("hidden");
  $("#bulk-track-status").textContent = "";
  $("#bulk-track-modal").classList.remove("hidden");
}

export function closeBulkTrackModal() {
  $("#bulk-track-modal").classList.add("hidden");
}

export async function previewBulkTrack() {
  const mediaType = $("#bulk-track-media-type").value;
  const titles = $("#bulk-track-titles").value.split("\n").map((t) => t.trim()).filter(Boolean);
  if (titles.length === 0) return;
  $("#bulk-track-status").textContent = "Searching TMDB...";
  try {
    const data = await api("/api/tracker/bulk-preview", {
      method: "POST",
      body: JSON.stringify({ titles, media_type: mediaType }),
    });
    bulkTrackPreview = data.items.map((item) => ({ ...item, media_type: mediaType }));
    renderBulkTrackTable();
    const matchedCount = bulkTrackPreview.filter((i) => i.matched).length;
    $("#bulk-track-status").textContent = `${matchedCount} of ${bulkTrackPreview.length} matched -- review before confirming.`;
  } catch (e) {
    $("#bulk-track-status").textContent = `Error: ${e.message}`;
  }
}

function renderBulkTrackTable() {
  const table = $("#bulk-track-table");
  table.querySelector("tbody").innerHTML = bulkTrackPreview.map((item, i) => `
    <tr>
      <td>${escapeAttr(item.input_title)}</td>
      <td>${item.matched
      ? `${escapeAttr(item.title)}${item.year ? ` (${item.year})` : ""}`
      : `<span class="hint">No match found</span>`}</td>
      <td><button class="bulk-track-fix-btn" data-index="${i}">Change Match</button></td>
    </tr>
  `).join("");
  table.classList.remove("hidden");
  $("#bulk-track-confirm-btn").classList.remove("hidden");
  table.querySelectorAll(".bulk-track-fix-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const index = Number(btn.dataset.index);
      const item = bulkTrackPreview[index];
      // hideIdEntry: true -- same reason openTrackAddModal hides it: "Use ID"
      // only ever hands back {tmdb_id}, with no title/poster/overview, and
      // this callback (unlike applyMatchOverride) has no server round trip
      // of its own to fill those back in. Search-results-only keeps every
      // field populated.
      openMatchModal(item.media_type, item.input_title, (candidate) => {
        bulkTrackPreview[index] = {
          ...item, matched: true, tmdb_id: candidate.tmdb_id, title: candidate.title,
          year: candidate.year, poster_path: candidate.poster_path, overview: candidate.overview,
        };
        renderBulkTrackTable(); // applyPickerChoice closes the popup once this returns
      }, { hideIdEntry: true });
    });
  });
}

export async function confirmBulkTrack() {
  const items = bulkTrackPreview
    .filter((i) => i.matched)
    .map((i) => ({
      tmdb_id: i.tmdb_id, media_type: i.media_type, title: i.title,
      poster_path: i.poster_path || null, overview: i.overview || null,
    }));
  if (items.length === 0) return;
  $("#bulk-track-status").textContent = "Adding...";
  try {
    const res = await api("/api/tracker/bulk-add", { method: "POST", body: JSON.stringify({ items }) });
    $("#bulk-track-status").textContent = `Now tracking ${res.added} title(s).`;
    closeBulkTrackModal();
    loadTrackedList();
  } catch (e) {
    $("#bulk-track-status").textContent = `Error: ${e.message}`;
  }
}

function selectedItems() {
  return $all(".row-check:checked").map((cb) => state.previewItems[Number(cb.dataset.index)]);
}

export function showConfirm(text) {
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

export async function approveSelected() {
  const items = selectedItems();
  if (items.length === 0) return;

  const isOrganize = state.previewMode === "organize";
  const dryRun = $("#dry-run-toggle").checked;
  const endpoint = isOrganize ? "/api/library/organize" : "/api/archive/confirm";
  const body = isOrganize ? { items, dry_run: dryRun } : { items, purge_subtitles: true, dry_run: dryRun };

  if (!dryRun) {
    const confirmMsg = isOrganize
      ? `Organize ${items.length} file(s)? This moves them to their correct name/folder in place — no duplicate is created.`
      : `Archive ${items.length} file(s)? This copies them to the archive location.`;
    const ok = await showConfirm(confirmMsg);
    if (!ok) return;
  }

  $("#scan-status").textContent = dryRun ? "Checking (dry run, nothing will change)..." : (isOrganize ? "Organizing..." : "Archiving...");
  try {
    const result = await api(endpoint, { method: "POST", body: JSON.stringify(body) });
    const failures = result.results.filter((r) => r.status === "failed");

    if (dryRun) {
      const okCount = result.results.length - failures.length;
      $("#scan-status").textContent = failures.length
        ? `Dry run: ${okCount} would succeed, ${failures.length} would fail (${failures.map((f) => f.error).join("; ")})`
        : `Dry run: all ${okCount} file(s) would succeed. Nothing was changed.`;
      return; // preview table untouched -- nothing on disk/DB changed
    }

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
