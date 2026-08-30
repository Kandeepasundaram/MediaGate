// Caches only the static dashboard shell (HTML/CSS/JS/icons) so the app
// installs as a PWA and reloads instantly on a flaky connection. /api/*
// requests are never intercepted -- library data must always be live.
const CACHE_NAME = "media-manager-shell-v1";
const SHELL_ASSETS = ["/", "/index.html", "/app.js", "/style.css", "/manifest.json", "/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/")) return;
  event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
});
