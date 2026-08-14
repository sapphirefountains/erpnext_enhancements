/**
 * The socket.io connection, room subscription, and the reconnect resync.
 *
 * THREE FACTS THIS FILE IS BUILT AROUND
 * =====================================
 *
 * 1. **Frappe realtime is fire-and-forget and not durable.** An event fired while a client is
 *    disconnected is simply gone. So realtime is an accelerator and the database is the
 *    truth, and every feature that uses it has a reconciliation path. The resync is built
 *    first and runs on every reconnect, so the recovery path is exercised constantly rather
 *    than rarely — which matters because the Google load balancer *will* disconnect us.
 *
 * 2. **A refused `doc_subscribe` is SILENT.** The socket server permission-checks the join by
 *    calling back into `chat_room_has_permission` under the joining user's session; if that
 *    says no, the promise is simply never settled. There is no error event. So a client must
 *    never treat "I emitted `doc_subscribe`" as "I am subscribed" — the only proof of
 *    subscription is receiving something, and the only correctness guarantee is the resync
 *    fetch that runs regardless.
 *
 * 3. **Membership is checked once, at join.** Eviction is therefore cooperative: removing
 *    somebody from a room closes the REST door immediately and the socket door on their next
 *    reconnect. Accepted and documented rather than hidden (ADR §H.4.3); it is why removing a
 *    member also publishes `chat_room_updated`, which makes a well-behaved client leave.
 *
 * The SPA is NOT inside the Desk bundle, so `frappe.realtime` does not exist on the page and
 * the connection is established here. The socket.io client itself is loaded from Frappe's own
 * asset path — the one script this application takes from outside its bundle, because
 * bundling a second copy of socket.io next to the one the framework already serves is how the
 * two end up on different protocol versions after a Frappe upgrade.
 */

import { boot } from "./transport.js";

/** Frappe serves the matching socket.io client here. */
const SOCKETIO_SRC = "/assets/frappe/node_modules/socket.io-client/dist/socket.io.min.js";

/** Debounce for the resync, so a flapping connection does not hammer the server. */
const RESYNC_DEBOUNCE_MS = 750;

export class ChatSocket {
	/**
	 * @param {object} handlers `{onEvent(name, payload), onResync(reason), onStatus(state)}`
	 */
	constructor(handlers) {
		this.handlers = handlers || {};
		this.io = null;
		this.socket = null;
		this.joined = new Set();
		this.connected = false;
		this._resyncTimer = null;
		/** Set once we have ever been connected, so the FIRST connect is not a "reconnect". */
		this._everConnected = false;
	}

	async connect() {
		const b = boot();
		const io = await loadSocketIo();
		if (!io) {
			// No socket: the SPA still works, entirely on polling-free manual refresh plus the
			// resync on visibility change. Degraded, and it says so, rather than looking broken.
			this._status("unavailable");
			return null;
		}

		const url = socketUrl(b);
		if (!url) {
			// No site name on the boot payload, so there is no namespace to join and every
			// connection would be refused as "Invalid namespace". Failing here, loudly, beats
			// connecting to a namespace the server will reject in silence.
			console.error(
				"[chat] no site_name in EE_CHAT_BOOT, so realtime cannot connect. " +
					"Check www/chat.py's boot payload."
			);
			this._status("unavailable");
			return null;
		}

		this.io = io;
		this.socket = io(url, {
			withCredentials: true,
			// The site is carried by the NAMESPACE in `url`, not by this query parameter — the
			// server's auth middleware compares `socket.nsp.name` and never reads the query.
			// Kept because `get_site_name` consults it as one of its fallbacks, so the two
			// agreeing costs nothing and disagreeing would be a puzzle.
			query: { site: b.site_name || "" },
			transports: ["websocket", "polling"],
			reconnection: true,
			reconnectionDelay: 1000,
			reconnectionDelayMax: 10000,
		});

		this.socket.on("connect", () => {
			this.connected = true;
			this._status("connected");
			// Re-join everything: a reconnect is a NEW socket and the server remembers nothing
			// about the rooms the old one was in.
			const rooms = Array.from(this.joined);
			this.joined.clear();
			rooms.forEach((room) => this.joinRoom(room));
			this._scheduleResync(this._everConnected ? "reconnect" : "connect");
			this._everConnected = true;
		});

		this.socket.on("disconnect", () => {
			this.connected = false;
			this._status("disconnected");
		});

		// A connect error used to set a status nobody surfaces, which is how a chat app ran for
		// weeks receiving no realtime events at all while looking healthy. The server's reason
		// is the whole diagnosis — "Invalid namespace", "Invalid origin", "Missing cookie" each
		// point at a different file — so it goes to the console rather than being swallowed.
		this.socket.on("connect_error", (err) => {
			this._status("error");
			console.error("[chat] realtime connection refused:", (err && err.message) || err);
		});

		for (const name of EVENT_NAMES) {
			this.socket.on(name, (payload) => {
				if (this.handlers.onEvent) this.handlers.onEvent(name, payload || {});
			});
		}

		return this.socket;
	}

