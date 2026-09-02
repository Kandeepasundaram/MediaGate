/**
 * Reports tab: periodic (quarterly/half-yearly/yearly/custom) library
 * growth, watch activity, and genre/resolution breakdown -- all computed
 * client-side into a plain start/end date range, then handed to
 * GET /api/reports/summary, which knows nothing about calendar quarters.
 */
import { escapeAttr, showConfirm } from "./archive-tab.js";
import { $, api, formatBytes } from "./core.js";
import {
  applyFilterPreset, deleteFilterPreset, downloadCsv, populatePresetSelect, rowsToCsv, saveFilterPreset,
} from "./gallery.js";
import { formatDuration } from "./stats-tab.js";

let lastReport = null;

// Saved report periods (period dropdown + custom start/end) -- same named
// localStorage-preset mechanism the Movies/TV tabs use for saved filter
// views (see gallery.js), just applied to report-period/-start-date/-end-date.
const REPORT_PRESET_IDS = ["report-period", "report-start-date", "report-end-date"];

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}

function quarterRange(year, quarter) {
  const startMonth = (quarter - 1) * 3;
  const start = new Date(Date.UTC(year, startMonth, 1));
  const end = new Date(Date.UTC(year, startMonth + 3, 0));
  return { start, end };
}

function halfRange(year, half) {
  const startMonth = half === 1 ? 0 : 6;
  const start = new Date(Date.UTC(year, startMonth, 1));
  const end = new Date(Date.UTC(year, startMonth + 6, 0));
  return { start, end };
}

// Named period -> {start, end} Date, all computed from "now" so "This
// Quarter"/"Last Quarter"/etc. never need updating as time passes.
function rangeForPeriod(period) {
  const now = new Date();
  const currentQuarter = Math.floor(now.getUTCMonth() / 3) + 1;
  const currentHalf = now.getUTCMonth() < 6 ? 1 : 2;

  switch (period) {
    case "this-quarter":
      return quarterRange(now.getUTCFullYear(), currentQuarter);
    case "last-quarter": {
      const q = currentQuarter === 1 ? 4 : currentQuarter - 1;
      const y = currentQuarter === 1 ? now.getUTCFullYear() - 1 : now.getUTCFullYear();
      return quarterRange(y, q);
    }
    case "this-half":
      return halfRange(now.getUTCFullYear(), currentHalf);
    case "last-half": {
      const h = currentHalf === 1 ? 2 : 1;
      const y = currentHalf === 1 ? now.getUTCFullYear() - 1 : now.getUTCFullYear();
      return halfRange(y, h);
    }
    case "this-year":
      return { start: new Date(Date.UTC(now.getUTCFullYear(), 0, 1)), end: new Date(Date.UTC(now.getUTCFullYear(), 11, 31)) };
    case "last-year":
      return { start: new Date(Date.UTC(now.getUTCFullYear() - 1, 0, 1)), end: new Date(Date.UTC(now.getUTCFullYear() - 1, 11, 31)) };
    default:
      return null;
  }
}

function updateCustomRangeVisibility() {
  const period = $("#report-period").value;
  $("#report-custom-range").classList.toggle("hidden", period !== "custom");
}

export function setupReportsTab() {
  $("#report-period").addEventListener("change", updateCustomRangeVisibility);
  updateCustomRangeVisibility();
  $("#report-generate-btn").addEventListener("click", generateReport);
  $("#report-export-csv-btn").addEventListener("click", exportReportCsv);
  $("#report-print-btn").addEventListener("click", () => window.print());

  $("#report-preset-select").addEventListener("change", (e) => {
    if (!e.target.value) return;
    applyFilterPreset("report", e.target.value, REPORT_PRESET_IDS);
    updateCustomRangeVisibility();
    generateReport();
  });
  $("#report-preset-save-btn").addEventListener("click", () => {
    const name = window.prompt("Save current period as:");
    if (name) saveFilterPreset("report", name, REPORT_PRESET_IDS);
  });
  $("#report-preset-delete-btn").addEventListener("click", async () => {
    const name = $("#report-preset-select").value;
    if (!name) return;
    const ok = await showConfirm(`Delete saved period "${name}"?`);
    if (ok) deleteFilterPreset("report", name);
  });
  populatePresetSelect("report");
}

