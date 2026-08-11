/**
 * Chat push service worker (served at /chat-sw.js, registered at scope /chat/).
 *
 * THIS WORKER CACHES NOTHING AND HAS NO `fetch` HANDLER, AND BOTH ARE DELIBERATE.
 *
 * A worker with no fetch handler never intercepts a request, so it cannot serve a stale
 * asset to anything. That matters here more than it sounds: this app already learned the
 * expensive version of the lesson in v1.229.0, when the kiosk worker answered for the whole
 * of /assets/erpnext_enhancements/ cache-first with `ignoreSearch`, which turned the `?v=`
 * deploy token into decoration and froze the training player four releases behind in any
 * browser that had ever opened /kiosk. `ci.yml` guards the kiosk worker for exactly that.
 * A precache list added here "just for the icon" reproduces it.
 *
 * ---------------------------------------------------------------------------------------
 * WHY SCOPE /chat/ RATHER THAN ROOT
 * ---------------------------------------------------------------------------------------
 *
 * A service worker registration is keyed by (storage key, SCOPE URL) — not by script URL.
 * The spec is explicit that registering an identical scope replaces the existing
 * registration. So three scripts all asking for scope `/` compete for one slot per origin,
 * and a browser that opens /kiosk and then the desk flips the registration back and forth.
 * `kiosk-sw.js` and `wall-sw.js` both already register at root, so root was never available.
 *
 * Narrowing is always permitted and needs no `Service-Worker-Allowed` header — only widening
 * past the script's own directory does. And narrowing costs nothing that matters: a push
 * subscription belongs to a REGISTRATION, not to the pages it controls, so this worker
 * receives push and shows notifications for the whole origin while controlling only /chat/.
 *
 * The consequence for callers, which is the one sharp edge: `getRegistration()` with no
 * argument resolves against the CURRENT page, so from a Desk page at /app/... it returns the
 * root-scope kiosk or wall worker, not this one. Always pass the scope, or keep the object
 * that `register()` returned.
 *
 * ---------------------------------------------------------------------------------------
 * AND WHY IT DELETES NOTHING
 * ---------------------------------------------------------------------------------------
 *
 * Both existing workers' `activate` handlers run `caches.keys()` and delete every cache whose
 * name is not their own. CacheStorage is per-ORIGIN, not per-worker, so each of them already
 * wipes the other's. This worker owns no cache and deletes none, which is the only way for a
 * third worker to coexist without joining that fight.
 */

const VERSION = new URL(self.location.href).searchParams.get('v') || 'dev';

/** Where a click lands when the payload carries no usable link. */
const FALLBACK_URL = '/chat';

self.addEventListener('install', () => {
  // Nothing to precache. skipWaiting so a deploy's new worker takes over on the next page
  // load rather than waiting for every chat tab in the company to be closed — a push worker
  // that is one release behind is not a rendering nuisance, it is notifications that
  // silently do not arrive.
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  // claim(), and NO cache sweep. See the header: deleting caches this worker does not own
  // would wipe the kiosk's offline shell and the wall's last-good data.
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', (event) => {
  const payload = readPayload(event);

  // A data message asking the worker to clear a conversation's banners, sent when the person
  // read the room on another device. THIS IS THE STEP EVERYONE FORGETS, and without it a
  // notification read on a phone sits on the desktop until it is dismissed by hand.
  if (payload.kind === 'chat-clear') {
    event.waitUntil(clearTag(payload.tag));
    return;
  }

  event.waitUntil(show(payload));
});

self.addEventListener('notificationclick', (event) => {
  const data = (event.notification && event.notification.data) || {};
  event.notification.close();

  // Post the read back BEFORE navigating. Reading on any surface must clear every other one,
  // and a click is a read — so the server advances the mark, marks the bell row, publishes to
  // this person's other tabs and pushes the close instruction to their other devices.
  event.waitUntil(
    Promise.all([reportRead(data), focusOrOpen(data.url || FALLBACK_URL)]),
  );
});

self.addEventListener('notificationclose', (event) => {
  // Dismissing a banner is NOT reading the message, so this does not advance the read mark.
  // Treating a swipe-away as a read is how somebody loses a conversation they meant to come
  // back to. It only clears the OS-level siblings for that room, so a dismissal on one device
  // does not leave four identical banners on the others.
  const data = (event.notification && event.notification.data) || {};
  if (data.tag) event.waitUntil(clearTag(data.tag));
});