	/**
	 * Join a room's document room. Permission-checked server-side, and silently refused when
	 * it fails — see the header. Idempotent.
	 */
	joinRoom(room) {
		if (!room || !this.socket) return;
		if (this.joined.has(room)) return;
		this.joined.add(room);
		this.socket.emit("doc_subscribe", "Chat Room", room);
	}

	leaveRoom(room) {
		if (!room || !this.socket) return;
		this.joined.delete(room);
		this.socket.emit("doc_unsubscribe", "Chat Room", room);
	}

	/**
	 * Ask for a resync. Debounced, because a flapping connection would otherwise produce one
	 * full refetch per flap and the flaps come in bursts.
	 */
	_scheduleResync(reason) {
		if (this._resyncTimer) clearTimeout(this._resyncTimer);
		this._resyncTimer = setTimeout(() => {
			this._resyncTimer = null;
			if (this.handlers.onResync) this.handlers.onResync(reason);
		}, RESYNC_DEBOUNCE_MS);
	}

	_status(state) {
		if (this.handlers.onStatus) this.handlers.onStatus(state);
	}

	close() {
		if (this._resyncTimer) clearTimeout(this._resyncTimer);
		if (this.socket) this.socket.close();
		this.joined.clear();
		this.connected = false;
	}
}

/**
 * Every event the client listens for. Generated from the same list the server publishes
 * (`chat/realtime.py::ALL_EVENTS`) so a name added on one side and not the other is visible
 * — a client listening for an event nobody publishes is indistinguishable, from the client's
 * side, from a quiet room. `tests/test_chat_event_contract.py` asserts the two lists match.
 */
export const EVENT_NAMES = [
	"chat_message_created",
	"chat_message_edited",
	"chat_message_deleted",
	"chat_typing",
	"chat_typing_stopped",
	"chat_presence",
	"chat_read_receipt",
	"chat_room_updated",
	"chat_unread_updated",
	"chat_mention",
	// Phase 4. Fires whenever a read lands on ANY surface — this tab, another tab, or a
	// notification tapped on a phone — carrying the new high-water mark and the bell rows it
	// cleared. It is what makes reading on a phone clear the desktop badge.
	// (No quoted text in this comment: the parity gate extracts quoted strings from this
	// array and would read one as an event name nothing publishes.)
	"chat_notification_state",
];

/**
 * Where to connect, **including the site namespace**.
 *
 * THE NAMESPACE IS NOT DECORATION — IT IS THE AUTHENTICATION CHECK
 * ===============================================================
 *
 * Frappe's socket server namespaces every connection by site, and its middleware compares the
 * namespace against the site it resolved from the request:
 *
 *     let namespace = socket.nsp.name.slice(1);      // "" for the default namespace
 *     if (namespace != get_site_name(socket)) next(new Error("Invalid namespace"));
 *
 * So a client that connects to the bare origin lands in `/`, the comparison is
 * `"" != "erp.example.com"`, and the connection is **rejected**. This function used to return
 * the bare origin and pass the site as a *query parameter* instead — which that check never
 * consults.
 *
 * **The failure was completely silent, and each symptom pointed away from the cause.** The
 * engine.io handshake succeeds, because it runs before any namespace middleware — so
 * `/socket.io/?EIO=4&transport=polling` answers with a real `sid` and the transport looks
 * healthy. The rejection happens inside the Node process, so nothing reaches ERPNext's Error
 * Log. And the SPA's own `connect_error` handler only sets a status nobody surfaces. The
 * observable result was a chat app that never received a single realtime event — not another
 * person's message, not `@triton`'s reply, **not even its own** — and looked fine doing it.
 *
 * Matching `frappe/public/js/frappe/socketio_client.js`'s `get_host()` exactly, because the
 * two clients talk to the same server and a hand-rolled variant is how they drift apart.
 */
export function socketUrl(b, loc) {
	// Same host, framework port. Behind the Google load balancer /socket.io/ is proxied on
	// 443 and the explicit port is wrong, so an explicit port is used only when the page
	// itself is on a dev port.
	const { protocol, hostname, host, port } = loc || window.location;
	const devPort = port && port !== "80" && port !== "443";
	const origin =
		devPort && b.socketio_port
			? `${protocol}//${hostname}:${b.socketio_port}`
			: `${protocol}//${host}`;

	// No site name means no namespace to join. Returning the bare origin would connect to `/`
	// and be refused; returning `origin + "/"` would be refused the same way with an uglier
	// URL. Either is broken, so `connect()` refuses to try and says so.
	const site = (b && b.site_name) || "";
	return site ? `${origin}/${site}` : "";
}

let _socketIoPromise = null;

function loadSocketIo() {
	if (typeof window !== "undefined" && window.io) return Promise.resolve(window.io);
	if (_socketIoPromise) return _socketIoPromise;
	_socketIoPromise = new Promise((resolve) => {
		const script = document.createElement("script");
		script.src = SOCKETIO_SRC;
		script.async = true;
		script.onload = () => resolve(window.io || null);
		// Resolving null rather than rejecting: the SPA degrades to no-realtime, which is a
		// worse experience and a working one. A rejected promise here would take the mount
		// with it.
		script.onerror = () => resolve(null);
		document.head.appendChild(script);
	});
	return _socketIoPromise;
}
