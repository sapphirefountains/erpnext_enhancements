/**
 * Time Kiosk service worker (served at /kiosk-sw.js → root scope, controls /kiosk).
 *
 * Registered by the page (app.js) at the site root so its scope covers /kiosk and
 * the precached /assets/erpnext_enhancements/... files.
 *
 * Two jobs:
 *   1. Offline app shell — cache static assets + last good /kiosk navigation so the
 *      app launches without a network.
 *   2. Durable location queue — receive points from the page (postMessage), persist
 *      them in IndexedDB, POST them in batches to log_geolocation_batch, and retry
 *      via Background Sync when connectivity returns (even if the tab is asleep).
 *
 * NOTE: a service worker cannot read GPS either — acquisition happens on the page
 * (geo.js). This worker only persists + ships what the page hands it.
 *
 * Lifecycle:
 *   - install:  open the CACHE and precache the app-shell assets (PRECACHE), then
 *               skipWaiting() so a new worker activates immediately.
 *   - activate: delete this worker's own older caches ('time-kiosk-' + an earlier
 *               token), never another worker's, then clients.claim() so this worker
 *               controls already-open tabs.
 *   - fetch:    /kiosk navigations → network-first with a cached-shell fallback;
 *               kiosk GET APIs → network-first with a fallback to the answer kept
 *               for the SAME person (see "Kept API answers" below); our /assets
 *               (+ /kiosk-manifest.json) → cache-first with background refresh.
 *               A sign-out POST forgets every kept answer and the kept shell; it,
 *               like every other non-GET or cross-origin request, is passed through.
 *   - message:  'config' stores csrf_token / max_batch_size in IndexedDB; 'enqueue'
 *               persists a point and tries to flush; 'flush' flushes on demand;
 *               'stats' answers { queued } over the MessageChannel port the page
 *               sends with it (the Settings tab's diagnostics).
 *   - sync:     the SYNC_TAG Background Sync event re-runs flushQueue() once the
 *               browser regains connectivity.
 *
 * Cache versioning — AUTOMATIC, per deploy. The page registers this script as
 * /kiosk-sw.js?v=<deploy token> (kiosk.py::get_deploy_version — the mtime of
 * sites/assets/assets.json, i.e. a new value on every bench build). A deploy is
 * therefore a new script URL: the browser installs the new worker, whose CACHE
 * name embeds the token, and activate deletes its older ones — no manual
 * version bump needed anymore. Precaching uses `cache: 'reload'` + the same
 * ?v= suffix the page uses, so the 1-year-immutable HTTP cache for raw /assets
 * can never feed a stale copy into a fresh cache.
 * (The IndexedDB schema is versioned separately via DB_VERSION below — it does
 * NOT rotate per deploy; bump it only on schema changes.)
 */

// 'dev' only if registered without ?v= (e.g. a manual register() in devtools).
const VERSION = new URL(self.location.href).searchParams.get('v') || 'dev';
const CACHE = 'time-kiosk-' + VERSION;
const SYNC_TAG = 'flush-geo';
const BATCH_ENDPOINT =
  '/api/method/erpnext_enhancements.api.time_kiosk.log_geolocation_batch';
// The kiosk's GET APIs, which this worker keeps and replays offline, per person.
const API_PREFIX = '/api/method/erpnext_enhancements.api.time_kiosk.';
// app.js names the person its screen belongs to (BOOT.user, URI-encoded) on every
// kiosk call in this header, and this query parameter carries that name in a kept
// answer's cache key. The key is only ever a cache address; it is never fetched.
const USER_HEADER = 'X-Kiosk-User';
const USER_PARAM = '__kiosk_user';
// Frappe's sign-out requests (read from v16.53.0): desk's Log out POSTs
// /api/method/logout, and the /logout page (the website's Logout link) POSTs '/'
// with the method in an X-Frappe-CMD header. web_logout is the same, POST only.
const SIGN_OUT_PATH = /^\/api\/(?:v\d+\/)?method\/(?:logout|web_logout)$/;
const SIGN_OUT_CMDS = new Set(['logout', 'web_logout']);

// Every js/css URL kiosk.html loads, and nothing else (tests/test_kiosk_frontend.py
// checks both directions). Leaflet is deliberately absent: it is frappe's vendored
// file, never changes, and a root-scope worker may only answer for its own shell.
const PRECACHE = [
  '/assets/erpnext_enhancements/css/kiosk/kiosk.css',
  '/assets/erpnext_enhancements/js/kiosk/ui.js',
  '/assets/erpnext_enhancements/js/kiosk/geo.js',
  '/assets/erpnext_enhancements/js/kiosk/myday.js',
  '/assets/erpnext_enhancements/js/kiosk/map.js',
  '/assets/erpnext_enhancements/js/kiosk/settings.js',
  '/assets/erpnext_enhancements/js/kiosk/app.js',
  '/assets/erpnext_enhancements/kiosk/icons/kiosk-icon.svg',
  '/assets/erpnext_enhancements/kiosk/icons/kiosk-icon-192.png',
  '/assets/erpnext_enhancements/kiosk/icons/kiosk-icon-512.png',
  '/assets/erpnext_enhancements/kiosk/icons/kiosk-maskable-512.png',
  '/kiosk-manifest.json',
];