self.addEventListener('pushsubscriptionchange', (event) => {
  // Browsers rotate a subscription without asking, and when they do the old endpoint stops
  // working and nothing tells the server. Re-subscribing here with the SAME application
  // server key and re-registering is what stops a device going quiet permanently after a
  // rotation nobody observed.
  event.waitUntil(resubscribe(event));
});

self.addEventListener('message', (event) => {
  // In-page dismissal: the SPA read a room in this tab, so clear that room's banners without
  // a round trip to the push service.
  const data = event.data || {};
  if (data.type === 'chat-clear' && data.tag) clearTag(data.tag);
});

// ---------------------------------------------------------------- helpers

function readPayload(event) {
  try {
    return (event.data && event.data.json()) || {};
  } catch (e) {
    // A payload that is not JSON is a bug on the server, but showing nothing is worse than
    // showing something generic: a silent push is indistinguishable from a broken one.
    return {};
  }
}

function show(payload) {
  const title = payload.title || 'New message';
  const room = payload.room_label ? String(payload.room_label) : '';

  // `body` is empty unless Chat Settings.push_show_preview is on, and it ships off — a lock
  // screen is legible to anyone glancing at a phone on a table, and this chat carries
  // customer names and prices. When it is off the notification says who and where, which is
  // enough to decide whether to open it.
  const body = payload.body || (room ? 'in ' + room : '');

  return self.registration.showNotification(title, {
    body: body,
    // Stable per room, so a later message REPLACES the previous banner rather than stacking.
    // Twenty messages to a busy room must not produce twenty banners, and this is the half of
    // that the browser does for us.
    tag: payload.tag || 'chat',
    renotify: false,
    data: { url: payload.url || FALLBACK_URL, room: payload.room, message: payload.message, tag: payload.tag },
    icon: '/assets/erpnext_enhancements/images/chat-notification.png',
    timestamp: Date.now(),
  });
}

function clearTag(tag) {
  return self.registration
    .getNotifications({ tag: tag })
    .then((list) => list.forEach((n) => n.close()))
    .catch(() => {});
}

function focusOrOpen(url) {
  return self.clients
    .matchAll({ type: 'window', includeUncontrolled: true })
    .then((clientList) => {
      // includeUncontrolled matters: this worker controls only /chat/, so the Desk tab the
      // person actually has open is NOT one of its clients. Without it, clicking a
      // notification would open a second window beside the ERPNext tab they were already in.
      for (const client of clientList) {
        if (client.url.indexOf('/chat') !== -1 && 'focus' in client) {
          client.postMessage({ type: 'chat-open', url: url });
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    })
    .catch(() => self.clients.openWindow(url));
}

function reportRead(data) {
  if (!data || !data.room) return Promise.resolve();
  // No CSRF header is available in a worker, so this endpoint is written to accept the
  // session cookie alone and to do nothing a GET could not: it advances a monotonic read mark
  // for the CALLER's own membership and takes no user parameter.
  return fetch('/api/method/erpnext_enhancements.chat.notifications.api.read_from_notification', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ room: data.room, message: data.message || null }),
  }).catch(() => {});
}

function resubscribe(event) {
  const key = (event.oldSubscription && event.oldSubscription.options && event.oldSubscription.options.applicationServerKey) || null;
  if (!key) return Promise.resolve();

  return self.registration.pushManager
    .subscribe({ userVisibleOnly: true, applicationServerKey: key })
    .then((subscription) => {
      const json = subscription.toJSON();
      return fetch('/api/method/erpnext_enhancements.chat.notifications.api.subscribe', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          endpoint: subscription.endpoint,
          p256dh: json.keys && json.keys.p256dh,
          auth: json.keys && json.keys.auth,
          user_agent: self.navigator ? self.navigator.userAgent : '',
        }),
      });
    })
    .catch(() => {});
}

// Referenced so a reader can confirm which build is installed from devtools without
// guessing, and so the constant is not dead code a cleanup deletes.
self.__EE_CHAT_SW_VERSION = VERSION;
