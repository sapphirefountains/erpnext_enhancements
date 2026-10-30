// Marketing Performance (v1.756.0). Nik, 2026-10-29: Google Analytics 4 and Search Console merged into
// one marketing view, with inbound calls, Google Business Profile, ad spend and social page stats beside
// them, on one page that replaces the GA4 Dashboard and the Marketing Dashboard widgets.
//
//   /desk/marketing-performance                                   the last 28 days
//   /desk/marketing-performance?from=2026-09-01&to=2026-09-30     a range
//   ...&compare=1                                                 beside the range before it
//   ...&ghost=previous | ghost=last_year                          a faint line of that period on the trend
//   /desk/marketing-performance/search                            the Search tab (v1.757.0)
//   /desk/marketing-performance/inquiries                         the Inquiries and calls tab
//
// THE TAB IS THE ROUTE, the range the query. A tab is one frappe.set_route (a history entry, the query
// carried along as route options), like the Reorder Board's tabs.
//
// THE RANGE IS IN THE ADDRESS, and each change of it is a history entry of its own (never break
// Back/Forward). Like the Rental Planner's start date: the page writes the query with
// history.pushState (routing to the page it is on would make older v16 routers drop the query), and
// Back or Forward lands through the router like any entry, which copies the query into
// frappe.route_options and fires on_page_show. route_changed reads the range there and consumes it, so
// a later plain set_route to this page does not replay it. A bare address is the default range.
//
// Everything shown comes from one POST (marketing/performance/api.get_performance), which reads stored
// rows only -- nothing here waits on Google. What a viewer may not see (money, who called, the
// Opportunity rows) is left out by the server, not hidden here. Calls go through sf_kit's quiet path,
// so a failure is said once, in the page. The CSS is marketing_performance.css beside this file, every
// selector under .mkp- (a Desk Page's stylesheet is global once loaded).

frappe.pages["marketing-performance"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Marketing Performance"),
		single_column: true,
	});
	wrapper.marketing_performance = new MarketingPerformancePage(page);
};

frappe.pages["marketing-performance"].on_page_show = function (wrapper) {
	if (wrapper.marketing_performance) wrapper.marketing_performance.route_changed();
};

const MKP_PAGE = "marketing-performance";
const MKP_API = "erpnext_enhancements.marketing.performance.api.get_performance";
const MKP_TAB_API = {
	search: "erpnext_enhancements.marketing.performance.api.get_search_tab",
	inquiries: "erpnext_enhancements.marketing.performance.api.get_inquiries_tab",
};
const MKP_TABS = [
	{ key: "overview", label: () => __("Overview") },
	{ key: "search", label: () => __("Search") },
	{ key: "inquiries", label: () => __("Inquiries and calls") },
];
const MKP_GHOSTS = ["", "previous", "last_year"];
const MKP_BACKFILL = "erpnext_enhancements.marketing.performance.web_sync.start_backfill";
const MKP_DAY_MS = 86400000;
const MKP_PRESETS = [
	{ key: "7", days: 7, label: () => __("7 days") },
	{ key: "28", days: 28, label: () => __("28 days") },
	{ key: "90", days: 90, label: () => __("90 days") },
	{ key: "365", days: 365, label: () => __("12 months") },
];

// The page's decisions, without the DOM. Tests load this file under node (tests/test_marketing_performance.py).
const MKP = {
	valid_day(text) {
		const value = String(text === null || text === undefined ? "" : text).trim();
		if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
		const d = new Date(value + "T00:00:00Z");
		return Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== value ? null : value;
	},

	add_days(day, n) {
		const d = new Date(day + "T00:00:00Z");
		return new Date(d.getTime() + n * MKP_DAY_MS).toISOString().slice(0, 10);
	},

	// The range for a preset: the last `days` days ending yesterday (today is still moving).
	preset_range(days, today) {
		const to = MKP.add_days(today, -1);
		return { from: MKP.add_days(to, -(days - 1)), to };
	},

	// The state an address asks for. Unreadable days are dropped; a reversed range is swapped; nothing
	// readable is the default (null from/to: the server's 28 days).
	state_from(options) {
		const opts = options || {};
		let from = MKP.valid_day(opts.from);
		let to = MKP.valid_day(opts.to);
		if (from && to && from > to) [from, to] = [to, from];
		const compare = ["1", "true", "yes"].includes(String(opts.compare || "").toLowerCase());
		const ghost = MKP_GHOSTS.includes(String(opts.ghost || "")) ? String(opts.ghost || "") : "";
		return { from, to, compare, ghost };
	},

	// The tab a route names; the bare page is the Overview.
	tab_from_route(route) {
		const tab = (route || [])[1];
		return MKP_TABS.some((t) => t.key === tab) ? tab : "overview";
	},

	route_for(tab) {
		return tab && tab !== "overview" ? [MKP_PAGE, tab] : [MKP_PAGE];
	},

	// The state as route options, for a tab change that keeps the range.
	options_for(state) {
		const out = {};
		if (state.from) out.from = state.from;
		if (state.to) out.to = state.to;
		if (state.compare) out.compare = 1;
		if (state.ghost) out.ghost = state.ghost;
		return out;
	},

	// Running totals for stacked areas: out[k][i] is the sum of the first k+1 series at day i.
	stack(series) {
		const out = [];
		series.forEach((values, k) => {
			out.push(values.map((v, i) => (Number(v) || 0) + (k ? out[k - 1][i] : 0)));
		});
		return out;
	},

	// 0..4: how dark a heat-map cell is, by its share of the busiest cell.
	heat_level(value, max) {
		if (!value || !max) return 0;
		return Math.min(4, Math.max(1, Math.ceil((value / max) * 4)));
	},

	// An http(s) address, or "" for anything else (javascript:, data: ...): a user-entered link is never
	// an href until it passes this.
	http_link(value) {
		const text = String(value || "").trim();
		return /^https?:\/\//i.test(text) ? text : "";
	},

	// The x position (0..w) of a date on a run of dates; null when it is not on it.
	x_of(dates, day, w) {
		const i = (dates || []).indexOf(day);
		if (i < 0) return null;
		return dates.length > 1 ? (i / (dates.length - 1)) * w : w / 2;
	},

	// The query for a state, "" for the default. Order is fixed so equal states write equal addresses.
	query_for(state) {
		const parts = [];
		if (state.from) parts.push("from=" + encodeURIComponent(state.from));
		if (state.to) parts.push("to=" + encodeURIComponent(state.to));
		if (state.compare) parts.push("compare=1");
		if (state.ghost) parts.push("ghost=" + encodeURIComponent(state.ghost));
		return parts.length ? "?" + parts.join("&") : "";
	},

	same(a, b) {
		return MKP.query_for(a || {}) === MKP.query_for(b || {});
	},

	// Which preset a state is, so its button reads as pressed; null for a custom range.
	preset_of(state, today) {
		if (!state.from && !state.to) return "28";
		for (const p of MKP_PRESETS) {
			const r = MKP.preset_range(p.days, today);
			if (r.from === state.from && r.to === state.to) return p.key;
		}
		return null;
	},

	fmt_int(value) {
		if (value === null || value === undefined || value === "") return "–";
		return Math.round(Number(value) || 0).toLocaleString();
	},

	fmt_pct(value, digits) {
		if (value === null || value === undefined || !Number.isFinite(Number(value))) return "–";
		return (Number(value) * 100).toFixed(digits === undefined ? 1 : digits) + "%";
	},

	fmt_pos(value) {
		return value === null || value === undefined ? "–" : Number(value).toFixed(1);
	},

	fmt_money(value, currency) {
		if (value === null || value === undefined) return "–";
		try {
			return new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "USD", maximumFractionDigits: 0 }).format(value);
		} catch (e) {
			return MKP.fmt_int(value);
		}
	},

	// "+12%" / "−8%" against the previous range, and a tone: none when either side is missing or 0.
	delta(current, previous) {
		if (current === null || current === undefined || !previous) return null;
		const change = (Number(current) - Number(previous)) / Number(previous);
		if (!Number.isFinite(change)) return null;
		const pct = Math.round(change * 100);
		return { text: (pct > 0 ? "+" : pct < 0 ? "−" : "±") + Math.abs(pct) + "%", tone: pct > 0 ? "good" : pct < 0 ? "danger" : "neutral" };
	},

	// SVG points for one series on a w x h box: null days break the line into segments.
	// `scale_max`, when given, is the top of the box: a line and its ghost share one, or a drop between them
	// would draw flat.
	line_segments(values, w, h, scale_max) {
		const n = values.length;
		const nums = values.filter((v) => v !== null && v !== undefined).map(Number);
		const max = scale_max || (nums.length ? Math.max(...nums, 1) : 1);
		const segments = [];
		let current = [];
		values.forEach((v, i) => {
			if (v === null || v === undefined) {
				if (current.length) segments.push(current);
				current = [];
				return;
			}
			const x = n > 1 ? (i / (n - 1)) * w : w / 2;
			const y = h - (Number(v) / max) * h;
			current.push(x.toFixed(1) + "," + y.toFixed(1));
		});
		if (current.length) segments.push(current);
		return { segments, max };
	},

	// The one scale a line and its ghost line are both drawn to.
	shared_max(...series) {
		const nums = [].concat(...series.map((s) => s || [])).filter((v) => v !== null && v !== undefined).map(Number);
		return nums.length ? Math.max(...nums, 1) : 1;
	},

	sum(values) {
		const nums = (values || []).filter((v) => v !== null && v !== undefined);
		return nums.length ? nums.reduce((a, b) => a + Number(b), 0) : null;
	},
};

