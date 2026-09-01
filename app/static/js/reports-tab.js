/**
 * Reports tab: periodic (quarterly/half-yearly/yearly/custom) library
 * growth, watch activity, and genre/resolution breakdown -- all computed
 * client-side into a plain start/end date range, then handed to
 * GET /api/reports/summary, which knows nothing about calendar quarters.
 */
import { escapeAttr } from "./archive-tab.js";
import { $, api, formatBytes } from "./core.js";

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
  try {
    const data = await api(`/api/reports/summary?start=${start}&end=${end}`);
    output.innerHTML = renderReport(data);
  } catch (e) {
    output.innerHTML = `<p>Error generating report: ${e.message}</p>`;
  }
}

function renderReport(data) {
  const g = data.growth;
  const w = data.watch_activity;
  const t = data.tracker_activity;
  const trackerTitles = t.titles.length
    ? `<p class="hint">${t.titles.map(escapeAttr).join(", ")}</p>`
    : `<p class="hint">No tracker notifications in this period.</p>`;
  const viewerRows = w.by_viewer.length
    ? `<table class="insights-table">
        <thead><tr><th>Viewer</th><th>Watched</th></tr></thead>
        <tbody>${w.by_viewer.map((v) => `<tr><td>${escapeAttr(v.viewer_name)}</td><td>${v.count}</td></tr>`).join("")}</tbody>
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

  return `
    <h4>Report: ${data.start_date} to ${data.end_date}</h4>

    <div class="card">
      <h5>Library Growth</h5>
      <p>Movies added: <strong>${g.movies_added}</strong></p>
      <p>TV episodes added: <strong>${g.tv_episodes_added}</strong></p>
      <p>Total size added: <strong>${formatBytes(g.total_size_bytes_added)}</strong></p>
    </div>

    <div class="card">
      <h5>Watch Activity</h5>
      <p>Movies watched: <strong>${w.movies_watched}</strong></p>
      <p>TV episodes watched: <strong>${w.tv_episodes_watched}</strong></p>
      ${viewerRows}
    </div>

    <div class="card">
      <h5>Tracker Activity</h5>
      <p>Notifications sent: <strong>${t.notifications_sent}</strong> (${t.movies_notified} movie(s), ${t.tv_shows_notified} show(s))</p>
      ${trackerTitles}
    </div>

    <div class="card">
      <h5>Genres (this period)</h5>
      ${genreRows || `<p class="hint">No genre data for this period.</p>`}
      <h5>Resolution Breakdown</h5>
      ${resolutionRows || `<p class="hint">No resolution data for this period.</p>`}
      <h5>By Month</h5>
      ${growthRows || `<p class="hint">No archive activity in this period.</p>`}
    </div>
  `;
}
