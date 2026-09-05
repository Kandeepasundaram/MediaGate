/**
 * Settings tab: the settings form, naming templates, media-server/backup settings, config history, viewers, API tokens, and library export/import.
 */

import { escapeAttr, showConfirm } from "./archive-tab.js";
import { loadStatus } from "./chrome.js";
import { $, api, formatBytes, setStoredApiToken, showToast, state } from "./core.js";
import { getActiveViewerId, loadMoviesGallery, loadTvGallery, setActiveViewerId } from "./gallery.js";

// ---- Settings tab ----
export async function loadSettings() {
  try {
    const s = await api("/api/settings");
    $("#setting-incoming-movies").value = s.incoming_movies;
    $("#setting-incoming-tv").value = s.incoming_tv;
    $("#setting-archive-movies").value = s.archive_movies;
    $("#setting-archive-tv").value = s.archive_tv;
    const keyInput = $("#setting-tmdb-key");
    keyInput.disabled = s.tmdb_api_key_locked_by_env;
    $("#tmdb-key-note").textContent = s.tmdb_api_key_locked_by_env
      ? "Locked by the TMDB_API_KEY environment variable; edit it there instead."
      : s.tmdb_api_key_set
        ? "A key is currently set. Leave blank to keep it."
        : "No key set — running in TMDB scraper fallback mode.";
    $("#setting-webhook-url").value = s.webhook_url || "";
    $("#setting-discord-webhook-url").value = s.discord_webhook_url || "";
    $("#telegram-token-note").textContent = s.telegram_bot_token_set ? "A token is currently set. Leave blank to keep it." : "";
    $("#setting-telegram-chat-id").value = s.telegram_chat_id || "";
    $("#pushover-token-note").textContent = s.pushover_api_token_set ? "A token is currently set. Leave blank to keep it." : "";
    $("#pushover-user-note").textContent = s.pushover_user_key_set ? "A key is currently set. Leave blank to keep it." : "";
    $("#setting-auto-track-new").checked = !!s.auto_track_new;
    $("#setting-digest-mode").checked = !!s.digest_mode;
    $("#setting-digest-interval-days").value = s.digest_interval_days || 1;
    $("#setting-watcher-enabled").checked = !!s.watcher_enabled;
    $("#omdb-key-note").textContent = s.omdb_api_key_set
      ? "A key is currently set. Leave blank to keep it. Powers IMDb/Rotten Tomatoes ratings in the detail pane."
      : "Powers IMDb/Rotten Tomatoes ratings in the detail pane. Free key at omdbapi.com/apikey.aspx.";
    $("#api-token-note").textContent = s.api_token_set
      ? "A token is currently set and required on every request. Leave blank to keep it."
      : "Disabled -- every request is currently allowed with no token.";
    $("#disable-api-token-btn").classList.toggle("hidden", !s.api_token_set);
    $("#setting-plex-url").value = s.plex_url || "";
    $("#plex-token-note").textContent = s.plex_token_set ? "A token is currently set. Leave blank to keep it." : "";
    $("#setting-jellyfin-url").value = s.jellyfin_url || "";
    $("#jellyfin-key-note").textContent = s.jellyfin_api_key_set ? "A key is currently set. Leave blank to keep it." : "";
    $("#setting-write-nfo-files").checked = s.write_nfo_files !== false;
    $("#setting-subtitle-languages").value = (s.subtitle_keep_languages || []).join(", ");
    $("#setting-subtitle-languages-movies").value = (s.subtitle_keep_languages_movies || []).join(", ");
    $("#setting-subtitle-languages-tv").value = (s.subtitle_keep_languages_tv || []).join(", ");
    $("#setting-movie-folder-template").value = s.movie_folder_template || "";
    $("#setting-tv-season-folder-template").value = s.tv_season_folder_template || "";
    $("#setting-tv-file-template").value = s.tv_file_template || "";
    $("#setting-collision-policy").value = s.collision_policy || "suffix";
    $("#setting-low-disk-alert-enabled").checked = !!s.low_disk_alert_enabled;
    $("#setting-low-disk-threshold-gb").value = s.low_disk_threshold_gb ?? 10;
    $("#setting-reports-enabled").checked = !!s.reports_enabled;
    $("#setting-reports-frequency").value = s.reports_frequency || "monthly";
    $("#setting-reports-cron-time").value = s.reports_cron_time || "08:00";
    $("#setting-backup-enabled").checked = s.backup_enabled !== false;
    $("#setting-backup-retention-days").value = s.backup_retention_days ?? 14;
    $("#setting-webdav-url").value = s.webdav_url || "";
    $("#setting-webdav-username").value = s.webdav_username || "";
    $("#webdav-password-note").textContent = s.webdav_password_set ? "A password is currently set. Leave blank to keep it." : "";
    $("#setting-webdav-remote-path").value = s.webdav_remote_path || "media-manager-backups";
    $("#opensubtitles-key-note").textContent = s.opensubtitles_api_key_set
      ? "A key is currently set. Leave blank to keep it. Required for auto-fetch below."
      : "Free key at opensubtitles.com/en/consumers. Required for auto-fetch below.";
    $("#setting-auto-fetch-subtitles").checked = !!s.auto_fetch_missing_subtitles;
    $("#setting-tvmaze-enabled").checked = !!s.tvmaze_enabled;
  } catch (e) {
    $("#settings-status").textContent = `Error loading settings: ${e.message}`;
  }
}

