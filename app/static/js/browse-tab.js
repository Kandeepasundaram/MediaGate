/**
 * Browse & Clean Up tab: raw-filesystem table, library health (orphans/duplicates/orphaned artwork), and delete/organize actions.
 */

import { escapeAttr, previewPaths, setPreviewMode, showConfirm } from "./archive-tab.js";
import { $, $all, api, formatBytes, state } from "./core.js";
import { loadMoviesGallery, loadTvGallery } from "./gallery.js";

// ---- Browse & Clean Up tab ----
state.browseItems = [];
state.browseFiltered = [];

export function renderBrowseTable() {
  const tbody = $("#browse-table tbody");
  const query = $("#browse-search").value.trim().toLowerCase();
  const filterMode = $("#browse-filter").value;
  const trackedFilter = $("#browse-tracked").value;
  const sortMode = $("#browse-sort").value;

  let items = state.browseItems;
  if (filterMode === "unmatched") items = items.filter((i) => i.tmdb_id == null);
  if (trackedFilter === "tracked") items = items.filter((i) => i.tracked);
  if (trackedFilter === "untracked") items = items.filter((i) => !i.tracked);
  if (query) items = items.filter((i) => i.path.toLowerCase().includes(query) || i.parsed_title.toLowerCase().includes(query));
  items = [...items].sort((a, b) => {
    if (sortMode === "size") return b.size_bytes - a.size_bytes;
    if (sortMode === "title") return a.parsed_title.localeCompare(b.parsed_title);
    return a.path.localeCompare(b.path);
  });
  state.browseFiltered = items;

  if (state.browseDirectory) {
    $("#browse-status").textContent = `${state.browseItems.length} file(s) in ${state.browseDirectory}` +
      (items.length !== state.browseItems.length ? ` (${items.length} shown)` : "");
  }

  if (state.browseItems.length === 0) {
    tbody.innerHTML = `<tr><td colspan=6>No files found.</td></tr>`;
    return;
  }
  if (items.length === 0) {
    tbody.innerHTML = `<tr><td colspan=6>No files match the current filters.</td></tr>`;
    return;
  }
  tbody.innerHTML = items.map((item, i) => `
    <tr>
      <td><input type="checkbox" class="browse-check" data-index="${i}" aria-label="Select ${escapeAttr(item.path.split(/[\\/]/).pop())}"></td>
      <td title="${item.path}">${item.path.split(/[\\/]/).pop()}</td>
      <td>${item.parsed_title}${item.year ? ` (${item.year})` : ""}${
        item.season != null ? ` S${String(item.season).padStart(2, "0")}E${String(item.episode).padStart(2, "0")}` : ""
      }</td>
      <td>${formatBytes(item.size_bytes)}</td>
      <td class="${item.tracked ? "tracked-yes" : "tracked-no"}">${item.tracked ? "tracked" : "untracked"}</td>
      <td><button class="danger browse-delete-btn" data-index="${i}">Delete</button></td>
    </tr>
  `).join("");
  $all(".browse-delete-btn").forEach((btn) => {
    btn.addEventListener("click", () => deleteBrowseItem(Number(btn.dataset.index)));
  });
}

