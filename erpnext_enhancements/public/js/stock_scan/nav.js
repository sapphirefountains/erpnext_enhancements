/**
 * Browser Back and Forward for the Stock Scan page: history entries that never carry a URL.
 *
 * iOS Safari asks for camera permission again whenever the URL changes, and the camera is used
 * on every shelf. So the URL stays the label the phone first opened, and every entry is written
 * as `pushState(state, "")` / `replaceState(state, "")`, with no third argument
 * (tests/test_stock_scan_surface.py fails the build on any other call, and on one outside this
 * file). `location.href` is byte-identical throughout.
 *
 * SCREEN entries map to a snapshot `{view, back}` kept HERE, in memory: item and supplier names
 * never go into `history.state`, which browsers write to disk. A MARKER entry is pushed when a
 * sheet opens (the camera is one), so the phone's Back closes it rather than leaving the
 * screen. One marker covers however many sheets are stacked.
 *
 * The "Report a problem" form is NOT one of ours: capture/panel.js pushes and removes its own
 * entry (`{ee_capture: id}`) and answers Back itself while it is open, and app.js stands aside
 * then. Any entry this document did not write — the form's, one from an earlier load of the
 * tab, one past MAX_ENTRIES — is never restored from: the page stays where it is and stamps the
 * entry as its own current screen. The form's entry while the form is still open is never
 * written over at all: app.js leaves `popstate` alone then, and `screen()` checks
 * `history.state`, not only its own model.
 *
 * Push only from a tap. Chrome's Back button skips an entry the page added without a user tap
 * since the one before it, and a camera read is not a tap. So a screen reached from a marker
 * takes over the marker's entry, and a screen the page opens by itself replaces the one it was
 * opened from.
 *
 * The document's first entry is stamped with replaceState, never pushed, so Back from the first
 * screen leaves the page as it always did; and `history.back()` is only ever called onto an
 * entry of ours, checked against `history.state` first, so script never leaves the page.
 *
 * DOM-free: scripts/test_stock_scan_client.mjs drives it against a fake session history.
 */

/** Chrome and Firefox keep 50 session-history entries per tab and drop the oldest; so does this. */
export const MAX_ENTRIES = 50;

/** How long to wait for the popstate our own history.back() causes before going on without it. */
export const TRAVERSE_TIMEOUT_MS = 1500;

function sameList(a, b) {
	return a.length === b.length && a.every((x, i) => x === b[i]);
}

export class NavHistory {
	/**
	 * `history`: window.history, or null to switch this off (the page's own stack still works).
	 * `key`: this document's stamp; an entry from an earlier load of the tab carries another.
	 * `hooks`: {busy() — a navigation is loading, later(fn, ms), cancel(timer)}.
	 */
	constructor(history, key, hooks) {
		const h = hooks || {};
		this.h = history || null;
		this.enabled = !!(
			history &&
			typeof history.pushState === "function" &&
			typeof history.replaceState === "function" &&
			typeof history.back === "function"
		);
		this.key = key;
		this.busy = h.busy || (() => false);
		this.later = h.later || ((fn, ms) => setTimeout(fn, ms));
		this.cancel = h.cancel || ((t) => clearTimeout(t));
		this.snaps = new Map(); // id -> {view, back} | {marker: true, parent}
		this.trail = []; // ids in history order, as far as this document knows them
		this.pos = -1;
		this.shown = null; // the screen entry whose snapshot is on screen
		this.next = 0;
		this.overlays = 0; // sheets open now
		this.marker = null; // our marker's id, while it is (believed) current
		this.releasing = false; // sheets all closed; marker waiting to be stepped back over
		this.traversing = false; // our own history.back() is in flight
		this.timer = null; // ... and gives up waiting for its popstate at TRAVERSE_TIMEOUT_MS
		this.queue = []; // history writes waiting for that traversal to land
		this.waiting = []; // whenQuiet callbacks, run once it has
	}

	current() {
		return this.pos < 0 ? null : this.trail[this.pos];
	}

	/** Written by this document, and not the report form's (anything carrying `ee_capture` is the form's). */
	ours(state) {
		return !!(state && typeof state === "object" && state.ee_ss === this.key && !("ee_capture" in state));
	}

