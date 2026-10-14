/*
 * scanner_kit: a page's sheets as route segments, so the phone's Back button closes them.
 *
 * Moved here (v1.666.0) from the Device Console and the Inventory Scanner Audit, which each
 * carried the same code: Camera Scan and Choose Employee are device-console/camera and
 * /employee, Camera Scan and Find Item are inventory-scanner-audit/camera and /find. Frappe v16's
 * router owns `popstate` on the Desk and closes the open dialog on every route change, so a sheet
 * with no entry of its own made Back leave the page. Back now closes the sheet and stays on the
 * page, and Forward opens it again where the page can.
 *
 *   const nav = sf_scanner.sheets({ route: "device-console", names: ["camera", "employee"],
 *                                   mark: "dc_sheet", back_timeout_ms: 1500 });
 *   nav.watch(wrapper);                           // once, from the page's constructor
 *   nav.open(name, show, reopen, took);           // from a tap (or a Forward, with reopen)
 *   nav.sheet = { name, dialog };                 // set by `show` as it puts the dialog up
 *   d.onhide = () => nav.closed(name, d, then);   // or nav.gone(d) and nav.leave(name, then)
 *   const { sheet, came } = nav.show({ reopen, hold });   // from on_page_show
 *   nav.current();  nav.hide(d);  nav.sheet;  nav.opening
 *
 * A sheet's route is pushed from the tap that opens it, and the sheet is shown only once the route
 * has settled: every route change closes the open dialog (the router's set_history and
 * container.change_to both do), this one included. Back then pops the segment and the router
 * closes the sheet with nothing more from here. A sheet closed any other way (a read, a pick, X,
 * Escape) steps back off its own entry, so Back never lands on a sheet that is already shut. The
 * entry behind a sheet's is always the page's own: one on the first show is replaced if it is a
 * pasted link, and stepped back off if it is the page's own (a reload), as is one Back or Forward
 * lands on that cannot be opened again.
 *
 * Only an entry this page pushed is a sheet's. Each is marked in history.state as it is pushed
 * (`mark`), because the same URL also arrives from outside: frappe records every route with a
 * second segment in Route History, and the awesome bar offers the most used as links. A sheet
 * opened from one of those would have another page behind it, so X, or a camera read's step back,
 * would land there. An unmarked sheet URL is handed to the page, as a pasted link's is — or, when
 * it was pushed over the page's own entry while that was showing, stepped back off, onto that
 * entry. A marked one on the first show (a reload, or Android restoring a discarded tab:
 * history.state survives both) is stepped back off too, since the entry behind a marked one is
 * always the page's own. "Showing" means no other Desk page has been shown since the last show:
 * frappe triggers "hide" on the page it leaves for another (`watch`).
 *
 * Each replace asked for here clears `route_flags.replace_route` as soon as set_route returns.
 * set_route reads the flag as it writes the entry, but clears route_flags itself only once its
 * promise settles, and that waits on every request then in flight (v16's after_ajax) — on a first
 * show, the page's bootstrap call whenever it goes through frappe.call. Left set, the flag would
 * turn the next tap into a replace of the page's own entry, and Back or X from the sheet it opened
 * would then leave the page.
 *
 * The router never reads history.state, and nothing here pushes an entry of its own: every entry
 * is set_route's. Pure apart from `env`, so scripts/test_scanner_kit.mjs and
 * scripts/test_desk_page_history.js run it against a model of the v16 router.
 */

const BACK_TIMEOUT_MS = 1500;

