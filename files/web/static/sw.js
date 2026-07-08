// Shell-only service worker: caches app shell for offline load.
// API routes and the live stream JPEG are never cached.
const CACHE = "gate-anpr-shell-v1";
const SHELL = [
  "/",
  "/static/app.js",
  "/static/styles.css",
  "/static/manifest.json",
  "/static/fonts/space-grotesk.woff2",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Never intercept API calls or the live stream JPEG.
  if (url.pathname.startsWith("/api/") || url.pathname === "/static/stream.jpg") {
    return;
  }
  e.respondWith(
    caches.match(e.request).then((cached) => cached || fetch(e.request))
  );
});
