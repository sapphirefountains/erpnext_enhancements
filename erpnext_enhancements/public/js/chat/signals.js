/**
 * Read receipts, typing, and presence — the client halves. All three are pure state machines
 * with the clock injected, so `scripts/test_chat_signals.js` drives them under plain `node`.
 *
 * These three signals are also THE INPUT TO PHASE 4's SUPPRESSION MATRIX (decision #3). This
 * phase reports; Phase 4 decides. Any inaccuracy here becomes a notification bug there, which
 * is why the read rule is stricter than "the room is open" and why the multi-tab union is
 * evaluated as "**no** client of this user has that room focused" rather than "the last tab
 * to report".
 */

// ---------------------------------------------------------------- read receipts

/** At most one `mark_read` call per this many ms, except for the forced flushes below. */
export const READ_FLUSH_MS = 2000;

/** How long a message must satisfy every read condition before it counts. A fast scroll past
 *  200 messages on the way to the bottom must not mark them all read. */
export const READ_DWELL_MS = 400;

/**
 * The batcher. Monotonic by construction: it never emits a value lower than one already
 * emitted, and it never lowers its own high-water mark.
 *
 * A message counts as read only when ALL of these hold (§4.7):
 *   1. its element is meaningfully visible (`IntersectionObserver`, not a 1px sliver);
 *   2. `document.visibilityState === "visible"`;
 *   3. `document.hasFocus()` — the window itself, not merely non-hidden. A chat tab behind a
 *      spreadsheet is not being read, and this is the condition people leave out;
 *   4. it has held for {@link READ_DWELL_MS}.
 *
 * The caller feeds `observe(seq, conditionsMet, now)`; this class owns the dwell timing, the
 * monotonicity and the flush cadence, and knows nothing about the DOM.
 */
export class ReadBatcher {
	constructor(options) {
		const o = options || {};
		this.flushMs = o.flushMs || READ_FLUSH_MS;
		this.dwellMs = o.dwellMs == null ? READ_DWELL_MS : o.dwellMs;
		/** The highest seq confirmed READ locally (dwell satisfied). */
		this.candidate = 0;
		/** The highest seq already handed to the server. Never decreases. */
		this.emitted = 0;
		// -Infinity, not 0: "no flush has happened yet" must mean "flush now", not "wait 2s
		// from the epoch". With a real `Date.now()` the difference is invisible — the first
		// call is 1.7e12 ms past zero — which is exactly why it survives review and only shows
		// up under a test clock, or in the one place it matters in production: a room opened
		// and closed inside the first two seconds of the page's own timebase.
		this.lastFlushAt = -Infinity;
		/** seq -> the timestamp at which its conditions first held continuously. */
		this.dwelling = new Map();
	}

	/** Seed from the server so a reload does not re-send a mark the server already has. */
	prime(seq) {
		const value = Number(seq) || 0;
		if (value > this.emitted) this.emitted = value;
		if (value > this.candidate) this.candidate = value;
	}

	/**
	 * Report one message's conditions. Returns true when it has just become read.
	 *
	 * Conditions that stop holding **reset the dwell**. Otherwise a message that flickered
	 * into view during a fast scroll accumulates dwell across separate appearances and gets
	 * marked read without ever having been on screen for 400ms at once.
	 */
	observe(seq, conditionsMet, now) {
		const value = Number(seq) || 0;
		if (value <= this.candidate) return false;

		if (!conditionsMet) {
			this.dwelling.delete(value);
			return false;
		}

		const since = this.dwelling.get(value);
		if (since == null) {
			this.dwelling.set(value, now);
			// A zero dwell is legitimate (tests, and reduced-motion users who disable it) and
			// must take effect on the same call rather than needing a second observation.
			if (this.dwellMs > 0) return false;
		} else if (now - since < this.dwellMs) {
			return false;
		}

		this.dwelling.delete(value);
		this.candidate = value;
		return true;
	}

	/**
	 * Whether a flush should happen now.
	 * @param {boolean} force set on room switch, window blur, tab hide and `pagehide`.
	 */
	shouldFlush(now, force) {
		if (this.candidate <= this.emitted) return false;
		return !!force || now - this.lastFlushAt >= this.flushMs;
	}

	/** Take the value to send. Returns 0 when there is nothing to send. */
	take(now) {
		if (this.candidate <= this.emitted) return 0;
		this.emitted = this.candidate;
		this.lastFlushAt = now;
		return this.emitted;
	}

	/**
	 * Accept the server's answer, which may be HIGHER than what we sent — another tab of the
	 * same user got there first. Taking the max in both directions is what stops two tabs
	 * sawing the mark back and forth.
	 */
	acknowledge(serverSeq) {
		const value = Number(serverSeq) || 0;
		if (value > this.emitted) this.emitted = value;
		if (value > this.candidate) this.candidate = value;
	}

	/** Reset for a different room. The high-water marks are per (user, room). */
	reset() {
		this.candidate = 0;
		this.emitted = 0;
		this.lastFlushAt = -Infinity;
		this.dwelling.clear();
	}
}

/**
 * **Pure.** Has `user` read `message`? Derived from the high-water mark, never from a
 * per-message row.
 *
 * The boundary is `<=`, not `<`: `last_read_seq` names the message the user has read, not
 * the one after it. Off by one here shows up as "the last message never shows as read",
 * which is the state everybody looks at.
 */
export function hasRead(messageSeq, lastReadSeq) {
	return (Number(messageSeq) || 0) <= (Number(lastReadSeq) || 0);
}