// Exactly the paths above, for the fetch handler to test membership against.
// This worker is registered at ROOT SCOPE — it sees every request on the origin,
// including every other page's JavaScript — so what it chooses to answer has to
// be an explicit list rather than a prefix.
const PRECACHE_PATHS = new Set(PRECACHE);

// The page requests these URLs with the same ?v= token (kiosk.html), so cache
// keys line up; fetch matching uses ignoreSearch as a belt-and-braces anyway.
function versioned(url) {
  return url + (url.indexOf('?') === -1 ? '?' : '&') + 'v=' + encodeURIComponent(VERSION);
}

// --- IndexedDB -------------------------------------------------------------
// Two object stores, created/upgraded in openDB().onupgradeneeded:
//   GeoQueue — pending geolocation points, keyed by the page-assigned client_id
//              (the dedupe key the server echoes back as accepted/rejected).
//   Meta     — small config kv: csrf_token + max_batch_size, keyed by `key`.
// Bump DB_VERSION (independently of the Cache version) when the schema changes.
const DB_NAME = 'TimeKioskDB';
const DB_VERSION = 2;
const QUEUE_STORE = 'GeoQueue';
const META_STORE = 'Meta';

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(QUEUE_STORE)) {
        db.createObjectStore(QUEUE_STORE, { keyPath: 'client_id' });
      }
      if (!db.objectStoreNames.contains(META_STORE)) {
        db.createObjectStore(META_STORE, { keyPath: 'key' });
      }
    };
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

function tx(db, store, mode) {
  return db.transaction(store, mode).objectStore(store);
}

async function putPoint(point) {
  const db = await openDB();
  return new Promise((res, rej) => {
    const r = tx(db, QUEUE_STORE, 'readwrite').put(point);
    r.onsuccess = res;
    r.onerror = () => rej(r.error);
  });
}

async function getAllPoints() {
  const db = await openDB();
  return new Promise((res, rej) => {
    const r = tx(db, QUEUE_STORE, 'readonly').getAll();
    r.onsuccess = () => res(r.result || []);
    r.onerror = () => rej(r.error);
  });
}

async function deletePoints(clientIds) {
  if (!clientIds || !clientIds.length) return;
  const db = await openDB();
  const store = db.transaction(QUEUE_STORE, 'readwrite').objectStore(QUEUE_STORE);
  await Promise.all(clientIds.map((id) => new Promise((res) => {
    const r = store.delete(id);
    r.onsuccess = res;
    r.onerror = res; // best-effort
  })));
}

async function setMeta(key, value) {
  const db = await openDB();
  return new Promise((res, rej) => {
    const r = tx(db, META_STORE, 'readwrite').put({ key, value });
    r.onsuccess = res;
    r.onerror = () => rej(r.error);
  });
}

async function getMeta(key) {
  const db = await openDB();
  return new Promise((res) => {
    const r = tx(db, META_STORE, 'readonly').get(key);
    r.onsuccess = () => res(r.result ? r.result.value : null);
    r.onerror = () => res(null);
  });
}

// --- Networking ------------------------------------------------------------
// Permanent reject reasons get dropped from the queue; everything else is kept
// for a later retry.
const PERMANENT_REJECTS = new Set(['invalid_coords', 'low_accuracy']);

let flushing = false;

async function flushQueue() {
  if (flushing) return;
  flushing = true;
  try {
    const csrf = await getMeta('csrf_token');
    let points = await getAllPoints();
    const batchSize = (await getMeta('max_batch_size')) || 50;

    while (points.length) {
      const batch = points.slice(0, batchSize);
      const res = await fetch(BATCH_ENDPOINT, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'X-Frappe-CSRF-Token': csrf || '',
        },
        body: JSON.stringify({ points: batch }),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);

      const data = await res.json();
      // Frappe wraps whitelisted return values under `message`. The endpoint
      // reports which client_ids it accepted and which it rejected (with a reason).
      const msg = (data && data.message) || {};
      const accepted = msg.accepted || [];
      // Only permanently-rejected points are dropped; transient rejects stay queued.
      const dropped = (msg.rejected || [])
        .filter((r) => PERMANENT_REJECTS.has(r.reason))
        .map((r) => r.client_id);

      // Remove everything the server is done with (accepted + permanently dropped).
      await deletePoints([...accepted, ...dropped]);

      // Stop if the server made no progress (avoid infinite loop on stuck batch).
      if (!accepted.length && !dropped.length) break;
      points = points.slice(batch.length);
    }
    return true;
  } catch (e) {
    // Leave the queue intact; Background Sync / next message will retry.
    return false;
  } finally {
    flushing = false;
  }
}

