/**
 * Settings tab's Stats & Insights panel: library stats, genre/resolution/growth charts, background-task status, storage forecast.
 */

import { escapeAttr } from "./archive-tab.js";
import { $, api, formatBytes } from "./core.js";

// ---- Settings/stats tab ----
export function formatDuration(seconds) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

export async function loadStats() {
  const card = $("#stats-card");
  card.textContent = "Loading...";
  try {
    const [stats, health] = await Promise.all([api("/api/stats"), api("/api/status")]);
    card.innerHTML = `
      <p>Total media items: <strong>${stats.total_media_items}</strong></p>
      <p>Movies: <strong>${stats.total_movies}</strong></p>
      <p>TV episodes: <strong>${stats.total_tv_episodes}</strong></p>
      <p>Total archived size: <strong>${formatBytes(stats.total_size_bytes)}</strong>
        (movies: ${formatBytes(stats.movies_size_bytes)}, TV: ${formatBytes(stats.tv_size_bytes)})</p>
      <p class="hint">
        Server uptime: ${formatDuration(health.uptime_seconds)}
        · Database: ${formatBytes(health.database_size_bytes)}
        · ffprobe: ${health.ffprobe_available ? "available" : "not installed"}
        ${health.next_tracker_check_in_seconds != null ? `· Next tracker check in ${formatDuration(health.next_tracker_check_in_seconds)}` : ""}
      </p>
    `;
  } catch (e) {
    card.textContent = `Error: ${e.message}`;
  }
}

// Plain inline SVG polyline -- no charting library needed for one line.
// viewBox-scaled so it stays crisp at any container width via CSS.
function sparklineSvg(values, width = 280, height = 48) {
  if (values.length < 2) return "";
  const max = Math.max(1, ...values);
  const stepX = width / (values.length - 1);
  const points = values.map((v, i) => `${(i * stepX).toFixed(1)},${(height - (v / max) * height).toFixed(1)}`);
  const areaPoints = `0,${height} ${points.join(" ")} ${width},${height}`;
  return `
    <svg class="growth-sparkline" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Library growth trend over the last 12 months">
      <polygon points="${areaPoints}" class="growth-sparkline-area"></polygon>
      <polyline points="${points.join(" ")}" class="growth-sparkline-line"></polyline>
    </svg>
  `;
}

export async function loadInsights() {
  const card = $("#insights-card");
  card.textContent = "Loading...";
  try {
    const data = await api("/api/stats/insights");
    const maxGenreCount = Math.max(1, ...data.top_genres.map((g) => g.count));
    const genreRows = data.top_genres.length
      ? data.top_genres.map((g) => `
          <div class="storage-row"><span>${escapeAttr(g.genre)}</span><span class="hint">${g.count}</span></div>
          <div class="storage-bar"><div class="storage-bar-fill" style="width:${Math.round((g.count / maxGenreCount) * 100)}%"></div></div>
        `).join("")
      : `<p class="hint">No genre data yet.</p>`;

    const resolutionRows = data.resolution_breakdown.length
      ? `<table class="insights-table">
          <thead><tr><th>Resolution</th><th>Count</th><th>Avg size</th></tr></thead>
          <tbody>${data.resolution_breakdown.map((r) => `
            <tr><td>${r.resolution}</td><td>${r.count}</td><td>${formatBytes(r.avg_size_bytes)}</td></tr>
          `).join("")}</tbody>
        </table>`
      : `<p class="hint">No resolution data yet -- open a title's file details to probe it.</p>`;

    const recentGrowth = data.growth_by_month.slice(-12);
    const maxGrowthCount = Math.max(1, ...recentGrowth.map((m) => m.count));
    const growthSparkline = sparklineSvg(recentGrowth.map((m) => m.count));
    const growthRows = data.growth_by_month.length
      ? recentGrowth.map((m) => `
          <div class="storage-row"><span>${m.month}</span><span class="hint">${m.count} added</span></div>
          <div class="storage-bar"><div class="storage-bar-fill" style="width:${Math.round((m.count / maxGrowthCount) * 100)}%"></div></div>
        `).join("")
      : `<p class="hint">No archive activity yet.</p>`;

    card.innerHTML = `
      <h4>Insights</h4>
      <h5>Top Genres</h5>
      ${genreRows}
      <h5>Average Size by Resolution</h5>
      ${resolutionRows}
      <h5>Library Growth (last 12 months)</h5>
      ${growthSparkline}
      ${growthRows}
    `;
  } catch (e) {
    card.textContent = `Error loading insights: ${e.message}`;
  }
}

function taskStatusLine(label, task) {
  if (task.last_run_at == null) return `${label}: no run recorded yet since last restart`;
  const when = new Date(task.last_run_at).toLocaleString();
  return task.last_error
    ? `${label}: failed at ${when} -- ${task.last_error}`
    : `${label}: last ran ${when}`;
}

export async function loadBackgroundTaskStatus() {
  const card = $("#background-tasks-card");
  card.textContent = "Loading...";
  try {
    const t = await api("/api/status/tasks");
    const lines = [
      t.tracker.last_check_at
        ? `Tracker check: last ran ${new Date(t.tracker.last_check_at).toLocaleString()} (${t.tracker.last_check_status})`
        : "Tracker check: no run recorded yet",
      `Metadata backfill: ${t.backfill.pending} pending, ${t.backfill.failed} failed to match`,
      t.backup.enabled ? taskStatusLine("Backup", t.backup) : "Backup: disabled",
      taskStatusLine("Maintenance", t.maintenance),
    ];
    card.innerHTML = `<h4>Background Tasks</h4>${lines.map((l) => `<p class="hint">${l}</p>`).join("")}`;
  } catch (e) {
    card.textContent = `Error loading task status: ${e.message}`;
  }
}

export async function loadStorageStatus() {
  const card = $("#storage-card");
  card.textContent = "Loading...";
  try {
    const data = await api("/api/status/storage");
    const rows = data.paths.map((p) => {
      if (!p.exists) {
        return `<div class="storage-row"><span>${p.label}</span><span class="hint">${p.path} — does not exist</span></div>`;
      }
      const pct = p.total_bytes ? Math.round((p.used_bytes / p.total_bytes) * 100) : 0;
      const forecast = p.days_to_full != null
        ? ` · <span class="${p.days_to_full <= 30 ? 'status-warning' : ''}">~${Math.round(p.days_to_full)} day(s) to full</span>`
        : (p.history_days < 2 ? " · building forecast (needs 2+ days of history)" : "");
      return `
        <div class="storage-row">
          <span>${p.label}</span>
          <span class="hint">${formatBytes(p.used_bytes)} used of ${formatBytes(p.total_bytes)} (${formatBytes(p.free_bytes)} free)${forecast}</span>
        </div>
        <div class="storage-bar"><div class="storage-bar-fill" style="width:${pct}%"></div></div>
      `;
    });
    card.innerHTML = `<h4>Storage</h4>${rows.join("")}`;
  } catch (e) {
    card.textContent = `Error loading storage status: ${e.message}`;
  }
}
