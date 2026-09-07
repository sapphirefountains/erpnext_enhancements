/**
 * Wall Display service worker (served at /wall-sw.js → root scope, controls /wall).
 *
 * A trimmed clone of kiosk-sw.js: same automatic per-deploy cache versioning
 * (registered as /wall-sw.js?v=<deploy token>, the CACHE name embeds the token,
 * activate deletes every other cache), same network-first-with-fallback
 * strategies — minus the kiosk's entire IndexedDB geolocation queue, which a
 * read-only display doesn't need.
 *
 * Jobs:
 *   1. Offline shell — precache the wall assets + last good /wall navigation so
 *      the display keeps rendering through brief network blips.
 *   2. Last-good data — cache the wall data endpoint responses (network-first)
 *      so a refresh during an outage shows stale-but-present data instead of a
 *      blank screen.
 */

// 'dev' only if registered without ?v= (e.g. a manual register() in devtools).
const VERSION = new URL(self.location.href).searchParams.get('v') || 'dev';
const CACHE = 'wall-display-' + VERSION;

const PRECACHE = [
  '/assets/erpnext_enhancements/css/wall/wall.css',
  '/assets/erpnext_enhancements/js/wall/app.js',
];

// Exactly the paths above, for the fetch handler to test membership against.
// This worker is registered at ROOT SCOPE — it sees every request on the origin,
// including every other page's JavaScript — so what it answers has to be an
// EXPLICIT list, never a prefix. kiosk-sw.js learned this the expensive way in
// v1.229.0: a root-scope worker that answered the whole app asset root cache-first
// with `ignoreSearch` froze the training player four releases stale in any browser
// that had opened the app once, and no `?v=` could reach it. This file is that
// worker's trimmed twin and had kept the same hole — see the fetch handler.
const PRECACHE_PATHS = new Set(PRECACHE);

function versioned(url) {
  return url + (url.indexOf('?') === -1 ? '?' : '&') + 'v=' + encodeURIComponent(VERSION);
}

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // `cache: 'reload'` bypasses the HTTP cache — raw /assets are served
    // immutable for a year, and a new deploy must precache fresh bytes.
    await Promise.allSettled(
      PRECACHE.map((url) => cache.add(new Request(versioned(url), { cache: 'reload' })))
    );
    self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // App navigation: network-first, fall back to cached shell when offline.
  if (req.mode === 'navigate' && url.pathname === '/wall') {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(req);
        const cache = await caches.open(CACHE);
        cache.put('/wall', fresh.clone());
        return fresh;
      } catch (e) {
        return (await caches.match('/wall')) ||
               (await caches.match(req)) ||
               new Response('Offline', { status: 503 });
      }
    })());
    return;
  }

  // Wall data endpoint: network-first, last-good fallback so the display
  // survives brief outages (a Pi without internet can't reach Frappe anyway,
  // so this only papers over flaps, not real disconnection).
  if (url.pathname.startsWith('/api/method/erpnext_enhancements.api.task_dashboard.')) {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(req);
        if (fresh && fresh.ok) {
          const cache = await caches.open(CACHE);
          cache.put(req, fresh.clone());
        }
        return fresh;
      } catch (e) {
        return (await caches.match(req)) || new Response('', { status: 504 });
      }
    })());
    return;
  }

  // THE WALL'S OWN SHELL ONLY — never "everything under /assets/erpnext_enhancements/".
  //
  // A root-scope worker is only replaced when its own script URL changes, i.e. when
  // somebody opens /wall. Answering for the whole asset root cache-first would serve
  // THIS deploy's bytes to the desk, the portal and the training player until the
  // next /wall visit, with `ignoreSearch` reducing every `?v=` deploy token to
  // decoration. kiosk-sw.js shipped exactly that (v1.229.0) and was cut back to its
  // precache list under test; this trimmed twin kept the hole. Answer only the shell
  // and let everything else reach the network like a normal request.
  if (PRECACHE_PATHS.has(url.pathname)) {
    event.respondWith((async () => {
      // `ignoreSearch` is right for THESE files and only these: page and worker can
      // disagree by one `?v=` token mid-update, and the shell must still resolve.
      // `activate` drops every other cache, so these entries are this deploy's.
      const cached = await caches.match(req, { ignoreSearch: true });
      const network = fetch(req).then((res) => {
        if (res && res.ok) {
          // Clone SYNCHRONOUSLY, before `res` is returned — by the time the async
          // caches.open() resolves the page has often already consumed the body,
          // throwing "Response body is already used" on every page this root-scope
          // worker controls (it was spamming the console on /training and /desk).
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      }).catch(() => null);
      return cached || (await network) || new Response('', { status: 504 });
    })());
  }
});
