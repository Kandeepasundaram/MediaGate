/**
 * The full-screen detail pane opened from a gallery card: metadata, tags, ratings/trailer/cast, TV season/movie-collection status, and the manual-match/override flow entry points.
 */

import { closeMatchPicker, escapeAttr, openMatchModal } from "./archive-tab.js";
import { $, api, formatBytes, state } from "./core.js";
import { TV_SHOW_STATUSES, downloadMovieNote, downloadTvNote, effectiveWatched, getActiveViewerId, groupEpisodesByShow, loadMoviesGallery, loadTvGallery, mapApiStatusToManual, posterMarkup, posterUrl, renderMoviesGallery, renderTvGallery, saveMovieNote, saveTvNote, setTvShowStatus, toggleWatched } from "./gallery.js";
import { loadNotifications, loadTrackerTab, TRACKER_CATEGORIES } from "./notifications-tab.js";
import { formatDuration } from "./stats-tab.js";

// ---- Detail pane ----
state.detailPane = null; // { kind: "movie"|"tv", data }

export function renderDetailPane() {
  const pane = state.detailPane;
  const content = $("#detail-pane-content");
  if (!pane) return;

  if (pane.kind === "movie") {
    const item = pane.data;
    content.innerHTML = `
      <div id="detail-movie-status"></div>
      ${(item.tmdb_id == null && !item.manual_override) ? `<p class="unidentified-badge">⚠ Unidentified — no TMDB match yet</p>` : ""}
      ${posterMarkupLarge(item.title, item.poster_path)}
      <div class="detail-title">${item.title}</div>
      <div class="detail-year">${item.year || ""}</div>
      <label class="watched-toggle">
        <input type="checkbox" id="detail-watched-toggle" data-id="${item.id}" ${effectiveWatched(item) ? "checked" : ""}>
        Watched${getActiveViewerId() != null ? ` (${escapeAttr(state.viewers?.find((v) => v.id === getActiveViewerId())?.name || "viewer")})` : ""}
      </label>
      ${detailTagsMarkup(item.tags)}
      <div id="detail-ratings" class="detail-ratings"></div>
      <div id="detail-trailer" class="detail-trailer"></div>
      <p class="detail-overview">${item.overview || "No overview available."}</p>
      <div id="detail-more-info"></div>
      <div class="detail-file-info">
        <div class="detail-file-row"><span>File</span><span class="detail-file-value" title="${item.file_name || ""}">${item.file_name || "—"}</span></div>
        <div class="detail-file-row"><span>Size</span><span>${item.size_bytes != null ? formatBytes(item.size_bytes) : "—"}</span></div>
        <div class="detail-file-row"><span>Path</span><span class="detail-file-value" title="${item.final_path || ""}">${item.final_path || "—"}</span></div>
        <div id="detail-file-extra" class="hint">Loading file details…</div>
      </div>
      <div class="detail-note-actions">
        <button id="detail-note-download-btn">Download Note (.md)</button>
        <button id="detail-note-save-btn">Save Note to Movie Folder</button>
        <span id="detail-note-status" class="hint"></span>
      </div>
      ${detailFixMarkup()}
    `;
    $("#detail-note-download-btn").addEventListener("click", () => downloadMovieNote(item.id));
    $("#detail-note-save-btn").addEventListener("click", () => saveMovieNote(item.id));
    $("#detail-watched-toggle").addEventListener("change", async (e) => {
      try {
        await toggleWatched(item.id, e.target.checked);
        if (getActiveViewerId() != null) item.viewer_watched = e.target.checked;
        else item.watched = e.target.checked;
        renderMoviesGallery(); // syncs the same checkbox shown on the gallery card
      } catch (err) {
        e.target.checked = !e.target.checked;
      }
    });
    loadRatings(item.id);
    loadTrailer(item.id);
    loadMoreInfo(item.id);
    loadFileInfo(item.id, "#detail-file-extra");
    if (item.tmdb_id != null) loadMovieStatus(item.tmdb_id);
    wireDetailTags([item.id], (tags) => { item.tags = tags; renderMoviesGallery(); });
  } else if (pane.kind === "tracker") {
    const item = pane.data;
    const statusLine = item.media_type === "tv"
      ? (item.pending_notification
          ? `⚡ Season ${item.latest_known_season} available`
          : (item.latest_known_season != null ? `Up to date through season ${item.latest_known_season}` : "Not checked yet"))
      : (item.pending_notification ? `⚡ ${item.movie_release_status || "New release detected"}` : (item.movie_release_status || "Watching for a new release"));
    content.innerHTML = `
      ${posterMarkupLarge(item.title, item.poster_path)}
      <div class="detail-title">${escapeAttr(item.title)}</div>
      <div class="detail-year">${item.media_type === "tv" ? "TV Show" : "Movie"}${item.muted ? " · Muted" : ""}</div>
      <label class="show-status-select-label">
        Category
        <select id="detail-tracker-category-select">
          ${TRACKER_CATEGORIES.map((c) => `<option value="${c.value}" ${c.value === item.category ? "selected" : ""}>${c.label}</option>`).join("")}
        </select>
      </label>
      <p class="detail-overview">${item.overview || "No overview available."}</p>
      <div class="detail-file-info">
        <div class="detail-file-row"><span>Status</span><span>${statusLine}</span></div>
        <div class="detail-file-row"><span>Last checked</span><span>${item.last_checked ? new Date(item.last_checked).toLocaleString() : "not checked yet"}</span></div>
        ${item.next_episode_air_date ? `<div class="detail-file-row"><span>Next episode</span><span>${item.next_episode_air_date}</span></div>` : ""}
        ${item.snoozed_until ? `<div class="detail-file-row"><span>Snoozed until</span><span>${new Date(item.snoozed_until).toLocaleString()}</span></div>` : ""}
        <div class="detail-file-row"><span>Check interval</span><span>${item.check_interval_hours ? `every ${item.check_interval_hours}h (override)` : "Default (daily)"}</span></div>
      </div>
      ${item.media_type === "tv" ? trackerWatchProgressMarkup(item) : ""}
      ${item.media_type === "tv" ? `<div id="detail-tracker-season-info"></div>` : `<div id="detail-movie-status"></div>`}
      <div id="detail-ratings" class="detail-ratings"></div>
      <div id="detail-trailer" class="detail-trailer"></div>
      <div id="detail-more-info"></div>
      <div class="tracked-item-actions">
        <label class="watched-toggle">
          <input type="checkbox" id="detail-tracker-mute-toggle" ${item.muted ? "checked" : ""}>
          Muted
        </label>
        <button id="detail-tracker-check-now-btn">Check Now</button>
        ${item.pending_notification ? `
          <button id="detail-tracker-ack-btn">Mark Downloaded</button>
          <button id="detail-tracker-snooze-btn">Remind Me in 7 Days</button>
        ` : ""}
      </div>
    `;
    if (item.media_type === "tv") wireTrackerWatchProgress(item);
    if (item.tmdb_id != null) {
      if (item.media_type === "tv") loadTrackerSeasonInfo(item);
      else loadMovieStatus(item.tmdb_id);
      loadRatingsByTmdb(item.tmdb_id, item.media_type);
      loadTrailerByTmdb(item.tmdb_id, item.media_type);
      loadMoreInfoByTmdb(item.tmdb_id, item.media_type);
    }
    $("#detail-tracker-mute-toggle").addEventListener("change", async (e) => {
      await api(`/api/tracker/${item.id}/mute`, { method: "POST", body: JSON.stringify({ muted: e.target.checked }) });
      item.muted = e.target.checked;
      renderDetailPane();
      loadTrackerTab();
    });
    $("#detail-tracker-category-select").addEventListener("change", async (e) => {
      const select = e.target;
      const previous = item.category;
      select.disabled = true;
      try {
        await api(`/api/tracker/${item.id}/category`, { method: "POST", body: JSON.stringify({ category: select.value }) });
        item.category = select.value;
        loadTrackerTab();
      } catch (err) {
        select.value = previous;
      } finally {
        select.disabled = false;
      }
    });
    $("#detail-tracker-check-now-btn").addEventListener("click", async (e) => {
      const btn = e.target;
      btn.disabled = true;
      btn.textContent = "Checking...";
      try {
        const res = await api(`/api/tracker/${item.id}/check-now`, { method: "POST" });
        state.detailPane.data = res.tracker;
        renderDetailPane();
        loadTrackerTab();
      } catch (err) {
        btn.disabled = false;
        btn.textContent = "Check Now";
      }
    });
    const ackBtn = $("#detail-tracker-ack-btn");
    if (ackBtn) ackBtn.addEventListener("click", async () => {
      await api("/api/tracker/acknowledge", { method: "POST", body: JSON.stringify({ tracker_id: item.id }) });
      item.pending_notification = false;
      renderDetailPane();
      loadNotifications();
      loadTrackerTab();
    });
    const snoozeBtn = $("#detail-tracker-snooze-btn");
    if (snoozeBtn) snoozeBtn.addEventListener("click", async () => {
      await api(`/api/tracker/${item.id}/snooze`, { method: "POST", body: JSON.stringify({ days: 7 }) });
      item.pending_notification = false;
      renderDetailPane();
      loadNotifications();
      loadTrackerTab();
    });
  } else {
    const show = pane.data;
    const hasEpisodes = show.episodes.length > 0;
    content.innerHTML = `
      <div id="detail-watched-summary"></div>
      <div id="detail-tv-status"></div>
      ${(show.tmdb_id == null && !show.manual_override) ? `<p class="unidentified-badge">⚠ Unidentified — no TMDB match yet</p>` : ""}
      ${show.noFilesOnDisk ? `<p class="unidentified-badge">🗑 Removed from disk — still tracked</p>` : ""}
      ${posterMarkupLarge(show.title, show.poster_path)}
      <div class="detail-title">${show.title}</div>
      <div class="detail-year">${show.episodes.length} episode(s)</div>
      ${show.tmdb_id != null ? `
        <label class="show-status-select-label">
          Status
          <select id="detail-show-status-select">
            ${TV_SHOW_STATUSES.map((s) => `<option value="${s.value}" ${s.value === show.show_status ? "selected" : ""}>${s.label}</option>`).join("")}
          </select>
          <span id="detail-api-status-pill"></span>
        </label>
      ` : ""}
      ${hasEpisodes ? detailTagsMarkup(show.tags) : ""}
      <div id="detail-ratings" class="detail-ratings"></div>
      <div id="detail-trailer" class="detail-trailer"></div>
      <p class="detail-overview">${show.overview || "No overview available."}</p>
      <div id="detail-more-info"></div>
      <div id="detail-tv-body"></div>
      ${show.tmdb_id != null ? `
        <div class="detail-note-actions">
          <button id="detail-note-download-btn">Download Note (.md)</button>
          <button id="detail-note-save-btn">Save Note to Show Folder</button>
          <span id="detail-note-status" class="hint"></span>
        </div>
      ` : `<p class="hint">Notes need a TMDB match first.</p>`}
      ${detailFixMarkup()}
    `;
    if (show.tmdb_id != null) {
      $("#detail-note-download-btn").addEventListener("click", () => downloadTvNote(show.tmdb_id));
      $("#detail-note-save-btn").addEventListener("click", () => saveTvNote(show.tmdb_id));
      $("#detail-show-status-select").addEventListener("change", async (e) => {
        const select = e.target;
        const previous = show.show_status;
        select.disabled = true;
        try {
          await setTvShowStatus(show.tmdb_id, select.value);
          show.show_status = select.value;
          renderTvGallery();
        } catch (err) {
          select.value = previous;
        } finally {
          select.disabled = false;
        }
      });
    }
    renderTvBody();
    if (hasEpisodes) {
      loadRatings(show.episodes[0].id); // ratings are show-level; episodes are pre-sorted, so [0] is stable
      loadTrailer(show.episodes[0].id);
      loadMoreInfo(show.episodes[0].id);
      wireDetailTags(show.episodes.map((e) => e.id), (tags) => {
        show.tags = tags;
        show.episodes.forEach((e) => { e.tags = tags; });
        renderTvGallery();
      });
    }
    if (show.tmdb_id != null) loadTvStatus(show);
  }

  if (pane.kind !== "tracker") wireDetailFix();
}

