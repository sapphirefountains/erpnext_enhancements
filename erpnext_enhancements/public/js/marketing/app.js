/**
 * The marketing app (TASK-2026-01487). One class, plain DOM, no framework.
 *
 * State is plain objects and rendering is "clear the pane and rebuild it", as in /feedback. The
 * views live in their own modules (`view_*.js`) and are handed this object, which owns the
 * layout, the router, the notices and the one placeholder writer.
 *
 * Pure logic lives where node can check it: `routes.js`, `calendar.js`, `composer.js`.
 */

import { call, M } from "./transport.js";
import { VIEW_MONTH, VIEW_WEEK, VIEW_NEW, VIEW_QUEUE, VIEW_MEDIA, VIEW_POST, VIEW_RESULTS, buildRoute, parseRoute, tabOf } from "./routes.js";
import { monthOf, startOfWeek, weekStartIndex } from "./calendar.js";
import { append, button, clear, closeDialogs, dialog, el, fill, link } from "./dom.js";
import { renderCalendar } from "./view_calendar.js";
import { renderComposer } from "./view_composer.js";
import { renderQueue } from "./view_queue.js";
import { renderMedia } from "./view_media.js";
import { renderResults } from "./view_results.js";

/**
 * The key of this app's own history entries, `{ee_mk: <n>}`. `n` is the entry's place counted
 * from the one the page opened on, so a popstate says how far the browser moved and which way.
 * That is what lets a view with unsaved work put a Back or Forward back while it asks.
 */
const HISTORY_KEY = "ee_mk";
/** Our own step back onto the screen that stayed lands within milliseconds. After this, stop waiting. */
const UNDO_SETTLE_MS = 1500;

export class MarketingApp {
	constructor(root, boot) {
		this.root = root;
		this.boot = boot || {};
		this.data = null; // get_bootstrap()
		this.route = parseRoute("");
		// The path the pane was last drawn for.
		this.here = "";
		// A view with unsaved work sets this to a function returning true while it has some.
		this.leaveGuard = null;
		// "Copy into a new draft" hands the composer a starting state through here.
		this.seed = null;
		// The current entry's place in the history (HISTORY_KEY).
		this.idx = 0;
		// Our own `history.go()` putting a refused Back or Forward back, until it lands: `{to, timer}`.
		this.undoing = null;
		// The "Leave without saving?" question while it is open, so a second Back does not stack one.
		this.leaving = null;
	}

	async mount() {
		this.buildLayout();
		// The entry the page opened on is stamped, never pushed, so Back from the first screen leaves
		// the page as it always has. One this app stamped before a reload keeps its place, and the
		// copy it may carry (`write`).
		const idx = entryIndex(window.history.state);
		if (idx === null) this.write(false);
		else this.idx = idx;
		window.addEventListener("popstate", (ev) => this.onPopState(ev));
		window.addEventListener("beforeunload", (ev) => {
			if (this.leaveGuard && this.leaveGuard()) {
				ev.preventDefault();
				ev.returnValue = "";
			}
		});
		try {
			this.data = await call(M.BOOTSTRAP);
		} catch (e) {
			this.showPlaceholder(this.pane, "!", "The marketing app could not load", e.message);
			return;
		}
		this.weekStart = weekStartIndex(this.data.week_start);
		this.renderHeader();
		this.renderBanner();
		this.routeTo(window.location.pathname);
	}

	// ------------------------------------------------------------------ layout

	buildLayout() {
		clear(this.root);
		this.header = el("header", "ee-mk-header");
		this.nav = el("nav", "ee-mk-nav");
		this.nav.setAttribute("aria-label", "Marketing");
		this.banner = el("div", "ee-mk-banner");
		this.banner.hidden = true;
		this.notice = el("div", "ee-mk-notice");
		this.notice.setAttribute("role", "status");
		this.notice.setAttribute("aria-live", "polite");
		this.notice.hidden = true;
		this.pane = el("main", "ee-mk-pane");
		append(this.header, el("h1", "ee-mk-title", "Marketing"));
		append(this.root, this.header, this.nav, this.banner, this.notice, this.pane);
	}

