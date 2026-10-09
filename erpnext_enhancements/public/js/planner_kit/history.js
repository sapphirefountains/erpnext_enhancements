/*
 * Planner kit: Back closes the planner's drawer or menu, without leaving the planner or reloading it.
 *
 * Nik's rule is that Back and Forward never break, and a drawer over a calendar is exactly where
 * people press Back expecting it to close. The planners move between weeks and views only through
 * `frappe.set_route`, so this has to live alongside frappe v16's router without fighting it. What
 * that router does, read from `git show origin/version-16:frappe/public/js/frappe/router.js`:
 *
 *   - ONE `popstate` listener on `window`, added at Desk boot (bubble phase), which calls
 *     `frappe.router.route()`: it re-parses the address, pushes Route History, closes the open
 *     dialog, and re-renders the page — `container.change_to` triggers the page's "show" (the
 *     planners' handle_route, which reloads their data) and scrolls to the top. On a Back off an
 *     entry with the same address that is a reload of the planner, which is what this avoids.
 *   - `set_route` pushes a PATH only (`history.pushState(null, null, path)`), or replaces when
 *     `frappe.route_flags.replace_route` is set, skips an unchanged path, and clears route_flags
 *     only once its promise settles (after a 100 ms timeout and every request in flight).
 *
 * The mechanism:
 *
 *   1. While any kit overlay (drawer, menu) is open there is exactly one kit history entry on top:
 *      `history.pushState({planner_kit: <id>}, "")`, no URL, so the address never changes. It is
 *      pushed from the click that opens the first overlay (a user activation, so Chrome's history
 *      intervention never marks it skippable). A second overlay shares it.
 *   2. The kit decides about each popstate at the moment frappe's own listener asks the router to
 *      route: `install()` wraps `frappe.router.route` (env.gate_router) so that, when it is called
 *      during a popstate (`window.event`), the kit's `on_router_pop` answers first, and for a
 *      popstate the kit owns the router does not route at all: nothing reloads, nothing scrolls.
 *      Every other call passes straight through, unchanged. Why not simply listen first: a popstate
 *      is dispatched AT `window`, and a browser runs the listeners there in the order they were
 *      added, a capture listener included (checked in Chromium 152 in the Phase 6A session: on an
 *      element capture runs first, on window it does not). frappe's listener is added at Desk boot,
 *      long before this bundle loads, so no listener of ours could ever run ahead of it.
 *      `install()` checks that `window.event` is set during a dispatch; where it is not, or there is
 *      no router to wrap, no entry is ever pushed (Esc and the close button still work; Back
 *      behaves as it always did). A plain popstate listener, after the router's, keeps the kit's
 *      own state right if a popstate ever reaches it without passing the router.
 *   3. The popstates it owns: a Back off its entry while the planner's route is still the one the
 *      overlay opened on (it closes every overlay, and any frappe dialog on top, as the router
 *      would have); its own cleanup Back (below); and a Forward onto an entry of a drawer that has
 *      closed, on the same route, which opens that drawer again. Everything else is the router's.
 *   4. An overlay closed any other way (×, Esc, Save, an action) steps back off the entry, so the
 *      next Back is never spent on a drawer that is already shut. It does that on a timer of 0 and
 *      sets `route_flags.replace_route` until then: when the close is the first half of
 *      `dialog.hide(); frappe.set_route("Form", "Task", ...)`, the set_route REPLACES the kit's
 *      entry instead of stacking on it, and there is nothing left to step back off.
 *   5. A planner that changes its own route while a drawer is open does it through `route(fn,
 *      keep)`: the kit's entry is replaced by the new route, and with `keep` the drawer stays open
 *      and gets a new entry on top of it once the route has landed. Any other route change closes
 *      every overlay (the router's own `set_history` closes dialogs the same way); an entry it
 *      buried behind it is then treated as the router's.
 *
 * `create_guard` takes its window and router as arguments, so tests/test_planner_phase6a.py drives
 * it under node against a fake history that behaves as a browser's does.
 */

export const HISTORY_KEY = "planner_kit";
// Our own history.back() lands within milliseconds; after this long it stops waiting for it.
export const BACK_SETTLE_MS = 1500;