// Redraws just the season tabs + episode list (not the outer shell), so
// switching seasons or toggling name/watched state doesn't re-trigger the
// show-level ratings/status fetches (loadRatings especially -- OMDb isn't
// cached server-side, unlike TMDBClient).
export function renderTvBody() {
  const pane = state.detailPane;
  if (!pane || pane.kind !== "tv") return;
  const show = pane.data;
  renderWatchedSummary(show);
  const container = $("#detail-tv-body");
  if (!container) return;

  if (show.episodes.length === 0) {
    container.innerHTML = `<p class="hint">No files on disk for this show -- status is still tracked above.</p>`;
    return;
  }

  const seasons = Array.from(new Set(show.episodes.map((e) => e.season_number))).sort((a, b) => a - b);
  if (pane.selectedSeason == null || !seasons.includes(pane.selectedSeason)) {
    pane.selectedSeason = seasons[seasons.length - 1];
  }
  if (pane.nameMode == null) pane.nameMode = "episode";

  const seasonEpisodes = show.episodes.filter((e) => e.season_number === pane.selectedSeason);
  const allWatched = seasonEpisodes.length > 0 && seasonEpisodes.every((e) => e.watched);
  const hasEpisodeNames = show.episodes.some((e) => e.episode_title);
  // Every episode in a season strictly before the one currently selected --
  // lets someone who's picking up a show mid-way mark everything they're
  // already past as watched without also touching the season they're on.
  const earlierEpisodes = show.episodes.filter((e) => e.season_number < pane.selectedSeason);
  const earlierAllWatched = earlierEpisodes.length > 0 && earlierEpisodes.every((e) => e.watched);
  // Recomputed on every render (not just at groupEpisodesByShow time) so it
  // stays correct after a season- or episode-level toggle changes it.
  show.watched = show.episodes.every((e) => e.watched);

  container.innerHTML = `
    <div class="season-tabs">
      ${seasons.map((s) => `<button class="season-tab-btn ${s === pane.selectedSeason ? "active" : ""}" data-season="${s}">Season ${s}</button>`).join("")}
    </div>
    <div class="episode-toolbar">
      ${hasEpisodeNames ? `
        <label class="name-mode-toggle">
          <input type="checkbox" id="detail-name-mode-toggle" ${pane.nameMode === "episode" ? "checked" : ""}>
          Show episode names
        </label>
      ` : "<span></span>"}
      <button id="detail-season-watched-btn">${allWatched ? "Mark Season Unwatched" : "Mark Season Watched"}</button>
      ${earlierEpisodes.length > 0 ? `
        <button id="detail-earlier-watched-btn">${earlierAllWatched ? "Mark Earlier Seasons Unwatched" : "Mark Earlier Seasons Watched"}</button>
      ` : ""}
      <button id="detail-show-watched-btn">${show.watched ? "Mark Show Unwatched" : "Mark Show Watched"}</button>
    </div>
    <div class="detail-episodes">
      ${seasonEpisodes.map((ep) => `
        <div class="detail-episode-row">
          <span>S${String(ep.season_number).padStart(2, "0")}E${String(ep.episode_number).padStart(2, "0")}</span>
          <span class="detail-ep-file hint" title="${ep.file_name || ""}">${(pane.nameMode === "episode" && ep.episode_title) ? ep.episode_title : (ep.file_name || "")}${ep.air_date ? ` · ${ep.air_date}` : ""}${ep.size_bytes != null ? ` · ${formatBytes(ep.size_bytes)}` : ""}</span>
          <label class="watched-toggle">
            <input type="checkbox" class="detail-ep-watched" data-id="${ep.id}" ${effectiveWatched(ep) ? "checked" : ""}>
            Watched
          </label>
          <button class="ep-details-btn" data-id="${ep.id}">Details</button>
        </div>
        <div class="detail-ep-extra hint" id="detail-ep-extra-${ep.id}" hidden></div>
      `).join("")}
    </div>
  `;

  container.querySelectorAll(".season-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      pane.selectedSeason = Number(btn.dataset.season);
      renderTvBody();
    });
  });

  const nameModeToggle = $("#detail-name-mode-toggle");
  if (nameModeToggle) {
    nameModeToggle.addEventListener("change", (e) => {
      pane.nameMode = e.target.checked ? "episode" : "file";
      renderTvBody();
    });
  }

  $("#detail-season-watched-btn").addEventListener("click", async (e) => {
    const btn = e.target;
    const newWatched = !allWatched;
    btn.disabled = true;
    try {
      await api("/api/library/watched-batch", {
        method: "POST",
        body: JSON.stringify({ ids: seasonEpisodes.map((ep) => ep.id), watched: newWatched }),
      });
      seasonEpisodes.forEach((ep) => { ep.watched = newWatched; });
      renderTvBody();
      renderTvGallery(); // keeps the gallery card's "all watched" badge in sync
    } catch (err) {
      btn.disabled = false;
    }
  });

  const earlierBtn = $("#detail-earlier-watched-btn");
  if (earlierBtn) {
    earlierBtn.addEventListener("click", async (e) => {
      const btn = e.target;
      const newWatched = !earlierAllWatched;
      btn.disabled = true;
      try {
        await api("/api/library/watched-batch", {
          method: "POST",
          body: JSON.stringify({ ids: earlierEpisodes.map((ep) => ep.id), watched: newWatched }),
        });
        earlierEpisodes.forEach((ep) => { ep.watched = newWatched; });
        renderTvBody();
        renderTvGallery();
      } catch (err) {
        btn.disabled = false;
      }
    });
  }

  $("#detail-show-watched-btn").addEventListener("click", async (e) => {
    const btn = e.target;
    const newWatched = !show.watched;
    btn.disabled = true;
    try {
      await api("/api/library/watched-batch", {
        method: "POST",
        body: JSON.stringify({ ids: show.episodes.map((ep) => ep.id), watched: newWatched }),
      });
      show.episodes.forEach((ep) => { ep.watched = newWatched; });
      renderTvBody();
      renderTvGallery();
    } catch (err) {
      btn.disabled = false;
    }
  });

  container.querySelectorAll(".detail-ep-watched").forEach((input) => {
    input.addEventListener("change", async () => {
      try {
        await toggleWatched(Number(input.dataset.id), input.checked);
        const ep = show.episodes.find((e) => e.id === Number(input.dataset.id));
        if (ep) {
          if (getActiveViewerId() != null) ep.viewer_watched = input.checked;
          else ep.watched = input.checked;
        }
        renderTvBody(); // keeps the "Mark Season Watched" button label in sync
        renderTvGallery(); // keeps the gallery card's "all watched" badge in sync
      } catch (err) {
        input.checked = !input.checked;
      }
    });
  });

  container.querySelectorAll(".ep-details-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const el = document.getElementById(`detail-ep-extra-${btn.dataset.id}`);
      if (!el) return;
      if (!el.hidden) { el.hidden = true; return; }
      el.hidden = false;
      loadFileInfo(Number(btn.dataset.id), `#detail-ep-extra-${btn.dataset.id}`);
    });
  });
}

