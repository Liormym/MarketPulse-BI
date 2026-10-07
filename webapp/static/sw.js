// MarketPulse BI service worker.
//
// Strategy:
//  - App shell (HTML pages, CSS/JS, icons, fonts): stale-while-revalidate,
//    so repeat loads are instant but still quietly refresh in the background.
//  - Navigations (the dashboard / a stock page): network-first, falling back
//    to the last cached version only when the network genuinely fails -
//    financial data should never silently look live when it's stale.
//  - /api/* (GET only): same network-first policy as navigations.
//  - Anything non-GET (the refresh endpoint is POST) is never intercepted.
//
// Bump CACHE_VERSION on any change to this file's caching behavior, or to
// SHELL_ASSETS, so old clients pick up the new strategy on next load.
const CACHE_VERSION = "v1";
const SHELL_CACHE = `marketpulse-shell-${CACHE_VERSION}`;
const API_CACHE = `marketpulse-api-${CACHE_VERSION}`;
const CURRENT_CACHES = [SHELL_CACHE, API_CACHE];

const SHELL_ASSETS = [
  "/",
  "/manifest.json",
  "/static/css/style.css",
  "/static/js/dashboard.js",
  "/static/js/stock_detail.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((key) => !CURRENT_CACHES.includes(key)).map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return cached;
    throw err;
  }
}

async function staleWhileRevalidate(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  const networkFetch = fetch(request)
    .then((response) => {
      if (response.ok || response.type === "opaque") cache.put(request, response.clone());
      return response;
    })
    .catch(() => undefined);
  return cached || (await networkFetch) || fetch(request);
}

function isFontHost(hostname) {
  return hostname.endsWith("gstatic.com") || hostname.endsWith("googleapis.com");
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, SHELL_CACHE));
    return;
  }
  if (url.origin === self.location.origin && url.pathname.startsWith("/api/")) {
    event.respondWith(networkFirst(request, API_CACHE));
    return;
  }
  if (url.origin === self.location.origin || isFontHost(url.hostname)) {
    event.respondWith(staleWhileRevalidate(request, SHELL_CACHE));
  }
});