export async function loadBrowse() {
  const tbody = $("#browse-table tbody");
  const mediaType = $("#browse-type").value;
  tbody.innerHTML = `<tr><td colspan=6>Loading...</td></tr>`;
  $("#browse-status").textContent = "";

  try {
    const data = await api(`/api/library/browse?media_type=${mediaType}`);
    state.browseItems = data.items;
    state.browseDirectory = data.directory;
    renderBrowseTable();
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan=6>Error: ${e.message}</td></tr>`;
  }
  loadLibraryHealth();
}

async function loadLibraryHealth() {
  const card = $("#library-health-card");
  const summary = $("#library-health-summary");
  const cleanupBtn = $("#cleanup-orphans-btn");
  const reviewBtn = $("#review-duplicates-btn");
  const artworkBtn = $("#cleanup-artwork-btn");
  try {
    const data = await api("/api/library/health");
    state.libraryDuplicates = data.duplicates;
    const orphanCount = data.orphans.length;
    const duplicateCount = data.duplicates.length;
    const artworkCount = data.orphaned_artwork.length;
    if (orphanCount === 0 && duplicateCount === 0 && artworkCount === 0) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");
    const parts = [];
    if (orphanCount > 0) parts.push(`${orphanCount} orphaned record(s) (file missing on disk)`);
    if (duplicateCount > 0) parts.push(`${duplicateCount} duplicate group(s)`);
    if (artworkCount > 0) parts.push(`${artworkCount} folder(s) with leftover artwork/subtitles`);
    summary.textContent = parts.join(" — ");
    cleanupBtn.classList.toggle("hidden", orphanCount === 0);
    reviewBtn.classList.toggle("hidden", duplicateCount === 0);
    artworkBtn.classList.toggle("hidden", artworkCount === 0);
  } catch (e) {
    card.classList.add("hidden");
  }
}

function isBrowseDryRun() {
  return $("#browse-dry-run-toggle").checked;
}

export async function cleanupOrphanedArtwork() {
  const dryRun = isBrowseDryRun();
  if (!dryRun) {
    const ok = await showConfirm("Delete poster/nfo/subtitle files left behind in folders whose video was renamed or moved away? This does not touch any video file.");
    if (!ok) return;
  }
  try {
    const data = await api(`/api/library/orphaned-artwork/cleanup?dry_run=${dryRun}`, { method: "POST" });
    if (dryRun) {
      const fileCount = data.groups.reduce((n, g) => n + g.files.length, 0);
      $("#browse-status").textContent = data.groups.length
        ? `Dry run: would remove ${fileCount} file(s) across ${data.groups.length} folder(s). Nothing was changed.`
        : "Dry run: nothing to clean up. Nothing was changed.";
      return;
    }
    $("#browse-status").textContent = `Removed ${data.removed} orphaned artwork file(s).`;
    loadLibraryHealth();
  } catch (e) {
    $("#browse-status").textContent = `Error: ${e.message}`;
  }
}

export function openDuplicatesModal() {
  renderDuplicatesList();
  $("#duplicates-modal").classList.remove("hidden");
}

// Best in a duplicate group = highest resolution, then HDR, then more audio
// channels, then bigger file -- a reasonable "which copy would I keep"
// default the group's own "Keep Best" button can act on in one click,
// without needing a per-file ffprobe/codec fetch just to rank duplicates.
const RESOLUTION_RANK = { "4K": 4, "1080p": 3, "720p": 2, "SD": 1 };

function duplicateQualityRank(item) {
  return [
    RESOLUTION_RANK[item.resolution] || 0,
    item.hdr ? 1 : 0,
    item.audio_channels || 0,
    item.size_bytes || 0,
  ];
}

function bestDuplicateIndex(group) {
  let bestIndex = 0;
  for (let i = 1; i < group.length; i++) {
    const a = duplicateQualityRank(group[i]);
    const b = duplicateQualityRank(group[bestIndex]);
    for (let d = 0; d < a.length; d++) {
      if (a[d] !== b[d]) { if (a[d] > b[d]) bestIndex = i; break; }
    }
  }
  return bestIndex;
}

function renderDuplicatesList() {
  const groups = state.libraryDuplicates || [];
  const container = $("#duplicates-list");
  if (groups.length === 0) {
    container.innerHTML = "<p>No duplicate groups remaining.</p>";
    return;
  }
  container.innerHTML = groups.map((group, groupIndex) => {
    const first = group[0];
    const label = first.media_type === "tv" && first.season_number != null
      ? `${first.title} S${String(first.season_number).padStart(2, "0")}E${String(first.episode_number).padStart(2, "0")}`
      : `${first.title}${first.year ? ` (${first.year})` : ""}`;
    const bestIndex = bestDuplicateIndex(group);
    return `
      <div class="duplicate-group">
        <div class="duplicate-group-header">
          <h4>${label}</h4>
          <button class="duplicate-keep-best-btn" data-group-index="${groupIndex}">Keep Best, Delete Others</button>
        </div>
        ${group.map((item, i) => `
          <div class="duplicate-row ${i === bestIndex ? "duplicate-row-best" : ""}">
            ${i === bestIndex ? `<span class="badge badge-ok" title="Highest resolution/HDR/audio/size in this group">★ Best</span>` : ""}
            <span class="duplicate-row-path" title="${item.final_path || ""}">${item.final_path || "(no file)"}</span>
            <span class="hint">${formatBytes(item.size_bytes)}${item.resolution ? ` · ${item.resolution}` : ""}${item.hdr ? " · HDR" : ""}${item.audio_channels ? ` · ${item.audio_channels}ch` : ""}</span>
            <button class="danger duplicate-delete-btn" data-path="${item.final_path || ""}">Delete This Copy</button>
          </div>
        `).join("")}
      </div>
    `;
  }).join("");
  container.querySelectorAll(".duplicate-delete-btn").forEach((btn) => {
    btn.addEventListener("click", () => deleteDuplicateCopy(btn.dataset.path));
  });
  container.querySelectorAll(".duplicate-keep-best-btn").forEach((btn) => {
    btn.addEventListener("click", () => keepBestDuplicate(groups[Number(btn.dataset.groupIndex)]));
  });
}

async function keepBestDuplicate(group) {
  const bestIndex = bestDuplicateIndex(group);
  const toDelete = group.filter((_, i) => i !== bestIndex).map((item) => item.final_path).filter(Boolean);
  if (toDelete.length === 0) return;
  const ok = await showConfirm(`Keep "${group[bestIndex].final_path}" and permanently delete the other ${toDelete.length} copy/copies? This cannot be undone.`);
  if (!ok) return;
  try {
    const data = await api("/api/library/delete-batch", { method: "POST", body: JSON.stringify({ paths: toDelete }) });
    await loadLibraryHealth();
    renderDuplicatesList();
    loadMoviesGallery();
    loadTvGallery();
    if (data.errors.length) $("#duplicates-list").insertAdjacentHTML("afterbegin", `<p>${data.errors.length} deletion(s) failed: ${data.errors.join("; ")}</p>`);
  } catch (e) {
    $("#duplicates-list").insertAdjacentHTML("afterbegin", `<p>Error: ${e.message}</p>`);
  }
}

async function deleteDuplicateCopy(path) {
  if (!path) return;
  const ok = await showConfirm(`Permanently delete "${path}"? This cannot be undone.`);
  if (!ok) return;
  try {
    await api("/api/library/delete-file", { method: "POST", body: JSON.stringify({ path }) });
    await loadLibraryHealth();
    renderDuplicatesList();
    loadMoviesGallery();
    loadTvGallery();
  } catch (e) {
    $("#duplicates-list").insertAdjacentHTML("afterbegin", `<p>Error: ${e.message}</p>`);
  }
}

export async function cleanupOrphans() {
  const dryRun = isBrowseDryRun();
  if (!dryRun) {
    const ok = await showConfirm("Remove database records for archived files that no longer exist on disk? This does not touch any files.");
    if (!ok) return;
  }
  try {
    const data = await api(`/api/library/orphans/cleanup?dry_run=${dryRun}`, { method: "POST" });
    $("#browse-status").textContent = dryRun
      ? `Dry run: would remove ${data.removed} orphaned record(s). Nothing was changed.`
      : `Removed ${data.removed} orphaned record(s).`;
    if (!dryRun) loadLibraryHealth();
  } catch (e) {
    $("#browse-status").textContent = `Error: ${e.message}`;
  }
}

async function deleteBrowseItem(index) {
  const item = state.browseFiltered[index];
  const dryRun = isBrowseDryRun();
  if (!dryRun) {
    const ok = await showConfirm(`Permanently delete "${item.path}"? This cannot be undone.`);
    if (!ok) return;
  }

  $("#browse-status").textContent = dryRun ? "Checking (dry run, nothing will change)..." : "Deleting...";
  try {
    const data = await api("/api/library/delete-file", { method: "POST", body: JSON.stringify({ path: item.path, dry_run: dryRun }) });
    if (dryRun) {
      const p = data.preview;
      const extra = p.sibling_files.length ? `, ${p.sibling_files.length} sibling file(s)` : "";
      $("#browse-status").textContent = `Dry run: would delete "${item.path}"${extra}${p.folder_removed ? ", and its now-empty folder" : ""}. Nothing was changed.`;
      return;
    }
    loadBrowse();
  } catch (e) {
    $("#browse-status").textContent = `Error: ${e.message}`;
  }
}

export async function deleteSelectedBrowseItems() {
  const selected = $all(".browse-check:checked").map((cb) => state.browseFiltered[Number(cb.dataset.index)]);
  if (selected.length === 0) return;
  const dryRun = isBrowseDryRun();
  if (!dryRun) {
    const totalBytes = selected.reduce((sum, item) => sum + (item.size_bytes || 0), 0);
    const ok = await showConfirm(`Permanently delete ${selected.length} file(s) (${formatBytes(totalBytes)})? This cannot be undone.`);
    if (!ok) return;
  }

  $("#browse-status").textContent = dryRun ? "Checking (dry run, nothing will change)..." : "Deleting...";
  try {
    const data = await api("/api/library/delete-batch", {
      method: "POST",
      body: JSON.stringify({ paths: selected.map((item) => item.path), dry_run: dryRun }),
    });
    if (dryRun) {
      const wouldDelete = data.previews.filter((p) => p.would_delete).length;
      const folders = data.previews.filter((p) => p.folder_removed).length;
      $("#browse-status").textContent = data.errors.length
        ? `Dry run: ${wouldDelete} would succeed (${folders} folder(s) removed too), ${data.errors.length} would fail: ${data.errors.join("; ")}`
        : `Dry run: all ${wouldDelete} file(s) would succeed (${folders} folder(s) removed too). Nothing was changed.`;
      return;
    }
    $("#browse-status").textContent = data.errors.length
      ? `Deleted ${data.deleted}, ${data.errors.length} failed: ${data.errors.join("; ")}`
      : `Deleted ${data.deleted} file(s).`;
    loadBrowse();
  } catch (e) {
    $("#browse-status").textContent = `Error: ${e.message}`;
  }
}

export async function organizeSelected() {
  const selected = $all(".browse-check:checked").map((cb) => state.browseFiltered[Number(cb.dataset.index)]);
  if (selected.length === 0) return;
  const sizeByPath = Object.fromEntries(selected.map((item) => [item.path, item.size_bytes]));
  await organizePaths(selected.map((item) => item.path), sizeByPath);
}

// Switches to the Archive tab and feeds the given paths into the existing
// preview-then-organize flow (moves files in place, updates existing
// media_items rows) -- shared by Browse's "Organize Selected" and the
// Movies/TV galleries' "Rename Selected".
export async function organizePaths(paths, sizeByPath) {
  if (paths.length === 0) return;

  $all(".tab-btn").forEach((b) => b.classList.remove("active"));
  $all(".tab-panel").forEach((p) => p.classList.remove("active"));
  $('.tab-btn[data-tab="archive"]').classList.add("active");
  $("#tab-archive").classList.add("active");

  setPreviewMode("organize");
  await previewPaths(paths, sizeByPath);
}