// Permanent watched-count banner, styled like the season-gap/next-episode
// banners below it but always present (no network round trip, so it's never
// blank while those load, and it survives every watched-state toggle since
// renderTvBody -- called after each one -- refreshes it every time).
function renderWatchedSummary(show) {
  const el = $("#detail-watched-summary");
  if (!el) return;
  if (show.episodes.length === 0) {
    el.innerHTML = "";
    return;
  }
  const watchedCount = show.episodes.filter((e) => effectiveWatched(e)).length;
  el.innerHTML = `<div class="status-banner status-banner-ok">👁️ ${watchedCount} of ${show.episodes.length} watched</div>`;
}

// GET /api/library/tv-status, memoized in state.tvStatusCache so the gallery
// badges and the detail pane banner (and repeat renders of either) don't
// keep re-issuing the same TMDB lookup for a show already checked this session.
export async function getTvStatus(tmdbId) {
  if (tmdbId in state.tvStatusCache) return state.tvStatusCache[tmdbId];
  try {
    const status = await api(`/api/library/tv-status?tmdb_id=${tmdbId}`);
    state.tvStatusCache[tmdbId] = status;
    return status;
  } catch (e) {
    state.tvStatusCache[tmdbId] = null;
    return null;
  }
}

// Pure comparison of a TvStatusOut against what's actually archived --
// shared by the gallery "new season" badge and the detail pane banner so
// the two never disagree about what counts as "behind".
export function computeTvStatusInfo(status, episodes) {
  if (!status || !status.data_available) return null;
  let gapMessage = null;
  if (status.latest_known_season != null) {
    const localMaxSeason = Math.max(...episodes.map((e) => e.season_number));
    const localEpisodeCountInMaxSeason = episodes.filter((e) => e.season_number === localMaxSeason).length;
    if (status.latest_known_season > localMaxSeason) {
      gapMessage = `Season ${status.latest_known_season} is out — you have up to season ${localMaxSeason}.`;
    } else if (
      status.latest_known_season === localMaxSeason &&
      status.latest_season_episode_count != null &&
      status.latest_season_episode_count > localEpisodeCountInMaxSeason
    ) {
      gapMessage = `Season ${localMaxSeason} has ${status.latest_season_episode_count} episode(s) — you have ${localEpisodeCountInMaxSeason}.`;
    }
  }

  return {
    hasGap: gapMessage != null,
    gapMessage,
    totalArchived: episodes.length,
    totalEpisodes: status.total_episodes,
    statusLabel: status.status,
    network: status.network,
    nextEpisodeAirDate: status.next_episode_air_date,
    nextEpisodeCode: status.next_episode_code,
  };
}

