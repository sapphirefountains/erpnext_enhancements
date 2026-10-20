/**
 * page_search.js: find this app's pages by name or by a short word, and jump to the six
 * busiest ones from the keyboard (custom pages S4c, TASK-2026-02806, v1.680.0).
 *
 * Three surfaces, one table (PAGE_ENTRIES below):
 *
 *   1. The awesomebar (Ctrl+K, "Search or type a command"). Frappe v16.50 already lists a
 *      Page whose *title* fuzzy-matches what you type (search_utils.get_pages), so "Project
 *      Planner" was findable; "planner" found it only by luck of the fuzzy score, and "qbo",
 *      "kiosk", "scan" or "day board" found nothing a person would guess. Each entry here
 *      carries aliases, and its result says "Page" and shows its shortcut.
 *   2. The full search dialog (Enter in the awesomebar, or Ctrl+G) gets a "Pages" section, the
 *      same way global_search_doctypes.js gives it a "DocTypes" one. Its content index is
 *      MariaDB full-text, which skips words under four letters (ft_min_word_len), so "QBO" or
 *      "PO" can never match there. This section does its own matching, so they do.
 *   3. Six global shortcuts, Alt+Shift+<letter>, registered through frappe.ui.keys.add_shortcut
 *      so they show in Frappe's Keyboard Shortcuts dialog (Shift+/), plus a "Go to ..." command
 *      for each in the awesomebar (search_utils.make_function_searchable, the API Frappe uses
 *      for its own "Background Jobs" command). Typing "go" in Ctrl+K lists them.
 *
 * Who sees what: a Desk page is offered only when frappe.boot.page_info has it, and page_info
 * holds only the pages the user's roles allow (frappe/desk/desk_views.py). The two www pages
 * (/stock-scan, reached here directly, and /kiosk, reached through its Desk redirect page
 * time-kiosk) are not Desk pages; Stock Scan carries the roles its controller checks
 * (inventory_enhancements/stock_scan_rules.py SCAN_ROLES; the guard test holds the two equal).
 * A shortcut is registered only for a page the user can open, so the help dialog never lists
 * one that would land on "not permitted".
 *
 * How it hooks in, and why it cannot break search: v16.50 has no client-side extension point
 * for awesomebar results. AwesomeBar.build_options is a fixed list of builders, and the
 * `awesomebar_search` hook is a server round trip on every keystroke for every user. So this
 * wraps AwesomeBar.prototype.build_options narrowly: Frappe's own list is computed first and
 * returned untouched if anything here throws, if build_options is missing (a renamed method
 * means this does nothing), or if nothing matches. SearchDialog.parse_results is wrapped the
 * same way. Each patch is marked on the prototype, so a second load is a no-op.
 *
 * Routing: a Desk page opens with frappe.set_route(<page name>), which pushes a history entry
 * (never break Back/Forward). A www page is a full navigation, location.assign in the shortcut
 * and the "Pages" section, and Frappe's own in-app-path handling in the awesomebar; both make
 * a history entry too.
 *
 * Keys: Frappe's get_key spells a chord "alt+shift+<letter>" (ctrl, then shift, then alt are
 * prefixed in that order), and handlers are keyed by that exact string. Nothing in Frappe
 * v16.50, ERPNext v16.50 or this app binds Alt+Shift+<letter>; Frappe's own Alt+<letter> menu
 * accelerators (alt_keyboard_shortcuts.js) step aside whenever Shift is held. T is left alone
 * (the Triton widget opens on Alt+T and does not exclude Shift, and Chrome uses Shift+Alt+T for
 * its toolbar), as are I and A (Chrome's feedback and pane-focus keys).
 * scripts/test_page_search.js holds the full list of what is taken.
 *
 * Never while typing: add_shortcut already skips input, select, textarea and
 * [contenteditable=true]; the action also refuses inside any other editable element (a Quill
 * or ProseMirror child, contenteditable="" or "plaintext-only"), on a held-down repeat, and
 * while a dialog is open. A refusal lets the keystroke through untouched.
 *
 * Loaded via erpnext_enhancements.bundle.js (app_include_js). A classic script: it reads
 * frappe at call time and exposes its pure parts on erpnext_enhancements.page_search for the
 * node harness.
 */
