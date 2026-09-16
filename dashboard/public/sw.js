// Minimal service worker: just enough for "Add to Home Screen" to count
// this as an installable PWA. This dashboard shows live operational data,
// so it deliberately does NOT cache API responses or pages — a stale
// offline view of the DB would be actively misleading for a one-owner
// ops tool. It caches the tiny static app shell (icons/manifest) and
// otherwise passes every request straight through to the network.
const CACHE_NAME = "jarvis-shell-v1";
const APP_SHELL = ["/manifest.json", "/icons/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (!APP_SHELL.includes(url.pathname)) return; // pass through everything else

  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request))
  );
});