// Per-season "N/total (M Aired)" breakdown -- the specific numbers behind
// computeTvStatusInfo's aggregate "X of Y episodes archived" line. Only
// seasons already started locally (owned > 0) are included, same reasoning
// as computeMissingEpisodes: an unstarted season is "not archived yet", not
// worth a "0/8" line. "(M Aired)" is appended only when it adds information
// -- a season that's fully aired already shows that through owned/total.
function computeSeasonBreakdown(status, episodes) {
  if (!status || !status.data_available || !status.seasons || status.seasons.length === 0) return [];
  const ownedBySeason = new Map();
  episodes.forEach((e) => ownedBySeason.set(e.season_number, (ownedBySeason.get(e.season_number) || 0) + 1));

  return status.seasons
    .map((s) => ({
      season: s.season_number,
      owned: ownedBySeason.get(s.season_number) || 0,
      total: s.episode_count,
      aired: s.aired_count,
    }))
    .filter((s) => s.owned > 0)
    .sort((a, b) => a.season - b.season)
    .map((s) => `Season ${s.season} ${s.owned}/${s.total}${s.aired != null && s.aired < s.total ? ` (${s.aired} Aired)` : ""}`);
}

// Per-season gap detector: unlike computeTvStatusInfo (which only flags the
// latest season), this diffs every season TMDB knows about against what's
// actually archived -- catches a hole left in an already-"complete" earlier
// season (e.g. S1E1-E2,E4 archived, E3 never downloaded), which a
// max-season/count-only check would never surface.
function computeMissingEpisodes(status, episodes) {
  if (!status || !status.data_available || !status.seasons || status.seasons.length === 0) return [];
  const ownedBySeason = new Map();
  episodes.forEach((e) => ownedBySeason.set(e.season_number, (ownedBySeason.get(e.season_number) || 0) + 1));

  return status.seasons
    .map((s) => ({
      season: s.season_number,
      owned: ownedBySeason.get(s.season_number) || 0,
      expected: s.episode_count,
    }))
    .filter((s) => s.owned > 0 && s.expected > s.owned) // only seasons already started -- an unstarted season is "not archived yet", not a gap
    .map((s) => ({ ...s, missing: s.expected - s.owned }));
}

async function loadTvStatus(show) {
  const el = $("#detail-tv-status");
  if (!el) return;
  const status = await getTvStatus(show.tmdb_id);
  const info = computeTvStatusInfo(status, show.episodes);
  const missingEpisodes = computeMissingEpisodes(status, show.episodes);
  const seasonBreakdown = computeSeasonBreakdown(status, show.episodes);
  renderApiStatusPill(show, info);
  if (!info && missingEpisodes.length === 0) {
    el.innerHTML = "";
    return;
  }
  let bannerHtml = "";
  if (info) {
    const fraction = seasonBreakdown.length > 0
      ? seasonBreakdown.join(", ")
      : info.totalEpisodes != null
        ? `${info.totalArchived} of ${info.totalEpisodes} episodes archived`
        : `${info.totalArchived} episode(s) archived`;
    const parts = info.hasGap ? [info.gapMessage, fraction] : [fraction];
    const cls = info.hasGap ? "status-banner" : "status-banner status-banner-ok";
    const icon = info.hasGap ? "📺" : "✅";
    const statusBits = [info.statusLabel, info.network].filter(Boolean).join(" · ");
    bannerHtml = `<div class="${cls}">${icon} ${parts.join(" · ")}${statusBits ? ` <span class="hint">(${statusBits})</span>` : ""}</div>`;
    if (info.nextEpisodeAirDate) {
      bannerHtml += `<div class="status-banner hint">📅 Next: ${info.nextEpisodeCode ? `${info.nextEpisodeCode} — ` : ""}${info.nextEpisodeAirDate}</div>`;
    }
  }
  const missingHtml = missingEpisodes.length > 0
    ? `<div class="status-banner missing-episodes">🕳️ Missing episodes: ${missingEpisodes.map((s) => `Season ${s.season} (${s.missing} missing)`).join(", ")}</div>`
    : "";
  el.innerHTML = bannerHtml + missingHtml;
}