frappe.pages["marketing-performance"].logic = MKP;

// sf_kit for the page; a Desk opened before the deploy that added the loader asks for the kit itself, and a
// kit that did not arrive leaves a plain stand-in of the same shape (esc, call, state.render, chip).
function mkp_kit() {
	if (typeof window.sf_kit_ready === "function") return window.sf_kit_ready();
	return new Promise((resolve) => {
		const ready = () => resolve(window.sf_kit && window.sf_kit.state ? window.sf_kit : mkp_plain_kit());
		try {
			frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css"], ready);
		} catch (e) {
			ready();
		}
	});
}

function mkp_plain_kit() {
	const esc = (v) => frappe.utils.escape_html(v === null || v === undefined ? "" : String(v));
	return {
		esc,
		chip: (label) => esc(label),
		call: (method, args, opts) =>
			Promise.resolve(frappe.call({ method, args: args || {}, type: (opts && opts.method) || "POST", silent: true })).then(
				(r) => r && r.message,
				() => Promise.reject({ message: __("The server did not answer. Try again.") })
			),
		state: {
			render(el, kind, opts) {
				$(el).empty().append($('<div class="text-muted"></div>').text((opts && opts.title) || ""));
			},
		},
		toast: (message) => frappe.show_alert({ message, indicator: "green" }),
	};
}

const MKP_FLAG_LABELS = {
	low_ctr: () => __("Low CTR"),
	striking: () => __("Striking distance"),
	no_key_events: () => __("No key events"),
};

class MarketingPerformancePage {
	constructor(page) {
		this.page = page;
		this.sf = null;
		this.state = { from: null, to: null, compare: false, ghost: "" };
		this.tab = "overview";
		this.data = null;
		this.tab_data = null;
		this.cache = {};
		this.seq = 0;
		this._timer = null;
		this.$root = $('<div class="mkp-app"></div>').appendTo(page.main);
		this.$root.html(`
			<form class="mkp-controls" aria-label="${__("Date range")}">
				<div class="mkp-presets" role="group" aria-label="${__("Quick ranges")}"></div>
				<label class="mkp-field"><span>${__("From")}</span><input type="date" name="from" class="mkp-input"></label>
				<label class="mkp-field"><span>${__("To")}</span><input type="date" name="to" class="mkp-input"></label>
				<button type="submit" class="mkp-btn">${__("Apply")}</button>
				<label class="mkp-check"><input type="checkbox" name="compare"> ${__("Compare to previous period")}</label>
				<label class="mkp-field"><span>${__("Ghost line")}</span><select name="ghost" class="mkp-input">
					<option value="">${__("None")}</option>
					<option value="previous">${__("Previous period")}</option>
					<option value="last_year">${__("Last year")}</option>
				</select></label>
			</form>
			<nav class="mkp-tabs" role="tablist" aria-label="${__("Marketing Performance")}"></nav>
			<div class="mkp-range-note" aria-live="polite"></div>
			<div class="mkp-body"></div>
		`);
		this.$controls = this.$root.find(".mkp-controls");
		this.$body = this.$root.find(".mkp-body");
		this.ready = mkp_kit().then((sf) => {
			this.sf = sf;
		});
		page.set_secondary_action(__("Refresh"), () => this.load(true), "refresh");
		if ((frappe.user_roles || []).includes("System Manager")) {
			page.add_menu_item(__("Backfill marketing history"), () => this.backfill());
		}
		this.bind();
	}

	esc(value) {
		return this.sf ? this.sf.esc(value) : frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
	}

	chip(label, tone) {
		return this.sf ? this.sf.chip(label, tone) : this.esc(label);
	}

	today() {
		return frappe.datetime.get_today();
	}

	showing() {
		return (frappe.get_route() || [])[0] === MKP_PAGE;
	}

	// One draw per route change, however many show events arrive for it.
	route_changed() {
		if (this._timer) return;
		this._timer = setTimeout(() => {
			this._timer = null;
			if (!this.showing()) return;
			const state = MKP.state_from(this.take_route_options());
			const tab = MKP.tab_from_route(frappe.get_route());
			const changed = !this.data || !MKP.same(state, this.state) || tab !== this.tab;
			this.state = state;
			this.tab = tab;
			this.sync_controls();
			if (changed) this.load();
		}, 0);
	}

	// The range from frappe.route_options (the router copies the address's query there on every route
	// change, Back included), taken out as the list view takes its filters.
	take_route_options() {
		const opts = frappe.route_options;
		if (!opts) return {};
		const out = {};
		for (const key of ["from", "to", "compare", "ghost"]) {
			if (Object.prototype.hasOwnProperty.call(opts, key)) {
				out[key] = opts[key];
				delete opts[key];
			}
		}
		if (!Object.keys(opts).length) frappe.route_options = null;
		return out;
	}

	// A new range the person chose: a history entry of its own, then the data.
	go(state) {
		if (MKP.same(state, this.state)) return;
		this.state = state;
		const url = window.location.pathname + MKP.query_for(state);
		if (url !== window.location.pathname + window.location.search) window.history.pushState(null, "", url);
		this.sync_controls();
		this.load();
	}

	// Another tab: a route of its own (a history entry), carrying the range as route options.
	go_tab(tab) {
		if (tab === this.tab) return;
		frappe.set_route(MKP.route_for(tab), MKP.options_for(this.state));
	}

	bind() {
		this.$root.on("click", ".mkp-tab", (e) => this.go_tab($(e.currentTarget).attr("data-tab")));
		this.$controls.on("change", 'select[name="ghost"]', (e) => {
			this.go(Object.assign({}, this.state, { ghost: e.currentTarget.value }));
		});
		this.$controls.on("submit", (e) => {
			e.preventDefault();
			const form = this.$controls.get(0);
			const from = MKP.valid_day(form.from.value);
			const to = MKP.valid_day(form.to.value);
			if (!from || !to) {
				frappe.show_alert({ message: __("Pick both a From and a To date."), indicator: "orange" });
				return;
			}
			this.go(MKP.state_from({ from, to, compare: this.state.compare ? "1" : "", ghost: this.state.ghost }));
		});
		this.$controls.on("change", 'input[name="compare"]', (e) => {
			this.go(Object.assign({}, this.state, { compare: e.currentTarget.checked }));
		});
		this.$controls.on("click", ".mkp-preset", (e) => {
			const days = Number($(e.currentTarget).attr("data-days"));
			const range = MKP.preset_range(days, this.today());
			const state = days === 28 ? { from: null, to: null } : range;
			this.go(Object.assign({}, state, { compare: this.state.compare, ghost: this.state.ghost }));
		});
	}

