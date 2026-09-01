// Caches only the static dashboard shell (HTML/CSS/JS/icons) so the app
// installs as a PWA and reloads instantly on a flaky connection. /api/*
// requests are never intercepted -- library data must always be live.
//
// Network-first, not cache-first: a self-hosted dashboard gets redeployed
// far more often than it's used offline, and a cache-first shell means a
// browser that already installed this worker keeps serving the shell it
// cached on day one *forever* -- every later deploy's app.js/style.css/
// index.html changes are invisible until someone manually clears site
// data, since the browser never re-checks the network for a cache hit.
// Network-first still falls back to the cache when the network fails
// (offline / server down), which is the only scenario this cache exists
// for in the first place.
const CACHE_NAME = "media-manager-shell-v3";
const SHELL_ASSETS = ["/", "/index.html", "/app.js", "/style.css", "/manifest.json", "/icon.png", "/icon-192.png"];

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
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
