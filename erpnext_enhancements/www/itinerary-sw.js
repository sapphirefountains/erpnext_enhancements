/**
 * Itinerary service worker (served at /itinerary-sw.js, registered with scope /itinerary).
 *
 * The one job: a traveler's itinerary, and the files that go with it, still open on their
 * phone in airplane mode or at a job site with no signal. The itinerary itself (every answer
 * the page got from get_trip_itinerary, confirmation numbers included) is kept by the page in
 * IndexedDB; see public/js/travel/itinerary.js. This worker keeps the two things the page
 * cannot: the page itself, and the files.
 *
 * Scope is /itinerary, never the site root. The Time Kiosk (kiosk-sw.js) and the Wall
 * Display (wall-sw.js) are both registered at "/", and a registration is keyed by its scope:
 * a second worker at "/" would replace the kiosk's, geolocation queue and all, on every
 * technician's phone. A worker only controls the pages inside its scope, so this one sees
 * the requests /itinerary makes and nothing any other page makes.
 *
 * Caches, every one of them named "itinerary-...":
 *   itinerary-shell-<deploy>  the page (the /itinerary navigation, kept under PAGE whatever
 *                             its ?trip=, ?as=, ?view= or ?file=) and its own stylesheet and
 *                             script at their ?v= addresses. Versioned per deploy, like the
 *                             kiosk's: the page registers /itinerary-sw.js?v=<deploy token>,
 *                             so a deploy is a new worker with a new shell cache.
 *   itinerary-files-<user>    the files the page asked to keep (boarding passes, booking
 *                             confirmations, site maps), for one signed-in person. Not
 *                             versioned: a deploy keeps them.
 *
 * Lifecycle:
 *   - install:  precache the stylesheet and script (cache: 'reload', so the year-long HTTP
 *               cache of raw /assets cannot hand over a stale copy), then the page itself,
 *               so the phone has it from the very first visit; then skipWaiting().
 *   - activate: carry the page forward from the previous deploy's shell when this one could
 *               not fetch it, then delete ONLY this worker's own old shell caches. Every other
 *               cache on the site belongs to somebody else (the kiosk's, the wall's), and so do
 *               this worker's files caches, which outlive a deploy. Then clients.claim().
 *   - fetch:    the /itinerary page: network first, the kept page when there is no answer (none
 *               within PAGE_WAIT_MS, or a server error). The stylesheet and script: kept copy
 *               first. A file
 *               under /private/files/ or /files/ that this person's files cache holds: the
 *               network when it answers within FILE_WAIT_MS, else the kept copy. Everything
 *               else, and every non-GET or other-site request, passes through untouched.
 *   - message:  {type: 'user', user}           this is who is signed in: every other person's
 *                                               files cache is deleted.
 *               {type: 'cache-files', user, urls} fetch and keep these files (this site's own
 *                                               /private/files/ and /files/ addresses only,
 *                                               a 200 only).
 *               {type: 'purge'}                 forget everything personal: every files cache
 *                                               and the kept page.
 *
 * Only a real answer is ever kept: a 200 from this site that was not redirected (a signed-out
 * request is sent to /login, and the login page kept as "the itinerary" would be worse than
 * nothing). A private file costs an Access Log row each time it is fetched, so a file already
 * kept is not fetched again.
 *
 * Served by Frappe as a Jinja template (www/*.js goes through TemplatePage), so this file must
 * never contain a double brace or a Jinja tag, comments included:
 * tests/test_itinerary_service_worker.py fails the build on one.
 */

// 'dev' only if registered without ?v= (e.g. a manual register() in devtools).
const VERSION = new URL(self.location.href).searchParams.get('v') || 'dev';
const SHELL_PREFIX = 'itinerary-shell-';
const SHELL = SHELL_PREFIX + VERSION;
const FILES_PREFIX = 'itinerary-files-';

// The page's one address. Every ?query of it is the same page (the query only says which trip,
// person, screen and picture the page draws), so it is kept once, under this key.
const PAGE = '/itinerary';

// The page's own stylesheet and script, and nothing else: itinerary.html loads both with
// ?v=<deploy token> (tests/test_itinerary_service_worker.py checks the two lists agree).
const PRECACHE = [
  '/assets/erpnext_enhancements/css/travel/itinerary.css',
  '/assets/erpnext_enhancements/js/travel/itinerary.js',
];
const PRECACHE_PATHS = new Set(PRECACHE);