	sync_controls() {
		const today = this.today();
		const shown = this.state.from && this.state.to ? this.state : MKP.preset_range(28, today);
		const form = this.$controls.get(0);
		form.from.value = shown.from;
		form.to.value = shown.to;
		form.compare.checked = !!this.state.compare;
		form.ghost.value = this.state.ghost || "";
		this.$root.find(".mkp-tabs").html(
			MKP_TABS.map(
				(t) =>
					`<button type="button" role="tab" class="mkp-tab${t.key === this.tab ? " mkp-tab-on" : ""}" data-tab="${t.key}" aria-selected="${
						t.key === this.tab
					}">${this.esc(t.label())}</button>`
			).join("")
		);
		const active = MKP.preset_of(this.state, today);
		this.$controls.find(".mkp-presets").html(
			MKP_PRESETS.map(
				(p) =>
					`<button type="button" class="mkp-preset${p.key === active ? " mkp-preset-on" : ""}" data-days="${p.days}" aria-pressed="${
						p.key === active
					}">${this.esc(p.label())}</button>`
			).join("")
		);
	}

	async load(fresh) {
		await this.ready;
		if (fresh) this.cache = {};
		const seq = ++this.seq;
		this.sf.state.render(this.$body.get(0), "loading", { title: __("Loading marketing performance…") });
		const args = { from_date: this.state.from || "", to_date: this.state.to || "" };
		const key = MKP.query_for(this.state);
		try {
			const main =
				this.cache[key] ||
				(await this.sf.call(MKP_API, Object.assign({}, args, { compare: this.state.compare ? 1 : 0, ghost: this.state.ghost || "" }), { quiet: true }));
			this.cache[key] = main;
			let extra = null;
			if (MKP_TAB_API[this.tab]) {
				const tab_key = this.tab + key;
				extra = this.cache[tab_key] || (await this.sf.call(MKP_TAB_API[this.tab], args, { quiet: true }));
				this.cache[tab_key] = extra;
			}
			if (seq !== this.seq) return;
			this.data = main;
			this.tab_data = extra;
			this.render();
		} catch (err) {
			if (seq !== this.seq) return;
			this.sf.state.render(this.$body.get(0), "error", {
				title: __("Marketing Performance did not load."),
				body: (err && err.message) || "",
				on_action: () => this.load(),
			});
		}
	}

	backfill() {
		frappe.confirm(
			__(
				"Read every ready source back as far as its API allows (Search Console 16 months, GA4 to its first day, Business Profile and the social pages as far as each serves)? It runs in the background, a month at a time, and is safe to run again."
			),
			async () => {
				await this.ready;
				try {
					await this.sf.call(MKP_BACKFILL, {}, { quiet: true });
					frappe.show_alert({ message: __("Backfill queued. Source health shows its progress."), indicator: "green" });
				} catch (err) {
					frappe.msgprint((err && err.message) || __("The backfill could not be queued."));
				}
			}
		);
	}

	// ---------------------------------------------------------------- drawing

	render() {
		const d = this.data;
		const note = d.previous
			? __("{0} to {1}, against {2} to {3}", [d.from_date, d.to_date, d.previous.from_date, d.previous.to_date])
			: __("{0} to {1}", [d.from_date, d.to_date]);
		this.$root.find(".mkp-range-note").text(note);
		const sections = {
			overview: () => [
				this.tiles_html(),
				this.section(__("Trend"), this.trend_html(), "mkp-wide"),
				this.section(__("Funnel"), this.funnel_bars_html()),
				this.section(__("Channel mix"), this.channel_mix_html()),
				this.section(__("Channels"), this.channels_html()),
				this.section(__("Ad spend"), this.spend_html()),
				this.section(__("Google Business Profile"), this.gbp_html()),
				this.section(__("Social pages"), this.social_html()),
				this.section(__("Source health"), this.health_html(), "mkp-wide"),
			],
			search: () => [
				this.section(__("Search trend"), this.search_trend_html(), "mkp-wide"),
				this.section(__("Fix first"), this.flags_html(), "mkp-wide"),
				this.section(__("Click rate against position"), this.scatter_html(), "mkp-wide"),
				this.section(__("Near page one"), this.near_html()),
				this.section(__("Fading pages"), this.fading_html()),
				this.section(__("Search queries"), this.query_changes_html(), "mkp-wide"),
				this.section(__("Landing pages"), this.pages_html(), "mkp-wide"),
			],
			inquiries: () => [
				this.kpis_html(),
				this.section(__("Inquiries by source"), this.by_source_html(), "mkp-wide"),
				this.section(__("Funnel"), this.funnel_html()),
				this.section(__("Speed to first reply"), this.reply_html()),
				this.section(__("Inbound calls"), this.calls_html()),
				this.section(__("Missed calls"), this.missed_html()),
				this.section(__("Calls by weekday and hour"), this.heatmap_html(), "mkp-wide"),
				this.section(__("Unsourced inquiries"), this.unsourced_html(), "mkp-wide"),
			],
		};
		this.$body.html((sections[this.tab] || sections.overview)().join(""));
	}

	section(title, body, extra) {
		return `<section class="mkp-card ${extra || ""}"><h3 class="mkp-h">${this.esc(title)}</h3>${body}</section>`;
	}

	empty(text) {
		return `<p class="mkp-empty">${this.esc(text)}</p>`;
	}

	table(headers, rows, opts) {
		if (!rows.length) return this.empty((opts && opts.empty) || __("Nothing in this range."));
		const head = headers.map((h) => `<th scope="col"${h.num ? ' class="mkp-num"' : ""}>${this.esc(h.label)}</th>`).join("");
		return `<div class="mkp-table-wrap"><table class="mkp-table"><thead><tr>${head}</tr></thead><tbody>${rows
			.map((cells) => `<tr>${cells.map((c, i) => `<td${headers[i] && headers[i].num ? ' class="mkp-num"' : ""}>${c}</td>`).join("")}</tr>`)
			.join("")}</tbody></table></div>`;
	}

	kpis_html() {
		const d = this.data;
		const prev = (d.previous && d.previous.kpis) || null;
		const tiles = [
			["sessions", __("Sessions")],
			["organic_clicks", __("Organic clicks")],
			["opportunities", __("Opportunities created")],
			["calls", __("Inbound calls")],
			["first_time_callers", __("First-time callers")],
			["key_events", __("Key events")],
			["won", __("Won")],
		];
		return `<div class="mkp-kpis">${tiles
			.map(([key, label]) => {
				const delta = prev ? MKP.delta(d.kpis[key], prev[key]) : null;
				const was = prev ? `<span class="mkp-was">${this.esc(__("was {0}", [MKP.fmt_int(prev[key])]))}</span>` : "";
				return `<div class="mkp-kpi"><span class="mkp-kpi-label">${this.esc(label)}</span><span class="mkp-kpi-value">${this.esc(
					MKP.fmt_int(d.kpis[key])
				)}</span>${delta ? this.chip(delta.text, delta.tone) : ""}${was}</div>`;
			})
			.join("")}</div>`;
	}

