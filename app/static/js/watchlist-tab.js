/**
 * Watchlist tab: one page listing everything that needs attention across
 * the whole library -- titles the tracker has flagged as having new
 * content not yet archived, and TV shows with archived episodes still
 * unwatched. Both sections come from GET /api/watchlist in one call.
 */
import { escapeAttr } from "./archive-tab.js";
import { $, api } from "./core.js";
import { downloadCsv, posterMarkup, rowsToCsv } from "./gallery.js";
import { switchToTab } from "../app.js";

let lastWatchlistData = null;

function needsDownloadCard(n) {
  const detail = n.media_type === "tv"
    ? `Season ${n.latest_known_season} now available`
    : (n.movie_release_status || "New release detected");
  return `
    <div class="gallery-card">
      <div class="gallery-badges"><span class="badge badge-new">⚡</span></div>
      ${posterMarkup(n.title, n.poster_path)}
      <div class="gallery-info">
        <div class="gallery-title" title="${escapeAttr(n.title)}">${escapeAttr(n.title)}</div>
        <div class="gallery-meta"><span class="hint">${escapeAttr(detail)}</span></div>
      </div>
    </div>
  `;
}

function needsWatchingCard(s) {
  const nextUp = s.next_up && s.next_up.season_number != null && s.next_up.episode_number != null
    ? `Next: S${String(s.next_up.season_number).padStart(2, "0")}E${String(s.next_up.episode_number).padStart(2, "0")}${s.next_up.episode_title ? ` — ${s.next_up.episode_title}` : ""}`
    : "";
  return `
    <div class="gallery-card" data-tmdb-id="${s.tmdb_id}">
      <div class="gallery-badges"><span class="badge">${s.unwatched_count}</span></div>
      ${posterMarkup(s.title, s.poster_path)}
      <div class="gallery-info">
        <div class="gallery-title" title="${escapeAttr(s.title)}">${escapeAttr(s.title)}</div>
        <div class="gallery-meta">
          <span class="hint">${s.unwatched_count} of ${s.total_count} unwatched</span>
          ${nextUp ? `<span class="hint">${escapeAttr(nextUp)}</span>` : ""}
        </div>
      </div>
    </div>
  `;
}

function sortedNeedsWatching(needsWatching) {
  const sortMode = $("#watchlist-watching-sort")?.value || "unwatched";
  const sorted = needsWatching.slice();
  if (sortMode === "title") sorted.sort((a, b) => a.title.localeCompare(b.title));
  else sorted.sort((a, b) => b.unwatched_count - a.unwatched_count);
  return sorted;
}

export function renderWatchlist() {
  const data = lastWatchlistData;
  if (!data) return;
  const downloadContainer = $("#watchlist-needs-download");
  const watchingContainer = $("#watchlist-needs-watching");

  downloadContainer.innerHTML = data.needs_download.length
    ? data.needs_download.map(needsDownloadCard).join("")
    : `<p class="hint">Nothing new -- every tracked title is up to date.</p>`;
  // Jumps to Notifications, where Mark Downloaded/Remind Me actually live.
  downloadContainer.querySelectorAll(".gallery-card").forEach((card) => {
    card.addEventListener("click", () => switchToTab("notifications"));
  });

  const needsWatching = sortedNeedsWatching(data.needs_watching);
  watchingContainer.innerHTML = needsWatching.length
    ? needsWatching.map(needsWatchingCard).join("")
    : `<p class="hint">No unwatched episodes -- you're caught up.</p>`;

  watchingContainer.querySelectorAll(".gallery-card").forEach((card, i) => {
    // Jumps to the TV tab filtered by this show's title, same pattern the
    // header's global search uses -- there's no per-show detail pane data
    // available from this aggregate view (just counts), so this opens the
    // gallery card the user can actually click into instead.
    card.addEventListener("click", () => {
      const show = needsWatching[i];
      switchToTab("tv");
      const searchInput = $("#tv-search");
      if (searchInput) {
        searchInput.value = show.title;
        searchInput.dispatchEvent(new Event("input"));
      }
    });
  });
}

export async function loadWatchlist() {
  const downloadContainer = $("#watchlist-needs-download");
  const watchingContainer = $("#watchlist-needs-watching");
  downloadContainer.innerHTML = "Loading...";
  watchingContainer.innerHTML = "Loading...";
  try {
    lastWatchlistData = await api("/api/watchlist");
    renderWatchlist();
  } catch (e) {
    lastWatchlistData = null;
    downloadContainer.innerHTML = `<p>Error loading watchlist: ${e.message}</p>`;
    watchingContainer.innerHTML = "";
  }
}

export function exportWatchlistCsv() {
  if (!lastWatchlistData) return;
  const rows = [
    ...lastWatchlistData.needs_download.map((n) => [
      "New / Not Yet Archived", n.title, n.media_type === "tv" ? "TV" : "Movie",
      n.media_type === "tv" ? `Season ${n.latest_known_season} available` : (n.movie_release_status || "New release detected"),
    ]),
    ...sortedNeedsWatching(lastWatchlistData.needs_watching).map((s) => [
      "Owned, Unwatched", s.title, "TV", `${s.unwatched_count} of ${s.total_count} unwatched`,
    ]),
  ];
  downloadCsv(`watchlist-${new Date().toISOString().slice(0, 10)}.csv`, rowsToCsv(["Section", "Title", "Type", "Detail"], rows));
}
