/**
 * The Desk's end of the postMessage bridge to the framed Triton chat page.
 *
 * Two rules, and they are the whole security argument for the bridge:
 *
 * 1. LISTEN TO ONE SENDER. A message is handled only when its origin is Triton's configured
 *    origin AND its source is our own frame's window. Every Desk page receives `message` events
 *    from anything that can get a reference to the window (another frame on the page, a browser
 *    extension, a window this tab opened), and the messages handled here do real things: they open
 *    records and they trigger a sign-in ticket. The origin check alone is not enough, because a
 *    second frame from the same origin would pass it; the source check alone is not enough,
 *    because our frame could have been navigated somewhere else. Everything that fails either
 *    check is dropped without a reply and without a log line (a page that is being probed should
 *    not be told what it got wrong).
 * 2. POST TO ONE RECEIVER. Every message goes out with Triton's origin as the target, never "*".
 *    The first thing sent is a sign-in ticket. If the frame were ever showing some other site,
 *    the browser then refuses to deliver it.
 *
 * The bridge knows nothing about what the messages mean. It hands each accepted one to
 * `onMessage({ version, type, body })` and the loader decides.
 */

import { envelope, readMessage } from "./contract.js";

/**
 * @param {object}   opts
 * @param {Window}   opts.win          the Desk window
 * @param {string}   opts.origin       Triton's origin, e.g. "https://triton.example.com"
 * @param {Function} opts.frameWindow  () => our iframe's contentWindow, or null before it exists
 * @param {Function} opts.onMessage    called with { version, type, body } for each accepted message
 */
export function createBridge({ win, origin, frameWindow, onMessage }) {
	let listening = false;
	const stats = { accepted: 0, dropped: 0, posted: 0 };

	function onEvent(event) {
		const target = frameWindow();
		if (!event || event.origin !== origin || !target || event.source !== target) {
			stats.dropped += 1;
			return;
		}
		const message = readMessage(event.data);
		if (!message) {
			stats.dropped += 1;
			return;
		}
		stats.accepted += 1;
		onMessage(message);
	}

	return {
		stats: stats,
		start() {
			if (listening) return;
			listening = true;
			win.addEventListener("message", onEvent);
		},
		stop() {
			if (!listening) return;
			listening = false;
			win.removeEventListener("message", onEvent);
		},
		/** Post one message to the frame. False when there is no frame to post to. */
		post(type, fields) {
			const target = frameWindow();
			if (!target) return false;
			try {
				target.postMessage(envelope(type, fields), origin);
			} catch (e) {
				// A value that cannot be cloned (a function in a context object, say). The
				// message is lost; the panel is not.
				return false;
			}
			stats.posted += 1;
			return true;
		},
	};
}