// Pill next to the manual Status dropdown showing the real broadcast status
// (TMDB/TVmaze), separate from and never silently overwriting the manual
// TvShowStatus dropdown -- clickable only when it maps to a terminal state
// (ended/cancelled) that the dropdown doesn't already reflect, so setting it
// is always one deliberate click, never automatic.
function renderApiStatusPill(show, info) {
  const pill = $("#detail-api-status-pill");
  if (!pill) return;
  if (!info || !info.statusLabel) {
    pill.innerHTML = "";
    return;
  }
  const mapped = mapApiStatusToManual(info.statusLabel);
  if (mapped && mapped !== show.show_status) {
    pill.innerHTML = `<button type="button" id="detail-api-status-apply" class="show-status-pill show-status-${mapped}" title="Set show status to match">${info.statusLabel} — set?</button>`;
    $("#detail-api-status-apply").addEventListener("click", async (e) => {
      const btn = e.target;
      btn.disabled = true;
      try {
        await setTvShowStatus(show.tmdb_id, mapped);
        show.show_status = mapped;
        const select = $("#detail-show-status-select");
        if (select) select.value = mapped;
        renderApiStatusPill(show, info);
        renderTvGallery();
      } catch (err) {
        btn.disabled = false;
      }
    });
  } else {
    pill.innerHTML = `<span class="show-status-pill">${info.statusLabel}</span>`;
  }
}

// GET /api/library/movie-status, memoized like getTvStatus.
export async function getMovieStatus(tmdbId) {
  if (tmdbId in state.movieStatusCache) return state.movieStatusCache[tmdbId];
  try {
    const status = await api(`/api/library/movie-status?tmdb_id=${tmdbId}`);
    state.movieStatusCache[tmdbId] = status;
    return status;
  } catch (e) {
    state.movieStatusCache[tmdbId] = null;
    return null;
  }
}

// Movie counterpart of computeTvStatusInfo: "gap" here means another movie
// in the same TMDB collection that isn't archived yet. No collection at all
// (the common case) returns null, same as a TV show with no TMDB match --
// no banner rather than a misleadingly empty one.
export function computeMovieStatusInfo(status, archivedTmdbIds) {
  if (!status || !status.data_available || status.collection_id == null) return null;
  const missing = status.related.filter((r) => r.tmdb_id != null && !archivedTmdbIds.has(r.tmdb_id));
  const hasGap = missing.length > 0;
  const gapMessage = hasGap
    ? (missing.length === 1
        ? `Related title available: ${missing[0].title}${missing[0].year ? ` (${missing[0].year})` : ""}.`
        : `${missing.length} related titles available: ${missing.map((m) => m.title).join(", ")}.`)
    : null;
  return {
    hasGap,
    gapMessage,
    collectionSize: status.related.length + 1, // + the movie itself
    archivedInCollection: status.related.length + 1 - missing.length,
  };
}

async function loadMovieStatus(tmdbId) {
  const el = $("#detail-movie-status");
  if (!el) return;
  const archivedTmdbIds = new Set(state.movieItems.map((i) => i.tmdb_id).filter((id) => id != null));
  const status = await getMovieStatus(tmdbId);
  const info = computeMovieStatusInfo(status, archivedTmdbIds);
  if (!info) {
    el.innerHTML = "";
    return;
  }
  const fraction = `${info.archivedInCollection} of ${info.collectionSize} collection titles archived`;
  const parts = info.hasGap ? [info.gapMessage, fraction] : [fraction];
  const cls = info.hasGap ? "status-banner" : "status-banner status-banner-ok";
  const icon = info.hasGap ? "🎬" : "✅";
  const trackBtn = info.hasGap ? `<button id="detail-track-missing-btn">Track Missing Titles</button>` : "";
  el.innerHTML = `<div class="${cls}">${icon} ${parts.join(" · ")}</div>${trackBtn}`;
  if (info.hasGap) {
    $("#detail-track-missing-btn").addEventListener("click", async (e) => {
      const btn = e.target;
      btn.disabled = true;
      btn.textContent = "Tracking...";
      const missing = status.related.filter((r) => r.tmdb_id != null && !archivedTmdbIds.has(r.tmdb_id));
      for (const m of missing) {
        try {
          await api("/api/tracker/add", {
            method: "POST",
            body: JSON.stringify({ tmdb_id: m.tmdb_id, media_type: "movie", title: m.title }),
          });
        } catch (err) { /* best-effort -- one failure shouldn't block the rest */ }
      }
      btn.textContent = `Tracking ${missing.length} title(s)`;
    });
  }
}

function renderTrailer(t) {
  const el = $("#detail-trailer");
  if (!el) return;
  if (t.youtube_key) {
    el.innerHTML = `<a href="https://www.youtube.com/watch?v=${encodeURIComponent(t.youtube_key)}" target="_blank" rel="noopener">▶ Watch Trailer</a>`;
  } else if (!t.tmdb_configured) {
    el.innerHTML = "";
  } else {
    el.innerHTML = `<span class="hint">No trailer found.</span>`;
  }
}

async function loadTrailer(itemId) {
  if (!$("#detail-trailer")) return;
  try {
    renderTrailer(await api(`/api/library/${itemId}/trailer`));
  } catch (e) {
    $("#detail-trailer").innerHTML = "";
  }
}

async function loadTrailerByTmdb(tmdbId, mediaType) {
  if (!$("#detail-trailer")) return;
  try {
    renderTrailer(await api(`/api/library/trailer?tmdb_id=${tmdbId}&media_type=${mediaType}`));
  } catch (e) {
    $("#detail-trailer").innerHTML = "";
  }
}