// Flat "section,metric,value" rows -- one CSV covering every card rather
// than a table per section, since the sections have unrelated shapes
// (scalars, per-viewer rows, genre/resolution/month breakdowns) and a
// single flat sheet is easier to paste into a spreadsheet than several.
function exportReportCsv() {
  if (!lastReport) return;
  const data = lastReport;
  const g = data.growth;
  const w = data.watch_activity;
  const t = data.tracker_activity;
  const rows = [
    ["Growth", "Movies added", g.movies_added],
    ["Growth", "TV episodes added", g.tv_episodes_added],
    ["Growth", "Total size added (bytes)", g.total_size_bytes_added],
    ["Watch Activity", "Movies watched", w.movies_watched],
    ["Watch Activity", "TV episodes watched", w.tv_episodes_watched],
    ["Tracker Activity", "Notifications sent", t.notifications_sent],
    ["Tracker Activity", "Movies notified", t.movies_notified],
    ["Tracker Activity", "TV shows notified", t.tv_shows_notified],
  ];
  for (const v of w.by_viewer) {
    rows.push([`Watch Activity / ${v.viewer_name}`, "Items watched", v.count]);
    rows.push([`Watch Activity / ${v.viewer_name}`, "Watch seconds", v.watch_seconds]);
  }
  for (const gen of data.insights.top_genres) {
    rows.push(["Genres", gen.genre, gen.count]);
  }
  for (const res of data.insights.resolution_breakdown) {
    rows.push(["Resolution", res.resolution, res.count]);
  }
  for (const m of data.insights.growth_by_month) {
    rows.push(["Growth by Month", m.month, m.count]);
  }
  const mb = data.metadata_backlog;
  rows.push(["Metadata Backlog", "Pending movies", mb.pending_movies]);
  rows.push(["Metadata Backlog", "Pending TV episodes", mb.pending_tv]);
  rows.push(["Metadata Backlog", "Failed-match movies", mb.failed_movies]);
  rows.push(["Metadata Backlog", "Failed-match TV episodes", mb.failed_tv]);
  const c = data.cleanup_activity;
  rows.push(["Cleanup Activity", "Files deleted", c.deleted_count]);
  rows.push(["Cleanup Activity", "Failed deletes", c.failed_count]);

  const cp = data.content_profile;
  rows.push(["Content Profile", "Avg file size (bytes)", cp.avg_file_size_bytes]);
  rows.push(["Content Profile", "Median file size (bytes)", cp.median_file_size_bytes]);
  rows.push(["Content Profile", "Largest title", cp.largest_title ?? ""]);
  rows.push(["Content Profile", "Largest file (bytes)", cp.largest_size_bytes]);
  rows.push(["Content Profile", "Avg release year", cp.avg_release_year ?? ""]);

  const mq = data.match_quality;
  rows.push(["Match Quality", "Matched", mq.matched_count]);
  rows.push(["Match Quality", "Unmatched", mq.unmatched_count]);
  rows.push(["Match Quality", "Match rate (%)", mq.match_rate_pct ?? ""]);
  rows.push(["Match Quality", "Manual overrides", mq.manual_override_count]);
  rows.push(["Match Quality", "IMDb-linked", mq.imdb_linked_count]);

  const ua = data.universe_activity;
  rows.push(["Universe Activity", "Titles added", ua.titles_added_count]);
  for (const title of ua.titles) rows.push(["Universe Activity", "Title", title]);

  for (const p of data.storage_trend.paths) {
    rows.push([`Storage Trend / ${p.label}`, "Start used (bytes)", p.start_used_bytes]);
    rows.push([`Storage Trend / ${p.label}`, "End used (bytes)", p.end_used_bytes]);
    rows.push([`Storage Trend / ${p.label}`, "Delta (bytes)", p.delta_bytes]);
  }

  const bl = data.backlog;
  rows.push(["Backlog", "Unwatched items", bl.unwatched_count]);
  rows.push(["Backlog", "Unwatched size (bytes)", bl.unwatched_size_bytes]);

  const eg = data.engagement;
  rows.push(["Engagement", "Distinct active viewers", eg.distinct_active_viewers]);
  for (const tag of eg.top_tags) rows.push(["Engagement / Top Tags", tag.genre, tag.count]);

  const oh = data.operations_health;
  rows.push(["Operations Health", "Succeeded", oh.succeeded]);
  rows.push(["Operations Health", "Failed", oh.failed]);
  rows.push(["Operations Health", "Success rate (%)", oh.success_rate_pct ?? ""]);

  const ta = data.tracker_activity;
  rows.push(["Tracker Activity", "New trackers added", ta.new_trackers_added]);
  rows.push(["Tracker Activity", "New watching", ta.new_trackers_watching]);
  rows.push(["Tracker Activity", "New interested", ta.new_trackers_interested]);
  rows.push(["Tracker Activity", "New watched (history)", ta.new_trackers_watched]);
  rows.push(["Tracker Activity", "Muted trackers (current)", ta.muted_trackers_total]);
  rows.push(["Tracker Activity", "Tracker checks run", ta.tracker_checks_run]);

  const csv = rowsToCsv(["Section", "Metric", "Value"], rows);
  downloadCsv(`report-${data.start_date}-to-${data.end_date}.csv`, csv);
}

