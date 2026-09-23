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
import { VIEW_MONTH, VIEW_NEW, VIEW_QUEUE, VIEW_MEDIA, VIEW_POST, VIEW_RESULTS, buildRoute, parseRoute, tabOf } from "./routes.js";
import { weekStartIndex } from "./calendar.js";
import { append, button, clear, dialog, el, fill, link } from "./dom.js";
import { renderCalendar } from "./view_calendar.js";
import { renderComposer } from "./view_composer.js";
import { renderQueue } from "./view_queue.js";
import { renderMedia } from "./view_media.js";
import { renderResults } from "./view_results.js";

export class MarketingApp {
	constructor(root, boot) {
		this.root = root;
		this.boot = boot || {};
		this.data = null; // get_bootstrap()
		this.route = parseRoute("");
		// A view with unsaved work sets this to a function returning true while it has some.
		this.leaveGuard = null;
		// "Copy into a new draft" hands the composer a starting state through here.
		this.seed = null;
	}

	async mount() {
		this.buildLayout();
		window.addEventListener("popstate", () => this.routeTo(window.location.pathname));
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

	/** Navigate, asking first if the current view has unsaved work. */
	navigate(href, replace) {
		const go = () => {
			this.leaveGuard = null;
			if (replace) window.history.replaceState({}, "", href);
			else window.history.pushState({}, "", href);
			this.routeTo(href);
		};
		if (this.leaveGuard && this.leaveGuard()) {
			dialog("Leave without saving?", el("p", null, "Your changes to this post have not been saved."), [
				{ label: "Stay" },
				{ label: "Discard changes", primary: true, onClick: go },
			]);
			return;
		}
		go();
	}

	routeTo(pathname) {
		this.route = parseRoute(String(pathname));
		this.leaveGuard = null;
		this.renderNav();
		this.clearNotice();
		const view = this.route.view;
		const run = () => {
			if (view === VIEW_NEW || view === VIEW_POST) return renderComposer(this, this.route);
			if (view === VIEW_RESULTS) return renderResults(this, this.route);
			if (view === VIEW_QUEUE) return renderQueue(this);
			if (view === VIEW_MEDIA) return renderMedia(this);
			return renderCalendar(this, this.route);
		};
		Promise.resolve()
			.then(run)
			.catch((e) => {
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

	/** Take (and forget) the state a "Copy into a new draft" left for the next new post. */
	takeSeed() {
		const seed = this.seed;
		this.seed = null;
		return seed;
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
