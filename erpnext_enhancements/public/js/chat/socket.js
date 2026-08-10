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
		this.io = io;
		this.socket = io(url, {
			withCredentials: true,
			// The site name is how the Node server namespaces rooms; a multi-site bench serves
			// every site from one socket process and gets this wrong silently without it.
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

		this.socket.on("connect_error", () => this._status("error"));

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
];

function socketUrl(b) {
	// Same host, framework port. Behind the Google load balancer /socket.io/ is proxied on
	// 443 and the explicit port is wrong, so an explicit port is used only when the page
	// itself is on a dev port.
	const { protocol, hostname, port } = window.location;
	const devPort = port && port !== "80" && port !== "443";
	if (devPort && b.socketio_port) return `${protocol}//${hostname}:${b.socketio_port}`;
	return `${protocol}//${window.location.host}`;
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