export async function previewDigest() {
  const output = $("#digest-preview-output");
  output.textContent = "Loading…";
  try {
    const data = await api("/api/tracker/digest-preview");
    output.textContent = data.count === 0
      ? "Nothing pending right now -- the digest would send nothing."
      : `Would send now: "${data.message}"`;
  } catch (e) {
    output.textContent = `Error: ${e.message}`;
  }
}

export async function testTmdbKey() {
  const status = $("#tmdb-key-test-status");
  const key = $("#setting-tmdb-key").value.trim();
  if (!key) {
    status.textContent = "Type a key above to test it.";
    return;
  }
  status.textContent = "Testing…";
  try {
    const data = await api("/api/settings/validate-tmdb-key", { method: "POST", body: JSON.stringify({ key }) });
    status.textContent = data.valid ? "✓ Key works." : "✗ Key rejected by TMDB.";
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
}

export async function saveSettings(e) {
  e.preventDefault();
  const payload = {
    incoming_movies: $("#setting-incoming-movies").value.trim(),
    incoming_tv: $("#setting-incoming-tv").value.trim(),
    archive_movies: $("#setting-archive-movies").value.trim(),
    archive_tv: $("#setting-archive-tv").value.trim(),
    webhook_url: $("#setting-webhook-url").value.trim(),
    discord_webhook_url: $("#setting-discord-webhook-url").value.trim(),
    telegram_chat_id: $("#setting-telegram-chat-id").value.trim(),
    auto_track_new: $("#setting-auto-track-new").checked,
    digest_mode: $("#setting-digest-mode").checked,
    digest_interval_days: Number($("#setting-digest-interval-days").value) || 1,
    watcher_enabled: $("#setting-watcher-enabled").checked,
    subtitle_keep_languages: $("#setting-subtitle-languages").value.split(",").map((s) => s.trim()).filter(Boolean),
    subtitle_keep_languages_movies: $("#setting-subtitle-languages-movies").value.split(",").map((s) => s.trim()).filter(Boolean),
    subtitle_keep_languages_tv: $("#setting-subtitle-languages-tv").value.split(",").map((s) => s.trim()).filter(Boolean),
    low_disk_alert_enabled: $("#setting-low-disk-alert-enabled").checked,
    low_disk_threshold_gb: Number($("#setting-low-disk-threshold-gb").value) || 10,
    reports_enabled: $("#setting-reports-enabled").checked,
    reports_frequency: $("#setting-reports-frequency").value,
    reports_cron_time: $("#setting-reports-cron-time").value.trim() || "08:00",
    auto_fetch_missing_subtitles: $("#setting-auto-fetch-subtitles").checked,
    tvmaze_enabled: $("#setting-tvmaze-enabled").checked,
  };
  const keyValue = $("#setting-tmdb-key").value;
  if (keyValue) payload.tmdb_api_key = keyValue;
  const omdbKeyValue = $("#setting-omdb-key").value;
  if (omdbKeyValue) payload.omdb_api_key = omdbKeyValue;
  const apiTokenValue = $("#setting-api-token").value;
  if (apiTokenValue) payload.api_token = apiTokenValue;
  const telegramTokenValue = $("#setting-telegram-bot-token").value;
  if (telegramTokenValue) payload.telegram_bot_token = telegramTokenValue;
  const pushoverTokenValue = $("#setting-pushover-api-token").value;
  if (pushoverTokenValue) payload.pushover_api_token = pushoverTokenValue;
  const pushoverUserValue = $("#setting-pushover-user-key").value;
  if (pushoverUserValue) payload.pushover_user_key = pushoverUserValue;
  const opensubtitlesKeyValue = $("#setting-opensubtitles-key").value;
  if (opensubtitlesKeyValue) payload.opensubtitles_api_key = opensubtitlesKeyValue;

  $("#settings-status").textContent = "Saving...";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    $("#setting-tmdb-key").value = "";
    $("#setting-omdb-key").value = "";
    $("#setting-api-token").value = "";
    $("#setting-telegram-bot-token").value = "";
    $("#setting-pushover-api-token").value = "";
    $("#setting-pushover-user-key").value = "";
    $("#setting-opensubtitles-key").value = "";
    // We just set this token server-side ourselves, so this browser needs it stored too, or the very next request 401s.
    if (apiTokenValue) setStoredApiToken(apiTokenValue);
    $("#settings-status").textContent = "Saved.";
    loadSettings();
    loadStatus();
    checkPermissions(); // catches a typo'd path immediately instead of waiting for a manual "Test Permissions" click
  } catch (e) {
    $("#settings-status").textContent = `Error: ${e.message}`;
  }
}

