/**
 * desk_search.js: the Desk search box (Ctrl+K) lists records first, grouped, with a preview
 * (v1.795.0; Nik, 2026-11-04).
 *
 * The problem, measured on prod (frappe 16.53.0): typing a customer's first name listed sixteen
 * screen names ("Drive Link Candidate" 200, "Report Cheques and Deposits Incorrectly cleared"
 * 195, from frappe's fuzzy DocType matcher) above her Contact, because this app's old
 * live search (api/search.py, wired from erpnext_enhancements.js) gave every record a flat 150.
 * Her records sat at rows 17 to 36, under the fold of a 300px list.
 *
 * What this does, as Nik picked it:
 *   - As you type (two characters, 120 ms after the last key), records come first, grouped under
 *     headings: People, Accounts, Projects, Opportunities, Quotes/orders/invoices, Fountains and
 *     maintenance, Suppliers, Items. api/desk_search.py ranks and groups them; best group first.
 *   - Each row has a preview line (account and status, a quote's amount), and a person or account
 *     has call and email buttons (tel:/mailto:) and links to their account, projects and
 *     opportunities. "See all N" opens the list of that group's matches.
 *   - Numbers, phones and emails find records too ("707", "PRJ-707", "555-0142").
 *   - Screens (DocType lists, reports, pages) drop below the records, best three only, unless the
 *     text names a screen ("project", "sales invoice"); then they stay on top.
 *   - Opening the box empty lists the records you opened last, on this browser.
 *   - Phones get the same rows, wrapped for a narrow screen.
 *
 * Enter opens the highlighted row. Once records have arrived that is the best record; until then
 * (the first ~150 ms) it is frappe's "Search for ..." row as before, so a fast Enter still opens
 * the full results. "Search everything for ..." stays in the list below the records, and Ctrl+G
 * opens the full results at any time.
 *
 * How it hooks in, and why it cannot break search: frappe 16.53 has no client extension point for
 * the box's rows. This wraps AwesomeBar.prototype.setup_event_listeners and, once frappe has made
 * its Awesomplete, decorates that one instance: its `list` setter composes frappe's rows with ours
 * (every writer of the list goes through it: frappe's input handler, its awesomebar_search hook
 * callback, our own fetch), its `item` renderer draws our rows, and capture-phase listeners on the
 * list take clicks on our buttons before Awesomplete selects the row. Every step falls back to
 * frappe's own list or row if anything throws. The request is a plain fetch, never frappe.call,
 * so a failed search can never pop a dialog while someone is typing.
 *
 * Routing goes through frappe.set_route, which pushes a history entry (never break Back).
 *
 * Loaded via erpnext_enhancements.bundle.js. A classic script: it reads frappe at call time and
 * exposes its pure parts on erpnext_enhancements.desk_search for scripts/test_desk_search.js.
 */