async function ensureSync() {
  try {
    if (self.registration.sync) {
      await self.registration.sync.register(SYNC_TAG);
    }
  } catch (e) {
    /* Background Sync unsupported (e.g. iOS) — page-driven flush still works. */
  }
}

// --- Kept API answers, one person at a time -------------------------------
// Most kiosk GETs are about the person asking: get_my_day, get_my_history,
// get_my_correction_requests, get_my_trail, the current status. They used to be
// kept under their URL alone, so on a shared phone the next technician, offline,
// was handed the last one's day, history, correction requests and Map trail.
// Now an answer is kept under the person app.js names in USER_HEADER, an offline
// replay looks only under the name the asking page sends, and the phone holds
// one person's answers at a time:
//   - keeping an answer for somebody new forgets everybody else's (keepAnswer);
//   - a sign-out forgets everybody's, and the kept /kiosk page with them, which
//     carries that person's boot payload (forgetEverybody).
// All of it lives in CACHE, so a deploy's activate still drops it with the rest.
// The worker cannot read cookies (not every browser lets one), so it never
// decides who is signed in: the page says whose screen is asking.

// Whose answers are kept, as last seen. A hint only: the worker is stopped when it
// is idle and this is lost, after which the next keep looks at every entry.
let keptFor = null;

function userKey(req, user) {
  const url = new URL(req.url);
  url.searchParams.set(USER_PARAM, user);
  return url.href;
}

function isSignOut(req, url) {
  if (req.method !== 'POST' || url.origin !== self.location.origin) return false;
  return SIGN_OUT_PATH.test(url.pathname) || SIGN_OUT_CMDS.has(req.headers.get('X-Frappe-CMD') || '');
}

// Delete kept API answers from this worker's caches: every one when `keep` is
// null, else every one not kept for `keep`. With `withShell`, the kept page too.
async function forgetAnswers(keep, withShell) {
  const names = (await caches.keys()).filter((k) => k.startsWith('time-kiosk-'));
  for (const name of names) {
    const cache = await caches.open(name);
    if (withShell) await cache.delete('/kiosk');
    for (const req of await cache.keys()) {
      const url = new URL(req.url);
      if (!url.pathname.startsWith(API_PREFIX)) continue;
      if (keep === null || url.searchParams.get(USER_PARAM) !== keep) await cache.delete(req);
    }
  }
}

async function keepAnswer(user, key, response) {
  if (user !== keptFor) {
    await forgetAnswers(user, false);
    keptFor = user;
  }
  const cache = await caches.open(CACHE);
  await cache.put(key, response);
}

async function forgetEverybody() {
  keptFor = null;
  await forgetAnswers(null, true);
}

