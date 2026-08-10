/**
 * Route parsing, route building, and the three-tier restoration resolver.
 *
 * Every function here is PURE — no DOM, no fetch, no storage, no clock. That is the whole
 * point: `erpnext_enhancements` has no Frappe integration-test job in CI, so the bench-free
 * tier is the only tier with automatic regression protection, and anything that matters is
 * pushed into a function a plain `node` script can call. `scripts/test_chat_routes.js`
 * exercises all of it.
 *
 * The route shape is a CONTRACT shared by three consumers — this router, Phase 4's
 * notification deep links, and Phase 5's `chat_message` citation resolver — and the
 * server-side builder (`erpnext_enhancements/chat/links.py::build_chat_route`) is the one
 * that mints them. This module parses that shape and rebuilds it for the address bar;
 * `buildRoute` below and `build_chat_route` there must agree byte for byte, which is what
 * `test_chat_routes.js` and `tests/test_chat_links.py` each assert against the same table.
 */

/** The mount path. Everything below is relative to it. */
export const BASE = "/chat";

/** Route kinds, as a closed set so a typo is a crash rather than a silent no-match. */
export const KIND = {
	ROOT: "root",
	ROOM: "room",
	SEARCH: "search",
	TRITON: "triton",
};

/**
 * Parse a pathname + search string into a route object.
 *
 * Tolerant of unknown query parameters by design: a link shared from a future phase, or one
 * carrying a tracking parameter somebody's mail client added, must still open the right
 * conversation rather than falling back to the room list. Unknown params are simply ignored.
 *
 * @param {string} pathname e.g. "/chat/room/CHAT-ROOM-01"
 * @param {string} search   e.g. "?message=chatmsg-1&thread=chatmsg-0"
 * @returns {{kind: string, room: ?string, message: ?string, thread: ?string, query: ?string, conversation: ?string}}
 */
export function parseRoute(pathname, search) {
	const params = parseQuery(search);
	const path = String(pathname || "").replace(/\/+$/, "") || BASE;
	const rest = path.startsWith(BASE) ? path.slice(BASE.length) : "";
	const parts = rest.split("/").filter(Boolean).map(decodeSegment);

	const base = {
		kind: KIND.ROOT,
		room: null,
		message: null,
		thread: null,
		query: params.q || null,
		conversation: null,
	};

	if (!parts.length) return base;

	if (parts[0] === "room" && parts[1]) {
		return {
			...base,
			kind: KIND.ROOM,
			room: parts[1],
			message: params.message || null,
			thread: params.thread || null,
		};
	}
	if (parts[0] === "search") {
		return { ...base, kind: KIND.SEARCH };
	}
	if (parts[0] === "triton") {
		return { ...base, kind: KIND.TRITON, conversation: parts[1] || null };
	}
	// An unrecognised sub-path is the root, not an error. The server serves this shell for
	// every path under /chat, so a mistyped URL must land somewhere usable.
	return base;
}

/**
 * Build the origin-relative route for a target. The inverse of {@link parseRoute}.
 *
 * Parameter ORDER is fixed at `message` then `thread`, and empty values are dropped rather
 * than emitted as `?message=`. Both matter more than they look: Phase 4 compares these URLs
 * for equality when de-duplicating notifications and stores them in `Notification Log.link`,
 * so two calls describing the same target must produce the same bytes — including the same
 * bytes as the Python builder.
 */
export function buildRoute(target) {
	const t = target || {};
	if (t.kind === KIND.SEARCH) {
		return `${BASE}/search` + queryString([["q", t.query]]);
	}
	if (t.kind === KIND.TRITON) {
		return `${BASE}/triton` + (t.conversation ? `/${encodeSegment(t.conversation)}` : "");
	}
	if (t.room) {
		return (
			`${BASE}/room/${encodeSegment(t.room)}` +
			queryString([
				["message", t.message],
				["thread", t.thread],
				["q", t.query],
			])
		);
	}
	return BASE;
}

/**
 * THE PRECEDENCE RULE, as one function so it can be tested rather than inferred.
 *
 * 1. **The URL wins, always.** If the incoming route names a room, that is the
 *    conversation, full stop. A deep link from a notification, a citation or a pasted URL
 *    must never be overridden by restored state. This is the tier people get wrong, and
 *    getting it wrong means notification deep links intermittently dump users in the wrong
 *    room — intermittently, because it only shows up when the user happens to have restored
 *    state pointing somewhere else.
 * 2. **Session handoff** — the record the bubble wrote immediately before navigating.
 *    Already validated (nonce, expiry) by `handoff.js`; this function takes the result.
 * 3. **Server-side last-open room** — cold loads, other devices, both storages empty.
 *
 * Anything unusable falls through to the next tier rather than erroring, and an empty
 * everything is the room list, which is a legitimate destination and not a failure.
 *
 * @param {object} route    from {@link parseRoute}
 * @param {?object} handoff validated handoff record, or null
 * @param {?object} boot    `{room, thread}` from the server, or null
 * @param {function(string): boolean} [canRead] optional gate; a room that fails it is
 *        skipped and the resolver falls to the next tier. Used for the "removed from the
 *        room" case, which must show a quiet notice rather than a blank screen.
 */