(function () {
	"use strict";

	const root = typeof window !== "undefined" ? window : globalThis;

	const METHOD = "erpnext_enhancements.api.desk_search.search";
	const MIN_CHARS = 2;
	const DEBOUNCE_MS = 120;
	const MAX_SCREENS = 3;
	const MAX_HELP = 3;
	const RECENT_MAX = 12;
	const RECENT_SHOW = 6;
	const RECENT_PREFIX = "ee-desk-search-recent:";
	// Our rows sort above anything frappe scores (its own maximum is a few hundred).
	const TOP = 10000000;
	const KB_DOCTYPE = "Knowledge Article";
	// A recent row's type, where the Desk's word differs from the DocType's name.
	const TYPE_LABELS = {
		Customer: "Account",
		"Serial No": "Fountain",
		"Sapphire Maintenance Contract": "Maintenance contract",
		"Sapphire Maintenance Record": "Maintenance visit",
	};
	// The title people see, where it is not the DocType's title_field.
	const TITLE_FIELDS = { Opportunity: ["custom_opportunity_name", "title"] };

	const t = (s, args) => (typeof root.__ === "function" ? root.__(s, args) : s);

	// ------------------------------------------------------------------ pure helpers

	function normalize(value) {
		return String(value == null ? "" : value)
			.trim()
			.replace(/\s\s+/g, " ");
	}

	function escape_html(value) {
		return String(value == null ? "" : value)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;")
			.replace(/'/g, "&#39;");
	}

	/** The text, escaped, with every typed word wrapped in <mark>. */
	function mark(text, query) {
		const s = String(text == null ? "" : text);
		const words = normalize(query)
			.split(" ")
			.filter(Boolean)
			.map((w) => w.toLowerCase());
		if (!words.length) return escape_html(s);
		const lower = s.toLowerCase();
		const hit = new Array(s.length).fill(false);
		words.forEach((w) => {
			let at = lower.indexOf(w);
			while (at !== -1) {
				for (let i = at; i < at + w.length; i++) hit[i] = true;
				at = lower.indexOf(w, at + w.length);
			}
		});
		let out = "";
		let open = false;
		for (let i = 0; i < s.length; i++) {
			if (hit[i] && !open) {
				out += "<mark>";
				open = true;
			} else if (!hit[i] && open) {
				out += "</mark>";
				open = false;
			}
			out += escape_html(s[i]);
		}
		return open ? out + "</mark>" : out;
	}

	function digits_of(value) {
		return String(value == null ? "" : value).replace(/\D/g, "");
	}

	/** 801-555-0142 for a US number however it was stored; anything else as stored. */
	function format_phone(value) {
		const raw = String(value == null ? "" : value).trim();
		let d = digits_of(raw);
		if (d.length === 11 && d[0] === "1") d = d.slice(1);
		if (d.length === 10 && (raw.startsWith("+1") || !raw.startsWith("+"))) {
			return d.slice(0, 3) + "-" + d.slice(3, 6) + "-" + d.slice(6);
		}
		return raw;
	}

	function tel_href(value) {
		const raw = String(value == null ? "" : value).trim();
		const d = digits_of(raw);
		if (d.length < 4) return null;
		return "tel:" + (raw.startsWith("+") ? "+" : "") + d;
	}

	function mail_href(value) {
		const raw = String(value == null ? "" : value).trim();
		return /^[^\s@<>"'()]+@[^\s@<>"'()]+\.[^\s@<>"'()]+$/.test(raw) ? "mailto:" + raw : null;
	}

	function route_key(route) {
		return Array.isArray(route) ? route.join("/") : String(route || "");
	}

	function is_help(option) {
		const r = option && option.route;
		return Array.isArray(r) && r[0] === "Form" && r[1] === KB_DOCTYPE;
	}

	/** Does the text name a screen, so the screens belong on top? */
	function names_a_screen(screens, txt) {
		const q = normalize(txt).toLowerCase();
		if (!q) return false;
		return screens.some((o) => {
			const match = String((o && o.match) || "").toLowerCase();
			const value = String((o && o.value) || "").toLowerCase();
			if (match === q || value === q) return true;
			return q.includes(" ") && (match.startsWith(q) || value.startsWith(q));
		});
	}

	/** One option per record row, in the server's order, each carrying what the renderer needs. */
	function record_options(results) {
		const out = [];
		((results && results.groups) || []).forEach((group) => {
			(group.rows || []).forEach((row, i) => {
				out.push({
					label: row.title,
					value: (row.type || row.doctype) + ": " + row.title + " · " + row.name,
					route: row.route,
					match: row.title,
					_ee: {
						kind: "record",
						row: row,
						heading:
							i === 0
								? {
										label: group.label,
										total: group.total,
										more: group.more,
										shown: (group.rows || []).length,
										see_all: group.see_all || null,
								  }
								: null,
					},
				});
			});
		});
		return out;
	}

	function recent_options(recents) {
		return (recents || []).slice(0, RECENT_SHOW).map((entry, i) => ({
			label: entry.t || entry.n,
			value: t("Recent") + ": " + (entry.t || entry.n) + " · " + entry.d + " " + entry.n,
			route: ["Form", entry.d, entry.n],
			match: entry.t || entry.n,
			_ee: {
				kind: "recent",
				row: {
					doctype: entry.d,
					name: entry.n,
					title: entry.t || entry.n,
					type: t(TYPE_LABELS[entry.d] || entry.d),
					subtitle: entry.t && entry.t !== entry.n ? entry.n : "",
					route: ["Form", entry.d, entry.n],
				},
				heading: i === 0 ? { label: t("Recent") } : null,
			},
		}));
	}

	function copy_of(option, extra) {
		return Object.assign({}, option, extra, { _ee_orig: option });
	}

	/**
	 * frappe's rows plus ours, in display order, each with a descending index (frappe's Awesomplete
	 * sorts by it). Pure. `list` is whatever was assigned to the Awesomplete; ctx is
	 * {txt, results (for that txt) or null, recents}.
	 */
	function compose(list, ctx) {
		const txt = normalize(ctx && ctx.txt);
		// Our own rows from an earlier composition drop out; frappe's copies go back to frappe's.
		const base = (list || [])
			.filter((o) => o && !(o._ee && !o._ee_orig))
			.map((o) => o._ee_orig || o);
		const search_row = base.find((o) => o.default === "Search");
		let ordered;

		if (txt.length < MIN_CHARS) {
			const recents = txt ? [] : recent_options(ctx && ctx.recents);
			if (!recents.length) return base;
			// frappe lists a form you opened this session too ("Contact Dana ..."): once is enough
			const recent_routes = new Set(recents.map((o) => route_key(o.route)));
			const rest = base.filter((o) => !recent_routes.has(route_key(o.route)));
			ordered = recents.concat(
				rest.map((o, i) => (i === 0 ? copy_of(o, { _ee_heading: { label: t("Pages") } }) : o))
			);
		} else {
			const records = record_options(ctx && ctx.results);
			if (!records.length) {
				// frappe's list as before, with "Search for ..." first (this app's long-standing order)
				if (!search_row) return base;
				ordered = [search_row].concat(base.filter((o) => o !== search_row));
			} else {
				const record_routes = new Set(records.map((o) => route_key(o.route)));
				const help = base
					.filter(is_help)
					.sort((a, b) => (b.index || 0) - (a.index || 0))
					.slice(0, MAX_HELP);
				const tools = base.filter((o) => o.default && o.default !== "Search");
				const screens = base
					.filter((o) => !o.default && !is_help(o) && !record_routes.has(route_key(o.route)))
					.sort((a, b) => (b.index || 0) - (a.index || 0));
				const top_screens = screens
					.slice(0, MAX_SCREENS)
					.map((o, i) => (i === 0 ? copy_of(o, { _ee_heading: { label: t("Screens") } }) : o));
				const help_rows = help.map((o, i) =>
					i === 0 ? copy_of(o, { _ee_heading: { label: t("Knowledge base") } }) : o
				);
				const search_all = search_row
					? [
							copy_of(search_row, {
								label:
									'<span class="text-medium">' +
									t("Search everything for {0}", ["<b>" + escape_html(txt) + "</b>"]) +
									"</span>",
								_ee: { kind: "search" },
							}),
					  ]
					: [];
				ordered = names_a_screen(screens, txt)
					? top_screens.concat(records, help_rows, tools, search_all)
					: records.concat(help_rows, tools, search_all, top_screens);
			}
		}
		return ordered.map((o, i) => {
			const index = TOP - i;
			if (o._ee && !o._ee_orig) {
				o.index = index; // our own row, made fresh above
				return o;
			}
			// frappe's row (or our copy of it, which carries a heading or a new label): a new copy
			// with the index, so frappe's own object is never changed
			return Object.assign({}, o, { index: index, _ee_orig: o._ee_orig || o });
		});
	}

	/** The recent list after opening `doc`: one entry per record, newest first, at most RECENT_MAX. */
	function push_recent(list, entry) {
		const out = [entry];
		(list || []).forEach((e) => {
			if (out.length < RECENT_MAX && e && !(e.d === entry.d && e.n === entry.n)) out.push(e);
		});
		return out;
	}

	/** Keep the last results while the new ones load, if the text only grew or shrank. */
	function results_for(state, txt) {
		const r = state.results;
		if (!r || !r.groups || !r.groups.length) return null;
		const a = normalize(r.txt).toLowerCase();
		const b = normalize(txt).toLowerCase();
		return a === b || b.startsWith(a) || a.startsWith(b) ? r : null;
	}

	// ------------------------------------------------------------------ browser side

	function storage() {
		try {
			return root.localStorage || null;
		} catch (e) {
			return null;
		}
	}

	function recent_key() {
		const user = root.frappe && root.frappe.session && root.frappe.session.user;
		return user && user !== "Guest" ? RECENT_PREFIX + user : null;
	}

	function read_recents() {
		const key = recent_key();
		const store = storage();
		if (!key || !store) return [];
		try {
			const list = JSON.parse(store.getItem(key) || "[]");
			return Array.isArray(list) ? list.filter((e) => e && e.d && e.n) : [];
		} catch (e) {
			return [];
		}
	}

	function remember(frm) {
		const fr = root.frappe;
		if (!frm || !frm.doc || !frm.doctype || !frm.docname) return;
		if (typeof frm.is_new === "function" && frm.is_new()) return;
		const meta = frm.meta || {};
		if (meta.istable || meta.issingle) return;
		const fields = TITLE_FIELDS[frm.doctype] || [meta.title_field];
		let title = "";
		fields.forEach((f) => {
			if (!title && f && frm.doc[f]) title = String(frm.doc[f]);
		});
		const key = recent_key();
		const store = storage();
		if (!key || !store) return;
		try {
			const at =
				fr && fr.datetime && typeof fr.datetime.now_datetime === "function"
					? fr.datetime.now_datetime()
					: "";
			const entry = { d: frm.doctype, n: String(frm.docname), t: title || "", at: at };
			store.setItem(key, JSON.stringify(push_recent(read_recents(), entry)));
		} catch (e) {
			/* storage full or blocked: recents are a convenience */
		}
	}

	/** "5 hours ago" through frappe 16.53's frappe.datetime.prettyDate; nothing when it is missing. */
	function pretty_date(at) {
		const dt = root.frappe && root.frappe.datetime;
		try {
			return dt && typeof dt.prettyDate === "function" ? String(dt.prettyDate(at) || "") : "";
		} catch (e) {
			return "";
		}
	}

	function desk_href(route) {
		const fr = root.frappe;
		try {
			return fr.router.make_url(
				fr.router.convert_from_standard_route(fr.router.get_route_from_arguments(route))
			);
		} catch (e) {
			return "#";
		}
	}

	function el(tag, cls, html) {
		const node = root.document.createElement(tag);
		if (cls) node.className = cls;
		if (html != null) node.innerHTML = html;
		return node;
	}

	function heading_node(heading, txt) {
		const head = el("div", "ee-ds-head");
		head.appendChild(el("span", "ee-ds-head-label", escape_html(heading.label)));
		const more = heading.total > (heading.shown || 0);
		if (more) {
			const label = heading.more
				? t("See all {0}+", [heading.total])
				: t("See all {0}", [heading.total]);
			const a = el("a", "ee-ds-action ee-ds-seeall", escape_html(label));
			if (heading.see_all) {
				a.href = desk_href(heading.see_all.route);
				a._ee_link = heading.see_all;
			} else {
				a.href = "#";
				a._ee_link = { search_all: txt };
			}
			head.appendChild(a);
		}
		return head;
	}

	function render_row(option, txt) {
		const info = option._ee;
		const row = info.row;
		const li = el("li", info.heading ? "ee-ds-li ee-ds-headed" : "ee-ds-li");
		if (info.heading) li.appendChild(heading_node(info.heading, txt));

		const line = el("div", "ee-ds-line");
		const main = el("a", "ee-ds-main");
		main.href = desk_href(row.route);
		const top = el("span", "ee-ds-top");
		top.appendChild(el("span", "ee-ds-title", mark(row.title, info.kind === "record" ? txt : "")));
		top.appendChild(el("span", "ee-ds-type", escape_html(row.type || row.doctype)));
		main.appendChild(top);
		let sub = row.subtitle || "";
		if (info.kind === "recent" && option._ee_at) sub = option._ee_at;
		if (sub) main.appendChild(el("span", "ee-ds-sub", escape_html(sub)));
		line.appendChild(main);

		const tel = tel_href(row.phone);
		const mail = mail_href(row.email);
		if (tel || mail) {
			const actions = el("span", "ee-ds-actions");
			if (tel) {
				const a = el("a", "ee-ds-action ee-ds-call", escape_html(format_phone(row.phone)));
				a.href = tel;
				a.title = t("Call {0}", [format_phone(row.phone)]);
				actions.appendChild(a);
			}
			if (mail) {
				const a = el("a", "ee-ds-action ee-ds-mail", escape_html(row.email));
				a.href = mail;
				a.title = t("Email {0}", [row.email]);
				actions.appendChild(a);
			}
			line.appendChild(actions);
		}
		li.appendChild(line);

		if (row.related && row.related.length) {
			const rel = el("div", "ee-ds-related");
			row.related.forEach((link) => {
				const a = el("a", "ee-ds-action ee-ds-rel");
				a.innerHTML =
					(link.kind ? '<span class="ee-ds-rel-kind">' + escape_html(link.kind) + "</span> " : "") +
					escape_html(link.label);
				a.href = desk_href(link.route);
				a._ee_link = link;
				rel.appendChild(a);
			});
			li.appendChild(rel);
		}
		const $ = root.jQuery;
		if ($) $(li).data("item.autocomplete", option);
		return li;
	}

	function close_box(bar) {
		try {
			const input = bar.awesomplete && bar.awesomplete.input;
			if (input) input.value = "";
			if (bar.search_modal) bar.search_modal.modal("hide");
		} catch (e) {
			/* the modal's own Escape still works */
		}
	}

	function follow(bar, link, event) {
		const fr = root.frappe;
		if (link.search_all != null) {
			close_box(bar);
			fr.searchdialog.search.init_search(link.search_all, "global_search");
			return;
		}
		if (link.route_options) fr.route_options = link.route_options;
		if (event && (event.ctrlKey || event.metaKey)) fr.open_in_new_tab = true;
		close_box(bar);
		fr.set_route(link.route);
	}

	function find_list_setter(aw) {
		let proto = Object.getPrototypeOf(aw);
		while (proto) {
			const d = Object.getOwnPropertyDescriptor(proto, "list");
			if (d && d.set) return d;
			proto = Object.getPrototypeOf(proto);
		}
		return null;
	}

	function current_text(aw) {
		return normalize(aw && aw.input ? aw.input.value : "");
	}

	function decorate(bar) {
		const aw = bar.awesomplete;
		if (!aw || aw._ee_desk_search) return;
		const desc = find_list_setter(aw);
		if (!desc) return; // an Awesomplete without a list accessor: leave frappe's box alone
		aw._ee_desk_search = true;
		const state = { results: null, seq: 0, timer: null, controller: null, recents: [] };
		bar._ee_desk_search = state;

		Object.defineProperty(aw, "list", {
			configurable: true,
			get() {
				return desc.get ? desc.get.call(this) : this._list;
			},
			set(value) {
				let out = value;
				try {
					const txt = current_text(this);
					if (!txt) state.recents = read_recents();
					out = compose(value, { txt: txt, results: results_for(state, txt), recents: state.recents });
					out.forEach((o) => {
						if (o._ee && o._ee.kind === "recent") {
							const entry = state.recents.find((e) => e.d === o._ee.row.doctype && e.n === o._ee.row.name);
							const pretty = entry && entry.at ? pretty_date(entry.at) : "";
							o._ee_at = pretty ? t("Opened {0}", [pretty]) : o._ee.row.subtitle;
						}
					});
				} catch (e) {
					console.error("[ERPNext Enhancements] desk search: compose failed", e);
					out = value;
				}
				desc.set.call(this, out);
			},
		});

		const original_item = aw.item;
		aw.item = function (suggestion, input, index) {
			let d = null;
			try {
				d = this.get_item(suggestion.value);
				if (d && d._ee && (d._ee.kind === "record" || d._ee.kind === "recent")) {
					return render_row(d, current_text(this));
				}
			} catch (e) {
				console.error("[ERPNext Enhancements] desk search: row failed", e);
			}
			const li = original_item.apply(this, arguments);
			try {
				if (d && d._ee_heading && li) {
					li.classList.add("ee-ds-headed");
					li.insertBefore(heading_node(d._ee_heading, ""), li.firstChild);
				}
			} catch (e) {
				/* frappe's row stands without its heading */
			}
			return li;
		};

		const ul = aw.ul;
		if (ul) {
			ul.classList.add("ee-ds-list");
			const own = (event) => {
				const node = event.target && event.target.closest ? event.target.closest(".ee-ds-action, .ee-ds-head") : null;
				return node && ul.contains(node) ? node : null;
			};
			// Capture phase on the list itself: these run before Awesomplete's own mousedown/click
			// (bubble phase on the same list) and stop it, so a button never selects its row.
			ul.addEventListener(
				"mousedown",
				(event) => {
					if (!own(event)) return;
					event.stopPropagation();
					event.preventDefault(); // keep the focus in the input
				},
				true
			);
			ul.addEventListener(
				"click",
				(event) => {
					const node = own(event);
					if (!node) return;
					event.stopPropagation();
					if (node.classList.contains("ee-ds-call") || node.classList.contains("ee-ds-mail")) {
						return; // the browser opens the phone or mail app
					}
					event.preventDefault();
					if (node._ee_link) follow(bar, node._ee_link, event);
				},
				true
			);
		}

		const fetch_results = (txt) => {
			state.seq += 1;
			const seq = state.seq;
			if (state.controller) state.controller.abort();
			state.controller = typeof root.AbortController === "function" ? new root.AbortController() : null;
			const url = "/api/method/" + METHOD + "?txt=" + encodeURIComponent(txt);
			root.fetch(url, {
				method: "GET",
				credentials: "same-origin",
				headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
				signal: state.controller ? state.controller.signal : undefined,
			})
				.then((r) => (r.ok ? r.json() : null))
				.then((body) => {
					if (seq !== state.seq || !body || !body.message) return;
					state.results = { txt: txt, groups: body.message.groups || [] };
					if (current_text(aw) !== txt) return;
					aw.list = bar.deduplicate(bar.options || []);
				})
				.catch(() => {
					/* aborted or offline: frappe's own rows stand */
				});
		};

		const input = aw.input;
		if (input) {
			input.addEventListener("input", () => {
				clearTimeout(state.timer);
				const txt = current_text(aw);
				if (txt.length < MIN_CHARS || txt.charAt(0) === "#") {
					state.seq += 1; // a reply for older text is stale now
					state.results = null;
					return;
				}
				state.timer = setTimeout(() => fetch_results(txt), DEBOUNCE_MS);
			});
		}
	}

	function install() {
		const fr = root.frappe;
		const AwesomeBar = fr && fr.search && fr.search.AwesomeBar;
		const proto = AwesomeBar && AwesomeBar.prototype;
		if (!proto || proto._ee_desk_search_patched || typeof proto.setup_event_listeners !== "function") {
			return false;
		}
		proto._ee_desk_search_patched = true;
		const original = proto.setup_event_listeners;
		proto.setup_event_listeners = function () {
			const out = original.apply(this, arguments);
			try {
				decorate(this);
			} catch (e) {
				console.error("[ERPNext Enhancements] desk search: not installed", e);
			}
			return out;
		};
		// A box frappe already set up (opened before this ran) gets decorated now. frappe 16.53 keeps
		// the one instance at frappe.app.awesome_bar (desk.js).
		const bar = fr.app && fr.app.awesome_bar;
		if (bar && bar.awesomplete) {
			try {
				decorate(bar);
			} catch (e) {
				/* the next open is still frappe's own box */
			}
		}
		return true;
	}

	root.erpnext_enhancements = root.erpnext_enhancements || {};
	root.erpnext_enhancements.desk_search = {
		normalize,
		escape_html,
		mark,
		format_phone,
		tel_href,
		mail_href,
		names_a_screen,
		record_options,
		recent_options,
		compose,
		push_recent,
		results_for,
		install,
		decorate,
		remember,
		constants: { MIN_CHARS, DEBOUNCE_MS, MAX_SCREENS, MAX_HELP, RECENT_MAX, RECENT_SHOW, TOP, METHOD },
	};

	if (root.jQuery && root.document) {
		root.jQuery(root.document).on("app_ready", install);
		root.jQuery(root.document).on("form-refresh", (event, frm) => {
			try {
				remember(frm);
			} catch (e) {
				/* recents are a convenience */
			}
		});
	}
})();