function renderMoreInfo(info) {
  const el = $("#detail-more-info");
  if (!el) return;
  if (!info.tmdb_configured || (info.cast.length === 0 && info.similar.length === 0)) {
    el.innerHTML = "";
    return;
  }
  const castHtml = info.cast.length ? `
    <div class="detail-section">
      <h4>Cast</h4>
      <div class="cast-row">
        ${info.cast.map((c) => `
          <div class="cast-card${c.id != null ? " cast-card-clickable" : ""}" title="${c.name || ""}${c.character ? ` as ${c.character}` : ""}${c.id != null ? " — click for filmography" : ""}" ${c.id != null ? `data-person-id="${c.id}" data-person-name="${escapeAttr(c.name || "")}"` : ""}>
            ${c.profile_path
              ? `<img src="https://image.tmdb.org/t/p/w185${c.profile_path}" alt="${c.name || ""}">`
              : `<div class="cast-card-placeholder"></div>`}
            <span class="cast-name">${c.name || ""}</span>
            ${c.character ? `<span class="cast-character hint">${c.character}</span>` : ""}
          </div>
        `).join("")}
      </div>
    </div>
  ` : "";
  const similarHtml = info.similar.length ? `
    <div class="detail-section">
      <h4>Similar Titles</h4>
      <div class="cast-row">
        ${info.similar.map((s) => `
          <div class="cast-card" title="${s.title}${s.year ? ` (${s.year})` : ""}">
            ${posterMarkup(s.title, s.poster_path)}
            <span class="cast-name">${s.title}</span>
          </div>
        `).join("")}
      </div>
    </div>
  ` : "";
  el.innerHTML = castHtml + similarHtml;
  el.querySelectorAll(".cast-card-clickable").forEach((card) => {
    card.addEventListener("click", () => openPersonModal(Number(card.dataset.personId), card.dataset.personName));
  });
}

// ---- Cast-card filmography click-through ----
export async function openPersonModal(personId, name) {
  const modal = $("#person-modal");
  if (!modal) return;
  $("#person-modal-title").textContent = name || "Filmography";
  $("#person-modal-results").innerHTML = `<p class="hint">Loading…</p>`;
  modal.classList.remove("hidden");
  try {
    const data = await api(`/api/library/person/${personId}/credits`);
    renderPersonCredits(data);
  } catch (e) {
    $("#person-modal-results").innerHTML = `<p class="hint">Error: ${e.message}</p>`;
  }
}

export function closePersonModal() {
  $("#person-modal")?.classList.add("hidden");
}

function renderPersonCredits(data) {
  const el = $("#person-modal-results");
  if (!data.tmdb_configured) {
    el.innerHTML = `<p class="hint">Filmography needs a TMDB key -- configure one in Settings.</p>`;
    return;
  }
  if (data.items.length === 0) {
    el.innerHTML = `<p class="hint">No credits found.</p>`;
    return;
  }
  el.innerHTML = `<div class="cast-row">${data.items.map((c) => `
    <div class="cast-card" title="${escapeAttr(c.title)}${c.year ? ` (${c.year})` : ""}">
      ${posterMarkup(c.title, c.poster_path)}
      <span class="cast-name">${escapeAttr(c.title)}</span>
      <span class="hint">${c.year || ""}${c.owned ? " · in your library" : ""}</span>
    </div>
  `).join("")}</div>`;
}

async function loadMoreInfo(itemId) {
  if (!$("#detail-more-info")) return;
  try {
    renderMoreInfo(await api(`/api/library/${itemId}/more-info`));
  } catch (e) {
    $("#detail-more-info").innerHTML = "";
  }
}

async function loadMoreInfoByTmdb(tmdbId, mediaType) {
  if (!$("#detail-more-info")) return;
  try {
    renderMoreInfo(await api(`/api/library/more-info?tmdb_id=${tmdbId}&media_type=${mediaType}`));
  } catch (e) {
    $("#detail-more-info").innerHTML = "";
  }
}

function renderRatings(r) {
  const el = $("#detail-ratings");
  if (!el) return;
  if (!r.omdb_configured) {
    el.innerHTML = `<span class="hint">Ratings unavailable — no OMDb API key configured in Settings.</span>`;
    return;
  }
  if (r.imdb_rating == null && !r.rotten_tomatoes) {
    el.innerHTML = `<span class="hint">No ratings found${r.imdb_id ? "" : " (no IMDb match yet)"}.</span>`;
    return;
  }
  const parts = [];
  if (r.imdb_rating != null) parts.push(`IMDb ${r.imdb_rating}/10${r.imdb_votes ? ` (${r.imdb_votes} votes)` : ""}`);
  if (r.rotten_tomatoes) parts.push(`🍅 ${r.rotten_tomatoes}`);
  if (r.metacritic) parts.push(`Metacritic ${r.metacritic}`);
  el.innerHTML = parts.map((p) => `<span class="rating-badge">${p}</span>`).join("");
}

async function loadRatings(itemId) {
  const el = $("#detail-ratings");
  if (!el) return;
  el.textContent = "Loading ratings...";
  try {
    renderRatings(await api(`/api/library/${itemId}/ratings`));
  } catch (e) {
    el.innerHTML = `<span class="hint">Ratings error: ${e.message}</span>`;
  }
}

async function loadRatingsByTmdb(tmdbId, mediaType) {
  const el = $("#detail-ratings");
  if (!el) return;
  el.textContent = "Loading ratings...";
  try {
    renderRatings(await api(`/api/library/ratings?tmdb_id=${tmdbId}&media_type=${mediaType}`));
  } catch (e) {
    el.innerHTML = `<span class="hint">Ratings error: ${e.message}</span>`;
  }
}

async function loadFileInfo(itemId, selector) {
  const el = $(selector);
  if (!el) return;
  el.textContent = "Loading file details…";
  try {
    const info = await api(`/api/library/${itemId}/file-info`);
    if (!info.probe_available) {
      el.innerHTML = `<span class="hint">Duration/codec info unavailable — ffprobe not installed on the server.</span>`;
      return;
    }
    const rows = [];
    if (info.duration_seconds != null) rows.push(["Duration", formatDuration(info.duration_seconds)]);
    if (info.width && info.height) rows.push(["Resolution", `${info.width}×${info.height}`]);
    if (info.video_codec) rows.push(["Video codec", info.video_codec]);
    if (info.audio_codec) rows.push(["Audio codec", info.audio_codec]);
    if (info.bitrate) rows.push(["Bitrate", `${Math.round(info.bitrate / 1000)} kbps`]);
    if (info.container) rows.push(["Container", info.container]);
    el.innerHTML = rows.length
      ? rows.map(([k, v]) => `<div class="detail-file-row"><span>${k}</span><span>${v}</span></div>`).join("")
      : `<span class="hint">No additional details available.</span>`;
  } catch (e) {
    el.innerHTML = `<span class="hint">File info error: ${e.message}</span>`;
  }
}

