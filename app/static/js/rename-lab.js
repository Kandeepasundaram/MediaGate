// Rename Lab: dry-run simulation of renaming every tracked episode of one TV
// show under one or more naming patterns (preset, saved or custom), with
// side-by-side comparison, per-episode edits, conflict fixing, filters, and
// batch undo. Nothing changes on disk until "Apply" -- which sends the
// checked rows to the existing /api/library/organize endpoint.
import { $, $all, api, formatBytes, showToast } from "./core.js";
import { escapeAttr, showConfirm } from "./archive-tab.js";
import { loadTvGallery } from "./gallery.js";
import {
  TOKEN_HINTS,
  basename,
  dirname,
  diffParts,
  formatTemplate,
  episodeContext,
  joinLike,
  renderFileName,
  renderSeasonFolder,
  sanitizeName,
  splitExt,
  suffixedName,
} from "./rename-lab-util.js";

const MAX_COMPARE = 3;
const BLOCKING_FLAGS = ["collision_on_disk", "batch_duplicate", "source_missing"];
const FLAG_LABELS = {
  unchanged: ["Already correct", "badge-ok"],
  collision_on_disk: ["File exists at destination", "badge-warn"],
  batch_duplicate: ["Same destination as another episode", "badge-warn"],
  missing_title: ["No episode title", "badge-new"],
  source_missing: ["Source file missing", "badge-warn"],
  skipped: ["Skipped", "badge-ok"],
};
const STATUS_FILTERS = [
  ["all", "All"],
  ["change", "Will change"],
  ["conflict", "Conflicts"],
  ["unchanged", "Already correct"],
  ["missing_title", "Missing title"],
  ["edited", "Edited"],
];
const EXTRA_TOKEN_CHIPS = ["season:02d", "episode:02d", "absolute_episode:03d"];

const lab = {
  show: null,
  showTitle: "",
  presets: [],
  tokens: [],
  slots: [], // patterns sent to the server, in column order
  results: [], // RenameLabPatternResult per slot
  activeIdx: 0,
  compare: false,
  compareIds: [],
  overrides: new Map(), // media_id -> {dest?, title?, skip?, overwrite?}
  existsCache: new Set(), // hand-edited destinations known to exist on disk
  selected: new Set(), // media_ids ticked for apply
  filter: { status: "all", season: "all", search: "" },
  editing: null, // media_id being edited inline
  showFolder: null,
  metadataAvailable: false,
  metadataNote: null,
  undoOps: [],
  debounce: null,
  cancelled: false,
  lastInput: null,
  simSeq: 0,
};

// ---------------------------------------------------------------- patterns

function showKey() {
  return `rename-lab:last:${lab.show.tmdb_id ?? lab.show.title}`;
}

function customPattern() {
  return { id: "custom", label: "Custom", tv_season_folder: $("#lab-season-folder").value, tv_file: $("#lab-file-template").value };
}

function patternById(id) {
  return id === "custom" ? customPattern() : lab.presets.find((p) => p.id === id);
}

function selectedPattern() {
  return patternById($("#lab-preset-select").value) || customPattern();
}

function currentSlots() {
  if (lab.compare && lab.compareIds.length > 0) {
    const slots = lab.compareIds.map(patternById).filter(Boolean);
    if (slots.length > 0) return slots;
  }
  return [selectedPattern()];
}

function activePattern() {
  return lab.slots[lab.activeIdx] || selectedPattern();
}

function syncCustomInputs() {
  const id = $("#lab-preset-select").value;
  const isCustom = id === "custom";
  $("#lab-season-folder").readOnly = !isCustom;
  $("#lab-file-template").readOnly = !isCustom;
  if (!isCustom) {
    const preset = lab.presets.find((p) => p.id === id);
    if (preset) {
      $("#lab-season-folder").value = preset.tv_season_folder;
      $("#lab-file-template").value = preset.tv_file;
    }
  }
  const saved = lab.presets.find((p) => p.id === id && p.saved);
  $("#lab-delete-saved-btn").classList.toggle("hidden", !saved);
  $("#lab-update-saved-btn").classList.toggle("hidden", !saved);
}

function renderPresetSelect(selectId) {
  const groups = [
    ["Built-in", lab.presets.filter((p) => !p.saved)],
    ["Saved", lab.presets.filter((p) => p.saved)],
  ];
  $("#lab-preset-select").innerHTML =
    groups
      .filter(([, list]) => list.length)
      .map(([label, list]) => `<optgroup label="${label}">${list.map((p) => `<option value="${escapeAttr(p.id)}">${escapeAttr(p.label)}</option>`).join("")}</optgroup>`)
      .join("") + `<option value="custom">Custom…</option>`;
  const ids = [...lab.presets.map((p) => p.id), "custom"];
  $("#lab-preset-select").value = ids.includes(selectId) ? selectId : (lab.presets[0]?.id || "custom");
  renderCompareList();
}

function renderCompareList() {
  const all = [...lab.presets, { id: "custom", label: "Custom (current inputs)" }];
  $("#lab-compare-list").innerHTML = all
    .map((p) => `<label class="checkbox-label"><input type="checkbox" class="lab-compare-check" value="${escapeAttr(p.id)}" ${lab.compareIds.includes(p.id) ? "checked" : ""}> ${escapeAttr(p.label)}</label>`)
    .join("");
  $all(".lab-compare-check").forEach((cb) => cb.addEventListener("change", () => {
    if (cb.checked) {
      if (lab.compareIds.length >= MAX_COMPARE) {
        cb.checked = false;
        setStatus(`Compare at most ${MAX_COMPARE} patterns at once.`);
        return;
      }
      lab.compareIds.push(cb.value);
    } else {
      lab.compareIds = lab.compareIds.filter((id) => id !== cb.value);
    }
    runSimulation();
  }));
}

function setCompare(on) {
  lab.compare = on;
  $("#lab-compare-btn").setAttribute("aria-pressed", String(on));
  $("#lab-compare-btn").classList.toggle("active", on);
  $("#lab-compare-panel").classList.toggle("hidden", !on);
  if (on && lab.compareIds.length === 0) {
    lab.compareIds = [$("#lab-preset-select").value];
    renderCompareList();
  }
  lab.activeIdx = 0;
  runSimulation();
}