// Where this site serves uploaded files. A file is only ever kept from one of these.
const FILE_PATHS = ['/private/files/', '/files/'];

// How long a request waits for the network before the kept copy answers. A phone with one bar
// of signal can take a minute to fail; a boarding pass is wanted at the gate now.
const PAGE_WAIT_MS = 6000;
const FILE_WAIT_MS = 4000;

// A person's files cache holds at most this many files (the oldest go first), none bigger than
// this, and one message asks for at most this many.
const MAX_FILES = 150;
const MAX_FILE_BYTES = 25 * 1024 * 1024;
const MAX_PER_MESSAGE = 40;

// The person the page last said is signed in. Only a hint: a worker is stopped whenever it is
// idle, and this is lost with it. After a 'user' message there is one files cache on the phone
// at most, so without the hint every files cache is looked in, which is the same one.
let currentUser = null;

function versioned(url) {
  return url + (url.indexOf('?') === -1 ? '?' : '&') + 'v=' + encodeURIComponent(VERSION);
}

function filesCacheFor(user) {
  return FILES_PREFIX + encodeURIComponent(user);
}

function isPerson(user) {
  return typeof user === 'string' && user !== '' && user !== 'Guest';
}

function isFilePath(pathname) {
  return FILE_PATHS.some((prefix) => pathname.startsWith(prefix));
}

// A response worth keeping: a 200 from this site, not a redirect to somewhere else (the login
// page), and not an opaque one.
function keepable(res) {
  return !!res && res.ok && res.type === 'basic' && !res.redirected;
}

function tooBig(res) {
  const length = Number(res.headers.get('content-length'));
  return Number.isFinite(length) && length > MAX_FILE_BYTES;
}

// One of this site's file addresses, absolute, or '' for anything else: another site, another
// path of this one (/api/..., a /files/../ that resolves outside), or not an address at all.
function fileHref(raw) {
  try {
    const url = new URL(String(raw), self.location.origin);
    if (url.origin !== self.location.origin || !isFilePath(url.pathname)) return '';
    url.hash = '';
    return url.href;
  } catch (e) {
    return '';
  }
}

// The network's answer, or null when it fails or has not answered within `ms`. The request
// itself carries on either way.
function within(network, ms) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), ms);
    network.then(
      (res) => {
        clearTimeout(timer);
        resolve(res);
      },
      () => {
        clearTimeout(timer);
        resolve(null);
      }
    );
  });
}