export function resolveRestoration(route, handoff, boot, canRead) {
	const readable = typeof canRead === "function" ? canRead : () => true;
	const from = (source, room, extra) => ({
		source,
		room,
		message: (extra && extra.message) || null,
		thread: (extra && extra.thread) || null,
		draft: (extra && extra.draft) || "",
		anchor: (extra && extra.anchor) || null,
		anchorRatio: extra && typeof extra.anchorRatio === "number" ? extra.anchorRatio : null,
		surface: (extra && extra.surface) || "coworker",
	});

	if (route && route.kind === KIND.SEARCH) {
		return { source: "url", room: null, kind: KIND.SEARCH, query: route.query || "" };
	}
	if (route && route.kind === KIND.TRITON) {
		return { source: "url", room: null, kind: KIND.TRITON, conversation: route.conversation };
	}

	// Tier 1.
	if (route && route.room && readable(route.room)) {
		return from("url", route.room, { message: route.message, thread: route.thread });
	}

	// Tier 2.
	if (handoff && handoff.room && readable(handoff.room)) {
		return from("handoff", handoff.room, {
			thread: handoff.thread,
			message: handoff.anchor_message,
			draft: handoff.draft,
			anchor: handoff.anchor_message,
			anchorRatio: handoff.anchor_offset_ratio,
			surface: handoff.surface,
		});
	}

	// Tier 3.
	if (boot && boot.room && readable(boot.room)) {
		return from("boot", boot.room, { thread: boot.thread });
	}

	return { source: "empty", room: null, message: null, thread: null, draft: "", surface: "coworker" };
}

/**
 * Step down one tier from a destination that turned out to be unreadable.
 *
 * §4.3's failure behaviour, written as its own function because the fall-back has to happen
 * at *load* time too — the room was readable when the link was made and is not now. Thread
 * first (the message was deleted but the room is fine), then room, then the list.
 */
export function degrade(resolution) {
	const r = resolution || {};
	if (r.thread || r.message) {
		return { ...r, thread: null, message: null, anchor: null, degradedFrom: "thread" };
	}
	if (r.room) {
		return { source: r.source, room: null, message: null, thread: null, draft: "", degradedFrom: "room" };
	}
	return { source: "empty", room: null, message: null, thread: null, draft: "" };
}

// --------------------------------------------------------------------------- internals

function parseQuery(search) {
	const out = {};
	const raw = String(search || "").replace(/^\?/, "");
	if (!raw) return out;
	for (const pair of raw.split("&")) {
		if (!pair) continue;
		const eq = pair.indexOf("=");
		const key = decodeSegment(eq < 0 ? pair : pair.slice(0, eq));
		const value = eq < 0 ? "" : decodeSegment(pair.slice(eq + 1));
		if (key && !(key in out)) out[key] = value;
	}
	return out;
}

function queryString(pairs) {
	const parts = [];
	for (const [key, value] of pairs) {
		const v = value == null ? "" : String(value).trim();
		if (v) parts.push(`${key}=${encodeSegment(v)}`);
	}
	return parts.length ? `?${parts.join("&")}` : "";
}

/**
 * `encodeURIComponent`, matching Python's `quote(..., safe="")`.
 *
 * The two disagree on exactly five characters — ! ' ( ) * — which `encodeURIComponent`
 * leaves alone and `quote(safe="")` percent-encodes. Room and message names are `hash`
 * autonames (32 hex characters) so in practice neither encodes anything, but "in practice"
 * is not what a URL two systems compare for equality runs on.
 */
export function encodeSegment(value) {
	return encodeURIComponent(String(value == null ? "" : value)).replace(
		/[!'()*]/g,
		(c) => "%" + c.charCodeAt(0).toString(16).toUpperCase()
	);
}

function decodeSegment(value) {
	try {
		return decodeURIComponent(String(value == null ? "" : value));
	} catch (e) {
		// A malformed percent-escape in a pasted URL. Returning the raw text beats throwing
		// on the way to rendering a page.
		return String(value == null ? "" : value);
	}
}