// Delta badge vs. the same-length previous period -- "+12%", "-3", or "new"
// when the previous period had zero (percentage would be undefined/infinite).
function deltaBadge(current, previous) {
  if (previous === 0) {
    return current === 0 ? "" : ` <span class="hint delta-up">(new)</span>`;
  }
  const diff = current - previous;
  if (diff === 0) return ` <span class="hint">(flat)</span>`;
  const pct = Math.round((diff / previous) * 100);
  const cls = diff > 0 ? "delta-up" : "delta-down";
  const sign = diff > 0 ? "+" : "";
  return ` <span class="hint ${cls}">(${sign}${pct}% vs prior period)</span>`;
}

function barRows(items, labelKey, countKey, suffix = "") {
  if (!items.length) return "";
  const max = Math.max(1, ...items.map((i) => i[countKey]));
  return items.map((i) => `
    <div class="storage-row"><span>${escapeAttr(String(i[labelKey]))}</span><span class="hint">${i[countKey]}${suffix}</span></div>
    <div class="storage-bar"><div class="storage-bar-fill" style="width:${Math.round((i[countKey] / max) * 100)}%"></div></div>
  `).join("");
}

async function generateReport() {
  const period = $("#report-period").value;
  const output = $("#report-output");

  let start, end;
  if (period === "custom") {
    const startVal = $("#report-start-date").value;
    const endVal = $("#report-end-date").value;
    if (!startVal || !endVal) {
      output.innerHTML = `<p class="hint">Pick both a start and end date for a custom range.</p>`;
      return;
    }
    start = startVal;
    end = endVal;
  } else {
    const range = rangeForPeriod(period);
    start = isoDate(range.start);
    end = isoDate(range.end);
  }

  output.innerHTML = "Generating report...";
  $("#report-export-csv-btn").classList.add("hidden");
  $("#report-print-btn").classList.add("hidden");
  try {
    const data = await api(`/api/reports/summary?start=${start}&end=${end}`);
    lastReport = data;
    output.innerHTML = renderReport(data);
    $("#report-export-csv-btn").classList.remove("hidden");
    $("#report-print-btn").classList.remove("hidden");
  } catch (e) {
    lastReport = null;
    output.innerHTML = `<p>Error generating report: ${e.message}</p>`;
  }
}

