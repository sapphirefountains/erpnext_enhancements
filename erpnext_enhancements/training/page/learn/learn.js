// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// The learner Desk Page — /desk/learn.
//
// It is a HOST, not a second player. It builds the mount, loads the runtime,
// fetches the boot payload and constructs `TR.Player(root, boot, transport)`; every
// pixel below the page head is rendered by the same four files the portal loads.
// Forking the player is the single most expensive mistake available in this module,
// and a Desk host is exactly where the temptation lives, because `frappe.*` is right
// there.
//
// WHY THIS EXISTS. 20 of 26 Training Assignments sat at "Not Started". The content
// was finished — six courses, 113 lessons — and the player was finished. There was
// simply no door: `/training` is linked only from `portal_menu_items`, which desk
// users never see, so the sole route in was a link inside an email. Meanwhile all
// fifteen Training Learner holders are System Users who spend their day in the Desk,
// and the workspace they DO see is the manager console.
//
// WHY THE ROUTE IS `learn` AND NOT `training`. `frappe.router` resolves the first
// path segment against `frappe.workspaces` BEFORE doctypes and before the page
// loader, and discards every segment after it:
//
//     if (frappe.workspaces[route[0]]) { route = ["Workspaces", …]; }
//
// `desk.js` keys that map by `slug(page.name)`, and the Training *workspace* is named
// "Training". So a page named `training` would never render — and, because
// `frappe.boot.allowed_workspaces` is permission-filtered, the same URL would be the
// workspace for the fifteen learners and the page for anybody who cannot see it. One
// URL, two destinations, no error in either. `tests/test_workspaces.py` fails the
// build on it now.
//
// TWO THINGS IN THIS FILE ARE HISTORY RATHER THAN DESIGN, and both were once
// written here as "not yet":
//   * the address bar. `boot.history` is still false — this file never READS the
//     URL, the Desk router does — but since v1.432.2 the player WRITES it through
//     the adapter below. Those are two jobs, which is why one flag does not cover
//     both.
//   * the rollout switch. `learn.json` shipped `roles: [System Manager]` so the
//     page could land dark; it carries `Training Learner` now.
//
// WHAT IT STILL DOES NOT DO: it renders no pixel of the learner surface itself,
// and the rail beside it renders none either. Everything below the page head and
// inside `.tl-desk-surface` is the player's.

frappe.pages["learn"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Training"),
		// The rail lives in `page.sidebar`, which only exists on a two-column page.
		// See TR.deskNav: the Desk's own left sidebar lists WORKSPACES, so it can
		// offer "My Training" as a destination and can never show the three courses
		// this particular person owes.
		single_column: false,
	});
	$(wrapper).addClass("tl-learn-page");
	wrapper.learn = new LearnPage(page, wrapper);
};

frappe.pages["learn"].on_page_show = function (wrapper) {
	if (wrapper.learn) wrapper.learn.handle_route();
};

// The runtime, in load order. player.js composes the other three, and transport.js
// is independent — but they are appended in this order and `TR.loadAsset` sets
// `async = false`, which makes dynamically-inserted scripts execute in insertion
// order. Getting that wrong would be intermittent rather than broken, which is worse.
const LEARN_ASSETS = [
	"/assets/erpnext_enhancements/css/training/player.css",
	"/assets/erpnext_enhancements/js/training/transport.js",
	"/assets/erpnext_enhancements/js/training/blocks.js",
	"/assets/erpnext_enhancements/js/training/video.js",
	"/assets/erpnext_enhancements/js/training/quiz.js",
	"/assets/erpnext_enhancements/js/training/player.js",
];

// The rail, loaded on its own chain. Chrome and player fail independently on
// purpose: if the runtime cannot load, a learner can still reach their
// certificates and a manager the dashboard; if the rail cannot load, the lesson
// still opens. Two files rather than one — the script is useless unstyled.
const LEARN_NAV_ASSETS = [
	"/assets/erpnext_enhancements/css/training/desk_nav.css",
	"/assets/erpnext_enhancements/js/training/desk_nav.js",
];

// Route segments under /desk/learn that name a VIEW rather than a course. Course
// names come from a `TRN-CRS-` naming series so a collision is not currently
// possible — which is exactly why it is written down rather than left to the series
// to guarantee. Mirrors COURSE_SCOPED_VIEWS in player.js from the other side.
const LEARN_VIEWS = ["record", "queue", "people", "feed"];