	trend_html() {
		const t = this.data.trend;
		const series = [
			["sessions", __("Sessions"), "design"],
			["organic_clicks", __("Organic clicks"), "service"],
			["opportunities", __("Opportunities created"), "build"],
			["calls", __("Inbound calls"), "rent"],
		];
		const w = 640;
		const h = 44;
		const dates = t.dates || [];
		const rows = series
			.map(([key, label, tone]) => {
				const values = (t.series && t.series[key]) || [];
				const ghost_values = (this.data.overview && this.data.overview.ghost && this.data.overview.ghost[key]) || null;
				const scale = ghost_values ? MKP.shared_max(values, ghost_values) : null;
				const { segments } = MKP.line_segments(values, w, h, scale);
				const max = MKP.shared_max(values);
				const total = MKP.sum(values);
				const lines = segments
					.map((points) =>
						points.length === 1
							? `<circle cx="${points[0].split(",")[0]}" cy="${points[0].split(",")[1]}" r="2" class="mkp-line mkp-line-${tone}"></circle>`
							: `<polyline points="${points.join(" ")}" class="mkp-line mkp-line-${tone}" fill="none"></polyline>`
					)
					.join("");
				const ghost_line = ghost_values ? this.ghost_svg(ghost_values, scale, w, h) : "";
				const summary = __("{0}: {1} in total, at most {2} a day", [label, MKP.fmt_int(total), MKP.fmt_int(max)]);
				return `<div class="mkp-trend-row">
					<div class="mkp-trend-label"><span>${this.esc(label)}</span><strong>${this.esc(MKP.fmt_int(total))}</strong></div>
					<svg class="mkp-spark" viewBox="0 -2 ${w} ${h + 4}" preserveAspectRatio="none" role="img" aria-label="${this.esc(summary)}">${ghost_line}${this.markers_svg(dates, w, h)}${lines}</svg>
				</div>`;
			})
			.join("");
		const axis = dates.length
			? `<div class="mkp-axis"><span>${this.esc(dates[0])}</span><span>${this.esc(dates[Math.floor((dates.length - 1) / 2)])}</span><span>${this.esc(
					dates[dates.length - 1]
			  )}</span></div>`
			: "";
		const g = this.data.overview && this.data.overview.ghost;
		const ghost_note = g
			? " " + __("The faint line is {0} to {1}.", [g.from_date, g.to_date])
			: "";
		return `<div class="mkp-trend">${rows}${axis}</div><p class="mkp-hint">${this.esc(
			__("One time axis, each line on its own scale. A gap is a day a source has no data for, not a zero.") + ghost_note
		)}</p>${this.events_list_html()}`;
	}

	flag_chips(flags) {
		return (flags || []).map((f) => this.chip((MKP_FLAG_LABELS[f] || (() => f))(), f === "no_key_events" ? "danger" : "warn")).join(" ");
	}

	flags_html() {
		const f = this.data.flags || {};
		const list = (rows, line) => (rows && rows.length ? `<ul class="mkp-list">${rows.map(line).join("")}</ul>` : this.empty(__("None in this range.")));
		const page = (r) => `<code>${this.esc(r.path)}</code>`;
		return `<div class="mkp-flags">
			<div><h4 class="mkp-h4">${this.esc(__("Seen, not clicked"))}</h4><p class="mkp-hint">${this.esc(
				__("200+ impressions and under 1.5% CTR: rewrite the title and description.")
			)}</p>${list(f.low_ctr, (r) => `<li>${page(r)} ${this.esc(MKP.fmt_int(r.impressions))} · ${this.esc(MKP.fmt_pct(r.ctr))}</li>`)}</div>
			<div><h4 class="mkp-h4">${this.esc(__("Striking distance"))}</h4><p class="mkp-hint">${this.esc(
				__("Average position 8 to 20: a small lift moves these onto page one.")
			)}</p>${list(f.striking, (r) => `<li>${page(r)} ${this.esc(__("position {0}", [MKP.fmt_pos(r.position)]))}</li>`)}${
			f.queries_striking && f.queries_striking.length
				? `<h4 class="mkp-h4">${this.esc(__("Queries"))}</h4>${list(
						f.queries_striking,
						(q) => `<li>${this.esc(q.query)} ${this.esc(__("position {0}", [MKP.fmt_pos(q.position)]))}</li>`
				  )}`
				: ""
		}</div>
			<div><h4 class="mkp-h4">${this.esc(__("Traffic, no key events"))}</h4><p class="mkp-hint">${this.esc(
				__("30+ sessions and no key event: the page brings people and asks nothing of them.")
			)}</p>${list(f.no_key_events, (r) => `<li>${page(r)} ${this.esc(__("{0} sessions", [MKP.fmt_int(r.sessions)]))}</li>`)}</div>
		</div>`;
	}

	pages_html() {
		const rows = (this.data.landing_pages || []).map((r) => [
			`<code>${this.esc(r.path)}</code>`,
			this.esc(MKP.fmt_int(r.sessions)),
			this.esc(MKP.fmt_int(r.key_events)),
			this.esc(MKP.fmt_pct(r.key_event_rate)),
			this.esc(MKP.fmt_int(r.clicks)),
			this.esc(MKP.fmt_int(r.impressions)),
			this.esc(MKP.fmt_pct(r.ctr)),
			this.esc(MKP.fmt_pos(r.position)),
			this.flag_chips(r.flags),
		]);
		return (
			this.table(
				[
					{ label: __("Page") },
					{ label: __("Sessions"), num: true },
					{ label: __("Key events"), num: true },
					{ label: __("Rate"), num: true },
					{ label: __("Clicks"), num: true },
					{ label: __("Impressions"), num: true },
					{ label: __("CTR"), num: true },
					{ label: __("Position"), num: true },
					{ label: __("Flags") },
				],
				rows,
				{ empty: __("No landing pages stored for this range. A System Manager can run the backfill from the menu.") }
			) +
			`<p class="mkp-hint">${this.esc(
				__("GA4 and Search Console joined on the page's path: host, scheme, trailing slash and query string ignored. A dash is a source with no data for that page.")
			)}</p>`
		);
	}

	queries_html() {
		const q = this.data.queries || { rows: [], split: {} };
		const split = q.split || {};
		const tile = (key, label) => {
			const s = split[key] || {};
			return `<div class="mkp-split"><span class="mkp-kpi-label">${this.esc(label)}</span><span class="mkp-kpi-value">${this.esc(
				MKP.fmt_int(s.clicks)
			)}</span><span class="mkp-hint">${this.esc(__("{0} impressions · CTR {1}", [MKP.fmt_int(s.impressions), MKP.fmt_pct(s.ctr)]))}</span></div>`;
		};
		const rows = (q.rows || []).map((r) => [
			this.esc(r.query) + (r.branded ? " " + this.chip(__("Branded"), "neutral") : ""),
			this.esc(MKP.fmt_int(r.clicks)),
			this.esc(MKP.fmt_int(r.impressions)),
			this.esc(MKP.fmt_pct(r.ctr)),
			this.esc(MKP.fmt_pos(r.position)),
		]);
		return `<div class="mkp-splits">${tile("branded", __("Branded clicks"))}${tile("unbranded", __("Unbranded clicks"))}</div>${this.table(
			[{ label: __("Query") }, { label: __("Clicks"), num: true }, { label: __("Impr."), num: true }, { label: __("CTR"), num: true }, { label: __("Pos."), num: true }],
			rows
		)}<p class="mkp-hint">${this.esc(
			__("Branded: contains {0} (Marketing Settings). Search Console leaves out rare queries, so these add up to less than the total.", [
				(this.data.branded_terms || []).join(", "),
			])
		)}</p>`;
	}

	channels_html() {
		const rows = (this.data.channels || []).map((c) => [
			this.esc(c.channel),
			this.esc(MKP.fmt_int(c.sessions)),
			`<span class="mkp-bar" style="--mkp-share:${Math.round((c.share || 0) * 100)}%"></span>${this.esc(MKP.fmt_pct(c.share, 0))}`,
			this.esc(MKP.fmt_int(c.key_events)),
			this.esc(MKP.fmt_pct(c.key_event_rate)),
		]);
		return this.table(
			[{ label: __("Channel") }, { label: __("Sessions"), num: true }, { label: __("Share") }, { label: __("Key events"), num: true }, { label: __("Rate"), num: true }],
			rows
		);
	}