	renderHeader() {
		const who = el("div", "ee-mk-who", this.data.full_name || "");
		const tz = el("div", "ee-mk-tz", `Times in ${this.data.timezone || "the site's time zone"}`);
		fill(this.header, el("h1", "ee-mk-title", "Marketing"), who, tz);
		this.renderNav();
	}

	renderNav() {
		const current = tabOf(this.route.view);
		const pending = this.data ? this.data.pending_approval || 0 : 0;
		const tabs = [
			{ key: "calendar", label: "Calendar", href: buildRoute(VIEW_MONTH) },
			{ key: "new", label: "New post", href: buildRoute(VIEW_NEW) },
			{ key: "queue", label: pending ? `Approval queue (${pending})` : "Approval queue", href: buildRoute(VIEW_QUEUE) },
			{ key: "media", label: "Media", href: buildRoute(VIEW_MEDIA) },
		];
		clear(this.nav);
		for (const tab of tabs) {
			const node = link(tab.label, tab.href, (href) => this.navigate(href), "ee-mk-tab");
			if (tab.key === current) {
				node.classList.add("ee-mk-tab-active");
				node.setAttribute("aria-current", "page");
			}
			this.nav.appendChild(node);
		}
	}

	/** What is switched off, said once at the top: writing and approving still work. */
	renderBanner() {
		const publishing = this.data.publishing || {};
		const on = Object.keys(publishing.networks || {}).filter((n) => publishing.networks[n]);
		let text = "";
		if (!publishing.enabled) {
			text =
				"Publishing is switched off. Posts can be written and approved, and they wait; nothing goes out until a System Manager switches publishing on.";
		} else if (!on.length) {
			text = "Publishing is on, but no network's own switch is. Approved posts wait until one is.";
		} else if (on.length < (this.data.networks || []).length) {
			text = `Publishing is on for ${on.join(", ")}. Posts to the other networks wait.`;
		}
		this.banner.hidden = !text;
		fill(this.banner, text ? el("span", null, text) : null);
	}

	// ------------------------------------------------------------------ routing

	/**
	 * Navigate, asking first if the current view has unsaved work. `replace` swaps the current
	 * entry instead of adding one: after a save, so Back never returns to a /marketing/new that has
	 * become a post. A link to the screen already showing replaces too, or the next Back would
	 * appear to do nothing; that is a comparison of screens, not addresses (`samePlace`), so Today
	 * on /marketing/calendar/<this month> re-stamps as surely as Today on /marketing does.
	 */
	navigate(href, replace) {
		const go = () => {
			this.leaveGuard = null;
			this.write(!replace && !this.samePlace(href, window.location.pathname), href);
			this.routeTo(href);
		};
		if (this.leaveGuard && this.leaveGuard()) {
			this.askToLeave(go);
			return;
		}
		go();
	}

	/**
	 * Leave a screen that is gone (a deleted post) for `href`. Its entry is replaced, so Back never
	 * lands on the address of something that no longer exists. When the entry underneath already
	 * shows `href` (the calendar the post was opened from, as it nearly always was), the page then
	 * steps back onto it: two entries showing one screen side by side would make the next Back
	 * appear to do nothing. The replaced entry stays above as a Forward onto that same screen,
	 * until the next tap drops it.
	 */
	retreat(href) {
		this.leaveGuard = null;
		const from = entryFrom(window.history.state);
		const back = !!from && this.idx > 0 && this.samePlace(from, href);
		// The entry underneath's own address, so the step back lands on the path already drawn.
		this.navigate(back ? from : href, true);
		if (!back) return;
		try {
			window.history.back();
		} catch (e) {
			// The entry stays where it is; the next Back takes one extra press, as it did before.
		}
	}