class LearnPage {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.player = null;
		this.booting = null;
		// What the page last told the player to show, in the adapter's own key shape.
		// Read by BOTH directions, which is what stops them driving each other.
		this.showing = null;
		// The same position as an object, for the rail. Kept beside `this.showing`
		// rather than parsed back out of it: the key is a string built for equality,
		// and splitting it again to find the course name would make the loop guard's
		// format load-bearing for something that is not about the loop.
		this.where = { view: "catalog", course: null };
		// The rail and the payload that fills it. Either can arrive first — the boot
		// is a round trip and the rail is two files — so each hands what it has to
		// the other when it lands.
		this.nav = null;
		this.nav_boot = null;
		// The mount, with the same boot line the portal shell rendered. Not cosmetic:
		// the runtime is six files and a round trip away, and an empty bordered box is
		// indistinguishable from a page that has failed.
		this.root = $('<div id="training-root" class="tr-shell" aria-busy="true"></div>')
			.append($('<div class="tr-boot"></div>').text(__("Loading training…")))
			.appendTo($('<div class="tl-desk-surface"></div>').appendTo(page.body))
			.get(0);

		// Teardown is a CORRECTNESS requirement here, not tidiness. frappe creates a
		// page div once (views/container.js add_page) and never removes it, so the
		// <video> element, its interval timers and the heartbeat all survive the
		// learner navigating to a Sales Invoice: the video keeps downloading and the
		// player keeps claiming watch time for a lesson nobody is looking at. There is
		// no on_page_hide hook in v16 — the page loader binds only "show" — so this
		// listens for the jQuery "hide" event frappe fires on the outgoing page, the
		// same idiom sales_pipeline.js uses for its polling timer.
		$(wrapper).on("hide", () => this.destroy());

