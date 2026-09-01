/**
 * Watchlist tab: one page listing everything that needs attention across
 * the whole library -- titles the tracker has flagged as having new
 * content not yet archived, and TV shows with archived episodes still
 * unwatched. Both sections come from GET /api/watchlist in one call.
 */
import { escapeAttr } from "./archive-tab.js";
import { $, api } from "./core.js";
import { posterMarkup } from "./gallery.js";
import { switchToTab } from "../app.js";

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

export async function loadWatchlist() {
  const downloadContainer = $("#watchlist-needs-download");
  const watchingContainer = $("#watchlist-needs-watching");
  downloadContainer.innerHTML = "Loading...";
  watchingContainer.innerHTML = "Loading...";
  try {
    const data = await api("/api/watchlist");

    downloadContainer.innerHTML = data.needs_download.length
      ? data.needs_download.map(needsDownloadCard).join("")
      : `<p class="hint">Nothing new -- every tracked title is up to date.</p>`;
    // Jumps to Notifications, where Mark Downloaded/Remind Me actually live.
    downloadContainer.querySelectorAll(".gallery-card").forEach((card) => {
      card.addEventListener("click", () => switchToTab("notifications"));
    });

    watchingContainer.innerHTML = data.needs_watching.length
      ? data.needs_watching.map(needsWatchingCard).join("")
      : `<p class="hint">No unwatched episodes -- you're caught up.</p>`;

    watchingContainer.querySelectorAll(".gallery-card").forEach((card, i) => {
      // Jumps to the TV tab filtered by this show's title, same pattern the
      // header's global search uses -- there's no per-show detail pane data
      // available from this aggregate view (just counts), so this opens the
      // gallery card the user can actually click into instead.
      card.addEventListener("click", () => {
        const show = data.needs_watching[i];
        switchToTab("tv");
        const searchInput = $("#tv-search");
        if (searchInput) {
          searchInput.value = show.title;
          searchInput.dispatchEvent(new Event("input"));
        }
      });
    });
  } catch (e) {
    downloadContainer.innerHTML = `<p>Error loading watchlist: ${e.message}</p>`;
    watchingContainer.innerHTML = "";
  }
}