const DEFAULT_NAMING_TEMPLATES = {
  movie_folder_template: "{title}{year_suffix}",
  tv_season_folder_template: "Season {season:02d}",
  tv_file_template: "{show_name} - {code}{episode_title_suffix}",
};

export async function saveNamingTemplates(e) {
  e.preventDefault();
  const payload = {
    movie_folder_template: $("#setting-movie-folder-template").value.trim() || DEFAULT_NAMING_TEMPLATES.movie_folder_template,
    tv_season_folder_template: $("#setting-tv-season-folder-template").value.trim() || DEFAULT_NAMING_TEMPLATES.tv_season_folder_template,
    tv_file_template: $("#setting-tv-file-template").value.trim() || DEFAULT_NAMING_TEMPLATES.tv_file_template,
    collision_policy: $("#setting-collision-policy").value,
  };
  $("#naming-templates-status").textContent = "Saving...";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    $("#naming-templates-status").textContent = "Saved.";
    loadSettings();
  } catch (e) {
    $("#naming-templates-status").textContent = `Error: ${e.message}`;
  }
}

export async function saveMediaServerSettings(e) {
  e.preventDefault();
  const payload = {
    plex_url: $("#setting-plex-url").value.trim(),
    jellyfin_url: $("#setting-jellyfin-url").value.trim(),
    write_nfo_files: $("#setting-write-nfo-files").checked,
  };
  const plexToken = $("#setting-plex-token").value;
  if (plexToken) payload.plex_token = plexToken;
  const jellyfinKey = $("#setting-jellyfin-api-key").value;
  if (jellyfinKey) payload.jellyfin_api_key = jellyfinKey;

  $("#media-server-status").textContent = "Saving...";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    $("#setting-plex-token").value = "";
    $("#setting-jellyfin-api-key").value = "";
    $("#media-server-status").textContent = "Saved.";
    loadSettings();
  } catch (e) {
    $("#media-server-status").textContent = `Error: ${e.message}`;
  }
}