function offlinePage() {
  return new Response(
    '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">' +
      '<title>My Itinerary</title>' +
      '<p style="font-family: sans-serif; padding: 24px; line-height: 1.5">' +
      'You are offline, and your itinerary is not saved on this phone yet. ' +
      'Open it once while you have a connection and it will be here next time.</p>',
    { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
  );
}

// --- Lifecycle ---------------------------------------------------------------
self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    // Best effort: one failure must not fail the install.
    await Promise.allSettled(
      PRECACHE.map((url) => cache.add(new Request(versioned(url), { cache: 'reload' })))
    );
    // The page itself, as the person who opened it sees it. The page that registered this
    // worker was not yet under it, so its own load was never kept; without this, a first
    // visit followed by a flight would find nothing.
    try {
      const page = await fetch(new Request(PAGE, { credentials: 'same-origin', cache: 'no-store' }));
      if (keepable(page)) await cache.put(PAGE, page);
    } catch (e) {
      // Offline at install: the next visit keeps it.
    }
    self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    // This worker's own shells from earlier deploys, and nothing else: not the kiosk's, not
    // the wall's, not this worker's files caches.
    const old = keys.filter((k) => k.startsWith(SHELL_PREFIX) && k !== SHELL);
    const shell = await caches.open(SHELL);
    if (!(await shell.match(PAGE))) {
      for (const name of old.slice().reverse()) {
        const kept = await (await caches.open(name)).match(PAGE);
        if (kept) {
          await shell.put(PAGE, kept);
          break;
        }
      }
    }
    await Promise.all(old.map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

// --- Answers -----------------------------------------------------------------
async function pageFromNetworkOrKept(req) {
  const shell = await caches.open(SHELL);
  const network = fetch(req).then((res) => {
    if (keepable(res)) {
      // Cloned before the response is handed back, while its body is still unread.
      const copy = res.clone();
      shell.put(PAGE, copy).catch(() => {});
    }
    return res;
  });
  const fresh = await within(network, PAGE_WAIT_MS);
  // A server error is no answer either: a deploy restarting the site answers 502 for a minute.
  // A redirect (signed out: off to /login) and a refusal are answers, and go through.
  if (fresh && fresh.status < 500) return fresh;
  const kept = await shell.match(PAGE);
  if (kept) return kept;
  if (fresh) return fresh;
  try {
    return await network;
  } catch (e) {
    return offlinePage();
  }
}

async function shellAsset(req) {
  const shell = await caches.open(SHELL);
  const exact = await shell.match(req);
  if (exact) return exact;
  try {
    const res = await fetch(req);
    if (keepable(res)) {
      const copy = res.clone();
      shell.put(req, copy).catch(() => {});
    }
    return res;
  } catch (e) {
    // Offline, and the page asks for another deploy's ?v= than this worker precached (a page
    // kept before a deploy): this deploy's copy of the same file answers. Only for these two
    // files, and only from this worker's own shell.
    return (await shell.match(req, { ignoreSearch: true })) || new Response('', { status: 504 });
  }
}

// The files cache that holds `href` for the person signed in, or null.
async function keptFile(href) {
  const names = currentUser
    ? [filesCacheFor(currentUser)]
    : (await caches.keys()).filter((k) => k.startsWith(FILES_PREFIX));
  for (const name of names) {
    if (!(await caches.has(name))) continue;
    const cache = await caches.open(name);
    const hit = await cache.match(href);
    if (hit) return { cache, res: hit };
  }
  return null;
}

async function fileFromNetworkOrKept(req, href) {
  const kept = await keptFile(href);
  const network = fetch(req);
  if (!kept) return network;
  const fresh = await within(network, FILE_WAIT_MS);
  if (keepable(fresh)) {
    const copy = fresh.clone();
    kept.cache.put(href, copy).catch(() => {});
    return fresh;
  }
  // No answer, a slow one, or a refusal (a server error, a session that has run out): the
  // copy this person already had.
  return kept.res;
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (req.mode === 'navigate' && url.pathname === PAGE) {
    event.respondWith(pageFromNetworkOrKept(req));
    return;
  }

  if (PRECACHE_PATHS.has(url.pathname)) {
    event.respondWith(shellAsset(req));
    return;
  }

  // A viewer asking for part of a file (a Range request) is left to the network.
  if (isFilePath(url.pathname) && !req.headers.has('range')) {
    const href = fileHref(url.href);
    if (href) event.respondWith(fileFromNetworkOrKept(req, href));
  }
});

// --- Messages from the page ----------------------------------------------------
async function forUser(user) {
  if (!isPerson(user)) return;
  currentUser = user;
  const own = filesCacheFor(user);
  const keys = await caches.keys();
  await Promise.all(keys.filter((k) => k.startsWith(FILES_PREFIX) && k !== own).map((k) => caches.delete(k)));
}

async function keepFiles(user, urls) {
  if (!isPerson(user) || !Array.isArray(urls)) return;
  // Files for somebody other than the person last named: that person is who is signed in now.
  if (user !== currentUser) await forUser(user);
  const cache = await caches.open(filesCacheFor(user));
  for (const raw of urls.slice(0, MAX_PER_MESSAGE)) {
    const href = fileHref(raw);
    if (!href || (await cache.match(href))) continue;
    try {
      const res = await fetch(new Request(href, { credentials: 'same-origin' }));
      if (keepable(res) && !tooBig(res)) await cache.put(href, res);
    } catch (e) {
      // Offline, or refused: the page asks again on its next visit.
    }
  }
  const keys = await cache.keys();
  const over = keys.length - MAX_FILES;
  if (over > 0) await Promise.all(keys.slice(0, over).map((k) => cache.delete(k)));
}

async function purge() {
  currentUser = null;
  const keys = await caches.keys();
  await Promise.all(keys.filter((k) => k.startsWith(FILES_PREFIX)).map((k) => caches.delete(k)));
  for (const name of keys.filter((k) => k.startsWith(SHELL_PREFIX))) {
    await (await caches.open(name)).delete(PAGE);
  }
}

self.addEventListener('message', (event) => {
  const data = event.data || {};
  if (data.type === 'user') {
    event.waitUntil(forUser(data.user));
  } else if (data.type === 'cache-files') {
    event.waitUntil(keepFiles(data.user || currentUser, data.urls));
  } else if (data.type === 'purge') {
    event.waitUntil(purge());
  }
});