// Watch-progress marker for a tracked-but-not-yet-archived TV show -- no
// media_items rows/files exist to hang a per-episode "watched" checkbox off
// of (see gallery.js's TV episode toggle for that case), so this is a
// single "watched up through SxxEyy" value stored directly on the tracker
// row (archive_tracker.watched_through_season/episode).
function trackerWatchProgressMarkup(item) {
  const summary = item.watched_through_season != null
    ? `Watched through S${String(item.watched_through_season).padStart(2, "0")}${item.watched_through_episode != null ? `E${String(item.watched_through_episode).padStart(2, "0")}` : ""}`
    : "Not started";
  return `
    <div class="detail-file-info">
      <div class="detail-file-row"><span>Watch progress</span><span id="detail-tracker-progress-summary">${summary}</span></div>
      <div class="tracker-watch-progress-form">
        <label>Season <input type="number" id="detail-tracker-progress-season" min="0" value="${item.watched_through_season ?? ""}"></label>
        <label>Episode <input type="number" id="detail-tracker-progress-episode" min="0" value="${item.watched_through_episode ?? ""}"></label>
        <button id="detail-tracker-progress-save-btn">Save</button>
        ${item.watched_through_season != null ? `<button id="detail-tracker-progress-clear-btn">Clear</button>` : ""}
      </div>
    </div>
  `;
}

function wireTrackerWatchProgress(item) {
  const seasonInput = $("#detail-tracker-progress-season");
  const episodeInput = $("#detail-tracker-progress-episode");

  const save = async (season, episode) => {
    const res = await api(`/api/tracker/${item.id}/watch-progress`, {
      method: "POST",
      body: JSON.stringify({ season, episode }),
    });
    item.watched_through_season = res.tracker.watched_through_season;
    item.watched_through_episode = res.tracker.watched_through_episode;
    renderDetailPane();
    loadTrackerTab();
  };

  $("#detail-tracker-progress-save-btn").addEventListener("click", () => {
    const season = seasonInput.value === "" ? null : Number(seasonInput.value);
    const episode = episodeInput.value === "" ? null : Number(episodeInput.value);
    save(season, episode);
  });
  const clearBtn = $("#detail-tracker-progress-clear-btn");
  if (clearBtn) clearBtn.addEventListener("click", () => save(null, null));
}

// Season/episode summary for a tracked TV show, straight from TMDB/TVmaze
// via the cached getTvStatus(tmdbId) -- unlike the gallery detail pane's
// loadTvStatus, there's no locally-owned `episodes` array to diff against
// here (a tracked title need not be archived at all), so this just reports
// what TMDB knows rather than what's "missing".
async function loadTrackerSeasonInfo(item) {
  const el = $("#detail-tracker-season-info");
  if (!el) return;
  el.innerHTML = `<span class="hint">Loading season info…</span>`;
  const status = await getTvStatus(item.tmdb_id);
  if (!status || !status.data_available) {
    el.innerHTML = `<span class="hint">No TMDB season data available.</span>`;
    return;
  }
  const headerParts = [status.status, status.network].filter(Boolean);
  const seasons = [...status.seasons].sort((a, b) => a.season_number - b.season_number);

  const pane = state.detailPane;
  if (pane.selectedSeason == null || !seasons.some((s) => s.season_number === pane.selectedSeason)) {
    pane.selectedSeason = seasons.length ? seasons[seasons.length - 1].season_number : null;
  }

  el.innerHTML = `
    <div class="detail-file-info">
      ${headerParts.length ? `<div class="detail-file-row"><span>Status</span><span>${escapeAttr(headerParts.join(" · "))}</span></div>` : ""}
      <div class="detail-file-row"><span>Seasons</span><span>${status.latest_known_season ?? "—"}</span></div>
      <div class="detail-file-row"><span>Total episodes</span><span>${status.total_episodes ?? "—"}</span></div>
      ${status.next_episode_air_date ? `<div class="detail-file-row"><span>Next episode</span><span>${escapeAttr(status.next_episode_code || "")} ${status.next_episode_air_date}</span></div>` : ""}
    </div>
    ${seasons.length ? `
      <div class="season-tabs">
        ${seasons.map((s) => `
          <button class="season-tab-btn ${s.season_number === pane.selectedSeason ? "active" : ""}" data-season="${s.season_number}">
            Season ${s.season_number} (${s.episode_count}${s.aired_count != null && s.aired_count < s.episode_count ? `, ${s.aired_count} aired` : ""})
          </button>
        `).join("")}
      </div>
      <div class="detail-episodes" id="detail-tracker-episodes"></div>
    ` : ""}
  `;

  el.querySelectorAll(".season-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      pane.selectedSeason = Number(btn.dataset.season);
      el.querySelectorAll(".season-tab-btn").forEach((b) => b.classList.toggle("active", Number(b.dataset.season) === pane.selectedSeason));
      loadTrackerSeasonEpisodes(item.tmdb_id, pane.selectedSeason);
    });
  });
  if (pane.selectedSeason != null) loadTrackerSeasonEpisodes(item.tmdb_id, pane.selectedSeason);
}

async function loadTrackerSeasonEpisodes(tmdbId, seasonNumber) {
  const el = $("#detail-tracker-episodes");
  if (!el) return;
  el.innerHTML = `<span class="hint">Loading episodes…</span>`;
  try {
    const data = await api(`/api/library/tv-season?tmdb_id=${tmdbId}&season_number=${seasonNumber}`);
    if (!data.data_available || data.episodes.length === 0) {
      el.innerHTML = `<span class="hint">No episode details available for this season.</span>`;
      return;
    }
    el.innerHTML = data.episodes.map((ep) => `
      <div class="detail-episode-row">
        <span>E${String(ep.episode_number).padStart(2, "0")}</span>
        <span class="detail-ep-file hint">${escapeAttr(ep.name || "")}${ep.air_date ? ` · ${ep.air_date}` : ""}</span>
      </div>
    `).join("");
  } catch (e) {
    el.innerHTML = `<span class="hint">Error loading episodes: ${e.message}</span>`;
  }
}

function posterMarkupLarge(title, posterPath) {
  const url = posterUrl(posterPath);
  return url
    ? `<img class="detail-poster" src="${url}" alt="${title}">`
    : `<div class="detail-poster-placeholder">${title}</div>`;
}