export async function saveWebdavBackupSettings(e) {
  e.preventDefault();
  const payload = {
    backup_enabled: $("#setting-backup-enabled").checked,
    backup_retention_days: Number($("#setting-backup-retention-days").value) || 14,
    webdav_url: $("#setting-webdav-url").value.trim(),
    webdav_username: $("#setting-webdav-username").value.trim(),
    webdav_remote_path: $("#setting-webdav-remote-path").value.trim() || "media-manager-backups",
  };
  const webdavPassword = $("#setting-webdav-password").value;
  if (webdavPassword) payload.webdav_password = webdavPassword;

  $("#webdav-backup-status").textContent = "Saving...";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    $("#setting-webdav-password").value = "";
    $("#webdav-backup-status").textContent = "Saved.";
    loadSettings();
  } catch (e) {
    $("#webdav-backup-status").textContent = `Error: ${e.message}`;
  }
}

export async function loadConfigHistory() {
  const el = $("#config-history-list");
  el.textContent = "Loading...";
  try {
    const data = await api("/api/settings/history");
    if (data.versions.length === 0) {
      el.innerHTML = "<p class=\"hint\">No saved versions yet -- one is kept on every Settings save.</p>";
      return;
    }
    el.innerHTML = data.versions.map((v) => `
      <div class="config-history-row" data-version="${v.version}">
        <div class="config-history-summary">
          <span>${new Date(v.timestamp).toLocaleString()}</span>
          <span class="hint">${formatBytes(v.size_bytes)}</span>
          <button class="config-history-diff-btn" data-version="${v.version}">View Diff</button>
          <button class="danger config-history-rollback-btn" data-version="${v.version}">Rollback to This</button>
        </div>
        <pre class="config-history-diff hidden"></pre>
      </div>
    `).join("");
    el.querySelectorAll(".config-history-diff-btn").forEach((btn) => {
      btn.addEventListener("click", () => toggleConfigHistoryDiff(btn.dataset.version));
    });
    el.querySelectorAll(".config-history-rollback-btn").forEach((btn) => {
      btn.addEventListener("click", () => rollbackConfigVersion(btn.dataset.version));
    });
  } catch (e) {
    el.innerHTML = `<p>Error loading config history: ${e.message}</p>`;
  }
}

async function toggleConfigHistoryDiff(version) {
  const row = $(`.config-history-row[data-version="${version}"]`);
  const pre = row.querySelector(".config-history-diff");
  if (!pre.classList.contains("hidden")) {
    pre.classList.add("hidden");
    return;
  }
  pre.classList.remove("hidden");
  pre.textContent = "Loading diff...";
  try {
    const data = await api(`/api/settings/history/${encodeURIComponent(version)}/diff`);
    pre.textContent = data.diff.length ? data.diff.join("") : "No differences from the current config.";
  } catch (e) {
    pre.textContent = `Error loading diff: ${e.message}`;
  }
}

async function rollbackConfigVersion(version) {
  const ok = await showConfirm(`Roll back config.yaml to the version from this save? The current config is saved to history first, so this can be undone.`);
  if (!ok) return;
  try {
    await api(`/api/settings/history/${encodeURIComponent(version)}/rollback`, { method: "POST" });
    loadSettings();
    loadConfigHistory();
  } catch (e) {
    $("#config-history-list").insertAdjacentHTML("afterbegin", `<p>Rollback failed: ${e.message}</p>`);
  }
}

export async function loadViewers() {
  try {
    const data = await api("/api/library/viewers");
    state.viewers = data.viewers;

    const select = $("#viewer-select");
    const previousValue = select.value || (getActiveViewerId() != null ? String(getActiveViewerId()) : "");
    select.innerHTML = `<option value="">All viewers</option>` +
      data.viewers.map((v) => `<option value="${v.id}">${escapeAttr(v.name)}</option>`).join("");
    if (previousValue && data.viewers.some((v) => String(v.id) === previousValue)) {
      select.value = previousValue;
    } else if (previousValue) {
      setActiveViewerId(null); // previously-selected viewer no longer exists (deleted elsewhere)
    }

    const list = $("#viewers-list");
    if (list) {
      list.innerHTML = data.viewers.length
        ? data.viewers.map((v) => `
            <div class="tracked-item">
              <div><strong>${escapeAttr(v.name)}</strong> <span class="hint">since ${new Date(v.created_at).toLocaleDateString()}</span></div>
              <button class="danger delete-viewer-btn" data-id="${v.id}">Delete</button>
            </div>
          `).join("")
        : `<p class="hint">No viewers yet -- add one to enable per-viewer watch state.</p>`;
      list.querySelectorAll(".delete-viewer-btn").forEach((btn) => {
        btn.addEventListener("click", () => deleteViewerAction(Number(btn.dataset.id)));
      });
    }
  } catch (e) {
    // Best-effort -- an unpopulated viewer selector just leaves the app in its original single-flag behavior.
  }
}

