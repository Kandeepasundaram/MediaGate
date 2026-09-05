/**
 * Notifications tab, the Tracker tab's universes (franchise groupings), and browser-notification permission/polling -- grouped together since they reference each other heavily.
 */

import { closeMatchPicker, escapeAttr, openMatchModal, showConfirm } from "./archive-tab.js";
import { $, $all, api, state } from "./core.js";
import { openDetailPane, renderDetailPane } from "./detail-pane.js";
import { posterMarkup } from "./gallery.js";
import { switchToTab } from "../app.js";

// ---- Notifications tab ----
export async function loadNotifications() {
  const container = $("#notifications-list");
  container.innerHTML = "Loading...";
  try {
    const data = await api("/api/tracker/notifications");
    if (data.notifications.length === 0) {
      container.innerHTML = "<p>No pending notifications.</p>";
      return;
    }
    container.innerHTML = data.notifications.map((n) => `
      <div class="notification-item">
        <div>
          <strong>${n.title}</strong>
          <div>${n.media_type === "tv"
            ? `Season ${n.latest_known_season} now available`
            : (n.movie_release_status || "New release detected")}</div>
          ${n.next_episode_air_date ? `<div class="hint">📅 Next episode: ${n.next_episode_air_date}</div>` : ""}
        </div>
        <button data-id="${n.id}" class="ack-btn">Mark Downloaded</button>
        <button data-id="${n.id}" class="snooze-btn">Remind Me in 7 Days</button>
      </div>
    `).join("");
    $all(".ack-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await api("/api/tracker/acknowledge", {
          method: "POST",
          body: JSON.stringify({ tracker_id: Number(btn.dataset.id) }),
        });
        loadNotifications();
      });
    });
    $all(".snooze-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await api(`/api/tracker/${btn.dataset.id}/snooze`, {
          method: "POST",
          body: JSON.stringify({ days: 7 }),
        });
        loadNotifications();
      });
    });
  } catch (e) {
    container.innerHTML = `<p>Error loading notifications: ${e.message}</p>`;
  }
}

// Renders into every matching container passed (both the Notifications
// tab's own widget and the Tracker tab's copy render the same data -- the
// endpoint is cheap and re-fetching per container keeps this simple).
export async function loadUpcomingReleases(selectors = ["#upcoming-releases-list"]) {
  const containers = selectors.map((sel) => $(sel)).filter(Boolean);
  if (containers.length === 0) return;
  containers.forEach((c) => { c.innerHTML = "Loading..."; });
  try {
    const data = await api("/api/tracker/upcoming");
    const html = data.items.length === 0
      ? "<p>Nothing due in the next 90 days.</p>"
      : data.items.map((i) => `
        <div class="notification-item">
          <div>
            <strong>${i.title}</strong>
            <div>${i.label} — ${i.release_date}</div>
          </div>
        </div>
      `).join("");
    containers.forEach((c) => { c.innerHTML = html; });
  } catch (e) {
    containers.forEach((c) => { c.innerHTML = `<p>Error loading upcoming releases: ${e.message}</p>`; });
  }
}

// Shared mute-toggle/check-now wiring for a tracker-item row, scoped to a
// single container so re-rendering one section (e.g. the standalone list
// after a mute toggle) never double-binds listeners still attached to
// untouched nodes in a sibling section (e.g. universe member rows) that
// happens to reuse the same .mute-toggle/.check-now-btn classes.
// Keeps an open "tracker" detail pane in sync when its item is changed from
// a gallery card instead of the pane itself (they're separate DOM/state, so
// a card-driven mute/check-now wouldn't otherwise be reflected until the
// pane is closed and reopened).
function syncDetailPaneTracker(trackerId, patch) {
  const pane = state.detailPane;
  if (!pane || pane.kind !== "tracker" || pane.data.id !== trackerId) return;
  Object.assign(pane.data, patch);
  renderDetailPane();
}

function wireTrackerRowControls(container) {
  container.querySelectorAll(".mute-toggle").forEach((cb) => {
    cb.addEventListener("click", (e) => e.stopPropagation());
    cb.addEventListener("change", async () => {
      const id = Number(cb.dataset.id);
      await api(`/api/tracker/${id}/mute`, { method: "POST", body: JSON.stringify({ muted: cb.checked }) });
      syncDetailPaneTracker(id, { muted: cb.checked });
      loadNotifications();
      loadTrackerTab();
    });
  });
  container.querySelectorAll(".check-now-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      btn.textContent = "Checking...";
      try {
        const res = await api(`/api/tracker/${btn.dataset.id}/check-now`, { method: "POST" });
        syncDetailPaneTracker(Number(btn.dataset.id), res.tracker);
        loadNotifications();
        loadTrackerTab();
      } catch (err) {
        btn.disabled = false;
        btn.textContent = "Check Now";
      }
    });
  });
}