function detailTagsMarkup(tags) {
  return `
    <div class="detail-tags">
      <div class="detail-tags-chips">
        ${(tags || []).map((t) => `<span class="tag-chip">${escapeAttr(t)}</span>`).join("") || `<span class="hint">No tags</span>`}
      </div>
      <div class="detail-tags-edit">
        <input type="text" id="detail-tags-input" placeholder="comma, separated, tags" value="${escapeAttr((tags || []).join(", "))}">
        <button id="detail-tags-save-btn">Save Tags</button>
      </div>
      <span id="detail-tags-error" class="hint"></span>
    </div>
  `;
}

function wireDetailTags(ids, onSaved) {
  const btn = $("#detail-tags-save-btn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const raw = $("#detail-tags-input").value;
    const tags = Array.from(new Set(raw.split(",").map((t) => t.trim()).filter(Boolean)));
    const errorEl = $("#detail-tags-error");
    btn.disabled = true;
    errorEl.textContent = "";
    try {
      for (const id of ids) {
        await api(`/api/library/${id}/tags`, { method: "POST", body: JSON.stringify({ tags }) });
      }
      const chips = $(".detail-tags-chips");
      if (chips) chips.innerHTML = tags.map((t) => `<span class="tag-chip">${escapeAttr(t)}</span>`).join("") || `<span class="hint">No tags</span>`;
      onSaved(tags);
    } catch (e) {
      errorEl.textContent = `Failed to save tags: ${e.message}`;
    } finally {
      btn.disabled = false;
    }
  });
}

function detailFixMarkup() {
  return `
    <div class="detail-fix">
      <button id="detail-change-match-btn">Change Match (search TMDB)</button>
      <label>IMDb ID
        <input type="text" id="detail-imdb-input" placeholder="tt1234567">
      </label>
      <button id="detail-fetch-btn">Fetch Metadata from IMDb ID</button>
      <span id="detail-fetch-status" class="hint"></span>
      <details class="detail-custom-title">
        <summary>Not on TMDB? Set a custom title</summary>
        <label>Title
          <input type="text" id="detail-custom-title-input" placeholder="Family Vacation 2019">
        </label>
        <label>Year (optional)
          <input type="number" id="detail-custom-year-input" placeholder="2019">
        </label>
        <button id="detail-custom-title-btn">Set Custom Title</button>
      </details>
    </div>
  `;
}

async function openPaneMatchPicker() {
  const pane = state.detailPane;
  if (!pane) return;
  const mediaType = pane.kind === "movie" ? "movie" : "tv";
  openMatchModal(mediaType, pane.data.title, applyPaneMatchOverride);
}

async function applyPaneMatchOverride(candidate) {
  const pane = state.detailPane;
  closeMatchPicker();
  if (!pane || candidate.tmdb_id == null) return;

  const ids = pane.kind === "movie" ? [pane.data.id] : pane.data.episodes.map((e) => e.id);
  const mediaType = pane.kind === "movie" ? "movie" : "tv";

  $("#detail-fetch-status").textContent = "Applying match...";
  try {
    await api("/api/library/rematch-tmdb", {
      method: "POST",
      body: JSON.stringify({ ids, tmdb_id: candidate.tmdb_id, media_type: mediaType }),
    });
    await reopenDetailPaneAfterRematch(pane, ids);
  } catch (e) {
    $("#detail-fetch-status").textContent = `Error: ${e.message}`;
  }
}

async function reopenDetailPaneAfterRematch(pane, ids) {
  if (pane.kind === "movie") {
    await loadMoviesGallery();
    const updated = state.movieItems.find((i) => i.id === pane.data.id);
    if (updated) openDetailPane("movie", updated);
  } else {
    await loadTvGallery();
    const updated = groupEpisodesByShow(state.tvItems).find((s) => s.episodes.some((e) => ids.includes(e.id)));
    if (updated) openDetailPane("tv", updated);
  }
}

function wireDetailFix() {
  $("#detail-change-match-btn").addEventListener("click", openPaneMatchPicker);
  $("#detail-custom-title-btn").addEventListener("click", async () => {
    const pane = state.detailPane;
    if (!pane) return;
    const title = $("#detail-custom-title-input").value.trim();
    if (!title) return;
    const yearRaw = $("#detail-custom-year-input").value.trim();
    const year = yearRaw ? Number(yearRaw) : null;

    const ids = pane.kind === "movie" ? [pane.data.id] : pane.data.episodes.map((e) => e.id);
    $("#detail-fetch-status").textContent = "Setting custom title...";
    try {
      for (const id of ids) {
        await api(`/api/library/${id}/override`, { method: "POST", body: JSON.stringify({ title, year }) });
      }
      $("#detail-fetch-status").textContent = "Updated.";
      await reopenDetailPaneAfterRematch(pane, ids);
    } catch (e) {
      $("#detail-fetch-status").textContent = `Error: ${e.message}`;
    }
  });
  $("#detail-fetch-btn").addEventListener("click", async () => {
    const pane = state.detailPane;
    if (!pane) return;
    const imdbId = $("#detail-imdb-input").value.trim();
    if (!imdbId) return;

    const ids = pane.kind === "movie" ? [pane.data.id] : pane.data.episodes.map((e) => e.id);
    const mediaType = pane.kind === "movie" ? "movie" : "tv";

    $("#detail-fetch-status").textContent = "Fetching...";
    try {
      await api("/api/library/rematch-imdb", {
        method: "POST",
        body: JSON.stringify({ ids, imdb_id: imdbId, media_type: mediaType }),
      });
      $("#detail-fetch-status").textContent = "Updated.";
      await reopenDetailPaneAfterRematch(pane, ids);
    } catch (e) {
      $("#detail-fetch-status").textContent = `Error: ${e.message}`;
    }
  });
}

// Guards the click that opens the pane (e.g. a gallery card click) from
// also bubbling to the document listener below and immediately closing it.
let justOpened = false;

export function openDetailPane(kind, data) {
  state.detailPane = { kind, data };
  renderDetailPane();
  $("#detail-pane").classList.remove("hidden");
  justOpened = true;
  setTimeout(() => { justOpened = false; }, 0);
}

export function closeDetailPane() {
  state.detailPane = null;
  $("#detail-pane").classList.add("hidden");
}

document.addEventListener("click", (e) => {
  if (!state.detailPane || justOpened) return;
  const pane = $("#detail-pane");
  if (pane && !pane.classList.contains("hidden") && !pane.contains(e.target)) closeDetailPane();
});
