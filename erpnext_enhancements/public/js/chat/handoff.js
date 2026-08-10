/**
 * The bubble → SPA state handoff, and its reverse.
 *
 * Decision #8: expanding the bubble opens the SPA already in the conversation the user was
 * in, scrolled to where they were, with the thread they had open still open and their
 * unsent draft still in the composer. That record is written here and read here, by both
 * ends, from one module — a second copy of the shape is how the two halves drift.
 *
 * TWO STORAGES, ON PURPOSE:
 *
 * * `sessionStorage` is per-tab and survives a same-tab navigation, so it is the primary
 *   and expanding navigates with `location.assign` rather than opening a tab.
 * * `localStorage` is the mirror, because a user may middle-click the expand control (or it
 *   may be a real `<a href>` a browser opens in a new tab), and a new tab gets a fresh empty
 *   `sessionStorage`. The mirror carries a NONCE and a 60-second TTL and is
 *   consumed-and-deleted on read. Without both, a week-old record silently hijacks a fresh
 *   `/chat` visit — the user clicks "Chat" in a menu and lands in a conversation they had
 *   open last Tuesday, which reads as the application being broken rather than as a stale
 *   cache.
 *
 * Everything except the two `read*`/`write*` entry points is pure, so
 * `scripts/test_chat_handoff.js` exercises the validation with a plain object instead of a
 * browser.
 */

export const HANDOFF_KEY = "ee_chat_handoff";
export const HANDOFF_VERSION = 1;

/** How long a localStorage mirror is honoured. Long enough for a tab to open; short enough
 *  that it cannot be mistaken for a preference. */
export const MIRROR_TTL_MS = 60 * 1000;

/**
 * Build the record. Pure — takes everything, reads nothing.
 *
 * `anchor_offset_ratio` rather than a pixel offset: pixels are meaningless across a
 * bubble-sized and a full-width viewport, and they are wrong again the moment an image
 * finishes loading. The anchor is a message docname plus that message's top edge as a
 * fraction of viewport height, so restoring is "load the page containing this message, then
 * put it back at 42% down" — which survives both the resize and the reflow.
 */
export function buildRecord(state, nowMs, nonce) {
	const s = state || {};
	return {
		v: HANDOFF_VERSION,
		room: s.room || null,
		thread: s.thread || null,
		anchor_message: s.anchorMessage || null,
		anchor_offset_ratio: typeof s.anchorRatio === "number" ? clampRatio(s.anchorRatio) : null,
		draft: typeof s.draft === "string" ? s.draft : "",
		surface: s.surface === "triton" ? "triton" : "coworker",
		triton_conversation: s.tritonConversation || null,
		ts: Number(nowMs) || 0,
		nonce: String(nonce || ""),
	};
}

/**
 * Validate a record read out of storage. Returns the record or `null`.
 *
 * @param {object} raw parsed JSON, or anything at all
 * @param {object} opts `{nowMs, requireFreshWithin, expectNonce}`
 *
 * `requireFreshWithin` is only applied to the localStorage mirror. The sessionStorage copy
 * has no TTL because `sessionStorage` already IS the lifetime — it dies with the tab — and
 * a TTL on it would break the legitimate case of a user who opened the bubble, went to
 * lunch, and then clicked expand.
 */
export function validateRecord(raw, opts) {
	const o = opts || {};
	if (!raw || typeof raw !== "object") return null;
	if (raw.v !== HANDOFF_VERSION) return null;
	if (!raw.room && raw.surface !== "triton") return null;

	if (o.expectNonce != null && String(raw.nonce || "") !== String(o.expectNonce)) return null;

	if (typeof o.requireFreshWithin === "number") {
		const age = Number(o.nowMs || 0) - Number(raw.ts || 0);
		if (!(age >= 0 && age <= o.requireFreshWithin)) return null;
	}

	return {
		v: raw.v,
		room: raw.room || null,
		thread: raw.thread || null,
		anchor_message: raw.anchor_message || null,
		anchor_offset_ratio:
			typeof raw.anchor_offset_ratio === "number" ? clampRatio(raw.anchor_offset_ratio) : null,
		draft: typeof raw.draft === "string" ? raw.draft : "",
		surface: raw.surface === "triton" ? "triton" : "coworker",
		triton_conversation: raw.triton_conversation || null,
		ts: Number(raw.ts) || 0,
		nonce: String(raw.nonce || ""),
	};
}

/**
 * Write both copies. Called on every meaningful change (throttled by the caller to ~500 ms)
 * and SYNCHRONOUSLY immediately before navigating on expand.
 *
 * The synchronous write before navigation is the load-bearing one. A throttled write that
 * has not fired yet when `location.assign` runs is a handoff that silently does not happen,
 * and it fails exactly for the user who clicks expand quickly — which is most of them.
 */
export function writeHandoff(state, storages, nowMs) {
	const nonce = makeNonce();
	const record = buildRecord(state, nowMs, nonce);
	const blob = JSON.stringify(record);
	safeSet(storages && storages.session, HANDOFF_KEY, blob);
	safeSet(storages && storages.local, HANDOFF_KEY, blob);
	return record;
}

/**
 * Read the handoff, preferring the per-tab copy, and CONSUME the mirror either way.
 *
 * Consuming unconditionally is deliberate: once this tab has looked, the mirror has done its
 * job, and leaving it behind is exactly the stale-hijack bug. The session copy is left in
 * place because the SPA rewrites it continuously and the reverse handoff reads it back.
 */
export function readHandoff(storages, nowMs) {
	const session = validateRecord(safeParse(safeGet(storages && storages.session, HANDOFF_KEY)), {});
	const mirrorRaw = safeParse(safeGet(storages && storages.local, HANDOFF_KEY));
	safeRemove(storages && storages.local, HANDOFF_KEY);

	if (session) return session;

	return validateRecord(mirrorRaw, { nowMs, requireFreshWithin: MIRROR_TTL_MS });
}

/** Drop both copies. Used when the SPA is left deliberately, and after a failed restore. */
export function clearHandoff(storages) {
	safeRemove(storages && storages.session, HANDOFF_KEY);
	safeRemove(storages && storages.local, HANDOFF_KEY);
}

// --------------------------------------------------------------------------- internals

function clampRatio(value) {
	if (!(value >= 0)) return 0;
	return value > 1 ? 1 : value;
}

function makeNonce() {
	try {
		if (typeof crypto !== "undefined" && crypto.getRandomValues) {
			const buf = new Uint8Array(8);
			crypto.getRandomValues(buf);
			return Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
		}
	} catch (e) {
		// fall through
	}
	// Not a security token — it distinguishes "the record this tab just wrote" from "a
	// record left over from a previous visit", and a timestamp is enough for that.
	return String(Date.now()) + Math.random().toString(16).slice(2, 10);
}

function safeGet(storage, key) {
	try {
		return storage ? storage.getItem(key) : null;
	} catch (e) {
		// Private-mode Safari throws on access, not just on write. A handoff that cannot be
		// read is a cold load, which is a supported path.
		return null;
	}
}

function safeSet(storage, key, value) {
	try {
		if (storage) storage.setItem(key, value);
	} catch (e) {
		// Quota, or private mode. The SPA falls back to the server-side last-open room.
	}
}

function safeRemove(storage, key) {
	try {
		if (storage) storage.removeItem(key);
	} catch (e) {
		/* see safeSet */
	}
}

function safeParse(text) {
	if (!text) return null;
	try {
		return JSON.parse(text);
	} catch (e) {
		return null;
	}
}
