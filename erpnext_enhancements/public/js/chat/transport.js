/**
 * HTTP transport for the SPA. Plain `fetch` against `/api/method/...`.
 *
 * **No `frappe.xcall`, and no `frappe.*` at all.** The SPA is served from a website route,
 * not from Desk, so the Desk bundle is not on the page — a dependency on `frappe.call` works
 * in a developer's Desk tab and fails on the real page. Everything the transport needs (the
 * CSRF token, the site name) comes off `window.EE_CHAT_BOOT`, which `www/chat.py` put there.
 *
 * One `call()` for everything, so the CSRF header, the error shape and the "session expired"
 * handling exist once. Frappe answers a whitelisted method as `{"message": ...}`; unwrapping
 * that in every caller is how one caller forgets.
 */

const BOOT = (typeof window !== "undefined" && window.EE_CHAT_BOOT) || {};

/**
 * The CSRF token, from whichever of the two pages this module is running on.
 *
 * The SPA gets it from `window.EE_CHAT_BOOT` (put there by `www/chat.py`). The **bubble**
 * imports this same module from inside the Desk bundle, where `EE_CHAT_BOOT` does not exist
 * and `frappe.csrf_token` does. Read lazily rather than captured at module load: Desk sets
 * `frappe.csrf_token` during boot, and a value captured when the bundle evaluated is empty on
 * exactly the page loads that matter.
 *
 * A missing token is not defended against here — Frappe answers 417 and `call()` surfaces it.
 * Silently sending no token would produce "the button does nothing", which is worse.
 */
function csrfToken() {
	if (BOOT.csrf_token) return BOOT.csrf_token;
	try {
		return (typeof window !== "undefined" && window.frappe && window.frappe.csrf_token) || "";
	} catch (e) {
		return "";
	}
}

/** Endpoint names, written once. A typo in a dotted path is a 404 at runtime. */
export const M = {
	BOOTSTRAP: "erpnext_enhancements.chat.api.rooms.get_bootstrap",
	ROOMS: "erpnext_enhancements.chat.api.rooms.get_rooms",
	ROOM: "erpnext_enhancements.chat.api.rooms.get_room",
	MEMBERS: "erpnext_enhancements.chat.api.rooms.get_members",
	LAST_OPEN: "erpnext_enhancements.chat.api.rooms.set_last_open_room",

	NEW_DM: "erpnext_enhancements.chat.api.conversations.create_direct_message",
	NEW_GROUP: "erpnext_enhancements.chat.api.conversations.create_group",
	PEOPLE: "erpnext_enhancements.chat.api.conversations.search_people",

	MESSAGES: "erpnext_enhancements.chat.api.history.get_messages",
	THREAD: "erpnext_enhancements.chat.api.history.get_thread",
	CONTEXT: "erpnext_enhancements.chat.api.history.get_message_context",

	SEND: "erpnext_enhancements.chat.api.compose.send_message",
	EDIT: "erpnext_enhancements.chat.api.compose.edit_message",
	DELETE: "erpnext_enhancements.chat.api.compose.delete_message",
	UPLOAD_INFO: "erpnext_enhancements.chat.api.compose.prepare_upload",

	SEARCH: "erpnext_enhancements.chat.api.search.search_messages",
	MENTION_TARGETS: "erpnext_enhancements.chat.api.mentions.search_mention_targets",

	MARK_READ: "erpnext_enhancements.chat.api.readstate.mark_read",
	MARK_ALL_READ: "erpnext_enhancements.chat.api.readstate.mark_all_read",
	UNREAD: "erpnext_enhancements.chat.api.readstate.get_unread_state",
	READ_MARKS: "erpnext_enhancements.chat.api.readstate.get_read_marks",

	TYPING: "erpnext_enhancements.chat.api.presence.set_typing",
	HEARTBEAT: "erpnext_enhancements.chat.api.presence.heartbeat",
	GOODBYE: "erpnext_enhancements.chat.api.presence.goodbye",
	PRESENCE: "erpnext_enhancements.chat.api.presence.get_presence",
};

/** A refusal the SPA can recognise, so "you were removed from this room" degrades quietly
 *  rather than raising a modal. */
