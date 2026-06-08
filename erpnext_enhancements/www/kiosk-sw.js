/*
 * Time Kiosk service worker (served at /kiosk-sw.js → root scope, controls /kiosk).
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
 */

const CACHE = 'time-kiosk-v1';
const SYNC_TAG = 'flush-geo';
const BATCH_ENDPOINT =
  '/api/method/erpnext_enhancements.api.time_kiosk.log_geolocation_batch';

const PRECACHE = [
  '/assets/erpnext_enhancements/css/time-kiosk.bundle.css',
  '/assets/erpnext_enhancements/css/kiosk/kiosk.css',
  '/assets/erpnext_enhancements/js/kiosk/geo.js',
  '/assets/erpnext_enhancements/js/kiosk/app.js',
  '/assets/erpnext_enhancements/kiosk/icons/kiosk-icon.svg',
  '/kiosk-manifest.json',
];

// --- IndexedDB -------------------------------------------------------------
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
      const msg = (data && data.message) || {};
      const accepted = msg.accepted || [];
      const dropped = (msg.rejected || [])
        .filter((r) => PERMANENT_REJECTS.has(r.reason))
        .map((r) => r.client_id);

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

// --- Lifecycle -------------------------------------------------------------
self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // Best-effort: a single 404 shouldn't fail the whole install.
    await Promise.allSettled(PRECACHE.map((url) => cache.add(url)));
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

  // Our static assets: cache-first with background refresh.
  if (url.pathname.startsWith('/assets/erpnext_enhancements/') ||
      url.pathname === '/kiosk-manifest.json') {
    event.respondWith((async () => {
      const cached = await caches.match(req);
      const network = fetch(req).then((res) => {
        if (res && res.ok) caches.open(CACHE).then((c) => c.put(req, res.clone()));
        return res;
      }).catch(() => null);
      return cached || (await network) || new Response('', { status: 504 });
    })());
  }
});

// --- Messaging from the page ----------------------------------------------
self.addEventListener('message', (event) => {
  const { type, data } = event.data || {};
  if (type === 'config') {
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