	funnel_html() {
		const labels = {
			sessions: __("Sessions"),
			inquiries: __("Inquiries"),
			opportunities: __("Opportunities"),
			won: __("Won"),
		};
		const steps = (this.data.funnel || [])
			.map(
				(s) => `<li class="mkp-step"><span class="mkp-kpi-label">${this.esc(labels[s.step] || s.step)}</span><strong>${this.esc(
					MKP.fmt_int(s.value)
				)}</strong>${s.rate !== null && s.rate !== undefined ? `<span class="mkp-hint">${this.esc(MKP.fmt_pct(s.rate))}</span>` : ""}</li>`
			)
			.join("");
		return `<ol class="mkp-funnel">${steps}</ol><p class="mkp-hint">${this.esc(
			__("Inquiries are Opportunities created plus first-time callers who did not become one. Won counts deals closed in the range, historical ones left out.")
		)}</p>`;
	}

	calls_html() {
		const c = this.data.calls || {};
		const head = `<div class="mkp-splits">
			<div class="mkp-split"><span class="mkp-kpi-label">${this.esc(__("Incoming"))}</span><span class="mkp-kpi-value">${this.esc(MKP.fmt_int(c.incoming))}</span></div>
			<div class="mkp-split"><span class="mkp-kpi-label">${this.esc(__("First-time"))}</span><span class="mkp-kpi-value">${this.esc(MKP.fmt_int(c.first_time))}</span></div>
			<div class="mkp-split"><span class="mkp-kpi-label">${this.esc(__("Became an Opportunity"))}</span><span class="mkp-kpi-value">${this.esc(
				MKP.fmt_int(c.converted_calls)
			)}</span></div>
		</div>`;
		const note = `<p class="mkp-hint">${this.esc(
			__("A call became an Opportunity when one for its Customer or Contact was created within 30 days after it.")
		)}</p>`;
		if (!c.rows) return head + note;
		const rows = c.rows.map((r) => [
			this.esc(frappe.datetime.str_to_user(r.at)),
			`<a href="/desk/call-log/${encodeURIComponent(r.name)}">${this.esc(r.caller || r.number || r.name)}</a>`,
			r.first_time ? this.chip(__("First time"), "good") : "",
			(r.opportunities || []).map((o) => `<a href="/desk/opportunity/${encodeURIComponent(o)}">${this.esc(o)}</a>`).join(", "),
		]);
		return head + this.table([{ label: __("When") }, { label: __("Caller") }, { label: "" }, { label: __("Opportunity") }], rows) + note;
	}

	unsourced_html() {
		const u = this.data.unsourced || {};
		const head =
			`<p>${this.esc(
				__("{0} of {1} Opportunities created in this range have no source, or the recorded Unknown (pre-Aug 2026).", [MKP.fmt_int(u.count), MKP.fmt_int(u.total)])
			)}</p>` +
			(u.total && u.count === u.total
				? `<p class="mkp-hint">${this.esc(
						__("Website attribution is not set up yet: once the WordPress form capture is installed, new inquiries arrive with their source.")
				  )}</p>`
				: "");
		if (!u.rows) return head;
		const rows = u.rows.map((r) => [
			`<a href="/desk/opportunity/${encodeURIComponent(r.name)}">${this.esc(r.title || r.name)}</a>`,
			this.esc(r.status),
			this.esc(r.created),
			this.esc(r.lead_source || __("blank")),
		]);
		return head + this.table([{ label: __("Opportunity") }, { label: __("Status") }, { label: __("Created") }, { label: __("Source") }], rows, { empty: __("Every Opportunity in this range has a source.") });
	}

	spend_html() {
		const s = this.data.spend || {};
		const fin = this.data.views && this.data.views.financial;
		let out = "";
		if (!s.platforms || !s.platforms.length) {
			out += this.empty(
				!s.enabled
					? __("Not set up yet. Ad spend appears here once Google Ads, Meta or LinkedIn is connected on Marketing Connections and switched on in Marketing Settings; its first night brings back as much history as the platform keeps.")
					: s.connected && s.connected.length
					? __("Connected ({0}), but no spend is stored for this range.", [s.connected.join(", ")])
					: __("Not set up yet: Marketing Settings is on, and no ad platform is connected (Marketing Connections).")
			);
		} else {
			out += this.table(
				[{ label: __("Platform") }, { label: __("Impressions"), num: true }, { label: __("Clicks"), num: true }].concat(fin ? [{ label: __("Spend"), num: true }] : []),
				s.platforms.map((p) =>
					[this.esc(p.platform), this.esc(MKP.fmt_int(p.impressions)), this.esc(MKP.fmt_int(p.clicks))].concat(fin ? [this.esc(MKP.fmt_money(p.spend, p.currency))] : [])
				)
			);
		}
		if (fin && s.roas && s.roas.length) {
			out += `<h4 class="mkp-h4">${this.esc(__("Return on ad spend"))}</h4>` +
				this.table(
					[
						{ label: __("Platform") },
						{ label: __("Leads"), num: true },
						{ label: __("Won"), num: true },
						{ label: __("Cost / lead"), num: true },
						{ label: __("Contract value"), num: true },
						{ label: __("ROAS"), num: true },
					],
					s.roas.map((r) => [
						this.esc(r.platform),
						this.esc(MKP.fmt_int(r.leads)),
						this.esc(MKP.fmt_int(r.won)),
						this.esc(MKP.fmt_money(r.cost_per_lead)),
						this.esc(MKP.fmt_money(r.contract_value)),
						this.esc(r.roas_contract === null || r.roas_contract === undefined ? "–" : Number(r.roas_contract).toFixed(2) + "×"),
					])
				) +
				`<p class="mkp-hint">${this.esc(__("From the Ad Spend ROAS report: cohorts by lead month, so recent months fill in as deals close."))}</p>`;
		}
		if (fin && s.offline && s.offline.length) {
			out += `<h4 class="mkp-h4">${this.esc(__("Offline spend (Marketing Spend)"))}</h4>` +
				this.table([{ label: __("Channel") }, { label: __("Amount"), num: true }], s.offline.map((o) => [this.esc(o.channel), this.esc(MKP.fmt_money(o.amount))]));
		}
		return out;
	}

	gbp_html() {
		const g = this.data.business_profile || {};
		if (!g.listings || !g.listings.length) {
			return this.empty(
				g.connected
					? __("Connected, but nothing is stored for this range yet.")
					: __("Not set up yet. Google approves Business Profile API access first; then it is connected on Marketing Connections and switched on in Marketing Settings, and the first night brings back about 18 months.")
			);
		}
		return this.table(
			[
				{ label: __("Listing") },
				{ label: __("Calls"), num: true },
				{ label: __("Website"), num: true },
				{ label: __("Directions"), num: true },
				{ label: __("Maps impr."), num: true },
				{ label: __("Search impr."), num: true },
				{ label: __("Reviews"), num: true },
				{ label: __("Rating"), num: true },
				{ label: __("Unreplied"), num: true },
			],
			g.listings.map((l) => [
				this.esc(l.title || l.location_id),
				this.esc(MKP.fmt_int(l.call_clicks)),
				this.esc(MKP.fmt_int(l.website_clicks)),
				this.esc(MKP.fmt_int(l.direction_requests)),
				this.esc(MKP.fmt_int(l.maps_impressions)),
				this.esc(MKP.fmt_int(l.search_impressions)),
				this.esc(MKP.fmt_int(l.review_count)),
				this.esc(l.average_rating ? Number(l.average_rating).toFixed(1) : "–"),
				l.unreplied_reviews ? this.chip(MKP.fmt_int(l.unreplied_reviews), "warn") : this.esc(MKP.fmt_int(l.unreplied_reviews)),
			])
		);
	}