export async function loadTrackedList() {
  const container = $("#tracked-list");
  container.innerHTML = "Loading...";
  try {
    const data = await api("/api/tracker/list");
    const filtered = data.tracked.filter(
      (t) => t.media_type === state.activeUniverseType
        && t.category === state.activeTrackerCategory
        && !state.universeMemberIds.has(t.tmdb_id)
    );
    if (filtered.length === 0) {
      container.innerHTML = `<p>No titles under "${trackerCategoryLabel(state.activeTrackerCategory)}" yet.</p>`;
      return;
    }
    container.innerHTML = filtered.map((t) => `
      <div class="gallery-card">
        <div class="gallery-badges">
          ${t.pending_notification ? `<span class="badge badge-new" title="${t.media_type === "tv" ? `Season ${t.latest_known_season} available` : (t.movie_release_status || "New release detected")}">⚡</span>` : ""}
          ${t.muted ? `<span class="badge" title="Muted">🔇</span>` : ""}
          ${t.category === "watched" ? `<span class="badge" title="Deleted from the archive">🗑</span>` : ""}
        </div>
        ${posterMarkup(t.title, t.poster_path)}
        <div class="gallery-info">
          <div class="gallery-title" title="${escapeAttr(t.title)}">${escapeAttr(t.title)}</div>
          <div class="gallery-meta">
            <span class="hint">${t.last_checked ? `last checked ${new Date(t.last_checked).toLocaleString()}` : "not checked yet"}</span>
            ${t.media_type === "tv" && t.watched_through_season != null ? `<span class="hint">watched through S${String(t.watched_through_season).padStart(2, "0")}${t.watched_through_episode != null ? `E${String(t.watched_through_episode).padStart(2, "0")}` : ""}</span>` : ""}
          </div>
          <div class="tracked-item-actions">
            <label class="watched-toggle">
              <input type="checkbox" class="mute-toggle" data-id="${t.id}" ${t.muted ? "checked" : ""}>
              Muted
            </label>
            <button class="check-now-btn" data-id="${t.id}">Check Now</button>
          </div>
        </div>
      </div>
    `).join("");
    wireTrackerRowControls(container);
    container.querySelectorAll(".gallery-card").forEach((card, i) => {
      card.addEventListener("click", () => openDetailPane("tracker", filtered[i]));
    });
  } catch (e) {
    container.innerHTML = `<p>Error loading tracked titles: ${e.message}</p>`;
  }
}

// ---- Tracker tab: universes (franchise/shared-universe groupings) ----
export async function loadTrackerTab() {
  loadUpcomingReleases(["#tracker-upcoming-list"]);
  await loadUniverses();
  await loadTrackedList();
}

export function setupUniverseTypeTabs() {
  $all("#universe-type-tabs .season-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.classList.contains("active")) return;
      state.activeUniverseType = btn.dataset.universeType;
      $all("#universe-type-tabs .season-tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      loadTrackerTab();
    });
  });
}

export const TRACKER_CATEGORIES = [
  { value: "watching", label: "Watching" },
  { value: "interested", label: "Interested" },
  { value: "watched", label: "Watched / History" },
];

export function trackerCategoryLabel(category) {
  return TRACKER_CATEGORIES.find((c) => c.value === category)?.label || category;
}

export function setupTrackerCategoryTabs() {
  $all("#tracker-category-tabs .season-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.classList.contains("active")) return;
      state.activeTrackerCategory = btn.dataset.trackerCategory;
      $all("#tracker-category-tabs .season-tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      loadTrackedList();
    });
  });
}