export function create_guard(env) {
	const win = env.win;
	const state = {
		enabled: false,
		installed: false,
		stack: [],
		page: null,
		armed: null, // {id, route}: the kit's entry, while it is (believed to be) on top
		releasing: null, // {id, timer}: a cleanup Back waiting for its timer
		expect: null, // {timer}: our own history.back() in flight
		closed: [], // [{id, route, reopen}]: drawers a Forward may open again
		rearm: false,
		set_replace: false,
		seq: 0,
	};

	const safe = (fn) => {
		try {
			return fn();
		} catch (e) {
			if (env.warn) env.warn(e);
			return undefined;
		}
	};
	const route = () => safe(() => env.route()) || "";
	const page = () => safe(() => env.page()) || "";
	const current_token = () => {
		try {
			const value = win.history.state;
			return value && value[HISTORY_KEY] ? value[HISTORY_KEY] : null;
		} catch (e) {
			return null;
		}
	};

	function new_id() {
		state.seq += 1;
		return `pk${Date.now().toString(36)}${state.seq.toString(36)}`;
	}

	function push() {
		const id = new_id();
		try {
			win.history.pushState({ [HISTORY_KEY]: id }, "");
			state.armed = { id, route: route() };
		} catch (e) {
			// A sandboxed frame may refuse; the overlay still works, Back just does not close it.
			state.armed = null;
		}
	}

	function unset_replace() {
		if (!state.set_replace) return;
		state.set_replace = false;
		safe(() => env.set_replace(false));
	}

	function set_replace() {
		state.set_replace = true;
		safe(() => env.set_replace(true));
	}

	function cancel_release() {
		if (!state.releasing) return null;
		const id = state.releasing.id;
		env.clear_timeout(state.releasing.timer);
		state.releasing = null;
		unset_replace();
		return id;
	}

	function clear_expect() {
		if (!state.expect) return;
		env.clear_timeout(state.expect.timer);
		state.expect = null;
	}

	function arm() {
		if (!state.enabled) return;
		const token = current_token();
		if (state.armed && token === state.armed.id) return; // already on top: share it
		if (state.releasing && token === state.releasing.id) {
			// The entry of an overlay that has just closed is still there: take it over rather
			// than stacking a second one (an action in a menu that opens a drawer).
			const id = cancel_release();
			state.armed = { id, route: route() };
			return;
		}
		push();
	}

	// Step back off the kit's entry after an overlay closed any way but Back (see 4. above).
	function release() {
		const armed = state.armed;
		state.armed = null;
		if (!state.enabled || !armed || current_token() !== armed.id) return;
		set_replace();
		const timer = env.set_timeout(() => {
			if (!state.releasing || state.releasing.id !== armed.id) return;
			state.releasing = null;
			unset_replace();
			// A route change in the meantime replaced the entry: nothing to step off.
			if (current_token() !== armed.id) return;
			clear_expect();
			state.expect = { timer: env.set_timeout(clear_expect, BACK_SETTLE_MS) };
			try {
				win.history.back();
			} catch (e) {
				clear_expect();
			}
		}, 0);
		state.releasing = { id: armed.id, timer };
	}

	function remember(id, route_key, reopen) {
		if (!id || typeof reopen !== "function") return;
		state.closed = state.closed
			.filter((entry) => entry.id !== id)
			.concat([{ id, route: route_key, reopen }])
			.slice(-8);
	}

	function close_all(how) {
		const all = state.stack.splice(0);
		all.reverse().forEach((overlay) => safe(() => overlay.close(how)));
	}

	const guard = {
		get enabled() {
			return state.enabled;
		},
		// For tests and for the drawer: the overlays open, bottom first.
		stack() {
			return state.stack.slice();
		},
		top() {
			return state.stack[state.stack.length - 1] || null;
		},
		armed_on_top() {
			return !!(state.armed && current_token() === state.armed.id);
		},

		install() {
			if (state.installed) return guard;
			state.installed = true;
			state.enabled = guard.window_event_works() && safe(() => env.gate_router(guard.on_router_pop)) === true;
			if (state.enabled) win.addEventListener("popstate", guard.on_late_pop);
			safe(() => env.on_route_change(guard.on_route_change));
			return guard;
		},

		// Is `window.event` the event being dispatched, inside a listener? The router gate reads it to
		// know that a call to route() comes from a popstate (see 2. above).
		window_event_works() {
			if (!win || !win.history || typeof win.history.pushState !== "function") return false;
			const name = "planner-kit-event-probe";
			let seen = false;
			const Ctor = win.Event || (typeof Event === "function" ? Event : null);
			if (!Ctor) return false;
			const probe = new Ctor(name);
			const listener = () => {
				seen = win.event === probe;
			};
			try {
				win.addEventListener(name, listener);
				win.dispatchEvent(probe);
			} catch (e) {
				return false;
			} finally {
				safe(() => win.removeEventListener(name, listener));
			}
			return seen;
		},

		// Called by the wrapped frappe.router.route during a popstate, before it routes. True: the
		// popstate is the kit's, and the router leaves the page exactly as it is.
		on_router_pop(e) {
			if (e) e.__planner_kit_seen = true;
			return guard.on_pop(e);
		},

		// After the router's own listener: only for a popstate that never passed the gate (the kit's
		// state stays right; the router has already done whatever it does).
		on_late_pop(e) {
			if (!e || e.__planner_kit_seen) return;
			e.__planner_kit_seen = true;
			guard.on_pop(e);
		},

		// An overlay opens: it joins the stack and the kit's entry is pushed if there is none.
		open(overlay) {
			if (!state.stack.length) state.page = page();
			if (!state.stack.includes(overlay)) state.stack.push(overlay);
			arm();
		},

		// An overlay has closed (it calls this itself). Anything above it goes with it. When the last
		// one goes: "user" steps back off the entry; "back" finds it already gone; "route" leaves it
		// to the route change that replaced or buried it.
		close(overlay, how, reopen) {
			const index = state.stack.indexOf(overlay);
			if (index < 0) return;
			const above = state.stack.splice(index);
			above
				.slice(1)
				.reverse()
				.forEach((item) => safe(() => item.close("parent")));
			if (state.stack.length) return;
			state.rearm = false;
			const armed = state.armed;
			if (how === "user") {
				if (armed) remember(armed.id, armed.route, reopen);
				release();
			} else {
				state.armed = null;
			}
		},

		// The page changes its own route while an overlay may be open (see 5. above). `keep` keeps
		// the overlays open over the new route, with a fresh entry; otherwise they close.
		route(fn, keep) {
			if (!state.stack.length) return fn();
			const ours = guard.armed_on_top() || (state.releasing && current_token() === state.releasing.id);
			cancel_release();
			if (keep) state.rearm = true;
			else {
				state.armed = null;
				close_all("route");
			}
			if (ours && state.enabled) safe(() => env.set_replace(true));
			try {
				return fn();
			} finally {
				// Read already by set_route; left set, it would make the next tap a replace too.
				if (ours && state.enabled) safe(() => env.set_replace(false));
			}
		},

		on_route_change() {
			if (!state.stack.length) {
				state.rearm = false;
				return;
			}
			const same_page = page() === state.page;
			if (state.rearm && same_page) {
				state.rearm = false;
				state.armed = null;
				if (state.enabled) push();
				return;
			}
			// Moved by something else: the overlays belonged to the screen that has gone.
			state.rearm = false;
			state.armed = null;
			close_all("route");
		},

		// What a popstate means to the kit; true when it is the kit's (see 3. above) and the router must
		// leave the page alone.
		on_pop(e) {
			if (!state.enabled) return false;
			const token = e && e.state && e.state[HISTORY_KEY] ? e.state[HISTORY_KEY] : null;
			if (state.expect) {
				// The landing of our own cleanup Back.
				clear_expect();
				return true;
			}
			if (!state.stack.length) {
				// Forward (or Back) onto the entry of a drawer that has closed, on the same screen:
				// open it again there.
				const dead = token && state.closed.find((entry) => entry.id === token);
				if (!dead || route() !== dead.route) return false;
				state.closed = state.closed.filter((entry) => entry !== dead);
				state.armed = { id: token, route: dead.route };
				safe(() => dead.reopen());
				if (!state.stack.length) release();
				return true;
			}
			if (!state.armed || token === state.armed.id) return false; // still on (or back onto) our entry
			if (route() !== state.armed.route) return false; // the screen has moved on: the router's
			const left = state.armed;
			state.armed = null;
			state.rearm = false;
			safe(() => env.hide_open_dialog());
			const top = guard.top();
			remember(left.id, left.route, top && top.reopen);
			close_all("back");
			return true;
		},
	};
	return guard;
}