function loadLastPattern() {
  try {
    return JSON.parse(localStorage.getItem(showKey()) || "null");
  } catch (e) {
    return null;
  }
}

function rememberPattern() {
  try {
    const p = selectedPattern();
    localStorage.setItem(showKey(), JSON.stringify({ id: p.id, season: p.tv_season_folder, file: p.tv_file }));
  } catch (e) { /* storage unavailable -- just won't be remembered */ }
}

// ------------------------------------------------------------ token builder

function renderTokenChips() {
  const names = [...lab.tokens, ...EXTRA_TOKEN_CHIPS];
  $("#lab-token-chips").innerHTML = names
    .map((t) => `<button type="button" class="lab-token-chip" data-token="${escapeAttr(t)}" title="${escapeAttr(TOKEN_HINTS[t] || "")}">{${escapeAttr(t)}}</button>`)
    .join(" ");
  $all(".lab-token-chip").forEach((chip) => {
    // mousedown + preventDefault keeps focus (and caret) in the template input.
    chip.addEventListener("mousedown", (e) => e.preventDefault());
    chip.addEventListener("click", () => insertToken(chip.dataset.token));
  });
}

function insertToken(token) {
  if ($("#lab-preset-select").value !== "custom") {
    // Editing a preset/saved pattern's text: switch to Custom first, keeping its templates as a starting point.
    $("#lab-preset-select").value = "custom";
    syncCustomInputs();
  }
  const input = lab.lastInput && lab.lastInput.id.startsWith("lab-") ? lab.lastInput : $("#lab-file-template");
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? start;
  const text = `{${token}}`;
  input.value = input.value.slice(0, start) + text + input.value.slice(end);
  input.focus();
  input.setSelectionRange(start + text.length, start + text.length);
  scheduleSimulation();
}

function updateSampleLine() {
  const p = selectedPattern();
  const err = $("#lab-template-error");
  const sample = $("#lab-sample");
  const row = lab.results[0]?.rows?.[0];
  let message = "";
  try {
    formatTemplate(p.tv_season_folder, episodeContext(lab.showTitle || "Show", row || { season: 1, episode: 1, code: "S01E01", episode_title: "Title" }));
    formatTemplate(p.tv_file, episodeContext(lab.showTitle || "Show", row || { season: 1, episode: 1, code: "S01E01", episode_title: "Title" }));
  } catch (e) {
    message = e.message;
  }
  const serverError = lab.results[lab.activeIdx]?.error;
  const shown = message || serverError || "";
  err.textContent = shown ? `Template problem: ${shown}` : "";
  err.classList.toggle("hidden", !shown);
  for (const id of ["#lab-season-folder", "#lab-file-template"]) $(id).setAttribute("aria-invalid", String(Boolean(shown)));

  if (shown || !row) {
    sample.textContent = "";
    return;
  }
  const ext = splitExt(basename(row.current_path)).ext;
  try {
    const folder = renderSeasonFolder(p.tv_season_folder, lab.showTitle, row);
    sample.textContent = `Sample (${row.code}): ${folder} / ${renderFileName(p.tv_file, lab.showTitle, row, undefined, ext)}`;
  } catch (e) {
    sample.textContent = "";
  }
}

// ---------------------------------------------------------- effective rows

function usesTitle(result) {
  return result.tv_file.includes("{episode_title");
}

// The active pattern's rows with hand edits applied and conflict flags
// recomputed from the *effective* destinations (so fixes show up instantly).
function effectiveRows() {
  const result = lab.results[lab.activeIdx];
  if (!result || result.error) return [];
  const rows = result.rows.map((row) => {
    const ov = lab.overrides.get(row.media_id) || {};
    return {
      row, ov,
      dest: ov.dest ?? row.dest_path,
      title: ov.title !== undefined ? ov.title : row.episode_title,
      edited: ov.dest !== undefined || ov.title !== undefined,
      flags: [],
    };
  });
  const counts = new Map();
  rows.forEach((r) => counts.set(r.dest.toLowerCase(), (counts.get(r.dest.toLowerCase()) || 0) + 1));
  const needsTitle = usesTitle(result);
  rows.forEach((r) => {
    const unchanged = r.dest === r.row.current_path;
    if (unchanged) r.flags.push("unchanged");
    if (r.row.flags.includes("source_missing")) r.flags.push("source_missing");
    if (!unchanged && !r.ov.overwrite) {
      const exists = r.ov.dest !== undefined ? lab.existsCache.has(r.dest) : r.row.flags.includes("collision_on_disk");
      if (exists) r.flags.push("collision_on_disk");
    }
    if (counts.get(r.dest.toLowerCase()) > 1) r.flags.push("batch_duplicate");
    if (needsTitle && !r.title) r.flags.push("missing_title");
    if (r.ov.skip) r.flags.push("skipped");
    r.blocked = r.flags.some((f) => BLOCKING_FLAGS.includes(f)) && !r.ov.skip;
    r.unchanged = unchanged;
  });
  return rows;
}

function defaultSelection(rows) {
  lab.selected = new Set(rows.filter((r) => !r.blocked && !r.unchanged && !r.ov.skip).map((r) => r.row.media_id));
}

function passesFilter(r) {
  const f = lab.filter;
  if (f.status === "change" && (r.blocked || r.unchanged || r.ov.skip)) return false;
  if (f.status === "conflict" && !r.blocked) return false;
  if (f.status === "unchanged" && !r.unchanged) return false;
  if (f.status === "missing_title" && !r.flags.includes("missing_title")) return false;
  if (f.status === "edited" && !r.edited) return false;
  if (f.season !== "all" && String(r.row.season) !== f.season) return false;
  if (f.search) {
    const hay = `${r.row.code} ${r.title || ""} ${basename(r.row.current_path)} ${basename(r.dest)}`.toLowerCase();
    if (!hay.includes(f.search.toLowerCase())) return false;
  }
  return true;
}

// ------------------------------------------------------------------ open/sim

function setStatus(text) {
  $("#lab-status").textContent = text;
}