export async function createViewerAction() {
  const input = $("#new-viewer-name");
  const name = input.value.trim();
  if (!name) return;
  try {
    await api("/api/library/viewers", { method: "POST", body: JSON.stringify({ name }) });
    input.value = "";
    showToast(`Added viewer "${name}".`, "success");
    loadViewers();
  } catch (e) {
    $("#viewers-list").insertAdjacentHTML("afterbegin", `<p>Error: ${e.message}</p>`);
    showToast(`Could not add viewer: ${e.message}`, "error");
  }
}

async function deleteViewerAction(id) {
  const ok = await showConfirm("Delete this viewer? Their per-item watched history is deleted too.");
  if (!ok) return;
  try {
    await api(`/api/library/viewers/${id}`, { method: "DELETE" });
    if (getActiveViewerId() === id) {
      setActiveViewerId(null);
      loadMoviesGallery();
      loadTvGallery();
    }
    loadViewers();
  } catch (e) {
    $("#viewers-list").insertAdjacentHTML("afterbegin", `<p>Error: ${e.message}</p>`);
  }
}

// ---- Manage Tags (library-wide rename/delete, not per-item) ----
export async function loadManageTagsSelect() {
  const select = $("#manage-tags-select");
  if (!select) return;
  const previous = select.value;
  try {
    const data = await api("/api/library/tags");
    select.innerHTML = `<option value="">Select a tag…</option>` + data.tags.map((t) => `<option value="${escapeAttr(t)}">${escapeAttr(t)}</option>`).join("");
    if (data.tags.includes(previous)) select.value = previous;
  } catch (e) { /* best-effort -- select just stays at whatever it had */ }
}