	/**
	 * Do two addresses show the same screen? The calendar has several addresses for one:
	 * /marketing and /marketing/calendar/<this month> are both this month, and a week is the same
	 * week from any of its days, /marketing/week being this one. Anything else is the same screen
	 * only at the same address. Before the bootstrap has said what today is, only the address counts.
	 */
	samePlace(a, b) {
		const today = this.data ? this.data.today : "";
		return placeOf(a, today, this.weekStart) === placeOf(b, today, this.weekStart);
	}

	/**
	 * Push an entry for `href`, or re-stamp the current one (with `href` if given). Two arguments
	 * when there is no address to change. A push records the address of the entry it is laid on
	 * (`from`), and a replace keeps what the entry had: that is how `retreat` knows what Back would
	 * show. A "Copy into a new draft" rides in its entry, so Forward or a reload brings the copy
	 * back rather than an empty form.
	 */
	write(push, href) {
		const idx = push ? this.idx + 1 : this.idx;
		const state = { [HISTORY_KEY]: idx };
		const from = push ? window.location.pathname : entryFrom(window.history.state);
		if (from) state.from = from;
		if (this.seed && href && parseRoute(href).view === VIEW_NEW) state.seed = this.seed;
		try {
			if (href === undefined) window.history.replaceState(state, "");
			else if (push) window.history.pushState(state, "", href);
			else window.history.replaceState(state, "", href);
			this.idx = idx;
		} catch (e) {
			// Safari refuses past 100 calls in 10 s. The screen still changes; only the entry is lost.
		}
	}

	/**
	 * Back or Forward, or our own `history.go()`. The browser has already moved, and the screen
	 * follows the address as a click would. A view with unsaved work is asked first, as `navigate`
	 * asks: the step is put back while the question is open, so "Stay" keeps the screen and its
	 * address, and "Discard changes" takes the step again.
	 */
	onPopState(ev) {
		const landed = entryIndex(ev && ev.state);
		const undo = this.undoing;
		if (undo) {
			this.settleUndo();
			if (landed === undo.to) return; // our own step back onto the screen that stayed
		}
		if (landed === null) {
			// Not an entry of ours: a #fragment typed into the address bar makes one, on top of the
			// screen that is showing. Take it as the next step.
			this.idx += 1;
			this.write(false);
		} else {
			const delta = landed - this.idx;
			if (delta && this.leaveGuard && this.leaveGuard()) {
				this.putBack(delta);
				this.askToLeave(() => {
					this.leaveGuard = null;
					window.history.go(delta);
				});
				return;
			}
			this.idx = landed;
		}
		if (window.location.pathname === this.here) return; // the same screen: nothing to redraw
		this.routeTo(window.location.pathname);
	}

	/** Undo a Back or Forward of `delta` steps that the page refused: step onto the entry on screen. */
	putBack(delta) {
		this.settleUndo();
		const undo = { to: this.idx, timer: null };
		undo.timer = setTimeout(() => {
			if (this.undoing === undo) this.undoing = null;
		}, UNDO_SETTLE_MS);
		this.undoing = undo;
		try {
			window.history.go(-delta);
		} catch (e) {
			this.settleUndo();
		}
	}

	settleUndo() {
		if (this.undoing) clearTimeout(this.undoing.timer);
		this.undoing = null;
	}

	/** The one "Leave without saving?", for a click (`navigate`) and for Back or Forward alike. */
	askToLeave(discard) {
		if (this.leaving) this.leaving.close();
		const asked = dialog("Leave without saving?", el("p", null, "Your changes to this post have not been saved."), [
			{ label: "Stay" },
			{ label: "Discard changes", primary: true, onClick: discard },
		]);
		this.leaving = asked;
		asked.node.addEventListener("close", () => {
			if (this.leaving === asked) this.leaving = null;
		});
	}