export async function openRenameLab(show) {
  lab.show = show;
  lab.showTitle = show.title;
  lab.overrides.clear();
  lab.existsCache.clear();
  lab.selected.clear();
  lab.editing = null;
  lab.undoOps = [];
  lab.filter = { status: "all", season: "all", search: "" };
  lab.activeIdx = 0;
  lab.compare = false;
  lab.compareIds = [];
  $("#lab-search").value = "";
  $("#lab-undo-banner").classList.add("hidden");
  $("#lab-compare-panel").classList.add("hidden");
  $("#lab-compare-btn").setAttribute("aria-pressed", "false");
  $("#lab-compare-btn").classList.remove("active");

  // Stay "inside" the TV tab: keep its button active, swap the panel only.
  $all(".tab-panel").forEach((p) => p.classList.remove("active"));
  $("#tab-rename-lab").classList.add("active");
  window.scrollTo({ top: 0 });

  $("#lab-title").textContent = `Rename Lab — ${show.title}`;
  setStatus("Loading…");
  $("#lab-table thead").innerHTML = "";
  $("#lab-table tbody").innerHTML = "";
  $("#lab-summary").innerHTML = "";

  try {
    await loadPresets();
    renderTokenChips();
    const last = loadLastPattern();
    if (last && last.id === "custom") {
      renderPresetSelect("custom");
      $("#lab-season-folder").value = last.season;
      $("#lab-file-template").value = last.file;
    } else {
      renderPresetSelect(last?.id || "default");
    }
    syncCustomInputs();
    await runSimulation();
  } catch (e) {
    setStatus(`Error: ${e.message}`);
  }
}

async function loadPresets() {
  const data = await api("/api/library/rename-lab/presets");
  lab.presets = data.presets;
  lab.tokens = data.tokens;
}

function scheduleSimulation() {
  clearTimeout(lab.debounce);
  lab.debounce = setTimeout(runSimulation, 400);
}

async function runSimulation(note = "", extra = {}) {
  clearTimeout(lab.debounce);
  const seq = ++lab.simSeq;
  lab.slots = currentSlots();
  setStatus("Simulating…");
  try {
    const data = await api("/api/library/rename-lab/simulate", {
      method: "POST",
      body: JSON.stringify({
        tmdb_id: lab.show.tmdb_id ?? null,
        title: lab.show.tmdb_id == null ? lab.show.title : null,
        patterns: lab.slots.map(({ id, label, tv_season_folder, tv_file }) => ({ id, label, tv_season_folder, tv_file })),
        ...extra,
      }),
    });
    if (seq !== lab.simSeq) return; // a newer simulation is already in flight
    lab.results = data.patterns;
    lab.showTitle = data.show_title || lab.show.title;
    lab.metadataAvailable = data.metadata_available;
    lab.metadataNote = data.metadata_note;
    if (data.rescan_added != null) {
      note = `${data.rescan_added ? `Rescan added ${data.rescan_added} new file(s) to the library (they may need a TMDB match to show here).` : "Rescan found no new files."}`;
    }
    lab.showFolder = data.show_folder;
    lab.activeIdx = Math.min(lab.activeIdx, lab.results.length - 1);
    lab.overrides.clear();
    lab.editing = null;
    defaultSelection(effectiveRows());
    rememberPattern();
    render();
    const checked = `${lab.results[lab.activeIdx]?.rows.length ?? 0} episode file(s) re-simulated.`;
    setStatus(note ? `${note} ${checked}` : `${checked} Nothing has been changed.`);
  } catch (e) {
    if (seq !== lab.simSeq) return;
    lab.results = [];
    render();
    setStatus(`Error: ${e.message}`);
  }
}

// -------------------------------------------------------------------- render

function render() {
  updateSampleLine();
  renderFolders();
  renderSummary();
  renderFilters();
  renderTable();
  updateSelectionCount();
}

function renderSummary() {
  const rows = effectiveRows();
  if (rows.length === 0) {
    $("#lab-summary").innerHTML = "";
    return;
  }
  const conflicts = rows.filter((r) => r.blocked).length;
  const unchanged = rows.filter((r) => r.unchanged && !r.blocked).length;
  const skipped = rows.filter((r) => r.ov.skip).length;
  const changes = rows.length - conflicts - unchanged - skipped;
  $("#lab-summary").innerHTML = `
    <span class="badge badge-new">${changes} will change</span>
    <span class="badge badge-ok">${unchanged} already correct</span>
    <span class="badge badge-warn">${conflicts} conflict(s)</span>
    ${skipped ? `<span class="badge badge-ok">${skipped} skipped</span>` : ""}
    ${lab.metadataAvailable ? "" : `<span class="hint">${escapeAttr(lab.metadataNote || "No TMDB/TVmaze episode data.")} Titles/air dates fall back to stored metadata.</span>`}
  `;
}

function renderFilters() {
  const rows = effectiveRows();
  const countFor = (key) => rows.filter((r) => passesFilterIgnoringSeasonSearch(r, key)).length;
  $("#lab-filter-chips").innerHTML = STATUS_FILTERS
    .map(([key, label]) => `<button type="button" class="lab-chip ${lab.filter.status === key ? "active" : ""}" data-status="${key}" aria-pressed="${lab.filter.status === key}">${label} <span class="lab-chip-count">${countFor(key)}</span></button>`)
    .join("");
  $all(".lab-chip").forEach((chip) => chip.addEventListener("click", () => {
    lab.filter.status = chip.dataset.status;
    renderFilters();
    renderTable();
    updateSelectionCount();
  }));
  const seasons = [...new Set(rows.map((r) => r.row.season))].sort((a, b) => a - b);
  $("#lab-season-filter").innerHTML =
    `<option value="all">All seasons</option>` + seasons.map((s) => `<option value="${s}">Season ${s}</option>`).join("");
  $("#lab-season-filter").value = seasons.map(String).includes(lab.filter.season) ? lab.filter.season : "all";
  lab.filter.season = $("#lab-season-filter").value;
}

