/**
 * First-run setup wizard: a one-screen guided pass over the settings a fresh install needs (library folders + TMDB key), with inline validation, shown until a TMDB key exists or the person dismisses it.
 */

import { $, $all, api, formatBytes } from "./core.js";
import { loadSettings } from "./settings-tab.js";

const DISMISSED_KEY = "media-manager:setup-wizard-dismissed";

const PATH_FIELDS = [
  { id: "#wizard-incoming-movies", key: "incoming_movies" },
  { id: "#wizard-incoming-tv", key: "incoming_tv" },
  { id: "#wizard-archive-movies", key: "archive_movies" },
  { id: "#wizard-archive-tv", key: "archive_tv" },
];

function isDismissed() {
  try { return localStorage.getItem(DISMISSED_KEY) === "1"; } catch (e) { return false; }
}

function dismiss() {
  try { localStorage.setItem(DISMISSED_KEY, "1"); } catch (e) { /* storage disabled -- wizard just reappears next load */ }
  $("#setup-wizard-modal").classList.add("hidden");
}

export async function maybeShowSetupWizard() {
  if (isDismissed()) return;
  let s;
  try {
    s = await api("/api/settings");
  } catch (e) {
    return; // settings unreachable (e.g. token prompt pending) -- don't block the page on it
  }
  if (s.tmdb_api_key_set) return;
  PATH_FIELDS.forEach(({ id, key }) => { $(id).value = s[key] || ""; });
  $("#wizard-tmdb-key").value = "";
  $("#wizard-status").textContent = "";
  $("#wizard-permissions").innerHTML = "";
  $("#setup-wizard-modal").classList.remove("hidden");
}

async function validateKey() {
  const key = $("#wizard-tmdb-key").value.trim();
  const out = $("#wizard-key-status");
  if (!key) {
    out.textContent = "Leave blank to keep using the no-key scraper fallback.";
    return;
  }
  out.textContent = "Testing…";
  try {
    const data = await api("/api/settings/validate-tmdb-key", { method: "POST", body: JSON.stringify({ key }) });
    out.textContent = data.valid ? "✓ Key works." : "✗ Key rejected by TMDB.";
  } catch (e) {
    out.textContent = `Error: ${e.message}`;
  }
}

function wizardPayload() {
  const body = {};
  PATH_FIELDS.forEach(({ id, key }) => { body[key] = $(id).value.trim(); });
  const key = $("#wizard-tmdb-key").value.trim();
  if (key) body.tmdb_api_key = key;
  return body;
}

async function save() {
  const status = $("#wizard-status");
  status.textContent = "Saving…";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(wizardPayload()) });
    return true;
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
    return false;
  }
}

async function testPermissions() {
  const out = $("#wizard-permissions");
  if (!(await save())) return;
  $("#wizard-status").textContent = "";
  out.textContent = "Checking…";
  try {
    const data = await api("/api/settings/permissions-check");
    out.innerHTML = data.paths.map((p) => `
      <div class="${p.exists && p.writable ? "tracked-yes" : "tracked-no"}">
        ${p.exists && p.writable ? "✓" : "✗"} ${p.path}
        ${p.free_bytes != null ? `<span class="hint">· ${formatBytes(p.free_bytes)} free</span>` : ""}
        ${p.error ? `<span class="hint">— ${p.error}</span>` : ""}
        ${p.chown_hint ? `<div class="hint"><code>${p.chown_hint}</code></div>` : ""}
      </div>`).join("");
  } catch (e) {
    out.textContent = `Error: ${e.message}`;
  }
}

async function finish() {
  if (!(await save())) return;
  dismiss();
  loadSettings();
  $("#wizard-status").textContent = "";
  window.location.reload(); // pick up the new paths/key everywhere (galleries, scan, tracker)
}

export function setupSetupWizard() {
  $("#wizard-validate-key-btn").addEventListener("click", validateKey);
  $("#wizard-test-btn").addEventListener("click", testPermissions);
  $("#wizard-finish-btn").addEventListener("click", finish);
  $("#wizard-skip-btn").addEventListener("click", dismiss);
  $all("#setup-wizard-modal input").forEach((input) => {
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });
  });
  maybeShowSetupWizard();
}
