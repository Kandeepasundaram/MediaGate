/**
 * Ctrl+K command palette: the command list, fuzzy filtering, and running whichever command was selected.
 */

import { scanAndPreview } from "./archive-tab.js";
import { loadBrowse } from "./browse-tab.js";
import { $, state } from "./core.js";
import { openDetailPane } from "./detail-pane.js";
import { groupEpisodesByShow } from "./gallery.js";
import { switchToTab } from "../app.js";
import { checkPermissions } from "./settings-tab.js";
import { setTheme } from "./chrome.js";

// ---- Command palette ----
const COMMANDS = [
  { category: "Navigate", label: "Go to Movies", run: () => switchToTab("movies") },
  { category: "Navigate", label: "Go to TV", run: () => switchToTab("tv") },
  { category: "Navigate", label: "Go to Browse & Clean Up", run: () => switchToTab("browse") },
  { category: "Navigate", label: "Go to Ready to Archive", run: () => switchToTab("archive") },
  { category: "Navigate", label: "Go to Notifications", run: () => switchToTab("notifications") },
  { category: "Navigate", label: "Go to History", run: () => switchToTab("history") },
  { category: "Navigate", label: "Go to Settings", run: () => switchToTab("settings") },
  { category: "Library", label: "Scan Library", run: () => { switchToTab("archive"); scanAndPreview(); } },
  { category: "Library", label: "Refresh Browse & Clean Up", run: () => { switchToTab("browse"); loadBrowse(); } },
  { category: "Library", label: "Check Storage Permissions", run: () => { switchToTab("settings"); checkPermissions(); } },
  { category: "View", label: "Switch to Dark Theme", run: () => setTheme("dark") },
  { category: "View", label: "Switch to Light Theme", run: () => setTheme("light") },
  { category: "View", label: "Switch to Neumorphism Theme", run: () => setTheme("neumorphism") },
  { category: "View", label: "Switch to Claymorphism Theme", run: () => setTheme("claymorphism") },
  { category: "View", label: "Switch to Glassmorphism Theme", run: () => setTheme("glassmorphism") },
  { category: "View", label: "Show Keyboard Shortcuts", run: () => $("#shortcuts-modal").classList.remove("hidden") },
];
const PALETTE_CATEGORY_ORDER = ["Navigate", "Library", "Titles", "View"];
const TITLE_MATCH_LIMIT = 6;

state.paletteVisible = [];
state.paletteIndex = 0;

// Fuzzy jump-to-title: only built when there's a query (an empty palette
// listing every archived title would swamp the fixed command list) --
// TV shows are grouped the same way the gallery groups them, so one
// matching episode surfaces its show once, not once per episode.
function matchingTitleCommands(q) {
  const movieMatches = state.movieItems
    .filter((item) => item.title.toLowerCase().includes(q))
    .map((item) => ({
      category: "Titles", label: `🎬 ${item.title}${item.year ? ` (${item.year})` : ""}`,
      run: () => { switchToTab("movies"); openDetailPane("movie", item); },
    }));
  const tvMatches = groupEpisodesByShow(state.tvItems)
    .filter((show) => show.title.toLowerCase().includes(q))
    .map((show) => ({
      category: "Titles", label: `📺 ${show.title}`,
      run: () => { switchToTab("tv"); openDetailPane("tv", show); },
    }));
  return [...movieMatches, ...tvMatches].slice(0, TITLE_MATCH_LIMIT);
}

export function filterCommands(query) {
  const q = query.trim().toLowerCase();
  const matches = q ? COMMANDS.filter((c) => c.label.toLowerCase().includes(q)) : COMMANDS.slice();
  const titleMatches = q ? matchingTitleCommands(q) : [];
  return [...matches, ...titleMatches].sort(
    (a, b) => PALETTE_CATEGORY_ORDER.indexOf(a.category) - PALETTE_CATEGORY_ORDER.indexOf(b.category)
  );
}

export function renderPalette() {
  const results = $("#palette-results");
  if (state.paletteVisible.length === 0) {
    results.innerHTML = `<p class="palette-empty">No matching commands.</p>`;
    return;
  }
  let html = "";
  let lastCategory = null;
  state.paletteVisible.forEach((cmd, i) => {
    if (cmd.category !== lastCategory) {
      html += `<div class="palette-category">${cmd.category}</div>`;
      lastCategory = cmd.category;
    }
    html += `<div class="palette-item${i === state.paletteIndex ? " active" : ""}" data-index="${i}">${cmd.label}</div>`;
  });
  results.innerHTML = html;
  results.querySelectorAll(".palette-item").forEach((el) => {
    el.addEventListener("click", () => runPaletteCommand(Number(el.dataset.index)));
    el.addEventListener("mouseenter", () => {
      state.paletteIndex = Number(el.dataset.index);
      results.querySelectorAll(".palette-item").forEach((e2) => e2.classList.remove("active"));
      el.classList.add("active");
    });
  });
}

export function runPaletteCommand(index) {
  const cmd = state.paletteVisible[index];
  if (!cmd) return;
  closeCommandPalette();
  cmd.run();
}

export function openCommandPalette() {
  $("#command-palette").classList.remove("hidden");
  const input = $("#palette-input");
  input.value = "";
  state.paletteVisible = filterCommands("");
  state.paletteIndex = 0;
  renderPalette();
  input.focus();
}

export function closeCommandPalette() {
  $("#command-palette").classList.add("hidden");
}