export async function renameTagAction() {
  const oldTag = $("#manage-tags-select").value;
  const newTag = $("#manage-tags-rename-input").value.trim();
  const status = $("#manage-tags-status");
  if (!oldTag || !newTag) return;
  status.textContent = "Renaming…";
  try {
    const data = await api("/api/library/tags/rename", { method: "POST", body: JSON.stringify({ old: oldTag, new: newTag }) });
    status.textContent = `Renamed on ${data.updated} item(s).`;
    showToast(`Renamed tag "${oldTag}" to "${newTag}" on ${data.updated} item(s).`, "success");
    $("#manage-tags-rename-input").value = "";
    loadManageTagsSelect();
    loadMoviesGallery();
    loadTvGallery();
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
}

export async function deleteTagAction() {
  const tag = $("#manage-tags-select").value;
  if (!tag) return;
  const ok = await showConfirm(`Delete tag "${tag}" from every item that has it? This cannot be undone.`);
  if (!ok) return;
  const status = $("#manage-tags-status");
  status.textContent = "Deleting…";
  try {
    const data = await api("/api/library/tags/delete", { method: "POST", body: JSON.stringify({ tag }) });
    status.textContent = `Removed from ${data.updated} item(s).`;
    showToast(`Deleted tag "${tag}" from ${data.updated} item(s).`, "success");
    loadManageTagsSelect();
    loadMoviesGallery();
    loadTvGallery();
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
}

export async function loadApiTokensList() {
  const el = $("#api-tokens-list");
  el.textContent = "Loading...";
  try {
    const data = await api("/api/settings/tokens");
    if (data.tokens.length === 0) {
      el.innerHTML = `<p class="hint">No named tokens yet.</p>`;
      return;
    }
    el.innerHTML = data.tokens.map((t) => `
      <div class="tracked-item">
        <div>
          <strong>${t.name}</strong>
          <span class="badge">${t.scope === "read_only" ? "read-only" : "read-write"}</span>
          <span class="hint">created ${new Date(t.created_at).toLocaleString()}${
            t.last_used_at ? ` · last used ${new Date(t.last_used_at).toLocaleString()}` : " · never used"
          }</span>
        </div>
        <button class="danger revoke-api-token-btn" data-id="${t.id}">Revoke</button>
      </div>
    `).join("");
    el.querySelectorAll(".revoke-api-token-btn").forEach((btn) => {
      btn.addEventListener("click", () => revokeApiToken(Number(btn.dataset.id)));
    });
  } catch (e) {
    el.innerHTML = `<p class="hint">Error: ${e.message}</p>`;
  }
}

export async function createApiToken() {
  const nameInput = $("#new-api-token-name");
  const name = nameInput.value.trim();
  if (!name) return;
  const scope = $("#new-api-token-scope").value;
  const reveal = $("#new-api-token-reveal");
  try {
    const data = await api("/api/settings/tokens", { method: "POST", body: JSON.stringify({ name, scope }) });
    nameInput.value = "";
    reveal.classList.remove("hidden");
    reveal.innerHTML = `Token for "${data.name}" (${data.scope === "read_only" ? "read-only" : "read-write"}, copy it now — it won't be shown again): <code>${data.token}</code>`;
    // This browser needs the new token too, or its own next request 401s
    // the moment this becomes the first token ever created.
    setStoredApiToken(data.token);
    loadApiTokensList();
  } catch (e) {
    reveal.classList.remove("hidden");
    reveal.textContent = `Error: ${e.message}`;
  }
}

async function revokeApiToken(id) {
  const ok = await showConfirm("Revoke this token? Any client still using it will start getting 401 errors.");
  if (!ok) return;
  try {
    await api(`/api/settings/tokens/${id}`, { method: "DELETE" });
    loadApiTokensList();
  } catch (e) {
    $("#api-tokens-list").insertAdjacentHTML("afterbegin", `<p class="hint">Error: ${e.message}</p>`);
  }
}

export async function disableApiToken() {
  const ok = await showConfirm("Disable the API token? Every /api/* request will then be allowed with no token.");
  if (!ok) return;
  $("#settings-status").textContent = "Saving...";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify({ api_token: "" }) });
    setStoredApiToken("");
    $("#settings-status").textContent = "API token disabled.";
    loadSettings();
  } catch (e) {
    $("#settings-status").textContent = `Error: ${e.message}`;
  }
}

