// Minimal service worker so the dashboard installs like an app on phone and
// desktop. Network-first for pages (this is a live dashboard — never show
// stale approvals/leads), cache-first for static assets, with a tiny
// offline fallback if there's no connection at all.
const CACHE_NAME = "aurora-shell-v1";
const SHELL_ASSETS = [
  "/static/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/offline",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  const isStatic = url.pathname.startsWith("/static/");

  if (isStatic) {
    event.respondWith(
      caches.match(req).then((cached) => cached || fetch(req))
    );
    return;
  }

  event.respondWith(
    fetch(req)
      .then((resp) => {
        caches.open(CACHE_NAME).then((cache) => cache.put(req, resp.clone())).catch(() => {});
        return resp;
      })
      .catch(() => caches.match(req).then((cached) => cached || caches.match("/offline")))
  );
});