(function () {
	"use strict";

	const root = typeof window !== "undefined" ? window : globalThis;

	// Shortcut chords, in frappe.ui.keys.get_key's spelling.
	const KEY_PROJECT_PLANNER = "alt+shift+p";
	const KEY_MAINTENANCE_PLANNER = "alt+shift+m";
	const KEY_DAY_BOARD = "alt+shift+d";
	const KEY_RENTAL_PLANNER = "alt+shift+r";
	const KEY_PLAN_A_TRIP = "alt+shift+j";
	const KEY_STOCK_SCAN = "alt+shift+s";

	// The one alias map. `page` is a Desk Page name (its title comes from the Page record, via
	// frappe.boot.page_info); `url` is a www page, which has no Page record, so it carries its
	// own title and roles. Aliases are lower case; a short one (an acronym) matches only whole.
	const PAGE_ENTRIES = [
		{
			page: "project-planner",
			aliases: ["planner", "planners", "project schedule", "crew schedule", "schedule"],
			shortcut: KEY_PROJECT_PLANNER,
		},
		{
			page: "maintenance-planner",
			aliases: ["planner", "planners", "maintenance", "service planner", "visit schedule"],
			shortcut: KEY_MAINTENANCE_PLANNER,
		},
		{
			page: "maintenance-day-board",
			aliases: ["day board", "dayboard", "board", "today", "dispatch"],
			shortcut: KEY_DAY_BOARD,
		},
		{
			page: "rental-planner",
			aliases: ["rental", "rentals", "event rental", "fountain rental", "bookings", "planner"],
			shortcut: KEY_RENTAL_PLANNER,
		},
		{
			page: "plan-a-trip",
			aliases: ["trip", "trips", "travel", "itinerary"],
			shortcut: KEY_PLAN_A_TRIP,
		},
		{
			url: "/stock-scan",
			title: "Stock Scan",
			roles: ["System Manager", "Stock Manager", "Stock User", "Inventory Clerk"],
			aliases: ["scan", "scanner", "stock", "warehouse scan", "qr scan", "receive"],
			shortcut: KEY_STOCK_SCAN,
		},
		{
			page: "inventory-scanner-audit",
			aliases: ["scan", "scanner", "audit", "scanner audit", "inventory audit", "cycle count"],
		},
		// v1.693.0: one bin's items on a PC, without scanning (the same endpoints as /stock-scan).
		{ page: "bin-manager", aliases: ["bins", "bin", "shelf", "shelves", "putaway", "boneyard"] },
		{ page: "time-kiosk", aliases: ["kiosk", "clock in", "clock out", "time clock", "timeclock"] },
		{ page: "integrations-health", aliases: ["health", "integrations", "sync status"] },
		{ page: "device-fleet-dashboard", aliases: ["health", "devices", "fleet", "mdm"] },
		{
			page: "quickbooks-online-dashboard",
			aliases: ["qbo", "qb", "quickbooks", "health", "accounting sync"],
		},
		{ page: "my-work", aliases: ["todo", "todos", "to do", "my tasks", "assignments", "assigned to me", "inbox"] },
		{ page: "quickbooks-record-matching", aliases: ["qbo", "qb", "quickbooks", "matching"] },
		{ page: "stripe-payments-dashboard", aliases: ["stripe", "payments", "health", "card payments"] },
		{ page: "opportunity-board", aliases: ["board", "pipeline", "opportunities", "deals", "crm"] },
		{ page: "sales-pipeline", aliases: ["pipeline", "sales", "crm"] },
		{ page: "guided-entry", aliases: ["guided", "new opportunity", "intake"] },
		{ page: "visit-wizard", aliases: ["visit", "service visit", "wizard"] },
		{ page: "visit-review", aliases: ["visit", "review", "visit review"] },
		{ page: "contract-builder", aliases: ["contract", "contracts"] },
		{ page: "inspection-wizard", aliases: ["inspection", "qc", "quality", "wizard"] },
		{ page: "location-timeline", aliases: ["location", "gps", "timeline"] },
		{ page: "learn", aliases: ["training", "lms", "courses", "learn"] },
		{ page: "water-engineering-wizard", aliases: ["water", "hydraulics", "pump", "wizard"] },
		{ page: "ga4-dashboard", aliases: ["ga4", "analytics", "google analytics", "web traffic"] },
		{ page: "device-console", aliases: ["device", "devices", "mdm", "console"] },
		{ page: "fleet-maintenance-schedule", aliases: ["fleet", "vehicles", "vehicle maintenance"] },
	];

	// Ranking. Frappe's own awesomebar scores (fuzzy_match) run ~120-330 for these words: an
	// exact DocType name of seven letters scores ~265. An alias the user typed exactly is our
	// strongest signal and should sit at the top; a word that merely starts a title should not
	// push the "Project" DocType below "Project Planner".
	const PER_CHAR = 30;
	const TIER_EXACT = 150;
	const TIER_PREFIX = 40;
	const TIER_WORD = 20;
	const TIER_SUBSTRING = 0;
	const TITLE_BONUS = 5;
	// Below this length a query matches an alias or a title word only from its start, never
	// mid-word: "qb" finds the "qb" alias, but "an" must not find "Plan a Trip".
	const MIN_SUBSTRING = 3;

	function translate(text, args) {
		try {
			if (typeof root.__ === "function") return root.__(text, args);
		} catch (e) {
			/* fall through */
		}
		if (!args) return text;
		return String(text).replace(/\{(\d+)\}/g, (m, i) => (args[i] !== undefined ? args[i] : m));
	}

	function escape_html(text) {
		return String(text == null ? "" : text)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;")
			.replace(/'/g, "&#39;");
	}

	// Lower case, punctuation to spaces, runs of space collapsed: "Day-Board" -> "day board".
	function normalize(text) {
		return String(text == null ? "" : text)
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, " ")
			.trim();
	}

	function frappe_ref() {
		return typeof root.frappe !== "undefined" ? root.frappe : null;
	}

	function user_roles(fr) {
		if (!fr) return [];
		if (Array.isArray(fr.user_roles)) return fr.user_roles;
		const user = fr.boot && fr.boot.user;
		return (user && Array.isArray(user.roles) && user.roles) || [];
	}

	/**
	 * The entries this user may open, each with its title and target resolved:
	 * { key, title, page, url, aliases, shortcut }.
	 */
	function resolve_entries(fr) {
		fr = fr || frappe_ref();
		if (!fr || !fr.boot) return [];
		const page_info = fr.boot.page_info || {};
		const roles = user_roles(fr);
		const out = [];
		PAGE_ENTRIES.forEach((entry) => {
			if (entry.page) {
				const info = Object.prototype.hasOwnProperty.call(page_info, entry.page)
					? page_info[entry.page]
					: null;
				if (!info) return;
				const title = (info && info.title) || entry.page;
				out.push({
					key: entry.page,
					title: title,
					page: entry.page,
					url: null,
					aliases: entry.aliases || [],
					shortcut: entry.shortcut || null,
				});
			} else if (entry.url) {
				if (!(entry.roles || []).some((r) => roles.indexOf(r) !== -1)) return;
				out.push({
					key: entry.url,
					title: entry.title,
					page: null,
					url: entry.url,
					aliases: entry.aliases || [],
					shortcut: entry.shortcut || null,
				});
			}
		});
		return out;
	}

	// How well `query` (normalized) matches `text` (normalized): a tier, or -1.
	function tier_for(query, text) {
		if (!query || !text) return -1;
		if (text === query) return TIER_EXACT;
		if (text.indexOf(query) === 0) return TIER_PREFIX;
		const words = text.split(" ");
		for (let i = 1; i < words.length; i++) {
			if (words.slice(i).join(" ").indexOf(query) === 0) return TIER_WORD;
		}
		if (query.length >= MIN_SUBSTRING && text.indexOf(query) !== -1) return TIER_SUBSTRING;
		return -1;
	}

	/**
	 * Entries matching `query`, best first: [{ entry, score, via }], where `via` is the alias
	 * that matched (null when the title did).
	 */
	function match(query, entries) {
		const q = normalize(query);
		if (q.length < 2) return [];
		const hits = [];
		(entries || []).forEach((entry) => {
			let best = -1;
			let via = null;
			const title_tier = tier_for(q, normalize(entry.title));
			if (title_tier > best) {
				best = title_tier;
				via = null;
			}
			(entry.aliases || []).forEach((alias) => {
				const t = tier_for(q, normalize(alias));
				if (t > best) {
					best = t;
					via = alias;
				}
			});
			if (best < 0) return;
			// Among pages sharing an alias ("health"), the one whose own title says it comes first.
			const bonus = title_tier >= 0 ? TITLE_BONUS : 0;
			hits.push({ entry: entry, score: PER_CHAR * q.length + best + bonus, via: via });
		});
		hits.sort((a, b) => b.score - a.score || (a.entry.title < b.entry.title ? -1 : 1));
		return hits;
	}

	function shortcut_label(chord) {
		const fr = frappe_ref();
		try {
			if (fr && fr.ui && fr.ui.keys && typeof fr.ui.keys.get_shortcut_label === "function") {
				return fr.ui.keys.get_shortcut_label(chord);
			}
		} catch (e) {
			/* fall through */
		}
		return chord
			.split("+")
			.map((p) => p.charAt(0).toUpperCase() + p.slice(1))
			.join("+");
	}

	function route_of(entry) {
		return entry.url ? [entry.url] : [entry.page];
	}

	function describe(hit) {
		const parts = [translate("Page")];
		if (hit.entry.shortcut) parts.push(shortcut_label(hit.entry.shortcut));
		if (hit.via && normalize(hit.entry.title).indexOf(normalize(hit.via)) === -1) {
			parts.push(translate("matches “{0}”", [escape_html(hit.via)]));
		}
		return parts.join(" · ");
	}

	// Awesomebar options for `txt`, shaped like search_utils.get_pages' own.
	function awesomebar_options(txt, fr) {
		return match(txt, resolve_entries(fr)).map((hit) => {
			const title = translate(hit.entry.title);
			return {
				type: "Page",
				label: translate("Open {0}", ["<b>" + escape_html(title) + "</b>"]),
				value: translate("Open {0}", [title]),
				description: describe(hit),
				match: hit.entry.title,
				index: hit.score,
				route: route_of(hit.entry),
				ee_page_search: true,
			};
		});
	}

	function route_key(route) {
		if (route == null) return "";
		return Array.isArray(route) ? route.join("/") : String(route);
	}

	/**
	 * Frappe's build_options result plus ours. Frappe's own "Page" row for a page we also list
	 * is dropped and its score kept if higher, so one page is one row. Anything unexpected
	 * returns Frappe's list as it was.
	 */
	function merge_options(options, txt, fr) {
		if (!Array.isArray(options) || typeof txt !== "string" || txt.charAt(0) === "#") {
			return options;
		}
		const ours = awesomebar_options(txt, fr);
		if (!ours.length) return options;
		const by_route = Object.create(null);
		ours.forEach((o) => {
			by_route[route_key(o.route)] = o;
		});
		const kept = [];
		options.forEach((o) => {
			const mine = o && by_route[route_key(o.route)];
			if (mine && !o.ee_page_search) {
				if (typeof o.index === "number" && o.index > mine.index) mine.index = o.index;
				return;
			}
			kept.push(o);
		});
		return kept.concat(ours).sort((a, b) => (b.index || 0) - (a.index || 0));
	}

	function hide_search_dialog(fr) {
		try {
			const sd = fr.searchdialog && fr.searchdialog.search && fr.searchdialog.search.search_dialog;
			if (sd && typeof sd.hide === "function") sd.hide();
		} catch (e) {
			/* nothing to hide */
		}
	}

	/** Open an entry: set_route for a Desk page (a history entry), a full navigation for www. */
	function open_entry(entry, fr) {
		fr = fr || frappe_ref();
		if (!entry || !fr) return;
		if (entry.url) {
			root.location.assign(entry.url);
			return;
		}
		fr.set_route(entry.page);
	}

	/** The search dialog's "Pages" section (a `fetch_type: "Nav"` result set), or null. */
	function nav_set(keyword, fr) {
		fr = fr || frappe_ref();
		const hits = match(keyword, resolve_entries(fr));
		if (!hits.length) return null;
		return {
			title: translate("Pages"),
			fetch_type: "Nav",
			results: hits.map((hit) => {
				const title = translate(hit.entry.title);
				const row = {
					label: escape_html(title),
					value: title,
					match: hit.entry.title,
					onclick: () => {
						// A Ctrl/Cmd/Shift-click is the browser's: the row's href opens a new tab
						// (a middle-click never reaches here). A www row has no href, so it opens
						// the tab itself. The row handler is handed no event, hence window.event.
						const ev = root.event;
						if (ev && (ev.ctrlKey || ev.metaKey || ev.shiftKey)) {
							if (hit.entry.url && typeof root.open === "function") {
								root.open(hit.entry.url, "_blank");
							}
							return;
						}
						hide_search_dialog(fr);
						open_entry(hit.entry, fr);
					},
				};
				// A Desk page's route gives the row a real /desk/<page> href (SearchDialog.get_link
				// runs it through frappe.router.make_url), so it can open in a new tab. Not for a
				// www page: make_url would spell /stock-scan as a Desk path.
				if (hit.entry.page) row.route = [hit.entry.page];
				return row;
			}),
		};
	}

	// True when the keystroke belongs to something the user is typing into.
	function is_typing(target) {
		if (!target || target.nodeType !== 1) return false;
		if (target.isContentEditable) return true;
		const tag = String(target.tagName || "").toLowerCase();
		if (tag === "input" || tag === "textarea" || tag === "select") return true;
		if (typeof target.closest === "function") {
			return !!target.closest(
				"input, textarea, select, [contenteditable]:not([contenteditable='false'])"
			);
		}
		return false;
	}

	function dialog_open() {
		const d = root.cur_dialog;
		return !!(d && d.display);
	}

	// The action behind a shortcut: false (keystroke untouched) when it must not fire.
	function shortcut_action(entry, fr) {
		return function (e) {
			// frappe.ui.keys hands over a jQuery event, and jQuery 3.7.1 does not copy `repeat`
			// onto it: the native event's flag is only on originalEvent.
			const oe = e && (e.originalEvent || e);
			if (oe && oe.repeat) return false;
			const doc = root.document;
			if (is_typing(doc && doc.activeElement) || is_typing(e && e.target)) return false;
			if (dialog_open()) return false;
			open_entry(entry, fr);
			return true;
		};
	}

	/** Register the shortcuts and "Go to" commands for the entries this user can open. */
	function register_shortcuts(fr) {
		fr = fr || frappe_ref();
		const registered = [];
		if (!fr || !fr.ui || !fr.ui.keys || typeof fr.ui.keys.add_shortcut !== "function") {
			return registered;
		}
		resolve_entries(fr).forEach((entry) => {
			if (!entry.shortcut) return;
			const title = translate(entry.title);
			fr.ui.keys.add_shortcut({
				shortcut: entry.shortcut,
				action: shortcut_action(entry, fr),
				description: translate("Go to {0}", [title]),
			});
			registered.push(entry.shortcut);
			const utils = fr.search && fr.search.utils;
			if (utils && typeof utils.make_function_searchable === "function") {
				utils.make_function_searchable(
					() => open_entry(entry, fr),
					translate("Go to {0}", [title])
				);
			}
		});
		return registered;
	}

	function patch_awesomebar(fr) {
		const AwesomeBar = fr.search && fr.search.AwesomeBar;
		const proto = AwesomeBar && AwesomeBar.prototype;
		if (!proto || proto._ee_page_search_patched || typeof proto.build_options !== "function") {
			return false;
		}
		proto._ee_page_search_patched = true;
		const original = proto.build_options;
		proto.build_options = function (txt) {
			const options = original.apply(this, arguments);
			try {
				return merge_options(options, txt, fr);
			} catch (e) {
				return options;
			}
		};
		return true;
	}

	function patch_search_dialog(fr) {
		const SearchDialog = fr.search && fr.search.SearchDialog;
		const proto = SearchDialog && SearchDialog.prototype;
		if (!proto || proto._ee_page_nav_patched || typeof proto.parse_results !== "function") {
			return false;
		}
		proto._ee_page_nav_patched = true;
		const original = proto.parse_results;
		proto.parse_results = function (result_sets, keyword) {
			try {
				const is_global = this.searches && this.search === this.searches["global_search"];
				// Not when drilled into one DocType's content, nor in "#tag" mode.
				if (is_global && !this.global_doctype_filter && String(keyword || "").charAt(0) !== "#") {
					const set = nav_set(keyword, fr);
					if (set) result_sets = [set].concat(result_sets || []);
				}
			} catch (e) {
				/* Frappe's own results, as they were */
			}
			return original.call(this, result_sets, keyword);
		};
		return true;
	}

	function install() {
		const fr = frappe_ref();
		if (!fr || fr._ee_page_search_installed) return;
		fr._ee_page_search_installed = true;
		try {
			patch_awesomebar(fr);
		} catch (e) {
			/* search stays Frappe's */
		}
		try {
			patch_search_dialog(fr);
		} catch (e) {
			/* search stays Frappe's */
		}
		try {
			register_shortcuts(fr);
		} catch (e) {
			/* no shortcuts, nothing else lost */
		}
	}

	const api = {
		PAGE_ENTRIES: PAGE_ENTRIES,
		normalize: normalize,
		resolve_entries: resolve_entries,
		match: match,
		awesomebar_options: awesomebar_options,
		merge_options: merge_options,
		nav_set: nav_set,
		open_entry: open_entry,
		is_typing: is_typing,
		register_shortcuts: register_shortcuts,
		install: install,
	};
	root.erpnext_enhancements = root.erpnext_enhancements || {};
	root.erpnext_enhancements.page_search = api;

	const $ = root.jQuery || root.$;
	if (typeof $ === "function") {
		$(root.document).on("app_ready", install);
	}
})();