function passesFilterIgnoringSeasonSearch(r, status) {
  if (status === "change") return !(r.blocked || r.unchanged || r.ov.skip);
  if (status === "conflict") return r.blocked;
  if (status === "unchanged") return r.unchanged;
  if (status === "missing_title") return r.flags.includes("missing_title");
  if (status === "edited") return r.edited;
  return true;
}

function diffHtml(oldName, newName) {
  const [pre, mid, post] = diffParts(oldName, newName);
  return `${escapeAttr(pre)}${mid ? `<mark class="diff-highlight">${escapeAttr(mid)}</mark>` : ""}${escapeAttr(post)}`;
}

function destCell(destPath, currentPath) {
  const folder = destPath.split(/[\\/]/).slice(-3, -1).join("/");
  return `<div class="dest-diff" title="${escapeAttr(destPath)}">${escapeAttr(folder)}/${diffHtml(basename(currentPath), basename(destPath))}</div>`;
}

function flagBadges(flags) {
  return flags.map((f) => (FLAG_LABELS[f] ? `<span class="badge ${FLAG_LABELS[f][1]}">${FLAG_LABELS[f][0]}</span>` : "")).join(" ");
}

function fixMenu(r) {
  const collision = r.flags.includes("collision_on_disk");
  return `<select class="lab-fix-select" data-id="${r.row.media_id}" aria-label="Fix conflict for ${escapeAttr(r.row.code)}">
      <option value="">Fix…</option>
      <option value="suffix">Add (2) suffix</option>
      <option value="skip">Skip this episode</option>
      ${collision ? `<option value="overwrite">Overwrite existing file</option>` : ""}
    </select>`;
}

function renderTable() {
  const thead = $("#lab-table thead");
  const tbody = $("#lab-table tbody");
  if (lab.results.length === 0) {
    thead.innerHTML = "";
    tbody.innerHTML = "";
    return;
  }
  const multi = lab.results.length > 1;
  const headProposed = lab.results
    .map((res, k) => `<th class="${k === lab.activeIdx ? "lab-col-active" : ""}">
        ${multi ? `<label class="checkbox-label"><input type="radio" name="lab-active-pattern" class="lab-active-radio" value="${k}" ${k === lab.activeIdx ? "checked" : ""}> Use ${escapeAttr(res.label)}</label>` : "Proposed"}
        ${multi ? `<div class="hint">${res.error ? `<span class="badge badge-warn">${escapeAttr(res.error)}</span>` : `${res.changes} change · ${res.conflicts} conflict · ${res.unchanged} same`}</div>` : ""}
      </th>`)
    .join("");
  thead.innerHTML = `<tr><th></th><th>Ep</th><th>Current</th>${headProposed}<th>Episode data</th><th>File</th><th></th></tr>`;
  $all(".lab-active-radio").forEach((radio) => radio.addEventListener("change", () => {
    lab.activeIdx = Number(radio.value);
    lab.overrides.clear();
    lab.editing = null;
    defaultSelection(effectiveRows());
    render();
  }));

  const rows = effectiveRows();
  const visible = rows.map((r, i) => ({ r, i })).filter(({ r }) => passesFilter(r));
  $("#lab-visible-count").textContent = `${visible.length} of ${rows.length} shown`;

  let currentSeason = null;
  const html = [];
  visible.forEach(({ r, i }) => {
    const row = r.row;
    if (row.season !== currentSeason) {
      currentSeason = row.season;
      html.push(`<tr class="season-pack-header-row"><td><input type="checkbox" class="lab-season-check" data-season="${row.season}" aria-label="Select season ${row.season}"></td><td colspan="${5 + lab.results.length}">Season ${row.season}</td></tr>`);
    }
    const oldName = basename(row.current_path);
    const meta = [
      r.title && `“${r.title}”`,
      row.air_date,
      row.absolute_episode != null && `abs #${row.absolute_episode}`,
    ].filter(Boolean).join(" · ");
    const fileInfo = [
      row.size_bytes != null && formatBytes(row.size_bytes),
      row.duration_seconds && `${Math.round(row.duration_seconds / 60)} min`,
      row.height && `${row.height}p`,
    ].filter(Boolean).join(" · ");

    const proposedCells = lab.results.map((res, k) => {
      if (res.error) return `<td class="hint">—</td>`;
      if (k !== lab.activeIdx) {
        const other = res.rows[i];
        return `<td>${destCell(other.dest_path, other.current_path)}${flagBadges(other.flags)}</td>`;
      }
      if (lab.editing === row.media_id) return `<td class="lab-col-active">${editWidget(r)}</td>`;
      return `<td class="lab-col-active">
          ${destCell(r.dest, row.current_path)}
          ${r.edited ? `<span class="badge badge-new">edited</span>` : ""}
          ${flagBadges(r.flags)}
          ${r.blocked ? fixMenu(r) : ""}
          ${r.ov.skip || r.ov.overwrite ? `<button type="button" class="link-btn lab-clear-fix" data-id="${row.media_id}">undo ${r.ov.skip ? "skip" : "overwrite"}</button>` : ""}
        </td>`;
    }).join("");

    const checked = lab.selected.has(row.media_id);
    const disabled = r.unchanged || r.ov.skip;
    html.push(`
      <tr data-id="${row.media_id}">
        <td><input type="checkbox" class="lab-row-check" data-id="${row.media_id}" data-season="${row.season}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} aria-label="Select ${escapeAttr(row.code)}"></td>
        <td>${escapeAttr(row.code)}</td>
        <td title="${escapeAttr(row.current_path)}">${escapeAttr(oldName)}</td>
        ${proposedCells}
        <td class="hint" title="${escapeAttr(row.overview)}">${escapeAttr(meta)}</td>
        <td class="hint">${escapeAttr(fileInfo)}</td>
        <td>${lab.editing === row.media_id ? "" : `<button type="button" class="lab-edit-btn" data-id="${row.media_id}" title="Edit this episode's name or title">✎</button>`}</td>
      </tr>`);
  });
  tbody.innerHTML = html.join("") || `<tr><td colspan="${6 + lab.results.length}" class="hint">No episodes match the current filter.</td></tr>`;
  wireTable();
}

