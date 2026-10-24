/* Service worker for the Today page: shows the last data while offline.
   - Page files (/app/) and API data (GET /v1/) are both "network first, fall back to cache on failure": always fresh when online.
   - Cached data gets X-Cached-At (save time); when returned offline it gets X-Offline: 1, which the page uses to show the "offline" (「离线」) banner.
   - On 401 (not logged in / logged out) the data cache is cleared; the page also deletes it on logout. Writes (POST / PUT / PATCH / DELETE) are not cached.
   - The Watch2Whoop app's WKWebView runs no service worker; offline.js (flag offline_mode) keeps the page's own copies
     and the queue of writes made offline in IndexedDB, for the app and browsers alike. */
const SHELL = "be-shell-v130";
const DATA = "be-data";
const SHELL_FILES = ["./", "index.html", "app.css", "app.js", "ai.css", "ai.js", "jarvis-entry.js", "jarvis-entry.css", "md.js", "ai-media.js", "profile.js", "identities.js", "remarks.js", "circles.js", "circle-feed.js", "account.js", "feedback.js", "data-status.js", "notify.js", "grocery.js", "grocery.css", "diet-profile.js", "diet-profile.css", "water.js", "muscle-map.js", "muscle-body-data.js", "muscle-load.js", "custom-exercises.js", "journal.js", "exercise-plan.js", "training-day.js", "hevy-import.js", "sleep-detail.js", "intro.js", "sync.js", "offline.js", "i18n.js", "i18n-en.js", "ios-web.js", "avatars.js", "install.html", "manifest.webmanifest", "icon.svg", "apple-touch-icon.png"];
const TIMEOUT_MS = 8000;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith("be-shell-") && k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function fetchWithTimeout(req) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  return fetch(req, { signal: ctrl.signal }).finally(() => clearTimeout(t));
}

async function withHeaders(resp, extra) {
  const headers = new Headers(resp.headers);
  for (const [k, v] of Object.entries(extra)) headers.set(k, v);
  return new Response(await resp.blob(), { status: resp.status, statusText: resp.statusText, headers });
}

async function data(req) {
  const cache = await caches.open(DATA);
  try {
    const resp = await fetchWithTimeout(req);
    if (resp.ok) cache.put(req, await withHeaders(resp.clone(), { "X-Cached-At": new Date().toISOString() }));
    else if (resp.status === 401) await caches.delete(DATA);
    return resp;
  } catch (err) {
    const cached = await cache.match(req);
    if (cached) return withHeaders(cached, { "X-Offline": "1" });
    // No I18N in the worker: the detail follows the page language sent in X-Body-Lang
    const detail = req.headers.get("X-Body-Lang") === "en" ? "Offline, and this device has no saved copy of this data"
      : "离线，这台设备上也没有保存过这项数据";   // i18n-ok
    return new Response(JSON.stringify({ detail }),
      { status: 503, headers: { "Content-Type": "application/json", "X-Offline": "1" } });
  }
}

async function shell(req) {
  const cache = await caches.open(SHELL);
  try {
    const resp = await fetchWithTimeout(req);
    if (resp.ok) cache.put(req, resp.clone());
    return resp;
  } catch (err) {
    const cached = await cache.match(req, { ignoreSearch: true });
    if (cached) return cached;
    if (req.mode === "navigate") return (await cache.match("index.html")) || Response.error();
    return Response.error();
  }
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (req.headers.get("Accept") === "text/event-stream") return;   // live streams (chat resume, #114): never cached or timed out
  if (url.pathname.startsWith("/v1/")) e.respondWith(data(req));
  else if (url.pathname.startsWith("/app/")) e.respondWith(shell(req));
});
