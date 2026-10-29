// Marketing Performance (v1.756.0). Nik, 2026-10-29: Google Analytics 4 and Search Console merged into
// one marketing view, with inbound calls, Google Business Profile, ad spend and social page stats beside
// them, on one page that replaces the GA4 Dashboard and the Marketing Dashboard widgets.
//
//   /desk/marketing-performance                                   the last 28 days
//   /desk/marketing-performance?from=2026-09-01&to=2026-09-30     a range
//   ...&compare=1                                                 beside the range before it
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
		return { from, to, compare };
	},

	// The query for a state, "" for the default. Order is fixed so equal states write equal addresses.
	query_for(state) {
		const parts = [];
		if (state.from) parts.push("from=" + encodeURIComponent(state.from));
		if (state.to) parts.push("to=" + encodeURIComponent(state.to));
		if (state.compare) parts.push("compare=1");
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
	line_segments(values, w, h) {
		const n = values.length;
		const nums = values.filter((v) => v !== null && v !== undefined).map(Number);
		const max = nums.length ? Math.max(...nums, 1) : 1;
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
		this.state = { from: null, to: null, compare: false };
		this.data = null;
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
			</form>
			<div class="mkp-range-note" aria-live="polite"></div>
			<div class="mkp-body"></div>
		`);
		this.$controls = this.$root.find(".mkp-controls");
		this.$body = this.$root.find(".mkp-body");
		this.ready = mkp_kit().then((sf) => {
			this.sf = sf;
		});
		page.set_secondary_action(__("Refresh"), () => this.load(), "refresh");
		if ((frappe.user_roles || []).includes("System Manager")) {
			page.add_menu_item(__("Backfill GA4 and Search Console history"), () => this.backfill());
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
			const changed = !this.data || !MKP.same(state, this.state);
			this.state = state;
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
		for (const key of ["from", "to", "compare"]) {
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

	bind() {
		this.$controls.on("submit", (e) => {
			e.preventDefault();
			const form = this.$controls.get(0);
			const from = MKP.valid_day(form.from.value);
			const to = MKP.valid_day(form.to.value);
			if (!from || !to) {
				frappe.show_alert({ message: __("Pick both a From and a To date."), indicator: "orange" });
				return;
			}
			this.go(MKP.state_from({ from, to, compare: this.state.compare ? "1" : "" }));
		});
		this.$controls.on("change", 'input[name="compare"]', (e) => {
			this.go(Object.assign({}, this.state, { compare: e.currentTarget.checked }));
		});
		this.$controls.on("click", ".mkp-preset", (e) => {
			const days = Number($(e.currentTarget).attr("data-days"));
			const range = MKP.preset_range(days, this.today());
			const state = days === 28 ? { from: null, to: null } : range;
			this.go(Object.assign({}, state, { compare: this.state.compare }));
		});
	}

	sync_controls() {
		const today = this.today();
		const shown = this.state.from && this.state.to ? this.state : MKP.preset_range(28, today);
		const form = this.$controls.get(0);
		form.from.value = shown.from;
		form.to.value = shown.to;
		form.compare.checked = !!this.state.compare;
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

	async load() {
		await this.ready;
		const seq = ++this.seq;
		this.sf.state.render(this.$body.get(0), "loading", { title: __("Loading marketing performance…") });
		try {
			const data = await this.sf.call(
				MKP_API,
				{ from_date: this.state.from || "", to_date: this.state.to || "", compare: this.state.compare ? 1 : 0 },
				{ quiet: true }
			);
			if (seq !== this.seq) return;
			this.data = data;
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
				"Read Search Console's 16 months and the same span of GA4 into the daily tables? It runs in the background, a month at a time, and is safe to run again."
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
		this.$body.html(
			[
				this.kpis_html(),
				this.section(__("Trend"), this.trend_html(), "mkp-wide"),
				this.section(__("Fix first"), this.flags_html(), "mkp-wide"),
				this.section(__("Landing pages"), this.pages_html(), "mkp-wide"),
				this.section(__("Search queries"), this.queries_html()),
				this.section(__("Channels"), this.channels_html()),
				this.section(__("Funnel"), this.funnel_html()),
				this.section(__("Inbound calls"), this.calls_html()),
				this.section(__("Unsourced inquiries"), this.unsourced_html()),
				this.section(__("Ad spend"), this.spend_html()),
				this.section(__("Google Business Profile"), this.gbp_html()),
				this.section(__("Social pages"), this.social_html()),
				this.section(__("Source health"), this.health_html(), "mkp-wide"),
			].join("")
		);
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
				const { segments, max } = MKP.line_segments(values, w, h);
				const total = MKP.sum(values);
				const lines = segments
					.map((points) =>
						points.length === 1
							? `<circle cx="${points[0].split(",")[0]}" cy="${points[0].split(",")[1]}" r="2" class="mkp-line mkp-line-${tone}"></circle>`
							: `<polyline points="${points.join(" ")}" class="mkp-line mkp-line-${tone}" fill="none"></polyline>`
					)
					.join("");
				const summary = __("{0}: {1} in total, at most {2} a day", [label, MKP.fmt_int(total), MKP.fmt_int(max)]);
				return `<div class="mkp-trend-row">
					<div class="mkp-trend-label"><span>${this.esc(label)}</span><strong>${this.esc(MKP.fmt_int(total))}</strong></div>
					<svg class="mkp-spark" viewBox="0 -2 ${w} ${h + 4}" preserveAspectRatio="none" role="img" aria-label="${this.esc(summary)}">${lines}</svg>
				</div>`;
			})
			.join("");
		const axis = dates.length
			? `<div class="mkp-axis"><span>${this.esc(dates[0])}</span><span>${this.esc(dates[Math.floor((dates.length - 1) / 2)])}</span><span>${this.esc(
					dates[dates.length - 1]
			  )}</span></div>`
			: "";
		return `<div class="mkp-trend">${rows}${axis}</div><p class="mkp-hint">${this.esc(
			__("One time axis, each line on its own scale. A gap is a day a source has no data for, not a zero.")
		)}</p>`;
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
			__("Inquiries are Opportunities created plus first-time callers. Won counts deals closed in the range, historical ones left out.")
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
		const head = `<p>${this.esc(
			__("{0} of {1} Opportunities created in this range have no source, or the recorded Unknown (pre-Aug 2026).", [MKP.fmt_int(u.count), MKP.fmt_int(u.total)])
		)}</p>`;
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
					? __("No ad platform is connected: Marketing Settings is switched off, and Google Ads, Meta and LinkedIn each need connecting on Marketing Connections. Spend appears here the night after.")
					: s.connected && s.connected.length
					? __("Connected ({0}), but no spend is stored for this range.", [s.connected.join(", ")])
					: __("Marketing Settings is on, but no ad platform is connected yet (Marketing Connections).")
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
					: __("Not connected. Google has to approve the project's Business Profile API access first; then connect it on Marketing Connections and turn it on in Marketing Settings.")
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
					: __("Off. Turn on Social Page Stats in Marketing Settings once Facebook, Instagram, LinkedIn or YouTube is connected.")
			);
		}
		return this.table(
			[{ label: __("Account") }, { label: __("Network") }, { label: __("Followers"), num: true }, { label: __("Reach"), num: true }, { label: __("Views"), num: true }],
			s.accounts.map((a) => [this.esc(a.name), this.esc(a.network), this.esc(MKP.fmt_int(a.followers)), this.esc(MKP.fmt_int(a.reach)), this.esc(MKP.fmt_int(a.views))])
		);
	}

	health_html() {
		const h = this.data.health || {};
		const tone = { ok: "good", stale: "warn", empty: "warn", off: "neutral" };
		const label = { ok: __("Landing"), stale: __("Stale"), empty: __("Empty"), off: __("Off") };
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
}