/**
 * **Pure.** Who has read this message, given the roster's marks.
 *
 * Above `avatarLimit` members the caller collapses to "Read by N" with a popover rather than
 * painting an avatar per reader — computing and rendering 30 avatars per message is neither
 * useful nor cheap. The limit is a server-supplied constant, not a magic number here.
 */
export function readersOf(messageSeq, marks, options) {
	const o = options || {};
	const exclude = o.exclude || null;
	const readers = [];
	for (const mark of marks || []) {
		if (exclude && mark.user === exclude) continue;
		if (hasRead(messageSeq, mark.last_read_seq)) readers.push(mark.user);
	}
	return readers;
}

// ---------------------------------------------------------------- typing

export const TYPING_THROTTLE_MS = 3000;
export const TYPING_EXPIRY_MS = 5000;

/**
 * Sender side. At most one emission per {@link TYPING_THROTTLE_MS} **while the composer has
 * content and that content has changed since the last emission**.
 *
 * Both conditions matter. Throttling alone still emits every 3 s for a user who is staring at
 * a half-written message; requiring a change alone emits on every keystroke. A user holding
 * down a key must not generate a call per keystroke, which is the shape this endpoint would
 * otherwise be DoS'd by from inside the building.
 */
export class TypingThrottle {
	constructor(throttleMs = TYPING_THROTTLE_MS) {
		this.throttleMs = throttleMs;
		// -Infinity for the same reason as ReadBatcher.lastFlushAt: the FIRST keystroke must
		// emit. Zero-initialised, it is throttled against the epoch, which is invisible with a
		// real clock and means "the indicator never appears" for the first thing anybody types
		// after the page's own timebase starts.
		this.lastEmitAt = -Infinity;
		this.lastText = "";
		this.active = false;
	}

	/** @returns {"typing"|"stopped"|null} what to send, if anything. */
	onInput(text, now) {
		const value = String(text || "");
		if (!value) {
			this.lastText = "";
			if (this.active) {
				this.active = false;
				return "stopped";
			}
			return null;
		}
		if (value === this.lastText) return null;
		this.lastText = value;
		if (now - this.lastEmitAt < this.throttleMs) return null;
		this.lastEmitAt = now;
		this.active = true;
		return "typing";
	}

	/** Send, blur, or an explicit clear. Always emits the stop if one is outstanding. */
	stop() {
		this.lastText = "";
		if (!this.active) return null;
		this.active = false;
		return "stopped";
	}
}

/**
 * Receiver side. Indicators expire locally after {@link TYPING_EXPIRY_MS} without a refresh —
 * the safety net for a sender who closed their laptop mid-word, and the reason the explicit
 * stop event is an optimisation rather than a mechanism.
 *
 * Scoped by thread, so "Jane is typing" does not appear in the main transcript while she is
 * replying in a thread.
 */
export class TypingRegistry {
	constructor(expiryMs = TYPING_EXPIRY_MS) {
		this.expiryMs = expiryMs;
		/** key `${room}|${thread||""}` -> Map<user, expiresAt> */
		this.byScope = new Map();
	}

	start(room, thread, user, now) {
		const key = scopeKey(room, thread);
		if (!this.byScope.has(key)) this.byScope.set(key, new Map());
		this.byScope.get(key).set(user, now + this.expiryMs);
	}

	stop(room, thread, user) {
		const scope = this.byScope.get(scopeKey(room, thread));
		if (scope) scope.delete(user);
	}

	/** Everyone still typing in this scope, expiries applied. */
	active(room, thread, now) {
		const scope = this.byScope.get(scopeKey(room, thread));
		if (!scope) return [];
		const live = [];
		for (const [user, expiresAt] of scope) {
			if (expiresAt > now) live.push(user);
			else scope.delete(user);
		}
		return live;
	}

	/** Realtime is not durable, so everything held is stale after a disconnect. */
	clearAll() {
		this.byScope.clear();
	}
}

/**
 * **Pure.** "Jane is typing" / "Jane and Sam are typing" / "3 people are typing".
 *
 * Never an unbounded list. A busy room otherwise renders a paragraph of names where a line
 * of status belongs, and it reflows the transcript every time somebody starts a word.
 */
export function typingLabel(names, translate) {
	const t = translate || ((s) => s);
	const list = (names || []).filter(Boolean);
	if (!list.length) return "";
	if (list.length === 1) return t("{0} is typing…").replace("{0}", list[0]);
	if (list.length === 2) return t("{0} and {1} are typing…").replace("{0}", list[0]).replace("{1}", list[1]);
	return t("{0} people are typing…").replace("{0}", String(list.length));
}

// ---------------------------------------------------------------- presence

/**
 * **Pure.** The client-side twin of `chat/api/presence.py::focus_state`.
 *
 * Kept in both languages deliberately: the server owns the decision Phase 4 will read, and
 * the client needs the same answer to render its own dot without waiting for a round trip.
 * Both are exercised against the same table of cases — three clients of one user, one focused
 * on room R; that client's key expiring; all keys expiring — so the two cannot drift into
 * disagreeing about the multi-tab case, which is the one everybody gets wrong.
 */
export function unionPresence(clients, now, ttlSeconds, room) {
	const live = (clients || []).filter((c) => now - (Number(c.ts) || 0) <= ttlSeconds);
	if (!live.length) return { state: "offline", focusedHere: false, clients: 0 };

	const focusedHere =
		room != null && live.some((c) => !!c.focused && (c.room || null) === room);
	const anyVisible = live.some((c) => (c.visibility || "visible") !== "hidden" || c.focused);

	return {
		state: anyVisible ? "online" : "away",
		focusedHere,
		clients: live.length,
	};
}

function scopeKey(room, thread) {
	return `${room || ""}|${thread || ""}`;
}
