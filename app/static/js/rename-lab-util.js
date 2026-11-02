// Pure helpers for the Rename Lab page (no DOM, no network) so they can be
// unit-checked in isolation. Mirrors the server-side rules in
// app/core/renamer.py closely enough for live previews; the server stays
// the source of truth for the real rename plan.

const INVALID_CHARS = /[<>:"/\\|?*\x00-\x1f]/g;

export function sanitizeName(name) {
  const cleaned = String(name).replace(INVALID_CHARS, "").trim().replace(/\.+$/, "");
  return cleaned || "untitled";
}

export function sepOf(path) {
  return path.includes("\\") ? "\\" : "/";
}

export function basename(path) {
  return path.split(/[\\/]/).pop();
}

export function dirname(path) {
  const i = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
  return i < 0 ? "" : path.slice(0, i);
}

export function joinLike(samplePath, dir, name) {
  return `${dir}${sepOf(samplePath)}${name}`;
}

export function splitExt(fileName) {
  const i = fileName.lastIndexOf(".");
  return i <= 0 ? { stem: fileName, ext: "" } : { stem: fileName.slice(0, i), ext: fileName.slice(i) };
}

// "Show - S01E01.mkv" -> "Show - S01E01 (2).mkv" (same scheme as renamer._avoid_collision).
export function suffixedName(fileName, n) {
  const { stem, ext } = splitExt(fileName);
  return `${stem} (${n})${ext}`;
}

// Subset of Python's str.format used by the naming templates: {token} and
// {token:0Nd} / {token:d}. Throws Error(message) for anything else.
export function formatTemplate(template, ctx) {
  const escaped = template.replace(/\{\{/g, "\u0001").replace(/\}\}/g, "\u0002");
  const out = escaped.replace(/\{([A-Za-z_][A-Za-z0-9_]*)(?::([^{}]*))?\}/g, (_, token, spec) => {
    if (!(token in ctx)) throw new Error(`unknown token {${token}}`);
    const value = ctx[token];
    if (spec === undefined || spec === "") return String(value);
    const m = /^(0?)(\d*)d$/.exec(spec);
    if (!m) throw new Error(`unsupported format {${token}:${spec}}`);
    if (typeof value !== "number") throw new Error(`{${token}} is not a number`);
    const width = m[2] ? Number(m[2]) : 0;
    return m[1] ? String(value).padStart(width, "0") : String(value).padStart(width, " ");
  });
  if (/[{}]/.test(out)) throw new Error("unbalanced braces");
  return out.replace(/\u0001/g, "{").replace(/\u0002/g, "}");
}

// Token context for one simulated row -- same keys as plan_tv_rename's.
export function episodeContext(showTitle, row, titleOverride) {
  const title = titleOverride !== undefined ? titleOverride : row.episode_title;
  const clean = title ? sanitizeName(title) : "";
  return {
    show_name: sanitizeName(showTitle),
    season: row.season,
    episode: row.episode,
    code: row.code,
    episode_title: clean,
    episode_title_suffix: clean ? ` - ${clean}` : "",
    absolute_episode: row.absolute_episode != null ? row.absolute_episode : row.episode,
    part_suffix: "",
    air_date: row.air_date || "",
  };
}

// New file name (with extension) for a row under a template, or throws.
export function renderFileName(template, showTitle, row, titleOverride, ext) {
  return sanitizeName(formatTemplate(template, episodeContext(showTitle, row, titleOverride))) + ext;
}

export function renderSeasonFolder(template, showTitle, row) {
  return sanitizeName(formatTemplate(template, episodeContext(showTitle, row)));
}

// Longest common prefix/suffix diff, returns [prefix, changedMiddle, suffix] of newName.
export function diffParts(oldName, newName) {
  let p = 0;
  const maxP = Math.min(oldName.length, newName.length);
  while (p < maxP && oldName[p] === newName[p]) p++;
  let s = 0;
  const maxS = maxP - p;
  while (s < maxS && oldName[oldName.length - 1 - s] === newName[newName.length - 1 - s]) s++;
  return [newName.slice(0, p), newName.slice(p, newName.length - s), newName.slice(newName.length - s)];
}

export const TOKEN_HINTS = {
  show_name: "Show title (TMDB)",
  season: "Season number",
  "season:02d": "Season number, zero-padded to 2 digits",
  episode: "Episode number within the season",
  "episode:02d": "Episode number, zero-padded to 2 digits",
  code: "S01E02 style code",
  episode_title: "Episode title (empty when unknown)",
  episode_title_suffix: "' - Title', or nothing when there is no title",
  absolute_episode: "Cumulative episode number across seasons",
  "absolute_episode:03d": "Cumulative episode number, 3 digits",
  part_suffix: "' - CD1' style suffix for multi-part rips",
  air_date: "Episode air date (YYYY-MM-DD)",
};