function editWidget(r) {
  const { stem, ext } = splitExt(basename(r.dest));
  return `<div class="lab-edit">
      <label class="hint">File name
        <span class="lab-edit-line"><input type="text" class="lab-edit-name" value="${escapeAttr(stem)}" spellcheck="false" autocomplete="off"><span>${escapeAttr(ext)}</span></span>
      </label>
      <label class="hint">Episode title
        <input type="text" class="lab-edit-title" value="${escapeAttr(r.title || "")}" autocomplete="off">
      </label>
      <div class="lab-edit-actions">
        <button type="button" class="primary lab-edit-save" data-id="${r.row.media_id}">Save</button>
        <button type="button" class="lab-edit-cancel">Cancel</button>
        ${r.edited ? `<button type="button" class="lab-edit-reset" data-id="${r.row.media_id}">Reset</button>` : ""}
      </div>
    </div>`;
}

function wireTable() {
  $all(".lab-season-check").forEach((cb) => cb.addEventListener("change", () => {
    $all(`.lab-row-check[data-season="${cb.dataset.season}"]:not(:disabled)`).forEach((rc) => {
      rc.checked = cb.checked;
      toggleSelected(Number(rc.dataset.id), cb.checked);
    });
    updateSelectionCount();
  }));
  $all(".lab-row-check").forEach((cb) => cb.addEventListener("change", () => {
    toggleSelected(Number(cb.dataset.id), cb.checked);
    updateSelectionCount();
  }));
  $all(".lab-edit-btn").forEach((btn) => btn.addEventListener("click", () => {
    lab.editing = Number(btn.dataset.id);
    renderTable();
    $(".lab-edit-name")?.focus();
  }));
  $all(".lab-edit-cancel").forEach((btn) => btn.addEventListener("click", () => { lab.editing = null; renderTable(); }));
  $all(".lab-edit-reset").forEach((btn) => btn.addEventListener("click", () => {
    lab.overrides.delete(Number(btn.dataset.id));
    lab.editing = null;
    refreshAfterChange();
  }));
  $all(".lab-edit-save").forEach((btn) => btn.addEventListener("click", () => commitEdit(Number(btn.dataset.id))));
  $all(".lab-edit-name, .lab-edit-title").forEach((input) => input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") $(".lab-edit-save")?.click();
    if (e.key === "Escape") $(".lab-edit-cancel")?.click();
  }));
  $all(".lab-fix-select").forEach((sel) => sel.addEventListener("change", () => {
    if (sel.value) fixRow(Number(sel.dataset.id), sel.value);
  }));
  $all(".lab-clear-fix").forEach((btn) => btn.addEventListener("click", () => {
    const id = Number(btn.dataset.id);
    const ov = { ...(lab.overrides.get(id) || {}) };
    delete ov.skip;
    delete ov.overwrite;
    setOverride(id, ov);
    refreshAfterChange();
  }));
}

function toggleSelected(id, on) {
  if (on) lab.selected.add(id);
  else lab.selected.delete(id);
}

function setOverride(id, ov) {
  if (Object.keys(ov).length === 0) lab.overrides.delete(id);
  else lab.overrides.set(id, ov);
}

function refreshAfterChange() {
  render();
}

function updateSelectionCount() {
  const rows = effectiveRows();
  const ids = new Set(rows.map((r) => r.row.media_id));
  for (const id of [...lab.selected]) if (!ids.has(id)) lab.selected.delete(id);
  const n = lab.selected.size;
  $("#lab-selected-count").textContent = `${n} selected`;
  $("#lab-apply-btn").disabled = n === 0 || Boolean(lab.results[lab.activeIdx]?.error);
  $("#lab-resolve-btn").disabled = !rows.some((r) => r.blocked);
}

// ------------------------------------------------------------ edits & fixes

async function refreshExists(paths) {
  if (paths.length === 0) return;
  try {
    const res = await api("/api/library/rename-lab/check", { method: "POST", body: JSON.stringify({ paths }) });
    const existing = new Set(res.exists);
    paths.forEach((p) => (existing.has(p) ? lab.existsCache.add(p) : lab.existsCache.delete(p)));
  } catch (e) {
    setStatus(`Could not check destination: ${e.message}`);
  }
}

async function commitEdit(mediaId) {
  const r = effectiveRows().find((x) => x.row.media_id === mediaId);
  if (!r) return;
  const row = r.row;
  const ov = { ...(lab.overrides.get(mediaId) || {}) };
  const { stem: currentStem, ext } = splitExt(basename(r.dest));
  const newStem = $(".lab-edit-name").value.trim();
  const newTitle = $(".lab-edit-title").value.trim();
  const dirOfDest = dirname(r.dest);

  let dest = r.dest;
  if (newStem && newStem !== currentStem) {
    dest = joinLike(r.dest, dirOfDest, sanitizeName(newStem) + ext);
  } else if (newTitle !== (r.title || "")) {
    // Title changed but the name wasn't touched by hand: re-render the name from the pattern.
    try {
      dest = joinLike(r.dest, dirOfDest, renderFileName(activePattern().tv_file, lab.showTitle, row, newTitle, ext));
    } catch (e) { /* keep the current name; the title is still corrected in metadata */ }
  }

  if (dest === row.dest_path) delete ov.dest;
  else ov.dest = dest;
  if (newTitle === (row.episode_title || "")) delete ov.title;
  else ov.title = newTitle || null;
  delete ov.skip;
  setOverride(mediaId, ov);
  lab.editing = null;
  if (ov.dest) await refreshExists([ov.dest]);
  if (lab.selected.has(mediaId) === false && ov.dest) lab.selected.add(mediaId);
  refreshAfterChange();
}

function otherDests(rows, exceptId) {
  return new Set(rows.filter((x) => x.row.media_id !== exceptId).map((x) => x.dest.toLowerCase()));
}

// Finds the first "name (n).ext" that neither exists on disk nor is taken by another row.
async function nextFreeSuffix(r, rows) {
  const name = basename(r.dest);
  const taken = otherDests(rows, r.row.media_id);
  const candidates = [];
  for (let n = 2; n <= 12; n++) candidates.push(joinLike(r.dest, dirname(r.dest), suffixedName(name, n)));
  await refreshExists(candidates);
  return candidates.find((c) => !lab.existsCache.has(c) && !taken.has(c.toLowerCase()));
}