export class ChatCallError extends Error {
	constructor(message, status, payload) {
		super(message);
		this.name = "ChatCallError";
		this.status = status;
		this.payload = payload;
		this.forbidden = status === 403;
		this.missing = status === 404;
	}
}

export async function call(method, args, options) {
	const opts = options || {};
	const res = await fetch(`/api/method/${method}`, {
		method: "POST",
		credentials: "same-origin",
		headers: {
			"Content-Type": "application/json",
			Accept: "application/json",
			"X-Frappe-CSRF-Token": csrfToken(),
		},
		body: JSON.stringify(args || {}),
		signal: opts.signal,
		// `keepalive` for the unload flush. `navigator.sendBeacon` cannot set the CSRF header
		// — it sends a bare POST — so Frappe rejects it, which is exactly how "it never marks
		// the last message read when I close the tab" happens. `fetch(keepalive: true)` keeps
		// the headers and survives unload, and is the documented fallback in §4.7.
		keepalive: !!opts.keepalive,
	});

	let payload = null;
	try {
		payload = await res.json();
	} catch (e) {
		payload = null;
	}

	if (!res.ok) {
		throw new ChatCallError(errorMessage(payload, res.status), res.status, payload);
	}
	return payload ? payload.message : null;
}

/**
 * Upload one file through Frappe's own endpoint, then hand the resulting `File` docname to
 * `send_message`. **Upload first, then send**, so a failed upload never produces a message
 * pointing at nothing.
 *
 * `is_private: 1` always. A public file lives under `sites/<site>/public/files/` and is
 * served by the web server with no authentication at all — anyone with the URL gets the
 * bytes, and no hook of ours is consulted. This is the single decision that makes attachment
 * security correct by construction, and the server refuses a non-private file as well.
 *
 * Attached to the ROOM at upload time and re-pointed at the message by `send_message`,
 * because the message does not exist yet.
 */
export function upload(file, room, onProgress) {
	// XMLHttpRequest rather than fetch, for one reason: `fetch` still has no upload-progress
	// event. Per-file progress and a working Cancel are both requirements (§4.9), and both
	// need the xhr — which is why this returns `{promise, abort}` rather than a bare promise.
	const xhr = new XMLHttpRequest();

	const promise = new Promise((resolve, reject) => {
		const form = new FormData();
		form.append("file", file, file.name);
		form.append("is_private", "1");
		// Attached to the ROOM at upload time; `send_message` re-points it at the message,
		// which does not exist yet.
		form.append("doctype", "Chat Room");
		form.append("docname", room);

		xhr.open("POST", "/api/method/upload_file", true);
		xhr.withCredentials = true;
		xhr.setRequestHeader("X-Frappe-CSRF-Token", csrfToken());
		xhr.upload.onprogress = (ev) => {
			if (onProgress && ev.lengthComputable) onProgress(ev.loaded / ev.total);
		};
		xhr.onload = () => {
			let payload = null;
			try {
				payload = JSON.parse(xhr.responseText);
			} catch (e) {
				payload = null;
			}
			if (xhr.status >= 200 && xhr.status < 300 && payload && payload.message) {
				resolve(payload.message);
			} else {
				reject(new ChatCallError(errorMessage(payload, xhr.status), xhr.status, payload));
			}
		};
		xhr.onerror = () => reject(new ChatCallError("Upload failed", 0, null));
		xhr.onabort = () => reject(new ChatCallError("Upload cancelled", 0, null));
		xhr.send(form);
	});

	return { promise, abort: () => xhr.abort() };
}

export function boot() {
	return BOOT;
}

function errorMessage(payload, status) {
	if (payload) {
		// Frappe puts the useful text in different places depending on how it threw.
		const exc = payload._server_messages || payload.exception || payload.message;
		if (typeof exc === "string" && exc.trim()) {
			try {
				const parsed = JSON.parse(exc);
				const first = Array.isArray(parsed) ? parsed[0] : parsed;
				const inner = typeof first === "string" ? JSON.parse(first) : first;
				if (inner && inner.message) return String(inner.message);
			} catch (e) {
				return exc.replace(/<[^>]*>/g, "").trim() || `Request failed (${status})`;
			}
		}
	}
	if (status === 403) return "That conversation is no longer available.";
	if (status === 417 || status === 400) return "That request was refused.";
	return `Request failed (${status})`;
}
