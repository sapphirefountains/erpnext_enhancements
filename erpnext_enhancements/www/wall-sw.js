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

  // Our static assets: cache-first with background refresh.
  if (url.pathname.startsWith('/assets/erpnext_enhancements/')) {
    event.respondWith((async () => {
      const cached = await caches.match(req, { ignoreSearch: true });
      const network = fetch(req).then((res) => {
        if (res && res.ok) caches.open(CACHE).then((c) => c.put(req, res.clone()));
        return res;
      }).catch(() => null);
      return cached || (await network) || new Response('', { status: 504 });
    })());
  }
});