async function applyFix(mediaId, policy) {
  const rows = effectiveRows();
  const r = rows.find((x) => x.row.media_id === mediaId);
  if (!r) return false;
  const ov = { ...(lab.overrides.get(mediaId) || {}) };
  if (policy === "skip") {
    ov.skip = true;
    lab.selected.delete(mediaId);
  } else if (policy === "overwrite") {
    ov.overwrite = true;
    lab.selected.add(mediaId);
  } else {
    const free = await nextFreeSuffix(r, rows);
    if (!free) {
      setStatus(`No free "(n)" name found for ${r.row.code}; edit it by hand.`);
      return false;
    }
    ov.dest = free;
    lab.selected.add(mediaId);
  }
  setOverride(mediaId, ov);
  return true;
}

async function fixRow(mediaId, policy) {
  if (policy === "overwrite") {
    const r = effectiveRows().find((x) => x.row.media_id === mediaId);
    const ok = await showConfirm(`Overwrite the existing file "${basename(r.dest)}"? It will be replaced by ${r.row.code}.`);
    if (!ok) {
      renderTable();
      return;
    }
  }
  await applyFix(mediaId, policy);
  refreshAfterChange();
}

async function resolveAllConflicts() {
  const policy = $("#lab-resolve-policy").value;
  const blockedIds = effectiveRows().filter((r) => r.blocked).map((r) => r.row.media_id);
  if (blockedIds.length === 0) return;
  if (policy === "overwrite") {
    const ok = await showConfirm(`Overwrite existing files for up to ${blockedIds.length} conflicting episode(s)? Files already at the destination will be replaced.`);
    if (!ok) return;
  }
  setStatus("Resolving conflicts…");
  for (const id of blockedIds) {
    const rows = effectiveRows();
    const r = rows.find((x) => x.row.media_id === id);
    if (!r || !r.blocked) continue; // an earlier fix already resolved it
    const onlyDuplicate = r.flags.includes("batch_duplicate") && !r.flags.includes("collision_on_disk");
    if (onlyDuplicate) {
      // Keep the first episode of a duplicate group as is; fix the rest.
      const first = rows.find((x) => x.dest.toLowerCase() === r.dest.toLowerCase());
      if (first.row.media_id === id) continue;
    }
    // Overwrite only makes sense against a file on disk; duplicates inside the batch get a suffix instead.
    await applyFix(id, policy === "overwrite" && !r.flags.includes("collision_on_disk") ? "suffix" : policy);
  }
  refreshAfterChange();
  const left = effectiveRows().filter((r) => r.blocked).length;
  setStatus(left ? `${left} conflict(s) remain — edit them by hand.` : "All conflicts resolved.");
}

// -------------------------------------------------------------------- folders

function folderPlan(rows) {
  const sf = lab.showFolder;
  const showRename = sf && sf.will_rename ? { from: sf.current, to: sf.proposed } : null;
  const seasons = new Map();
  rows.forEach((r) => {
    const fromDir = dirname(r.row.current_path);
    const toName = basename(dirname(r.dest));
    if (basename(fromDir) !== toName && !seasons.has(r.row.season)) seasons.set(r.row.season, { season: r.row.season, fromName: basename(fromDir), toName });
  });
  return { showRename, seasons: [...seasons.values()], showConflict: Boolean(sf?.conflict) };
}

function renderFolders() {
  const rows = effectiveRows();
  const plan = folderPlan(rows);
  const any = plan.showRename || plan.seasons.length > 0 || plan.showConflict;
  $("#lab-folders").classList.toggle("hidden", !any);
  if (!any) {
    $("#lab-folders-check").checked = false;
    return;
  }
  const lines = [];
  if (plan.showRename) lines.push(`Show folder: <code>${escapeAttr(basename(plan.showRename.from))}</code> → <code>${escapeAttr(basename(plan.showRename.to))}</code>`);
  if (plan.showConflict) lines.push(`<span class="badge badge-warn">Show folder "${escapeAttr(basename(lab.showFolder.proposed))}" already exists</span> — folders can't be merged here, so the show folder is left alone.`);
  plan.seasons.slice(0, 6).forEach((s) => lines.push(`Season ${s.season}: <code>${escapeAttr(s.fromName)}</code> → <code>${escapeAttr(s.toName)}</code>`));
  if (plan.seasons.length > 6) lines.push(`…and ${plan.seasons.length - 6} more season folder(s)`);
  lines.push(`<em>Folder renames can't be undone automatically.</em>`);
  $("#lab-folders-text").innerHTML = lines.join("<br>");
}

// -------------------------------------------------------------------- apply

function organizeScope(fileStep) {
  return { file: fileStep, folder: false, sidecars: fileStep && $("#lab-scope-sidecars").checked };
}

async function postOrganize(items, scope, dryRun) {
  return api("/api/library/organize", { method: "POST", body: JSON.stringify({ items, dry_run: dryRun, scope }) });
}

function itemFor(r) {
  return { ...r.row.item, dest_path: r.dest, episode_title: r.title || null };
}