async function loadUniverses() {
  const mediaType = state.activeUniverseType;
  const container = $("#universes-list");
  container.innerHTML = "Loading...";
  try {
    const list = await api(`/api/universes?media_type=${mediaType}`);
    if (list.universes.length === 0) {
      container.innerHTML = `<p class="hint">No universes yet — group related titles with "+ New Universe" above.</p>`;
      state.universeMemberIds = new Set();
      return;
    }
    const details = await Promise.all(list.universes.map((u) => api(`/api/universes/${u.id}`)));
    const allMemberIds = new Set();
    details.forEach((d) => d.members.forEach((m) => allMemberIds.add(m.tmdb_id)));
    state.universeMemberIds = allMemberIds;

    container.innerHTML = details.map(({ universe: u, members }) => `
      <div class="universe-card">
        <div class="universe-card-header">
          <strong>${escapeAttr(u.name)}</strong>
          <span class="hint">${u.member_count} title(s)${u.pending_count ? ` · ${u.pending_count} pending` : ""}</span>
          <button class="delete-universe-btn" data-id="${u.id}">Delete</button>
        </div>
        <div class="universe-members">
          ${members.length === 0 ? `<p class="hint">No titles yet.</p>` : members.map((m) => `
            <div class="tracked-item">
              <div class="tracked-item-thumb">${posterMarkup(m.title, m.poster_path)}</div>
              <div class="tracked-item-text">
                <strong>${escapeAttr(m.title)}</strong>
                ${m.pending_notification ? `<span class="hint">⚡ ${mediaType === "tv" ? `Season ${m.latest_known_season} available` : (m.movie_release_status || "New release detected")}</span>` : ""}
              </div>
              <div class="tracked-item-actions">
                ${m.tracker_id != null ? `
                  <label class="watched-toggle">
                    <input type="checkbox" class="mute-toggle" data-id="${m.tracker_id}" ${m.muted ? "checked" : ""}>
                    Muted
                  </label>
                  <button class="check-now-btn" data-id="${m.tracker_id}">Check Now</button>
                ` : ""}
                <button class="remove-universe-member-btn" data-universe-id="${u.id}" data-member-id="${m.id}">Remove</button>
              </div>
            </div>
          `).join("")}
        </div>
        <div class="toolbar">
          <button class="add-universe-member-btn" data-id="${u.id}">+ Add Title</button>
        </div>
        <details class="recommendations-row universe-suggestions-row" data-universe-id="${u.id}">
          <summary>Possibly Related</summary>
          <p class="hint">Best-effort TMDB suggestions — not guaranteed accurate, especially for TV.</p>
          <div class="continue-watching-cards"></div>
        </details>
      </div>
    `).join("");

    wireTrackerRowControls(container);
    container.querySelectorAll(".delete-universe-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const ok = await showConfirm("Delete this universe? Its titles keep tracking standalone.");
        if (!ok) return;
        await api(`/api/universes/${btn.dataset.id}`, { method: "DELETE" });
        loadTrackerTab();
      });
    });
    container.querySelectorAll(".remove-universe-member-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await api(`/api/universes/${btn.dataset.universeId}/members/${btn.dataset.memberId}`, { method: "DELETE" });
        loadTrackerTab();
      });
    });
    container.querySelectorAll(".add-universe-member-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const universeId = Number(btn.dataset.id);
        openMatchModal(mediaType, "", (candidate) => addUniverseMember(universeId, candidate), { hideIdEntry: true });
      });
    });
    container.querySelectorAll(".universe-suggestions-row").forEach((row) => {
      row.addEventListener("toggle", () => {
        if (row.open) loadUniverseSuggestions(Number(row.dataset.universeId), mediaType, row.querySelector(".continue-watching-cards"));
      });
    });
  } catch (e) {
    container.innerHTML = `<p>Error loading universes: ${e.message}</p>`;
  }
}

export async function createUniverseAction() {
  const input = $("#new-universe-name");
  const name = input.value.trim();
  if (!name) return;
  $("#universe-status").textContent = "Creating…";
  try {
    await api("/api/universes", {
      method: "POST",
      body: JSON.stringify({ name, media_type: state.activeUniverseType }),
    });
    input.value = "";
    $("#universe-status").textContent = "";
    loadTrackerTab();
  } catch (e) {
    $("#universe-status").textContent = `Error: ${e.message}`;
  }
}

async function addUniverseMember(universeId, candidate) {
  closeMatchPicker();
  $("#universe-status").textContent = `Adding "${candidate.title}"...`;
  try {
    await api(`/api/universes/${universeId}/members`, {
      method: "POST",
      body: JSON.stringify({ tmdb_id: candidate.tmdb_id, title: candidate.title, poster_path: candidate.poster_path || null }),
    });
    $("#universe-status").textContent = "";
    loadTrackerTab();
  } catch (e) {
    $("#universe-status").textContent = `Error: ${e.message}`;
  }
}