	/** Is the browser really on entry `id`? Something else (the report form) may have pushed over it. */
	onEntry(id) {
		let state = null;
		try {
			state = this.h.state;
		} catch (e) {
			return false;
		}
		return this.ours(state) && state.id === id;
	}

	/** One history call, never with a URL. A refusal (Safari throttles bursts) costs the entry, not the screen. */
	write(kind, snap, extra) {
		const id = ++this.next;
		try {
			if (this.pos < 0 && "scrollRestoration" in this.h) this.h.scrollRestoration = "manual"; // render() scrolls
			const state = Object.assign({ ee_ss: this.key, id }, extra);
			if (kind === "push") this.h.pushState(state, "");
			else this.h.replaceState(state, "");
		} catch (e) {
			return null;
		}
		if (kind === "push" || this.pos < 0) {
			for (const gone of this.trail.splice(this.pos + 1)) this.snaps.delete(gone);
			this.trail.push(id);
			this.pos = this.trail.length - 1;
		} else {
			this.snaps.delete(this.trail[this.pos]);
			this.trail[this.pos] = id;
		}
		this.snaps.set(id, snap);
		while (this.trail.length > MAX_ENTRIES) {
			this.snaps.delete(this.trail.shift());
			this.pos -= 1;
		}
		// Snapshots of entries the trail lost track of (a foreign entry resets it) are kept so Back
		// onto them still restores; only so many, oldest first.
		if (this.snaps.size > MAX_ENTRIES * 2) {
			for (const old of this.snaps.keys()) {
				if (this.snaps.size <= MAX_ENTRIES) break;
				if (old !== this.shown && old !== id && this.trail.indexOf(old) === -1) this.snaps.delete(old);
			}
		}
		return id;
	}

	/** A screen. `entry` "push" (default) | "replace". The document's first is always a replace. */
	screen(snap, entry) {
		if (!this.enabled) return;
		if (this.traversing) {
			this.queue.push(() => this.screen(snap, entry));
			return;
		}
		let kind = entry === "replace" || this.pos < 0 ? "replace" : "push";
		if (this.pos >= 0 && !this.onEntry(this.current())) {
			// Something pushed over our entry (the report form, whose entry is never ours to
			// write over): this screen goes on top as a new entry, and a marker under it stays
			// the marker. app.js navigates nowhere while the form is open, so this is the
			// backstop, not the plan.
			kind = "push";
		} else if (this.marker !== null && this.marker === this.current()) {
			// Reached from a sheet or the camera: take over the marker's entry. Nothing is left to
			// step back over, and nothing is added without a tap.
			kind = "replace";
			this.marker = null;
			this.releasing = false;
		}
		const id = this.write(kind, snap);
		if (id !== null) this.shown = id;
	}

	overlayOpened() {
		if (!this.enabled || ++this.overlays > 1) return;
		if (this.marker !== null && this.marker === this.current()) {
			this.releasing = false; // closed and reopened in one go (job picker -> "Where from?")
			return;
		}
		const push = () => {
			if (this.overlays > 0 && this.marker === null) {
				this.marker = this.write("push", { marker: true, parent: this.shown }, { marker: 1 });
			}
		};
		if (this.traversing) this.queue.push(push);
		else push();
	}

	overlayClosed() {
		if (!this.enabled) return;
		this.overlays = Math.max(0, this.overlays - 1);
		if (this.overlays || this.marker === null) return;
		if (this.marker !== this.current()) {
			this.marker = null; // the browser already stepped off it (Back closed it)
			return;
		}
		this.releasing = true;
		this.later(() => this.release(), 0);
	}

	/** app.js calls this when the last load finishes: a close that started a navigation waits for it. */
	settled() {
		if (this.releasing) this.release();
	}

	release() {
		if (!this.releasing || this.overlays || this.busy()) return;
		this.stepOff();
	}

	/** Step back over our marker, now: nothing is left open on it and nothing will land on it. */
	stepOff() {
		this.releasing = false;
		const id = this.marker;
		this.marker = null;
		if (id !== null && id === this.current() && this.onEntry(id)) this.traverse();
	}

