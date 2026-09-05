/**
 * Shared state object, DOM/format micro-helpers, and the api() fetch wrapper (with the optional API-token prompt/retry). Everything else imports from here.
 */

export const state = {
  previewItems: [],
  sizeByPath: {},
  previewMode: "archive", // "archive" (copy, from Ready to Archive) or "organize" (move in place, from Browse)
  movieItems: [],
  tvItems: [],
  matchPicker: null, // { mediaType, onApply } for the current match-modal search
  tvStatusCache: {}, // tmdb_id -> TvStatusOut (or null on failure) -- shared by gallery badges and the detail pane banner
  movieStatusCache: {}, // tmdb_id -> MovieStatusOut (or null on failure) -- shared by gallery badges and the detail pane banner
  pendingGenreRestore: { movies: null, tv: null }, // saved genre filter value applied on the first gallery load only
  pendingYearRestore: { movies: null, tv: null }, // saved year filter value applied on the first gallery load only
  pendingTagRestore: { movies: null, tv: null }, // saved tag filter value applied on the first gallery load only
  pendingCollectionRestore: { movies: null, tv: null }, // saved collection filter value applied on the first gallery load only
  moviesRenderLimit: 60,
  moviesFilterSignature: "",
  tvRenderLimit: 60,
  tvFilterSignature: "",
  activeUniverseType: "movie", // "movie" | "tv" -- which sub-tab the Tracker tab is showing
  universeMemberIds: new Set(), // tmdb_ids already in a universe of activeUniverseType, so the standalone Tracked Titles list can exclude them
  activeTrackerCategory: "watching", // "watching" | "interested" | "watched" -- which Tracked Titles sub-tab is showing
};

export function $(sel) { return document.querySelector(sel); }
export function $all(sel) { return Array.from(document.querySelectorAll(sel)); }

// ---- Toasts ----
// For actions that reload silently on success with no adjacent status text
// (gallery batch buttons, preset save/delete, viewer create) -- most forms
// in this app already have their own dedicated inline status element and
// should keep using that instead of a toast.
let toastSeq = 0;

export function showToast(message, type = "info", duration = 4000) {
  const container = $("#toast-container");
  if (!container) return;
  const id = ++toastSeq;
  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  el.dataset.toastId = id;
  el.innerHTML = `<span class="toast-message"></span><button class="toast-close" aria-label="Dismiss">×</button>`;
  el.querySelector(".toast-message").textContent = message;
  const remove = () => el.remove();
  el.querySelector(".toast-close").addEventListener("click", remove);
  container.appendChild(el);
  setTimeout(remove, duration);
  return id;
}

export function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(1)} ${units[i]}`;
}

// ---- Optional API token (see Settings > API token) ----
const API_TOKEN_KEY = "media-manager:api-token";

export function getStoredApiToken() {
  try { return localStorage.getItem(API_TOKEN_KEY) || ""; } catch (e) { return ""; }
}

export function setStoredApiToken(token) {
  try {
    if (token) localStorage.setItem(API_TOKEN_KEY, token);
    else localStorage.removeItem(API_TOKEN_KEY);
  } catch (e) { /* private browsing / storage disabled -- token won't persist across reloads */ }
}

export async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getStoredApiToken();
  if (token) headers["X-API-Token"] = token;

  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401) {
    const entered = window.prompt("This dashboard requires an API token to continue. Enter it:");
    if (!entered) throw new Error("401: API token required");
    setStoredApiToken(entered);
    return api(path, options);
  }
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status}: ${text}`);
  }
  return resp.json();
}