async function applySelected() {
  const rows = effectiveRows().filter((r) => lab.selected.has(r.row.media_id));
  if (rows.length === 0) return;
  const blocked = rows.filter((r) => r.blocked);
  if (blocked.length > 0) {
    setStatus(`${blocked.length} selected episode(s) have conflicts (${blocked.slice(0, 4).map((r) => r.row.code).join(", ")}…). Fix or untick them first.`);
    return;
  }
  const fileStep = $("#lab-scope-file").checked;
  const folderStep = !$("#lab-folders-check").closest("#lab-folders").classList.contains("hidden") && $("#lab-folders-check").checked;
  if (!fileStep && !folderStep) {
    setStatus("Tick File and/or Also rename folders.");
    return;
  }
  const dryRun = $("#lab-dry-run").checked;
  const fileRows = rows.filter((r) => !r.unchanged);

  if (!dryRun) {
    const parts = [fileStep && `${fileRows.length} file name(s)`, folderStep && "folder names"].filter(Boolean).join(" and ");
    const ok = await showConfirm(`Rename ${parts} of "${lab.showTitle}" using pattern "${activePattern().label}"? Files are moved in place${folderStep ? "; folder renames can't be undone automatically" : "; you can undo this batch right after"}.`);
    if (!ok) return;
  }

  lab.cancelled = false;
  $("#lab-apply-btn").disabled = true;
  const cancelBtn = $("#lab-cancel-btn");
  const fileResults = new Map(); // media_id -> result
  let skipped = 0;
  try {
    if (fileStep) {
      const items = fileRows.map(itemFor);
      if (dryRun) {
        setStatus("Checking (dry run, nothing will change)…");
        const res = await postOrganize(items, organizeScope(true), true);
        res.results.forEach((x, i) => fileResults.set(fileRows[i].row.media_id, x));
      } else {
        cancelBtn.classList.remove("hidden");
        for (let i = 0; i < fileRows.length; i++) {
          if (lab.cancelled) break;
          setStatus(`Renaming ${i + 1}/${fileRows.length}…`);
          const res = await postOrganize([items[i]], organizeScope(true), false);
          fileResults.set(fileRows[i].row.media_id, res.results[0]);
        }
        skipped = fileRows.length - fileResults.size;
      }
    }

    if (dryRun) {
      const results = [...fileResults.values()];
      const failures = results.filter((x) => x.status === "failed");
      setStatus((failures.length
        ? `Dry run: ${results.length - failures.length} would succeed, ${failures.length} would fail (${failures.map((f) => f.error).join("; ")})`
        : `Dry run: all ${results.length} file(s) would succeed.`) + (folderStep ? " Folder renames aren't simulated by the dry run." : " Nothing was changed."));
      updateSelectionCount();
      return;
    }

    let folderFailures = [];
    let folderDone = false;
    if (folderStep && !lab.cancelled) {
      ({ failures: folderFailures, done: folderDone } = await runFolderStep(rows, fileResults, fileStep));
    }

    const ok = [...fileResults.values()].filter((x) => x.status === "success");
    const failed = [...fileResults.values()].filter((x) => x.status === "failed");
    lab.undoOps = folderDone ? [] : ok.map((x) => x.operation_id).filter((id) => id != null);
    const summary =
      `Renamed ${ok.length} file(s)` +
      (failed.length ? `, ${failed.length} failed (${failed.map((f) => f.error).join("; ")})` : "") +
      (folderDone ? ", folders renamed" : "") +
      (folderFailures.length ? `, folder problems: ${folderFailures.join("; ")}` : "") +
      (skipped > 0 ? `. Cancelled — ${skipped} not processed.` : ".");
    showUndoBanner(ok.length, folderDone);
    showToast?.(`Renamed ${ok.length} episode file(s)`);
    loadTvGallery();
    await runSimulation(summary);
  } catch (e) {
    setStatus(`Error: ${e.message}`);
    updateSelectionCount();
  } finally {
    cancelBtn.classList.add("hidden");
  }
}

// Renames the show folder + season folders once, after the file renames: one
// organize call per season, on a single already-renamed episode, so the
// organizer's in-place folder rename (and its DB path rebasing for every
// other tracked row inside) runs once per folder instead of once per file.
async function runFolderStep(rows, fileResults, fileStep) {
  const plan = folderPlan(effectiveRows());
  const failures = [];
  let done = false;
  let showFrom = plan.showRename?.from;
  const showTo = plan.showRename?.to;
  let showRenamed = false;
  const remap = (p) => (showRenamed && showFrom && p.startsWith(showFrom) ? showTo + p.slice(showFrom.length) : p);

  // One sample episode per season that was (or needed no) file rename.
  const bySeason = new Map();
  rows.forEach((r) => {
    const res = fileResults.get(r.row.media_id);
    if (fileStep && !r.unchanged && res?.status !== "success") return;
    if (!bySeason.has(r.row.season)) bySeason.set(r.row.season, r);
  });

  const seasonNames = new Map(plan.seasons.map((s) => [s.season, s.toName]));
  const calls = [...bySeason.values()].filter((r) => seasonNames.has(r.row.season));
  if (plan.showRename && calls.length === 0 && bySeason.size > 0) calls.push([...bySeason.values()][0]);

  for (const r of calls) {
    const nowName = fileStep && !r.unchanged ? basename(r.dest) : basename(r.row.current_path);
    const nowPath = remap(joinLike(r.row.current_path, dirname(r.row.current_path), nowName));
    const targetShow = plan.showRename ? showTo : dirname(dirname(nowPath));
    const targetSeason = seasonNames.get(r.row.season) || basename(dirname(nowPath));
    const destPath = joinLike(nowPath, joinLike(nowPath, targetShow, targetSeason), nowName);
    if (destPath === nowPath) continue;
    setStatus(`Renaming folders (season ${r.row.season})…`);
    const item = { ...itemFor(r), source_path: nowPath, dest_path: destPath };
    const res = await postOrganize([item], { file: false, folder: true, sidecars: false }, false);
    const out = res.results[0];
    if (out.status === "success") {
      done = true;
      if (plan.showRename) showRenamed = true;
    } else {
      failures.push(`season ${r.row.season}: ${out.error}`);
    }
  }
  return { failures, done };
}

// ---------------------------------------------------------------------- undo

function showUndoBanner(fileCount, folderDone) {
  const banner = $("#lab-undo-banner");
  if (fileCount === 0) {
    banner.classList.add("hidden");
    return;
  }
  banner.classList.remove("hidden");
  $("#lab-undo-text").textContent = folderDone
    ? `Renamed ${fileCount} file(s) and folders. Folder renames can't be undone automatically — use History for individual files.`
    : `Renamed ${fileCount} file(s).`;
  $("#lab-undo-btn").classList.toggle("hidden", lab.undoOps.length === 0);
  $("#lab-undo-btn").disabled = false;
}

