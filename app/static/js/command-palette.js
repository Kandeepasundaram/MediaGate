/**
 * Ctrl+K command palette: the command list, fuzzy filtering, and running whichever command was selected.
 */

import { scanAndPreview } from "./archive-tab.js";
import { loadBrowse } from "./browse-tab.js";
import { $, state } from "./core.js";
import { switchToTab } from "../app.js";
import { checkPermissions } from "./settings-tab.js";

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
  { category: "View", label: "Toggle Light / Dark Theme", run: () => $("#theme-toggle-btn").click() },
];
const PALETTE_CATEGORY_ORDER = ["Navigate", "Library", "View"];

state.paletteVisible = [];
state.paletteIndex = 0;

export function filterCommands(query) {
  const q = query.trim().toLowerCase();
  const matches = q ? COMMANDS.filter((c) => c.label.toLowerCase().includes(q)) : COMMANDS;
  return matches.slice().sort((a, b) => PALETTE_CATEGORY_ORDER.indexOf(a.category) - PALETTE_CATEGORY_ORDER.indexOf(b.category));
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
