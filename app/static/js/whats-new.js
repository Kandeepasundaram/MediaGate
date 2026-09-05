/**
 * "What's New" popover: shows once per bumped APP_VERSION, then stays
 * quiet until the version string changes again. Bump APP_VERSION and add
 * a matching CHANGELOG entry whenever a notable user-facing batch ships.
 */

import { $ } from "./core.js";

const APP_VERSION = "2026.09.1";

const CHANGELOG = [
  {
    version: "2026.09.1",
    items: [
      "Upcoming Releases: calendar view and countdown badges",
      "Compare Titles -- see two movies/shows side by side",
      "Collections: group titles into custom lists",
      "Recent searches remembered in the search box",
      "High Contrast theme, text size, and reduce-motion controls",
      "Install MediAerie as an app; swipe between tabs on mobile",
      "A-Z jump rail, Untag Selected, and file-size totals on delete",
    ],
  },
];

const SEEN_KEY = "media-manager:whats-new-seen-version";

export function maybeShowWhatsNew() {
  let seen = null;
  try { seen = localStorage.getItem(SEEN_KEY); } catch (e) { /* private browsing / storage disabled */ }
  if (seen === APP_VERSION) return;
  const entry = CHANGELOG.find((c) => c.version === APP_VERSION);
  if (!entry) return;
  $("#whats-new-version").textContent = APP_VERSION;
  $("#whats-new-list").innerHTML = entry.items.map((i) => `<li>${i}</li>`).join("");
  $("#whats-new-modal").classList.remove("hidden");
}

// Also called unconditionally on every Escape press (alongside every other
// modal's close function) -- bail out if this modal wasn't the one open, so
// an unrelated Escape can't mark the changelog "seen" before it's shown.
export function closeWhatsNew() {
  const modal = $("#whats-new-modal");
  if (modal.classList.contains("hidden")) return;
  modal.classList.add("hidden");
  try { localStorage.setItem(SEEN_KEY, APP_VERSION); } catch (e) { /* private browsing / storage disabled */ }
}