function renderReport(data) {
  const g = data.growth;
  const w = data.watch_activity;
  const t = data.tracker_activity;
  const p = data.previous_period;
  const cp = data.content_profile;
  const mq = data.match_quality;
  const ua = data.universe_activity;
  const bl = data.backlog;
  const eg = data.engagement;
  const oh = data.operations_health;
  const tagRows = barRows(eg.top_tags, "genre", "count");
  const storageRows = data.storage_trend.paths.map((sp) => `
    <div class="storage-row"><span>${escapeAttr(sp.label)}</span><span class="hint">${formatBytes(sp.start_used_bytes)} → ${formatBytes(sp.end_used_bytes)} (${sp.delta_bytes >= 0 ? "+" : "-"}${formatBytes(Math.abs(sp.delta_bytes))})</span></div>
  `).join("");
  const prevNote = p
    ? `<p class="hint">vs. ${p.start_date} to ${p.end_date}</p>`
    : "";
  const trackerTitles = t.titles.length
    ? `<p class="hint">${t.titles.map(escapeAttr).join(", ")}</p>`
    : `<p class="hint">No tracker notifications in this period.</p>`;
  const viewerRows = w.by_viewer.length
    ? `<table class="insights-table">
        <thead><tr><th>Viewer</th><th>Watched</th><th>Watch time</th></tr></thead>
        <tbody>${w.by_viewer.map((v) => `<tr><td>${escapeAttr(v.viewer_name)}</td><td>${v.count}</td><td>${v.watch_seconds > 0 ? formatDuration(v.watch_seconds) : "—"}</td></tr>`).join("")}</tbody>
      </table>`
    : `<p class="hint">No per-viewer watch state recorded for this period.</p>`;

  const genreRows = barRows(data.insights.top_genres, "genre", "count");
  const growthRows = barRows(data.insights.growth_by_month, "month", "count", " added");
  const resolutionRows = data.insights.resolution_breakdown.length
    ? `<table class="insights-table">
        <thead><tr><th>Resolution</th><th>Count</th><th>Avg size</th></tr></thead>
        <tbody>${data.insights.resolution_breakdown.map((r) => `<tr><td>${r.resolution}</td><td>${r.count}</td><td>${formatBytes(r.avg_size_bytes)}</td></tr>`).join("")}</tbody>
      </table>`
    : "";

  const mb = data.metadata_backlog;
  const cleanup = data.cleanup_activity;
  const cleanupPaths = cleanup.deleted_paths.length
    ? `<p class="hint">${cleanup.deleted_paths.map(escapeAttr).join(", ")}</p>`
    : "";

  return `
    <h4>Report: ${data.start_date} to ${data.end_date}</h4>

    <div class="card">
      <h5>Library Growth</h5>
      ${prevNote}
      <p>Movies added: <strong>${g.movies_added}</strong>${p ? deltaBadge(g.movies_added, p.movies_added) : ""}</p>
      <p>TV episodes added: <strong>${g.tv_episodes_added}</strong>${p ? deltaBadge(g.tv_episodes_added, p.tv_episodes_added) : ""}</p>
      <p>Total size added: <strong>${formatBytes(g.total_size_bytes_added)}</strong>${p ? deltaBadge(g.total_size_bytes_added, p.total_size_bytes_added) : ""}</p>
    </div>

    <div class="card">
      <h5>Watch Activity</h5>
      ${prevNote}
      <p>Movies watched: <strong>${w.movies_watched}</strong>${p ? deltaBadge(w.movies_watched, p.movies_watched) : ""}</p>
      <p>TV episodes watched: <strong>${w.tv_episodes_watched}</strong>${p ? deltaBadge(w.tv_episodes_watched, p.tv_episodes_watched) : ""}</p>
      ${viewerRows}
    </div>

    <div class="card">
      <h5>Tracker Activity</h5>
      ${prevNote}
      <p>Notifications sent: <strong>${t.notifications_sent}</strong>${p ? deltaBadge(t.notifications_sent, p.notifications_sent) : ""} (${t.movies_notified} movie(s), ${t.tv_shows_notified} show(s))</p>
      ${trackerTitles}
      <p>New trackers added: <strong>${t.new_trackers_added}</strong> (${t.new_trackers_watching} watching, ${t.new_trackers_interested} interested, ${t.new_trackers_watched} watched)</p>
      <p>Tracker checks run: <strong>${t.tracker_checks_run}</strong></p>
      <p class="hint">Muted trackers (current, not period-scoped): ${t.muted_trackers_total}</p>
    </div>

    <div class="card">
      <h5>Genres (this period)</h5>
      ${genreRows || `<p class="hint">No genre data for this period.</p>`}
      <h5>Resolution Breakdown</h5>
      ${resolutionRows || `<p class="hint">No resolution data for this period.</p>`}
      <h5>By Month</h5>
      ${growthRows || `<p class="hint">No archive activity in this period.</p>`}
    </div>

    <div class="card">
      <h5>Content Profile</h5>
      <p class="hint">Shape of what was added in this period.</p>
      <p>Avg file size: <strong>${formatBytes(cp.avg_file_size_bytes)}</strong> &middot; Median: <strong>${formatBytes(cp.median_file_size_bytes)}</strong></p>
      <p>Largest file added: <strong>${cp.largest_title ? escapeAttr(cp.largest_title) : "—"}</strong>${cp.largest_title ? ` (${formatBytes(cp.largest_size_bytes)})` : ""}</p>
      <p>Avg release year: <strong>${cp.avg_release_year ?? "—"}</strong></p>
    </div>

    <div class="card">
      <h5>Match Quality</h5>
      <p class="hint">TMDB match health of items added in this period.</p>
      <p>Matched: <strong>${mq.matched_count}</strong> / Unmatched: <strong>${mq.unmatched_count}</strong>${mq.match_rate_pct !== null ? ` (${mq.match_rate_pct}% match rate)` : ""}</p>
      <p>Manual overrides: <strong>${mq.manual_override_count}</strong> &middot; IMDb-linked: <strong>${mq.imdb_linked_count}</strong></p>
    </div>

    <div class="card">
      <h5>Universe Activity</h5>
      <p>Titles added to a franchise/universe: <strong>${ua.titles_added_count}</strong></p>
      ${ua.titles.length ? `<p class="hint">${ua.titles.map(escapeAttr).join(", ")}</p>` : `<p class="hint">No universe additions in this period.</p>`}
    </div>

    <div class="card">
      <h5>Storage Trend</h5>
      <p class="hint">From daily snapshots recorded while the dashboard was open (Settings/status page) -- empty if none fell in this period.</p>
      ${storageRows || `<p class="hint">No storage snapshots in this period.</p>`}
    </div>

    <div class="card">
      <h5>Unwatched Backlog</h5>
      <p class="hint">Current, not scoped to the period above.</p>
      <p>Unwatched items: <strong>${bl.unwatched_count}</strong> &middot; Size: <strong>${formatBytes(bl.unwatched_size_bytes)}</strong></p>
    </div>

    <div class="card">
      <h5>Engagement</h5>
      <p>Distinct active viewers this period: <strong>${eg.distinct_active_viewers}</strong></p>
      <h5>Top Tags (items added)</h5>
      ${tagRows || `<p class="hint">No tagged items added in this period.</p>`}
    </div>

    <div class="card">
      <h5>Operations Health</h5>
      <p class="hint">Archive + organize operations logged in this period.</p>
      <p>Succeeded: <strong>${oh.succeeded}</strong> &middot; Failed: <strong>${oh.failed}</strong>${oh.success_rate_pct !== null ? ` (${oh.success_rate_pct}% success rate)` : ""}</p>
    </div>

    <div class="card">
      <h5>Metadata Backlog</h5>
      <p class="hint">Current TMDB match backlog as of report generation -- not scoped to the period above.</p>
      <p>Pending movies: <strong>${mb.pending_movies}</strong> (${mb.failed_movies} failed match)</p>
      <p>Pending TV episodes: <strong>${mb.pending_tv}</strong> (${mb.failed_tv} failed match)</p>
    </div>

    <div class="card">
      <h5>Cleanup Activity</h5>
      ${prevNote}
      <p>Files deleted: <strong>${cleanup.deleted_count}</strong>${cleanup.failed_count ? ` <span class="hint delta-down">(${cleanup.failed_count} failed)</span>` : ""}</p>
      ${cleanupPaths}
    </div>
  `;
}