	routeTo(pathname) {
		const route = parseRoute(String(pathname));
		this.route = route;
		this.here = String(pathname).split("?")[0];
		this.leaveGuard = null;
		// A dialog belongs to the screen that opened it. After Back, "Delete this post?" must not
		// stand over another screen, still bound to the old post.
		closeDialogs();
		this.renderNav();
		this.clearNotice();
		const view = route.view;
		const run = () => {
			// A newer route has landed since: its own run draws the pane. The views check
			// `app.route === route` again after every wait, for the same reason.
			if (this.route !== route) return undefined;
			if (view === VIEW_NEW || view === VIEW_POST) return renderComposer(this, route);
			if (view === VIEW_RESULTS) return renderResults(this, route);
			if (view === VIEW_QUEUE) return renderQueue(this);
			if (view === VIEW_MEDIA) return renderMedia(this);
			return renderCalendar(this, route);
		};
		Promise.resolve()
			.then(run)
			.catch((e) => {
				if (this.route !== route) return; // the screen it was for is gone
				this.showPlaceholder(this.pane, "!", "That could not be shown", e.message || String(e));
			});
	}

	// ------------------------------------------------------------------ shared pieces

	/**
	 * **The one placeholder writer.** A pane or list with nothing to show says so here, with a
	 * mark, a title and a line of explanation. Clearing a list and leaving it empty reads as a
	 * broken page, which the retired chat app shipped three times; the source-rule check fails
	 * the build if a list renderer can reach "empty" without coming here.
	 */
	showPlaceholder(container, mark, title, sub) {
		const node = el("div", "ee-mk-placeholder");
		append(
			node,
			el("div", "ee-mk-placeholder-mark", mark),
			el("h2", "ee-mk-placeholder-title", title),
			sub ? el("p", "ee-mk-placeholder-sub", sub) : null
		);
		fill(container, node);
		return node;
	}

	/** A message beside the navigation, never in place of it. */
	say(text, kind) {
		this.notice.className = `ee-mk-notice ee-mk-notice-${kind || "info"}`;
		fill(this.notice, el("span", null, text), button("Dismiss", "ee-mk-btn ee-mk-btn-small", () => this.clearNotice()));
		this.notice.hidden = false;
	}

	fail(error) {
		this.say(error && error.message ? error.message : String(error), "error");
	}

	clearNotice() {
		this.notice.hidden = true;
		clear(this.notice);
	}

	/**
	 * Take (and forget) the state a "Copy into a new draft" left for the next new post. On Back,
	 * Forward or a reload onto that entry, the copy comes from the entry itself (`write`), as a
	 * fresh object each time: the composer edits what it is given.
	 */
	takeSeed() {
		const seed = this.seed;
		this.seed = null;
		if (seed) return seed;
		const state = window.history.state;
		if (entryIndex(state) === null || !state.seed) return null;
		return JSON.parse(JSON.stringify(state.seed));
	}

	/** The bootstrap's accounts, by name. */
	accountsByName() {
		const out = {};
		for (const account of this.data.accounts || []) out[account.name] = account;
		return out;
	}

	/** Refresh the counts the navigation shows (the approval queue) after an action. */
	async refreshBootstrap() {
		try {
			this.data = await call(M.BOOTSTRAP);
			this.renderNav();
			this.renderBanner();
		} catch (e) {
			// The page still works on the counts it has; the next navigation tries again.
		}
	}
}

/** The place an entry of ours records (HISTORY_KEY), or null for anything else. */
function entryIndex(state) {
	return state && typeof state === "object" && typeof state[HISTORY_KEY] === "number" ? state[HISTORY_KEY] : null;
}

/** The address of the entry underneath, as an entry of ours recorded it when pushed (`write`). */
function entryFrom(state) {
	if (entryIndex(state) === null || typeof state.from !== "string") return "";
	return state.from;
}

/** A name for the screen an address shows (`samePlace`): the calendar's month or week, else the path. */
function placeOf(pathname, today, weekStart) {
	const path = String(pathname || "").split("?")[0];
	const route = parseRoute(path);
	if (today && route.view === VIEW_MONTH) return `month ${route.month || monthOf(today)}`;
	if (today && route.view === VIEW_WEEK) return `week ${startOfWeek(route.day || today, weekStart || 0)}`;
	return path;
}