// --- Lifecycle -------------------------------------------------------------
self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // Best-effort: a single 404 shouldn't fail the whole install.
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
    // This worker's own caches from earlier deploys ('time-kiosk-<old token>'), and nothing
    // else. It used to delete every cache on the site but its own, which wiped the traveler
    // itinerary's offline copy (itinerary-sw.js) on any phone that also opened /kiosk, at
    // every deploy. Every cache this worker ever made starts with this prefix.
    await Promise.all(keys.filter((k) => k.startsWith('time-kiosk-') && k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  // Somebody is signing out, from desk or the /logout page (this worker is at root
  // scope, so it sees both): forget what the kiosk kept for them before the next
  // person picks the phone up. The request itself goes on untouched.
  if (isSignOut(req, url)) {
    event.waitUntil(forgetEverybody().catch(() => {}));
    return;
  }
  if (req.method !== 'GET') return;
  if (url.origin !== self.location.origin) return;

  // App navigation: network-first, fall back to cached shell when offline.
  if (req.mode === 'navigate' && url.pathname === '/kiosk') {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(req);
        const cache = await caches.open(CACHE);
        cache.put('/kiosk', fresh.clone());
        return fresh;
      } catch (e) {
        return (await caches.match('/kiosk')) ||
               (await caches.match(req)) ||
               new Response('Offline', { status: 503 });
      }
    })());
    return;
  }

  // Kiosk GET APIs (options, status, today's visits, maintenance context, My Day,
  // the Map trail): network-first, falling back to the last good response so the
  // idle screen and form links still render in a dead zone (equipment rooms).
  // POSTs (clock actions) are untouched — they need the server. Kept and replayed
  // per person (see "Kept API answers" above): a call that names nobody is neither
  // kept nor replayed, it just goes to the network.
  if (url.pathname.startsWith(API_PREFIX)) {
    const user = req.headers.get(USER_HEADER);
    if (!user) return;
    const key = userKey(req, user);
    event.respondWith((async () => {
      try {
        const fresh = await fetch(req);
        if (fresh && fresh.ok) {
          // Cloned here, before the response is returned (see the shell below).
          event.waitUntil(keepAnswer(user, key, fresh.clone()).catch(() => {}));
        }
        return fresh;
      } catch (e) {
        return (await caches.match(key)) || new Response('', { status: 504 });
      }
    })());
    return;
  }

  // THE KIOSK'S OWN SHELL ONLY. Not "everything under /assets/erpnext_enhancements/".
  //
  // This worker is registered at root scope, so it sees every request on the
  // origin. It used to answer any request under the app's asset root cache-first
  // with `ignoreSearch: true`, and the comment argued that was safe because
  // "the cache only ever holds this deploy's entries". It is not, and the hole is
  // the lifecycle: this worker is only replaced when its own script URL changes,
  // and that only happens when somebody opens /kiosk. Any browser that opened the
  // kiosk once then kept serving THAT deploy's JavaScript to every other page in
  // the app — desk scripts, portal pages, the training player — for as long as
  // nobody went back to /kiosk.
  //
  // `ignoreSearch` turned the ?v= deploy token into decoration: a brand-new token
  // matched the year-old entry. It was found with a training player four releases
  // stale in a browser whose cache was named after a deploy from weeks earlier,
  // and no amount of cache-busting on the page could have reached it.
  //
  // An offline kiosk needs its own shell available offline. It has no business
  // answering for anything else, so anything not on the precache list is simply
  // not handled here and goes to the network like a normal request.
  if (PRECACHE_PATHS.has(url.pathname)) {
    event.respondWith((async () => {
      // ignoreSearch is right for THESE files and only these: the page and the
      // worker can disagree by one ?v= token mid-update, and the shell must still
      // resolve. `activate` drops this worker's older caches, and no other
      // worker keeps these paths, so the entries here are this deploy's.
      const cached = await caches.match(req, { ignoreSearch: true });
      const network = fetch(req).then((res) => {
        if (res && res.ok) {
          // Clone synchronously, BEFORE the response is returned — by the
          // time the async caches.open() resolved, the page had often already
          // consumed the body, throwing "Response body is already used" on
          // every desk page load.
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      }).catch(() => null);
      return cached || (await network) || new Response('', { status: 504 });
    })());
  }
});

// --- Messaging from the page ----------------------------------------------
// The page (geo.js) drives the worker via postMessage({ type, data }):
//   config  → persist csrf_token / max_batch_size for later uploads
//   enqueue → store one point, then attempt an immediate flush
//   flush   → flush the queue on demand (e.g. on reconnect / app resume)
//   stats   → reply { queued: <points still in IndexedDB> } on event.ports[0]
//             (the page sends a MessageChannel port; KioskGeo.queuedCount)
// 'enqueue' and 'flush' register a Background Sync as a fallback if the flush fails.
self.addEventListener('message', (event) => {
  const { type, data } = event.data || {};
  if (type === 'stats') {
    const port = event.ports && event.ports[0];
    event.waitUntil((async () => {
      let queued = 0;
      try { queued = (await getAllPoints()).length; } catch (e) { queued = 0; }
      if (port) port.postMessage({ queued });
    })());
  } else if (type === 'config') {
    event.waitUntil((async () => {
      if (data.csrf_token) await setMeta('csrf_token', data.csrf_token);
      if (data.max_batch_size) await setMeta('max_batch_size', data.max_batch_size);
    })());
  } else if (type === 'enqueue') {
    event.waitUntil((async () => {
      await putPoint(data);
      const ok = await flushQueue();
      if (!ok) await ensureSync();
    })());
  } else if (type === 'flush') {
    event.waitUntil((async () => {
      const ok = await flushQueue();
      if (!ok) await ensureSync();
    })());
  }
});

self.addEventListener('sync', (event) => {
  if (event.tag === SYNC_TAG) {
    event.waitUntil(flushQueue());
  }
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientsArr) => {
      for (let i = 0; i < clientsArr.length; i++) {
        const client = clientsArr[i];
        if (client.url && client.url.includes('/kiosk') && 'focus' in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow('/kiosk');
      }
    })
  );
});