export function create_sheets(env, opts) {
	const route = opts.route;
	const names = opts.names || [];
	const key = opts.mark;
	// How long to wait for the router to land our own history.back() before going on without it.
	const back_ms = opts.back_timeout_ms || BACK_TIMEOUT_MS;

	const nav = {
		sheet: null, // {name, dialog} while a sheet is open
		opening: null, // the sheet whose route is being pushed
		shown: undefined, // the sheet segment at the last show; undefined before the first
		away: false, // another Desk page has been shown since the last show

		// The sheet the route names, or null.
		current() {
			const now = env.frappe.get_route() || [];
			return now[0] === route && names.includes(now[1]) ? now[1] : null;
		},

		// The mark goes on the current entry, with no URL, and keeps whatever else it holds.
		mark(name) {
			try {
				env.history.replaceState(Object.assign({}, env.history.state, { [key]: name }), "");
			} catch (e) {
				// Unmarked, the entry is handed to the page on its next show rather than reopened.
			}
		},

		own(name) {
			try {
				const state = env.history.state;
				return !!state && state[key] === name;
			} catch (e) {
				return false;
			}
		},

		// frappe triggers "hide" on the page it leaves for another. Only the page's own counts: a
		// Bootstrap "hide.bs.*" from inside the page bubbles up to the wrapper as well.
		watch(wrapper) {
			env.$(wrapper).on("hide", (e) => {
				if (e.target === wrapper) nav.away = true;
			});
		},

		// `reopen`: Forward landed on the sheet's entry; show it there, and push nothing. `took`
		// runs whenever this sheet takes the current entry or pushes its own (the scanner audit
		// drops its record of a camera entry left behind for a lookup).
		open(name, show, reopen, took) {
			const on = nav.current();
			if (reopen || (on === name && !nav.sheet)) {
				if (on === name && !nav.sheet) {
					if (took) took();
					show();
				}
				return;
			}
			nav.opening = name;
			if (took) took();
			// An entry left behind by a sheet that has closed is taken over, not stacked on.
			if (on) env.frappe.route_flags.replace_route = true;
			const settled = env.frappe.set_route(route, name);
			// Read already, and left set it would outlive this call (see the header).
			env.frappe.route_flags.replace_route = false;
			// set_route has written the entry by the time it returns (push_state runs before its
			// promise does), so the mark lands on the sheet's own entry.
			nav.mark(name);
			Promise.resolve(settled).then(() => {
				if (nav.opening !== name) return;
				nav.opening = null;
				// Back pressed before the route settled took the entry, and the sheet with it.
				if (nav.current() === name && !nav.sheet) show();
			});
		},

		// Bootstrap ignores hide() on a modal still fading in, so that waits for it to be shown.
		hide(d) {
			if (d.display) d.hide();
			else d.$wrapper.one("shown.bs.modal", () => d.hide());
		},

		// Every sheet's onhide calls this.
		gone(d) {
			if (nav.sheet && nav.sheet.dialog === d) nav.sheet = null;
		},

		// Step back off a sheet's entry. `then` runs once the page is back on its own entry, so a
		// dialog it opens (an enroll prompt, an error) is not closed by that route change. When Back
		// closed the sheet the route has already moved off the segment, and there is nothing to step
		// over.
		leave(name, then) {
			let done = false;
			const finish = () => {
				if (done) return;
				done = true;
				if (then) then();
			};
			if (nav.current() !== name) {
				finish();
				return;
			}
			env.frappe.router.once("change", finish);
			env.set_timeout(finish, back_ms);
			env.history.back();
		},

		closed(name, d, then) {
			nav.gone(d);
			nav.leave(name, then);
		},

		// The page's on_page_show. `hooks.reopen(sheet)` opens a sheet Back or Forward landed on
		// again and says whether it could; `hooks.hold(sheet)` keeps an entry as it is, neither
		// reopened nor stepped off (the scanner audit's camera entry while a read is looked up).
		// Returns the sheet the route names and the one the last show did: a page whose own entry
		// is showing again after a sheet closed (`came`) leaves focus where closing it left it.
		show(hooks) {
			hooks = hooks || {};
			const sheet = nav.current();
			const first = nav.shown === undefined;
			const came = nav.shown;
			// The page's own entry was showing at the last show, and no other page has been since.
			const stayed = !first && !nav.away && came === null;
			nav.away = false;
			nav.shown = sheet;
			// A sheet whose entry is no longer current. The router closes the open dialog on every
			// route change, but not one still fading in: it is not cur_dialog yet.
			if (nav.sheet && nav.sheet.name !== sheet) nav.hide(nav.sheet.dialog);
			if (!sheet) return { sheet, came };
			if (nav.opening === sheet || (nav.sheet && nav.sheet.name === sheet)) return { sheet, came };
			if (first || !nav.own(sheet)) {
				// A reload, a pasted link, or a sheet URL reached from another page (never start a
				// camera nobody asked for, over a page it would step back to): the entry becomes the
				// page, or is stepped back off onto the page's own.
				nav.shown = null;
				if (stayed || (first && nav.own(sheet))) {
					// Pushed over the page's own entry while it was showing (the awesome bar's link
					// to a sheet, picked on the page), or a reload on a sheet entry this page pushed
					// (its mark survives the reload): the entry behind is the page's, so step back
					// onto it. Replacing this one would leave two page entries in a row.
					env.history.back();
				} else {
					env.frappe.route_flags.replace_route = true;
					env.frappe.set_route(route);
					// Read already, and left set it would outlive this call (see the header).
					env.frappe.route_flags.replace_route = false;
				}
			} else if (hooks.hold && hooks.hold(sheet)) {
				// Kept as it is: the page says this entry is busy with something of its own.
			} else if (!(hooks.reopen && hooks.reopen(sheet))) {
				// Back or Forward onto a sheet that cannot be opened again: step back onto the
				// page's own entry, which is always the one behind a sheet's. Replacing this entry
				// instead would leave two page entries in a row, and the next Back would seem to do
				// nothing.
				env.history.back();
			}
			return { sheet, came };
		},
	};
	return nav;
}