async function loadUniverseSuggestions(universeId, mediaType, container) {
  if (!container || container.dataset.loading === "1") return;
  container.dataset.loading = "1";
  container.innerHTML = `<p class="hint">Loading…</p>`;
  try {
    const data = await api(`/api/universes/${universeId}/suggestions`);
    if (!data.tmdb_configured) {
      container.innerHTML = `<p class="hint">Suggestions need a TMDB key -- configure one in Settings.</p>`;
      return;
    }
    if (data.items.length === 0) {
      container.innerHTML = `<p class="hint">No suggestions right now.</p>`;
      return;
    }
    container.innerHTML = data.items.map((item) => `
      <div class="continue-watching-card">
        ${posterMarkup(item.title, item.poster_path)}
        <div class="continue-watching-info">
          <div class="gallery-title" title="${escapeAttr(item.title)}">${escapeAttr(item.title)}</div>
          <div class="hint">${item.year || ""}</div>
          <button class="universe-suggestion-add-btn" data-universe-id="${universeId}" data-tmdb-id="${item.tmdb_id}" data-title="${escapeAttr(item.title)}" data-poster="${escapeAttr(item.poster_path || "")}">+ Add</button>
        </div>
      </div>
    `).join("");
    container.querySelectorAll(".universe-suggestion-add-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "Adding…";
        try {
          await addUniverseMember(Number(btn.dataset.universeId), {
            tmdb_id: Number(btn.dataset.tmdbId), title: btn.dataset.title, poster_path: btn.dataset.poster || null,
          });
        } catch (e) {
          btn.disabled = false;
          btn.textContent = "+ Add";
        }
      });
    });
  } catch (e) {
    container.innerHTML = `<p>Error: ${e.message}</p>`;
  } finally {
    container.dataset.loading = "0";
  }
}

export async function loadNotificationHistory() {
  const container = $("#notification-history-list");
  container.innerHTML = "Loading...";
  try {
    const data = await api("/api/tracker/history");
    if (data.history.length === 0) {
      container.innerHTML = "<p>No notifications yet.</p>";
      return;
    }
    container.innerHTML = data.history.map((h) => `
      <div class="notification-history-item hint">
        ${new Date(h.created_at).toLocaleString()} — ${h.message}
      </div>
    `).join("");
  } catch (e) {
    container.innerHTML = `<p>Error loading notification history: ${e.message}</p>`;
  }
}

// ---- Browser notifications ----
const NOTIFIED_IDS_KEY = "media-manager:notified-ids";

function loadNotifiedIds() {
  try {
    return new Set(JSON.parse(localStorage.getItem(NOTIFIED_IDS_KEY) || "[]"));
  } catch (e) {
    return new Set();
  }
}

function saveNotifiedIds(ids) {
  try {
    localStorage.setItem(NOTIFIED_IDS_KEY, JSON.stringify(Array.from(ids)));
  } catch (e) { /* localStorage unavailable, skip persistence */ }
}

export function requestNotificationPermission() {
  if (!("Notification" in window)) return;
  if (Notification.permission === "default") Notification.requestPermission();
}

function notificationBody(n) {
  return n.media_type === "tv"
    ? `Season ${n.latest_known_season} of ${n.title} is out!`
    : `${n.title}: ${n.movie_release_status || "new release detected"}`;
}

function firePendingBrowserNotifications(notifications, notifiedIds) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  for (const n of notifications) {
    if (notifiedIds.has(n.id)) continue;
    const notif = new Notification("New Media Available", { body: notificationBody(n) });
    notif.onclick = () => {
      window.focus();
      switchToTab("notifications");
      notif.close();
    };
    notifiedIds.add(n.id);
  }
  saveNotifiedIds(notifiedIds);
}

export function pollNotifications() {
  const notifiedIds = loadNotifiedIds();
  const tick = async () => {
    try {
      const data = await api("/api/tracker/notifications");
      firePendingBrowserNotifications(data.notifications, notifiedIds);
      if ($("#tab-notifications").classList.contains("active")) loadNotifications();
    } catch (e) { /* offline or server restarting, retry on next tick */ }
  };
  tick();
  setInterval(tick, 30000);
}

// Polls for files the filesystem watcher has seen since the last scan
// (see app/core/fs_watcher.py) -- always 0/hidden when watcher.enabled is
// off, since nothing ever feeds the tracker in that case, so this is cheap
// to poll unconditionally rather than checking Settings first.
export async function refreshNewFilesBadge() {
  const badge = $("#new-files-badge");
  if (!badge) return;
  try {
    const data = await api("/api/scan/new-files");
    if (data.count > 0) {
      badge.textContent = `${data.count} new file(s) detected — Scan Library to pick them up`;
      badge.classList.remove("hidden");
    } else {
      badge.classList.add("hidden");
    }
  } catch (e) { /* offline or server restarting, retry on next tick */ }
}

export function pollNewFiles() {
  refreshNewFilesBadge();
  setInterval(refreshNewFilesBadge, 20000);
}