	traverse() {
		if (this.traversing) return;
		this.traversing = true;
		this.timer = this.later(() => {
			this.timer = null;
			if (!this.traversing) return;
			this.traversing = false;
			this.landed(true);
		}, TRAVERSE_TIMEOUT_MS);
		try {
			this.h.back();
		} catch (e) {
			this.cancel(this.timer);
			this.timer = null;
			this.traversing = false;
			this.landed(true);
		}
	}

	/** Is the entry behind this one the in-page parent `view` over `back`? Then the back link is Back. */
	behindIs(view, back) {
		if (!this.enabled || this.traversing || this.queue.length || this.pos < 1) return false;
		if (this.current() !== this.shown || !this.onEntry(this.shown)) return false;
		const s = this.snaps.get(this.trail[this.pos - 1]);
		return !!(s && !s.marker && s.view === view && sameList(s.back, back));
	}

	/** The in-page back link, after `behindIs` said yes. popstate draws the screen. */
	back() {
		this.traverse();
	}

	/**
	 * Run `fn` once none of our history work is in flight. A marker still waiting for a load is
	 * stepped back over first: the caller has dropped that load. For the report form, which
	 * pushes its own entry (capture/panel.js): that entry must sit on a screen of ours. On top of
	 * our marker, the marker's release would step back off the FORM's entry instead, and the
	 * form would take that Back for the person's.
	 */
	whenQuiet(fn) {
		if (!this.enabled) {
			fn();
			return;
		}
		if (this.releasing && !this.overlays) this.stepOff();
		if (this.traversing) this.waiting.push(fn);
		else fn();
	}

	/** Our traversal landed or gave up. `keep`: run the history writes queued behind it. */
	landed(keep) {
		const ops = this.queue.splice(0);
		if (keep) for (const op of ops) op();
		for (const fn of this.waiting.splice(0)) this.later(fn, 0);
	}

	/**
	 * A popstate (or a bfcache pageshow). Moves the model to the entry the browser is on and says
	 * what the page must do: `close` (a sheet covered the page), `cancel` (Back while a navigation
	 * started from a sheet was loading), `snap` (a different screen to show).
	 */
	popped(state) {
		const out = {};
		if (!this.enabled || this.pos < 0) return out;
		if (this.traversing) {
			this.cancel(this.timer);
			this.timer = null;
			this.traversing = false;
		}
		const snap = this.ours(state) ? this.snaps.get(state.id) : null;
		if (snap && state.id === this.current()) {
			// A bfcache restore or a spurious event: nothing moved. A marker with nothing open on
			// it and nothing on its way is dead weight, though; it becomes the screen under it.
			if (snap.marker && !this.overlays && !this.releasing) {
				const target = this.snaps.get(snap.parent) || this.snaps.get(this.shown);
				this.marker = null;
				const id = target ? this.write("replace", target) : null;
				if (id !== null) this.shown = id;
			}
			this.landed(true);
			return out;
		}
		out.close = this.overlays > 0;
		out.cancel = out.close || this.releasing;
		this.marker = null;
		this.releasing = false;
		const was = this.snaps.get(this.shown);
		if (!snap) {
			// Not an entry this document wrote: the report form's, one from an earlier load of this
			// tab (a reload keeps its entries), or one past MAX_ENTRIES. Nothing to rebuild from.
			// Stay on this screen and make the entry this document's.
			this.trail = [];
			this.pos = -1;
			const id = was ? this.write("replace", was) : null;
			if (id !== null) this.shown = id;
		} else {
			const at = this.trail.indexOf(state.id);
			if (at === -1) {
				this.trail = [state.id];
				this.pos = 0;
			} else this.pos = at;
			let target = snap;
			if (snap.marker) {
				// Forward onto a marker whose sheet is gone: it cannot reopen, so the entry
				// becomes a copy of the screen under it.
				target = this.snaps.get(snap.parent) || was;
				const id = target ? this.write("replace", target) : null;
				if (id !== null) this.shown = id;
			} else this.shown = state.id;
			if (target && target !== was) out.snap = target;
		}
		// The person moved: history writes queued for the screen they left are superseded.
		this.landed(!(out.snap || out.close));
		return out;
	}
}