		this.mount_nav();
	}

	// The deploy token every /assets URL this page pulls has to carry. "0" rather
	// than an empty string: TR.loadAssets refuses an unversioned path, and failing
	// loudly on a bootinfo with no version beats serving a year-old file from an
	// immutable cache.
	asset_version() {
		return (frappe.boot.versions && frappe.boot.versions.erpnext_enhancements) || "0";
	}

	mount_nav() {
		// Guarded rather than assumed. TR.loadAssets ships in the global desk bundle,
		// so this is only reachable if that bundle failed to build or to load -- and
		// an uncaught TypeError HERE is in the constructor, outside every promise
		// chain, which would take the whole page down to save a sidebar.
		if (!window.TR || typeof TR.loadAssets !== "function") return;
		TR.loadAssets(LEARN_NAV_ASSETS, this.asset_version())
			.then(() => {
				if (typeof TR.deskNav !== "function") return;
				this.nav = TR.deskNav({ page: this.page, active: this.where });
				// Whichever landed first wins the race harmlessly: if the boot payload
				// is already here the rail is filled immediately, and if it is not,
				// mount() hands it over when it arrives.
				if (this.nav_boot) this.nav.setLearner(this.nav_boot);
			})
			.catch(() => {
				// Swallowed deliberately. The rail is navigation; the page is the
				// lesson. A missing rail must not put an error where a course should
				// be, and there is nothing a learner could do about it anyway.
			});
	}

	// One place where the page's position changes, so the rail cannot disagree with
	// the player about where the learner is.
	mark(where) {
		this.where = { view: where.view || null, course: where.course || null };
		if (this.nav) this.nav.setActive(this.where);
	}

	destroy() {
		if (!this.player) return;
		try {
			this.player.destroy();
		} catch (e) {
			// A failing teardown must not stop the next mount; the worst case is a
			// stale node, and re-rendering clears the mount anyway.
		}
		this.player = null;
	}

	// Fires on every route change INTO this page, including /desk/learn ->
	// /desk/learn/TRN-CRS-00002/l3, because container.js triggers "show" outside its
	// own "is this a different page" guard.
	handle_route() {
		const route = frappe.get_route() || [];
		const target = { course: null, lesson_key: null, view: null };

		if (route[1] && LEARN_VIEWS.indexOf(route[1]) !== -1) {
			target.view = route[1];
		} else if (route[1]) {
			target.course = route[1];
			if (route[2]) target.lesson_key = route[2];
		}

		if (this.player) {
			this.apply_route(target);
			return;
		}
		if (this.booting) return;
		this.booting = this.boot(target).finally(() => {
			this.booting = null;
		});
	}

	// Moves an ALREADY-MOUNTED player, rather than re-booting it. A fresh boot per
	// route change would refetch the catalogue and throw away in-flight watch
	// progress on every click.
	//
	// THE NO-OP IS THE IMPORTANT PART. Since v1.432.2 the player writes the URL
	// through an adapter, so the two drive each other: player moves -> adapter ->
	// frappe.set_route -> router change -> on_page_show -> here -> player moves. That
	// closes into a loop unless one end stops, and this is the end that stops --
	// `this.showing` is what the page last told the player to show, so a route change
	// the player itself caused is recognised and dropped. frappe's own push_state
	// declines a no-op URL too, but only after the route event has already fired.
	apply_route(target) {
		const key = `${target.view || ""}|${target.course || ""}|${target.lesson_key || ""}`;
		if (key === this.showing) return;
		this.showing = key;
		this.mark(target);
		if (target.course) {
			this.player.openCourse(target.course, target.lesson_key || null);
		} else {
			this.player.go(target.view || "catalog");
		}
	}

	// The other half: what the player tells the Desk. Returns the adapter handed to
	// TR.Player on the boot payload.
	//
	// `frappe.set_route` and not an href: the router intercepts its own navigation,
	// and a hand-built /app/... link would be a full page reload plus a redirect hop.
	// It pushes rather than replaces, deliberately -- inside the Desk, Back is the
	// desk's Back, and it should walk the views the way it does everywhere else in
	// ERPNext. The portal deliberately does the opposite (replaceState, so Back means
	// "leave the course"), which is exactly why this is an adapter and not a flag.
	router_adapter() {
		return {
			write: (next) => {
				const parts = ["learn"];
				if (next.course) {
					parts.push(next.course);
					// The outline names a course and no single lesson, matching what the
					// portal puts in its query string.
					if (next.lesson && next.view !== "course") parts.push(next.lesson);
				} else if (next.view && LEARN_VIEWS.indexOf(next.view) !== -1) {
					parts.push(next.view);
				}

				const key = `${next.view || ""}|${next.course || ""}|${
					next.view !== "course" ? next.lesson || "" : ""
				}`;
				if (key === this.showing) return;
				this.showing = key;
				this.mark(next);

				// Compared before routing as well: a set_route to where we already are
				// still fires a route event, and that event arrives here as a fresh
				// handle_route.
				const current = (frappe.get_route() || []).join("/");
				if (current === parts.join("/")) return;
				frappe.set_route(parts);
			},
		};
	}

	boot(target) {
		this.page.set_indicator(__("Loading…"), "blue");
		// Same source the authoring canvas uses.
		const version = this.asset_version();
		// Native promises the whole way down. TR.loadAssets and the transport both
		// return real Promises; wrapping them in $.when would hand back a jQuery
		// Deferred whose .then() has subtly different semantics from the spec's, and
		// this chain has no need of jQuery at all.
		return TR.loadAssets(LEARN_ASSETS, version)
			.then(() => {
				if (!window.TR || typeof TR.Player !== "function" || typeof TR.makeTransport !== "function") {
					throw new Error(__("The training runtime did not load."));
				}
				// A FUNCTION, not the value: a desk session can outlive the token the
				// page booted with, and this page is long-lived by construction.
				this.transport = TR.makeTransport({ csrf: () => frappe.csrf_token });
				return this.transport.bootstrap({});
			})
			.then((boot) => this.mount(boot || {}, target))
			.catch((err) => this.fail(err));
	}

	mount(boot, target) {
		// history:false plus an explicit start is how the preview harness hosts the
		// same player. It keeps route() at a zero-line diff for this release: the
		// player neither reads nor writes the address bar, and the Desk router owns
		// the URL entirely.
		// history:false and an adapter together: the player never READS the address
		// bar (the Desk router owns Back, and this page answers it through
		// on_page_show) but it does WRITE it, through the adapter below. Those are two
		// different jobs; the player checks the adapter independently of this flag.
		boot.history = false;
		boot.router = this.router_adapter();
		if (target.course) {
			boot.start = { course: target.course, lesson_key: target.lesson_key || null };
		} else if (target.view) {
			boot.view = target.view;
		}
		boot.translate = (text) => __(text);

		// The rail's only data, and it costs nothing: `assigned`, `is_staff` and
		// `signoffs_to_record` are already in the payload the player is about to boot
		// from. A sidebar that fetched its own copy would be a second answer to
		// "which courses are mine", derived in the browser, and the two would
		// disagree the first time an assignment changed mid-session.
		this.nav_boot = boot;
		if (this.nav) this.nav.setLearner(boot);
		// Marked BEFORE the player is built, so a deep link highlights its course on
		// the first paint rather than after the fetch. The player's own adapter
		// overwrites this the moment it settles — which is the point of the ordering:
		// this is the page's guess, and the player's statement is the truth. Doing it
		// the other way round leaves the rail pointing at a course the player failed
		// to open.
		this.mark(target);

		this.destroy();
		this.player = new TR.Player(this.root, boot, this.transport);
		this.page.clear_indicator();
	}

	fail(err) {
		this.page.set_indicator(__("Unavailable"), "red");
		this.root.setAttribute("aria-busy", "false");
		$(this.root).empty().append(
			$('<div class="tl-error"></div>').text(
				(err && err.message) || __("Training could not start. Please reload the page.")
			)
		);
	}
}