	social_html() {
		const s = this.data.social || {};
		if (!s.accounts || !s.accounts.length) {
			return this.empty(
				s.enabled
					? __("On, but nothing is stored yet: each account needs its publishing connection connected and Enabled ticked.")
					: __("Not set up yet. Social page stats start once Facebook, Instagram, LinkedIn or YouTube is connected and Social Page Stats is switched on in Marketing Settings; the first night brings back what each network keeps.")
			);
		}
		return this.table(
			[
				{ label: __("Account") },
				{ label: __("Network") },
				{ label: __("Followers"), num: true },
				{ label: __("Net new"), num: true },
				{ label: __("Reach"), num: true },
				{ label: __("Views"), num: true },
			],
			s.accounts.map((a) => [
				this.esc(a.name),
				this.esc(a.network),
				this.esc(MKP.fmt_int(a.followers)),
				this.esc(MKP.fmt_int(a.follower_gain)),
				this.esc(MKP.fmt_int(a.reach)),
				this.esc(MKP.fmt_int(a.views)),
			])
		);
	}

	health_html() {
		const h = this.data.health || {};
		const tone = { ok: "good", stale: "warn", empty: "warn", off: "neutral" };
		const label = { ok: __("Landing"), stale: __("Stale"), empty: __("Empty"), off: __("Not set up yet") };
		const rows = (h.sources || []).map((s) => [this.esc(s.name), this.chip(label[s.state] || s.state, tone[s.state]), this.esc(s.detail)]);
		let out = this.table([{ label: __("Source") }, { label: __("State") }, { label: __("Detail") }], rows);
		if (h.snapshot) {
			out += `<p class="mkp-hint">${this.esc(
				__("Nightly 30-day snapshot {0}: GA4 {1}, Search Console {2}.", [h.snapshot.date, h.snapshot.ga4_ok ? "✓" : "✗", h.snapshot.gsc_ok ? "✓" : "✗"])
			)}${h.snapshot.error ? " " + this.esc(h.snapshot.error) : ""}</p>`;
		}
		if (h.runs && h.runs.length) {
			out += `<h4 class="mkp-h4">${this.esc(__("Recent pulls"))}</h4>` +
				this.table(
					[{ label: __("Source") }, { label: __("Kind") }, { label: __("Status") }, { label: __("Window") }, { label: __("Rows"), num: true }, { label: __("Error") }],
					h.runs.map((r) => [
						this.esc(r.platform),
						this.esc(r.sync_type),
						this.chip(r.status, r.status === "Completed" ? "good" : r.status === "Failed" ? "danger" : "neutral"),
						this.esc([r.window_from, r.window_to].filter(Boolean).join(" → ") + (r.progress_through ? " (" + __("done through {0}", [r.progress_through]) + ")" : "")),
						this.esc(MKP.fmt_int(r.created_count)),
						this.esc(r.error || ""),
					])
				);
		}
		return out;
	}

	// ---------------------------------------------------------------- v1.757.0: tiles and graphs

	// D1: each tile with its sparkline, the change against the previous period and last year, and its
	// month-to-date target where a Marketing KPI Target exists (hidden otherwise).
	tiles_html() {
		const o = this.data.overview || {};
		const labels = {
			sessions: __("Sessions"),
			search_clicks: __("Search clicks"),
			inquiries: __("Inquiries"),
			calls: __("Calls"),
			won_value: __("Won value"),
			cost_per_inquiry: __("Cost per inquiry"),
		};
		const money = { won_value: true, cost_per_inquiry: true };
		const fmt = (key, v) => (money[key] ? MKP.fmt_money(v) : MKP.fmt_int(v));
		const tiles = (o.tiles || []).map((t) => {
			const prev = MKP.delta(t.value, t.previous);
			const year = MKP.delta(t.value, t.last_year);
			// For cost, down is good: the chip's tone turns round.
			const tone = (d) => (d && t.key === "cost_per_inquiry" ? Object.assign({}, d, { tone: d.tone === "good" ? "danger" : d.tone === "danger" ? "good" : d.tone }) : d);
			const target = (o.targets || {})[t.key];
			const bar = target ? this.target_html(target, money[t.key]) : "";
			return `<div class="mkp-kpi">
				<span class="mkp-kpi-label">${this.esc(labels[t.key] || t.key)}</span>
				<span class="mkp-kpi-value">${this.esc(fmt(t.key, t.value))}</span>
				${t.spark ? this.spark_svg(t.spark, labels[t.key]) : ""}
				<span class="mkp-deltas">${prev ? this.chip(__("{0} vs before", [prev.text]), tone(prev).tone) : ""} ${
					year ? this.chip(__("{0} vs last year", [year.text]), tone(year).tone) : ""
				}</span>
				${bar}
			</div>`;
		});
		return `<div class="mkp-kpis">${tiles.join("")}</div>`;
	}

	target_html(target, money) {
		const value = Number(target.value) || 0;
		const share = target.target ? Math.max(0, Math.min(1, value / target.target)) : 0;
		const text = __("This month {0} of {1}", [money ? MKP.fmt_money(value) : MKP.fmt_int(value), money ? MKP.fmt_money(target.target) : MKP.fmt_int(target.target)]);
		return `<span class="mkp-target" role="img" aria-label="${this.esc(text)}"><span class="mkp-target-fill" style="--mkp-share:${Math.round(share * 100)}%"></span></span><span class="mkp-hint">${this.esc(text)}</span>`;
	}

	spark_svg(values, label) {
		const w = 120;
		const h = 24;
		const { segments } = MKP.line_segments(values, w, h);
		const lines = segments.map((points) => `<polyline points="${points.join(" ")}" class="mkp-line mkp-line-design" fill="none"></polyline>`).join("");
		return `<svg class="mkp-spark-mini" viewBox="0 -2 ${w} ${h + 4}" preserveAspectRatio="none" role="img" aria-label="${this.esc(
			__("{0}, day by day", [label])
		)}">${lines}</svg>`;
	}

	// G1: the faint line of the previous period or last year, drawn to `max`, the scale it shares with the
	// line it sits under (MKP.shared_max), so a fall from the ghost to now reads as one.
	ghost_svg(ghost, max, w, h) {
		const n = ghost.length;
		const points = ghost
			.map((v, i) => (v === null || v === undefined ? null : `${(n > 1 ? (i / (n - 1)) * w : w / 2).toFixed(1)},${(h - (Number(v) / max) * h).toFixed(1)}`))
			.filter(Boolean);
		return points.length > 1 ? `<polyline points="${points.join(" ")}" class="mkp-ghost" fill="none"></polyline>` : "";
	}

	// F1: a thin vertical line at each event's day, its title as the tooltip.
	markers_svg(dates, w, h) {
		const events = (this.data.overview && this.data.overview.events) || [];
		return events
			.map((e) => {
				const x = MKP.x_of(dates, e.date, w);
				return x === null ? "" : `<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="0" y2="${h}" class="mkp-marker"><title>${this.esc(e.date + ": " + e.title)}</title></line>`;
			})
			.join("");
	}

	events_list_html() {
		const events = (this.data.overview && this.data.overview.events) || [];
		if (!events.length) return "";
		return `<h4 class="mkp-h4">${this.esc(__("Events"))}</h4><ul class="mkp-list">${events
			.map((e) => `<li>${this.esc(e.date)} · ${this.chip(e.type, "neutral")} ${MKP.http_link(e.link) ? `<a href="${this.esc(MKP.http_link(e.link))}" target="_blank" rel="noopener">${this.esc(e.title)}</a>` : this.esc(e.title)}</li>`)
			.join("")}</ul>`;
	}