export async function exportLibrary() {
  const status = $("#backup-status");
  status.textContent = "Exporting...";
  try {
    const data = await api("/api/library/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `media-manager-library-${data.exported_at.slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    status.textContent = `Exported ${data.items.length} item(s).`;
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
}

export async function importLibrary(file) {
  const status = $("#backup-status");
  if (!file) return;
  status.textContent = "Importing...";
  try {
    const text = await file.text();
    const parsed = JSON.parse(text);
    const data = await api("/api/library/import", { method: "POST", body: JSON.stringify({ items: parsed.items || [] }) });
    status.textContent = `Imported ${data.imported} item(s), skipped ${data.skipped} already-tracked item(s).`;
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
}

// Minimal RFC4180-ish CSV line splitter -- handles quoted fields (commas
// and escaped "" inside them), which a plain String.split(",") can't;
// good enough for a diary export, not a general CSV library.
function parseCsv(text) {
  const rows = [];
  for (const line of text.split(/\r\n|\n/)) {
    if (!line) continue;
    const fields = [];
    let cur = "";
    let inQuotes = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (inQuotes) {
        if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
        else if (c === '"') { inQuotes = false; }
        else { cur += c; }
      } else if (c === '"') {
        inQuotes = true;
      } else if (c === ",") {
        fields.push(cur);
        cur = "";
      } else {
        cur += c;
      }
    }
    fields.push(cur);
    rows.push(fields);
  }
  return rows;
}

// Column names tolerated per logical field -- Letterboxd's diary.csv shape
// isn't pinned down precisely here, so this matches by name rather than
// fixed position/exact spelling.
const WATCH_HISTORY_COLUMN_ALIASES = {
  title: ["name", "title"],
  year: ["year"],
  watchedDate: ["watched date", "date"],
  rating: ["rating"],
};

function findColumnIndex(header, aliases) {
  const lower = header.map((h) => h.trim().toLowerCase());
  for (const alias of aliases) {
    const idx = lower.indexOf(alias);
    if (idx !== -1) return idx;
  }
  return -1;
}

export async function importWatchHistory(file) {
  const status = $("#watch-history-import-status");
  if (!file) return;
  status.textContent = "Parsing…";
  try {
    const text = await file.text();
    const [header, ...dataRows] = parseCsv(text);
    if (!header) throw new Error("Empty file");
    const col = {
      title: findColumnIndex(header, WATCH_HISTORY_COLUMN_ALIASES.title),
      year: findColumnIndex(header, WATCH_HISTORY_COLUMN_ALIASES.year),
      watchedDate: findColumnIndex(header, WATCH_HISTORY_COLUMN_ALIASES.watchedDate),
      rating: findColumnIndex(header, WATCH_HISTORY_COLUMN_ALIASES.rating),
    };
    if (col.title === -1) throw new Error(`Couldn't find a title column (looked for: ${WATCH_HISTORY_COLUMN_ALIASES.title.join(", ")})`);

    const rows = dataRows
      .filter((r) => r.length > 1 && r[col.title]?.trim())
      .map((r) => ({
        title: r[col.title].trim(),
        year: col.year !== -1 && r[col.year] ? Number(r[col.year]) : null,
        watched_date: col.watchedDate !== -1 && r[col.watchedDate] ? new Date(r[col.watchedDate]).toISOString() : null,
        rating: col.rating !== -1 && r[col.rating] ? Number(r[col.rating]) : null,
      }));
    if (rows.length === 0) throw new Error("No data rows found");

    status.textContent = `Importing ${rows.length} row(s)…`;
    const data = await api("/api/library/import-watch-history", { method: "POST", body: JSON.stringify({ rows }) });
    status.textContent = `Marked ${data.updated} movie(s) watched.` +
      (data.unmatched.length ? ` ${data.unmatched.length} not found in your library: ${data.unmatched.slice(0, 10).join(", ")}${data.unmatched.length > 10 ? "…" : ""}` : "");
    if (data.updated > 0) loadMoviesGallery();
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
}

export async function syncWatchedFromMediaServers() {
  const btn = $("#sync-watched-btn");
  const status = $("#sync-watched-status");
  btn.disabled = true;
  status.textContent = "Syncing…";
  try {
    const data = await api("/api/library/sync-watched", { method: "POST" });
    status.textContent = data.updated > 0
      ? `Marked ${data.updated} movie(s) watched.`
      : "No changes — nothing new to mark watched, or no server configured.";
    if (data.updated > 0) loadMoviesGallery(); // refetch -- state.movieItems is stale after a server-side watched change
  } catch (e) {
    status.textContent = `Sync failed: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

export async function checkPermissions() {
  const result = $("#permissions-result");
  result.textContent = "Checking...";
  try {
    const data = await api("/api/settings/permissions-check");
    const uidLine = data.running_uid !== null
      ? `<p class="hint">Container running as uid=${data.running_uid} gid=${data.running_gid}</p>`
      : "";
    result.innerHTML = uidLine + data.paths.map((p) => `
      <div class="path-check-row">
        <span title="${p.path}">${p.path}</span>
        <span class="${p.writable ? 'status-success' : 'status-failed'}">
          ${p.writable ? "writable" : (p.error || "not writable")}
        </span>
        <span class="${p.low_space ? 'status-warning' : 'hint'}">
          ${p.free_bytes != null ? `${formatBytes(p.free_bytes)} free${p.low_space ? " ⚠ low space" : ""}` : ""}
        </span>
      </div>
      ${p.chown_hint ? `<div class="hint">Fix: <code class="chown-hint" title="Click to select">${p.chown_hint}</code></div>` : ""}
    `).join("");
    result.querySelectorAll(".chown-hint").forEach((el) => {
      el.addEventListener("click", () => {
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      });
    });
  } catch (e) {
    result.textContent = `Error: ${e.message}`;
  }
}
