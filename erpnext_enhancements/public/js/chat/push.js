/**
 * Web Push registration — the client half. Shared by the SPA and the Desk bubble.
 *
 * NEVER PROMPTS ON PAGE LOAD. `Notification.requestPermission()` called without a gesture is
 * the single fastest way to have every person in the company press Block once and never be
 * offered notifications again — the browser remembers "denied" permanently and there is no
 * API to ask a second time. So {@link ensure} only ever attaches to an existing grant, and
 * {@link enable} is called from a click and nowhere else.
 *
 * SCOPE. The worker registers at `/chat/`, not at root, because a service worker registration
 * is keyed by (storage key, SCOPE URL) — a second script asking for the same scope REPLACES
 * the first, and `kiosk-sw.js` and `wall-sw.js` are already at root. Narrowing needs no
 * `Service-Worker-Allowed` header and costs nothing: a push subscription belongs to the
 * REGISTRATION rather than to the pages it controls, so this worker receives push for the
 * whole origin while controlling only /chat/.
 *
 * The sharp edge that follows: `navigator.serviceWorker.getRegistration()` with no argument
 * resolves against the CURRENT page, so from a Desk page it hands back the kiosk or wall
 * worker instead. Every lookup here passes the scope explicitly.
 */

import { M, call } from "./transport.js";

/** Cached so two surfaces on one page do not both register. */
let registering = null;

/**
 * Attach to push if — and only if — permission has already been granted.
 *
 * Called on load by both surfaces. It is what keeps a subscription fresh: browsers reissue
 * the same endpoint on every load and the server uses that to move `last_seen`, which is what
 * the 60-day stale sweep measures against. A device that stops calling this is a device that
 * eventually gets retired, which is the intended behaviour rather than a leak.
 */
export async function ensure() {
	if (!supported()) return null;
	if (Notification.permission !== "granted") return null;
	return attach();
}

/**
 * Turn push on. **Must be called from a user gesture.**
 *
 * Returns one of "granted" | "denied" | "unsupported" | "unavailable" so the caller can say
 * something true rather than failing silently — a person who has denied notifications at the
 * browser level still gets the bell and the badge, and the interface has to say so instead of
 * looking broken.
 */
export async function enable() {
	if (!supported()) return "unsupported";

	const config = await pushConfig();
	if (!config || !config.enabled || !config.application_server_key) return "unavailable";

	const permission = await Notification.requestPermission();
	if (permission !== "granted") return permission;

	await attach();
	return "granted";
}

/** Turn push off for this device, on both sides. */
export async function disable() {
	if (!supported()) return false;
	const registration = await registrationFor();
	if (!registration) return false;

	const subscription = await registration.pushManager.getSubscription();
	if (!subscription) return false;

	// Server first. If the browser unsubscribes and the round trip then fails, the server
	// keeps pushing to an endpoint that no longer exists — which is not harmful (it answers
	// 410 and the row is pruned) but it is a request per message until it happens.
	await call(M.PUSH_UNSUBSCRIBE, { endpoint: subscription.endpoint }).catch(() => {});
	await subscription.unsubscribe().catch(() => {});
	return true;
}

/** Whether this browser can do Web Push at all. */
export function supported() {
	return (
		typeof window !== "undefined" &&
		"serviceWorker" in navigator &&
		"PushManager" in window &&
		"Notification" in window
	);
}

/**
 * Tell the worker to clear a room's banners, without a round trip through the push service.
 *
 * The in-page half of cross-surface dismissal: when this tab reads a room, its own operating
 * system notifications for that room should go immediately rather than waiting for the data
 * push the server sends to the person's OTHER devices.
 */
export async function clearRoom(room) {
	if (!supported() || !room) return;
	const registration = await registrationFor();
	const worker = registration && (registration.active || navigator.serviceWorker.controller);
	if (!worker) return;
	worker.postMessage({ type: "chat-clear", tag: "chat-room-" + room });
}

// ---------------------------------------------------------------- internals

async function attach() {
	if (registering) return registering;
	registering = (async () => {
		const config = await pushConfig();
		if (!config || !config.enabled || !config.application_server_key) return null;

		const registration = await register(config);
		if (!registration) return null;

		let subscription = await registration.pushManager.getSubscription();
		if (!subscription) {
			subscription = await registration.pushManager.subscribe({
				// Required by every browser: a push that shows no notification is not permitted,
				// and Chrome revokes the permission of an origin that tries.
				userVisibleOnly: true,
				applicationServerKey: urlBase64ToUint8Array(config.application_server_key),
			});
		}

		const json = subscription.toJSON();
		await call(M.PUSH_SUBSCRIBE, {
			endpoint: subscription.endpoint,
			p256dh: json.keys && json.keys.p256dh,
			auth: json.keys && json.keys.auth,
			user_agent: navigator.userAgent,
		});
		return subscription;
	})()
		.catch(() => null)
		.finally(() => {
			registering = null;
		});
	return registering;
}

async function register(config) {
	try {
		const build = (window.EE_CHAT_BOOT && window.EE_CHAT_BOOT.build) || "dev";
		return await navigator.serviceWorker.register(
			config.worker + "?v=" + encodeURIComponent(build),
			{
				scope: config.scope,
				// The worker script itself is served from a raw www/ path with no content hash,
				// so the HTTP cache is the only thing between a deploy and a stale worker. This
				// bypasses it for the script and its imports; the ?v= token is the second belt.
				updateViaCache: "none",
			},
		);
	} catch (e) {
		return null;
	}
}

function registrationFor() {
	// Always by scope. Without the argument this resolves against the current page and returns
	// the root-scope kiosk or wall worker from a Desk page.
	return navigator.serviceWorker.getRegistration("/chat/").catch(() => null);
}

let configPromise = null;
function pushConfig() {
	if (!configPromise) {
		configPromise = call(M.PUSH_CONFIG).catch((err) => {
			// Do not memoise a *transient* failure. The promise itself is what is cached, so a
			// single refusal used to resolve `null` forever: `attach()` returns early on a null
			// config and nothing calls it again after boot, so push was dead for the rest of
			// the tab's session — silently, with no error anywhere and no way for the person
			// to tell that notifications had stopped.
			//
			// A throttle or a dropped connection is transient. A genuine "push is not
			// configured on this site" answer is not, and that one arrives as a resolved
			// response rather than a rejection, so it still memoises exactly as before.
			if (err && (err.throttled || !err.status)) configPromise = null;
			return null;
		});
	}
	return configPromise;
}

/**
 * base64url string to the Uint8Array `applicationServerKey` requires.
 *
 * It will not accept a string, and it will not accept standard base64 — the `-` and `_` have
 * to be translated back before decoding, and the padding has to be restored because the
 * server sends it unpadded like every other Web Push value.
 */
export function urlBase64ToUint8Array(value) {
	const padded = value + "=".repeat((4 - (value.length % 4)) % 4);
	const raw = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
	const out = new Uint8Array(raw.length);
	for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
	return out;
}