	// G2: sessions -> key events -> inquiries -> quotes -> won, each bar with the share kept.
	funnel_bars_html() {
		const steps = (this.data.overview && this.data.overview.funnel_bars) || [];
		const first = steps.length && steps[0].value ? steps[0].value : 0;
		if (!steps.length) return this.empty(__("Nothing in this range."));
		return `<ol class="mkp-bars">${steps
			.map((s) => {
				const share = first && s.value !== null ? Math.max(0.01, s.value / first) : 0;
				const kept = s.kept !== null && s.kept !== undefined ? __("{0} kept", [MKP.fmt_pct(s.kept)]) : "";
				return `<li class="mkp-bar-row"><span class="mkp-bar-label">${this.esc(s.label)}</span><span class="mkp-bar-track"><span class="mkp-bar-fill" style="--mkp-share:${Math.round(
					share * 100
				)}%"></span></span><strong class="mkp-num">${this.esc(MKP.fmt_int(s.value))}</strong><span class="mkp-hint">${this.esc(kept)}</span></li>`;
			})
			.join("")}</ol><p class="mkp-hint">${this.esc(
			__("Bars are scaled to sessions. Quotes are Quotations made in the range; inquiries are Opportunities plus first-time callers who did not become one.")
		)}</p>`;
	}

	// G4: sessions by channel, stacked, day by day.
	channel_mix_html() {
		const mix = (this.data.overview && this.data.overview.channel_mix) || {};
		const names = Object.keys(mix);
		if (!names.length) return this.empty(__("No GA4 channel figures stored for this range."));
		const w = 640;
		const h = 140;
		const stacked = MKP.stack(names.map((n) => mix[n]));
		const top = stacked[stacked.length - 1];
		const max = Math.max(...top, 1);
		const n = top.length;
		const x = (i) => (n > 1 ? (i / (n - 1)) * w : w / 2);
		const y = (v) => h - (v / max) * h;
		const tones = ["design", "service", "build", "rent", "neutral", "good", "warn"];
		const areas = stacked
			.map((upper, k) => {
				const lower = k ? stacked[k - 1] : upper.map(() => 0);
				const path = upper.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).concat(lower.map((v, i) => `${x(n - 1 - i).toFixed(1)},${y(lower[n - 1 - i]).toFixed(1)}`));
				return `<polygon points="${path.join(" ")}" class="mkp-area mkp-area-${tones[k % tones.length]}"><title>${this.esc(names[k])}</title></polygon>`;
			})
			.join("");
		const legend = names.map((name, k) => `<span class="mkp-legend"><span class="mkp-swatch mkp-area-${tones[k % tones.length]}"></span>${this.esc(name)}</span>`).join("");
		return `<svg class="mkp-chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="${this.esc(
			__("Sessions by channel, stacked by day")
		)}">${areas}</svg><div class="mkp-legends">${legend}</div>`;
	}

	// ---------------------------------------------------------------- the Search tab

	search_trend_html() {
		const t = (this.tab_data && this.tab_data.trend) || { dates: [], clicks: [], impressions: [] };
		const w = 640;
		const h = 60;
		const row = (label, values, tone, ghost) => {
			const scale = ghost ? MKP.shared_max(values, ghost) : null;
			const { segments } = MKP.line_segments(values, w, h, scale);
			const lines = segments.map((points) => `<polyline points="${points.join(" ")}" class="mkp-line mkp-line-${tone}" fill="none"></polyline>`).join("");
			return `<div class="mkp-trend-row"><div class="mkp-trend-label"><span>${this.esc(label)}</span><strong>${this.esc(MKP.fmt_int(MKP.sum(values)))}</strong></div>
				<svg class="mkp-spark" viewBox="0 -2 ${w} ${h + 4}" preserveAspectRatio="none" role="img" aria-label="${this.esc(label)}">${ghost ? this.ghost_svg(ghost, scale, w, h) : ""}${this.search_markers(t.dates, w, h)}${lines}</svg></div>`;
		};
		return `<div class="mkp-trend">${row(__("Search clicks"), t.clicks, "service", t.clicks_last_year)}${row(__("Impressions"), t.impressions, "design")}</div><p class="mkp-hint">${this.esc(
			__("The faint line is the same days last year, once the history reaches back that far.")
		)}</p>`;
	}

	search_markers(dates, w, h) {
		return ((this.tab_data && this.tab_data.events) || [])
			.map((e) => {
				const x = MKP.x_of(dates, e.date, w);
				return x === null ? "" : `<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="0" y2="${h}" class="mkp-marker"><title>${this.esc(e.date + ": " + e.title)}</title></line>`;
			})
			.join("");
	}

	// G3: CTR against position per page, over the expected curve; dots under the curve need better titles.
	scatter_html() {
		const d = this.tab_data || {};
		const points = d.scatter || [];
		if (!points.length) return this.empty(__("Not enough Search Console page data in this range yet."));
		const w = 640;
		const h = 220;
		const max_pos = 20;
		const max_ctr = Math.max(0.05, ...points.map((p) => p.ctr), ...Object.values((d.curve && d.curve.points) || {}).map(Number));
		const x = (pos) => ((Math.min(Number(pos), max_pos) - 1) / (max_pos - 1)) * w;
		const y = (ctr) => h - (Number(ctr) / max_ctr) * h;
		const curve = Object.entries((d.curve && d.curve.points) || {})
			.map(([pos, ctr]) => `${x(pos).toFixed(1)},${y(ctr).toFixed(1)}`)
			.join(" ");
		const dots = points
			.map((p) => {
				const under = p.expected && p.ctr < p.expected * 0.5;
				return `<circle cx="${x(p.position).toFixed(1)}" cy="${y(p.ctr).toFixed(1)}" r="${Math.min(9, 2 + Math.sqrt(p.impressions) / 6).toFixed(1)}" class="mkp-dot${under ? " mkp-dot-under" : ""}"><title>${this.esc(
					`${p.page}: ${__("position {0}", [MKP.fmt_pos(p.position)])}, CTR ${MKP.fmt_pct(p.ctr)}, ${MKP.fmt_int(p.impressions)} ${__("impressions")}`
				)}</title></circle>`;
			})
			.join("");
		const site = d.curve ? d.curve.site_buckets : 0;
		const below = (d.below_curve || []).map((r) => [`<code>${this.esc(r.value)}</code>`, this.esc(MKP.fmt_pos(r.position)), this.esc(MKP.fmt_pct(r.ctr)), this.esc(MKP.fmt_pct(r.expected_ctr))]);
		return `<svg class="mkp-chart mkp-scatter" viewBox="-4 -4 ${w + 8} ${h + 8}" role="img" aria-label="${this.esc(
			__("Click rate against position for each page, over the expected curve")
		)}"><polyline points="${curve}" class="mkp-curve" fill="none"></polyline>${dots}</svg>
		<div class="mkp-axis"><span>${this.esc(__("position 1"))}</span><span>10</span><span>20</span></div>
		<p class="mkp-hint">${this.esc(
			__("The curve is the site's own median click rate at {0} of 20 positions and a published fallback at the rest. Highlighted dots earn under half what their position should: rewrite their titles and descriptions.", [site])
		)}</p>${below.length ? this.table([{ label: __("Page") }, { label: __("Pos."), num: true }, { label: __("CTR"), num: true }, { label: __("Expected"), num: true }], below) : ""}`;
	}

	near_html() {
		const d = this.tab_data || {};
		const rows = (d.near_page_one || []).map((r) => [this.esc(r.value), this.esc(MKP.fmt_pos(r.position)), this.esc(MKP.fmt_int(r.impressions)), this.esc(MKP.fmt_int(r.clicks_to_gain))]);
		const pages = (d.near_page_one_pages || []).map((r) => [`<code>${this.esc(r.value)}</code>`, this.esc(MKP.fmt_pos(r.position)), this.esc(MKP.fmt_int(r.impressions)), this.esc(MKP.fmt_int(r.clicks_to_gain))]);
		const head = (label) => [{ label }, { label: __("Pos."), num: true }, { label: __("Impr."), num: true }, { label: __("Clicks to gain"), num: true }];
		return `${this.table(head(__("Query")), rows, { empty: __("No query sits at positions 8 to 20 with enough impressions.") })}<h4 class="mkp-h4">${this.esc(__("Pages"))}</h4>${this.table(
			head(__("Page")),
			pages,
			{ empty: __("No page sits at positions 8 to 20 with enough impressions.") }
		)}<p class="mkp-hint">${this.esc(__("Clicks to gain: impressions times the expected click rate at position 3, minus clicks now. The full list is the Near Page One report."))}</p>`;
	}

	fading_html() {
		const rows = ((this.tab_data && this.tab_data.fading) || []).map((r) => [`<code>${this.esc(r.key)}</code>`, this.esc(MKP.fmt_int(r.clicks)), this.esc(MKP.fmt_int(r.baseline)), this.chip(MKP.fmt_pct(r.change, 0), "danger")]);
		return (
			this.table([{ label: __("Page") }, { label: __("Clicks"), num: true }, { label: __("Before"), num: true }, { label: __("Change") }], rows, {
				empty: __("No page lost a large share of its search clicks against the period before."),
			}) + `<p class="mkp-hint">${this.esc(__("Against the period before; the Fading Pages report also compares with last year."))}</p>`
		);
	}

	// R2 inline: each query with its change against the period before and last year, new and lost.
	query_changes_html() {
		const d = this.tab_data || {};
		const row = (q) => [
			this.esc(q.query) + (q.branded ? " " + this.chip(__("Branded"), "neutral") : "") + (q.status === "new" ? " " + this.chip(__("New"), "good") : q.status === "lost" ? " " + this.chip(__("Lost"), "danger") : ""),
			this.esc(MKP.fmt_int(q.clicks)),
			this.esc(q.change_prev === null || q.change_prev === undefined ? "–" : MKP.fmt_pct(q.change_prev, 0)),
			this.esc(q.change_last_year === null || q.change_last_year === undefined ? "–" : MKP.fmt_pct(q.change_last_year, 0)),
			this.esc(MKP.fmt_int(q.impressions)),
			this.esc(MKP.fmt_pct(q.ctr)),
			this.esc(MKP.fmt_pos(q.position)),
		];
		const head = [{ label: __("Query") }, { label: __("Clicks"), num: true }, { label: __("vs before"), num: true }, { label: __("vs last year"), num: true }, { label: __("Impr."), num: true }, { label: __("CTR"), num: true }, { label: __("Pos."), num: true }];
		const split = ((this.data.queries || {}).split) || {};
		const tile = (key, label) => `<div class="mkp-split"><span class="mkp-kpi-label">${this.esc(label)}</span><span class="mkp-kpi-value">${this.esc(MKP.fmt_int((split[key] || {}).clicks))}</span></div>`;
		return `<div class="mkp-splits">${tile("branded", __("Branded clicks"))}${tile("unbranded", __("Unbranded clicks"))}</div>${this.table(head, (d.queries || []).map(row))}${
			(d.lost_queries || []).length ? `<h4 class="mkp-h4">${this.esc(__("Lost queries"))}</h4>${this.table(head, d.lost_queries.map(row))}` : ""
		}<p class="mkp-hint">${this.esc(__("Branded: contains {0}. The full list is the Search Queries report.", [(d.branded_terms || []).join(", ")]))}</p>`;
	}

	// ---------------------------------------------------------------- the Inquiries and calls tab

	by_source_html() {
		const b = (this.tab_data && this.tab_data.by_source) || { weeks: [], series: {} };
		const names = Object.keys(b.series || {});
		if (!b.weeks.length) return this.empty(__("No Opportunities created in this range."));
		const stacked = MKP.stack(names.map((n) => b.series[n]));
		const top = stacked[stacked.length - 1];
		const max = Math.max(...top, 1);
		const w = 640;
		const h = 140;
		const bw = w / b.weeks.length;
		const tones = ["design", "service", "build", "rent", "neutral", "good", "warn"];
		const bars = names
			.map((name, k) =>
				b.weeks
					.map((week, i) => {
						const lower = k ? stacked[k - 1][i] : 0;
						const upper = stacked[k][i];
						if (upper === lower) return "";
						return `<rect x="${(i * bw + 1).toFixed(1)}" width="${Math.max(1, bw - 2).toFixed(1)}" y="${(h - (upper / max) * h).toFixed(1)}" height="${(((upper - lower) / max) * h).toFixed(1)}" class="mkp-area mkp-area-${tones[k % tones.length]}"><title>${this.esc(
							`${week}: ${name} ${upper - lower}`
						)}</title></rect>`;
					})
					.join("")
			)
			.join("");
		const legend = names.map((name, k) => `<span class="mkp-legend"><span class="mkp-swatch mkp-area-${tones[k % tones.length]}"></span>${this.esc(name)}</span>`).join("");
		return `<svg class="mkp-chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="${this.esc(__("Opportunities created per week, by source"))}">${bars}</svg><div class="mkp-legends">${legend}</div>`;
	}

	heatmap_html() {
		const m = (this.tab_data && this.tab_data.heatmap) || [];
		if (!m.length) return this.empty(__("No calls."));
		const max = Math.max(1, ...m.map((row) => Math.max(...row)));
		const days = [__("Mon"), __("Tue"), __("Wed"), __("Thu"), __("Fri"), __("Sat"), __("Sun")];
		const head = `<tr><th scope="col"></th>${Array.from({ length: 24 }, (_x, hour) => `<th scope="col" class="mkp-heat-h">${hour % 3 === 0 ? hour : ""}</th>`).join("")}</tr>`;
		const rows = m
			.map(
				(row, d) =>
					`<tr><th scope="row">${this.esc(days[d])}</th>${row
						.map((v, hour) => `<td class="mkp-heat mkp-heat-${MKP.heat_level(v, max)}" title="${this.esc(`${days[d]} ${hour}:00 · ${v}`)}">${v || ""}</td>`)
						.join("")}</tr>`
			)
			.join("");
		return `<div class="mkp-table-wrap"><table class="mkp-heatmap" aria-label="${this.esc(__("Incoming calls by weekday and hour"))}"><thead>${head}</thead><tbody>${rows}</tbody></table></div>`;
	}

	missed_html() {
		const m = (this.tab_data && this.tab_data.missed) || {};
		const head = `<div class="mkp-splits">
			<div class="mkp-split"><span class="mkp-kpi-label">${this.esc(__("Missed"))}</span><span class="mkp-kpi-value">${this.esc(MKP.fmt_int(m.count))}</span></div>
			<div class="mkp-split"><span class="mkp-kpi-label">${this.esc(__("First-time callers"))}</span><span class="mkp-kpi-value">${this.esc(MKP.fmt_int(m.first_time))}</span></div>
			<div class="mkp-split"><span class="mkp-kpi-label">${this.esc(__("Waiting on a callback"))}</span><span class="mkp-kpi-value">${this.esc(MKP.fmt_int(m.waiting))}</span></div>
		</div>`;
		const note = `<p class="mkp-hint">${this.esc(__("Unanswered, and nobody called the number back within the window on Marketing Settings. The full list is the Missed Calls report."))}</p>`;
		if (!m.rows) return head + note;
		const rows = m.rows.map((r) => [
			this.esc(frappe.datetime.str_to_user(r.at)),
			`<a href="/desk/call-log/${encodeURIComponent(r.name)}">${this.esc(r.caller || r.number || r.name)}</a>`,
			r.first_time ? this.chip(__("First time"), "warn") : "",
			this.chip(r.state === "waiting" ? __("Waiting") : __("Missed"), r.state === "waiting" ? "neutral" : "danger"),
		]);
		return head + this.table([{ label: __("When") }, { label: __("Caller") }, { label: "" }, { label: __("State") }], rows, { empty: __("No missed calls in this range.") }) + note;
	}

	reply_html() {
		const r = (this.tab_data && this.tab_data.reply) || {};
		const hours = r.median_hours;
		const text = hours === null || hours === undefined ? "–" : hours < 48 ? __("{0} h", [hours.toFixed(1)]) : __("{0} days", [(hours / 24).toFixed(1)]);
		return `<div class="mkp-split"><span class="mkp-kpi-label">${this.esc(__("Median time to a first email"))}</span><span class="mkp-kpi-value">${this.esc(text)}</span><span class="mkp-hint">${this.esc(
			__("{0} of {1} Opportunities had a sent email.", [MKP.fmt_int(r.replied), MKP.fmt_int(r.total)])
		)}</span></div><p class="mkp-hint">${this.esc(__("From the Opportunity's creation to the first email sent from it. Calls and texts are not counted."))}</p>`;
	}
}