async function undoAll() {
  const ids = [...lab.undoOps].reverse();
  if (ids.length === 0) return;
  $("#lab-undo-btn").disabled = true;
  let ok = 0;
  const problems = [];
  for (let i = 0; i < ids.length; i++) {
    setStatus(`Undoing ${i + 1}/${ids.length}…`);
    try {
      await api(`/api/archive/history/${ids[i]}/undo`, { method: "POST" });
      ok++;
    } catch (e) {
      problems.push(e.message);
    }
  }
  lab.undoOps = [];
  $("#lab-undo-banner").classList.add("hidden");
  const summary = `Undid ${ok} of ${ids.length} rename(s)${problems.length ? ` — ${problems.length} failed (${problems.slice(0, 2).join("; ")})` : ""}.`;
  loadTvGallery();
  await runSimulation(summary);
}

// ----------------------------------------------------------- saved patterns

async function savePattern(update) {
  const current = selectedPattern();
  const savedEntry = lab.presets.find((p) => p.id === $("#lab-preset-select").value && p.saved);
  const name = update ? savedEntry?.label : $("#lab-save-name").value.trim();
  if (!name) {
    setStatus("Give the pattern a name.");
    return;
  }
  try {
    const saved = await api("/api/library/rename-lab/patterns", {
      method: "POST",
      body: JSON.stringify({
        label: name,
        tv_season_folder: current.tv_season_folder,
        tv_file: current.tv_file,
        ...(update && savedEntry ? { id: savedEntry.id } : {}),
      }),
    });
    await loadPresets();
    renderPresetSelect(saved.id);
    syncCustomInputs();
    $("#lab-save-form").classList.add("hidden");
    $("#lab-save-as-btn").classList.remove("hidden");
    $("#lab-save-name").value = "";
    showToast?.(update ? "Saved pattern updated" : `Saved pattern "${saved.label}"`);
    await runSimulation();
  } catch (e) {
    setStatus(`Could not save pattern: ${e.message}`);
  }
}

async function deleteSavedPattern() {
  const id = $("#lab-preset-select").value;
  const entry = lab.presets.find((p) => p.id === id && p.saved);
  if (!entry) return;
  if (!(await showConfirm(`Delete saved pattern "${entry.label}"?`))) return;
  try {
    await api(`/api/library/rename-lab/patterns/${encodeURIComponent(id)}`, { method: "DELETE" });
    lab.compareIds = lab.compareIds.filter((x) => x !== id);
    await loadPresets();
    renderPresetSelect("default");
    syncCustomInputs();
    await runSimulation();
  } catch (e) {
    setStatus(`Could not delete pattern: ${e.message}`);
  }
}

async function saveAsDefault() {
  const p = selectedPattern();
  const ok = await showConfirm(`Make this the default TV naming pattern for all future archive/organize runs?\n\nFolder: ${p.tv_season_folder}\nFile: ${p.tv_file}`);
  if (!ok) return;
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify({ tv_season_folder_template: p.tv_season_folder, tv_file_template: p.tv_file }) });
    await loadPresets();
    renderPresetSelect($("#lab-preset-select").value);
    syncCustomInputs();
    showToast?.("Default TV pattern saved");
  } catch (e) {
    setStatus(`Could not save default: ${e.message}`);
  }
}

// --------------------------------------------------------------------- setup

export function setupRenameLab() {
  $("#lab-rescan-btn").addEventListener("click", () => runSimulation("", { rescan: true }));
  $("#lab-refetch-btn").addEventListener("click", () => {
    runSimulation("Cached TMDB data for this show was cleared and re-fetched.", { refresh: true });
  });
  $("#lab-back-btn").addEventListener("click", () => {
    $('.tab-btn[data-tab="tv"]').click();
  });
  $("#lab-preset-select").addEventListener("change", () => {
    syncCustomInputs();
    if (lab.compare && lab.compareIds.length === 0) lab.compareIds = [$("#lab-preset-select").value];
    runSimulation();
  });
  for (const id of ["#lab-season-folder", "#lab-file-template"]) {
    $(id).addEventListener("focus", (e) => { lab.lastInput = e.target; });
    $(id).addEventListener("input", scheduleSimulation);
  }
  $("#lab-compare-btn").addEventListener("click", () => setCompare(!lab.compare));

  $("#lab-search").addEventListener("input", (e) => {
    lab.filter.search = e.target.value;
    renderTable();
    updateSelectionCount();
  });
  $("#lab-season-filter").addEventListener("change", (e) => {
    lab.filter.season = e.target.value;
    renderTable();
    updateSelectionCount();
  });

  // Select all/none act on what's currently shown (respecting filters).
  $("#lab-select-all").addEventListener("click", () => {
    effectiveRows().filter((r) => passesFilter(r) && !r.unchanged && !r.ov.skip).forEach((r) => lab.selected.add(r.row.media_id));
    renderTable();
    updateSelectionCount();
  });
  $("#lab-select-none").addEventListener("click", () => {
    effectiveRows().filter(passesFilter).forEach((r) => lab.selected.delete(r.row.media_id));
    renderTable();
    updateSelectionCount();
  });
  $("#lab-resolve-btn").addEventListener("click", resolveAllConflicts);

  $("#lab-apply-btn").addEventListener("click", applySelected);
  $("#lab-cancel-btn").addEventListener("click", () => { lab.cancelled = true; });
  $("#lab-undo-btn").addEventListener("click", undoAll);
  $("#lab-undo-dismiss").addEventListener("click", () => $("#lab-undo-banner").classList.add("hidden"));

  $("#lab-save-as-btn").addEventListener("click", () => {
    $("#lab-save-form").classList.remove("hidden");
    $("#lab-save-as-btn").classList.add("hidden");
    $("#lab-save-name").focus();
  });
  $("#lab-save-cancel").addEventListener("click", () => {
    $("#lab-save-form").classList.add("hidden");
    $("#lab-save-as-btn").classList.remove("hidden");
  });
  $("#lab-save-confirm").addEventListener("click", () => savePattern(false));
  $("#lab-save-name").addEventListener("keydown", (e) => { if (e.key === "Enter") savePattern(false); });
  $("#lab-update-saved-btn").addEventListener("click", () => savePattern(true));
  $("#lab-delete-saved-btn").addEventListener("click", deleteSavedPattern);
  $("#lab-save-default-btn").addEventListener("click", saveAsDefault);
}
