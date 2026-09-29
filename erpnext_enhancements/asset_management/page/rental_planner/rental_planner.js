// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Rental Planner — the fleet timeline and the guided "new rental" flow (v1.563.0).
//
//   /desk/rental-planner                     the board: one row per fountain, one per
//                                            accessory pool, across two weeks to a quarter
//   /desk/rental-planner/new/dates           1. when and where
//   /desk/rental-planner/new/fountains       2. which fountains and accessories (live availability)
//   /desk/rental-planner/new/customer        3. customer, contact, project
//   /desk/rental-planner/new/review          4. fees, then place the hold (or book it confirmed)
//
// Every screen is a route, so Back and Forward step through the flow (Nik's rule: never break
// Back/Forward). The page moves only with frappe.set_route; frappe's router owns history, and
// Back/Forward re-render the route through on_page_show -> handle_route. There is no popstate
// listener and no history write of the page's own.
//
// The draft lives in memory and in sessionStorage, so leaving the page, Back past step 1 or a
// reload loses nothing; an address for a step whose earlier steps are not done yet is corrected
// in place (replace, not a new entry) to the first unfinished step.
//
// The timeline is drawn here rather than on the Gantt widget: DHTMLX Gantt Standard draws one
// bar per row, and a fleet timeline is several bookings on each fountain's row (split tasks and
// the resource view are PRO-only). The server is still the authority on availability — the
// booking's own save re-checks with the fountains row-locked — so nothing here enforces anything.

const RP = {
	route: "rental-planner",
	steps: ["dates", "fountains", "customer", "review"],
	draft_key: "ee_rental_planner_draft",
	days_key: "ee_rental_planner_days",
	api: "erpnext_enhancements.asset_management.rental_planner",
	availability: "erpnext_enhancements.asset_management.rental_availability",
	day_ms: 24 * 60 * 60 * 1000,
	ranges: [14, 31, 92],
};

const RP_STEP_TITLES = {
	dates: __("When and where"),
	fountains: __("Fountains and accessories"),
	customer: __("Customer and project"),
	review: __("Review and hold"),
};

const RP_FEES = ["delivery_setup_fee", "pickup_removal_fee", "other_fee", "security_deposit"];

// A Date as "YYYY-MM-DD". Not frappe.datetime.obj_to_str, which returns a full ISO timestamp.
const rp_ymd = (date) => moment(date).format("YYYY-MM-DD");

frappe.pages[RP.route].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Rental Planner"),
		single_column: true,
	});
	wrapper.rental_planner = new RentalPlanner(page);
};

frappe.pages[RP.route].on_page_show = function (wrapper) {
	if (wrapper.rental_planner) {
		wrapper.rental_planner.handle_route();
	}
};

const RP_STYLE = `
.rp-wrap{padding-bottom:64px;color:var(--text-color);}
.rp-toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:4px 0 12px;}
.rp-toolbar .rp-range-label{font-weight:600;margin:0 6px;}
.rp-legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--text-muted);margin:0 0 10px;}
.rp-legend span{display:inline-flex;align-items:center;gap:5px;}
.rp-swatch{display:inline-block;width:18px;height:10px;border-radius:3px;}
.rp-scroll{overflow-x:auto;border:1px solid var(--border-color);border-radius:8px;background:var(--card-bg);}
.rp-row{display:flex;min-height:38px;border-bottom:1px solid var(--border-color);}
.rp-row:last-child{border-bottom:none;}
.rp-label{flex:0 0 190px;position:sticky;left:0;z-index:2;background:var(--card-bg);padding:6px 10px;border-right:1px solid var(--border-color);font-size:13px;line-height:1.25;}
.rp-label small{display:block;color:var(--text-muted);font-size:11px;}
.rp-track{position:relative;flex:0 0 auto;cursor:copy;
  background-image:linear-gradient(to right,var(--border-color) 1px,transparent 1px);
  background-size:var(--rp-day) 100%;}
.rp-head .rp-track,.rp-pool .rp-track,.rp-section .rp-track{cursor:default;}
.rp-head{position:sticky;top:0;z-index:3;background:var(--card-bg);min-height:42px;}
.rp-day{position:absolute;top:0;bottom:0;font-size:11px;text-align:center;color:var(--text-muted);padding-top:4px;line-height:1.2;overflow:hidden;}
.rp-day b{display:block;color:var(--text-color);font-size:12px;}
.rp-day.rp-today b{color:var(--primary,#2490ef);}
.rp-today-line{position:absolute;top:0;bottom:0;width:2px;background:var(--primary,#2490ef);opacity:.55;z-index:1;pointer-events:none;}
.rp-bar{position:absolute;top:7px;height:24px;border-radius:5px;font-size:11px;line-height:24px;padding:0 6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#fff;cursor:pointer;z-index:2;box-sizing:border-box;}
.rp-bar:hover{filter:brightness(1.08);box-shadow:0 1px 4px rgba(0,0,0,.25);}
.rp-bar.rp-rental{background:#2563eb;}
.rp-bar.rp-rental.rp-held{background:repeating-linear-gradient(45deg,#93c5fd,#93c5fd 6px,#bfdbfe 6px,#bfdbfe 12px);color:#1e3a8a;border:1px dashed #2563eb;}
.rp-bar.rp-prep,.rp-bar.rp-turnaround{background:#94a3b8;color:#0f172a;top:12px;height:14px;line-height:14px;font-size:10px;}
.rp-bar.rp-booking{background:#d97706;}
.rp-bar.rp-out_of_service{background:repeating-linear-gradient(45deg,#dc2626,#dc2626 6px,#b91c1c 6px,#b91c1c 12px);}
.rp-section .rp-label{font-weight:600;background:var(--subtle-fg,var(--control-bg));}
.rp-section{min-height:28px;background:var(--subtle-fg,var(--control-bg));}
.rp-cell{position:absolute;top:6px;bottom:6px;border-radius:4px;font-size:11px;text-align:center;display:flex;align-items:center;justify-content:center;}
.rp-cell.rp-low{background:rgba(37,99,235,.12);}
.rp-cell.rp-mid{background:rgba(217,119,6,.22);}
.rp-cell.rp-full{background:rgba(220,38,38,.28);font-weight:600;}
.rp-empty{padding:28px;text-align:center;color:var(--text-muted);}
.rp-steps{display:flex;gap:6px;flex-wrap:wrap;margin:4px 0 14px;}
.rp-step{padding:7px 12px;border-radius:16px;border:1px solid var(--border-color);background:var(--card-bg);font-size:13px;color:var(--text-muted);}
.rp-step.rp-done{color:var(--text-color);cursor:pointer;}
.rp-step.rp-active{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.rp-card{max-width:860px;background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:16px;margin-bottom:12px;}
.rp-card h4{margin:0 0 10px;font-size:15px;}
.rp-nav{max-width:860px;display:flex;justify-content:space-between;gap:8px;}
.rp-pick{display:flex;align-items:center;gap:10px;padding:9px 6px;border-bottom:1px solid var(--border-color);}
.rp-pick:last-child{border-bottom:none;}
.rp-pick .rp-pick-main{flex:1;min-width:0;}
.rp-pick .rp-why{font-size:12px;color:var(--text-muted);}
.rp-pick input[type=number]{width:96px;}
.rp-pick.rp-taken{opacity:.6;}
.rp-pill{display:inline-block;font-size:11px;border-radius:10px;padding:1px 8px;margin-left:6px;}
.rp-pill.rp-free{background:#dcfce7;color:#166534;}
.rp-pill.rp-no{background:#fee2e2;color:#991b1b;}
.rp-summary dt{color:var(--text-muted);font-weight:normal;font-size:12px;margin-top:8px;}
.rp-summary dd{margin:0;}
.rp-total{font-size:18px;font-weight:600;margin-top:10px;}
@media (max-width:640px){.rp-label{flex-basis:120px;}}
`;

class RentalPlanner {
	constructor(page) {
		this.page = page;
		if (!document.getElementById("rp-style")) {
			$("<style id='rp-style'>").text(RP_STYLE).appendTo(document.head);
		}
		this.$body = $('<div class="rp-wrap"></div>').appendTo(page.main);
		this.draft = this.load_draft();
		this.board_start = frappe.datetime.get_today();
		this.board_days = this.load_days();
		this.pushed = [];
		this.created = null;
		this.availability = null;
	}

	// ------------------------------------------------------------------ routing

	route_target() {
		const route = frappe.get_route() || [];
		if (route[0] !== RP.route) return null;
		if (route[1] !== "new") return { view: "board" };
		return { view: "wizard", step: RP.steps.includes(route[2]) ? route[2] : "" };
	}

	handle_route() {
		const target = this.route_target();
		if (!target) return;
		// Steps the flow pushed that the browser has since gone back past are no longer behind us.
		while (this.pushed.length && this.pushed[this.pushed.length - 1].step !== target.step) {
			this.pushed.pop();
		}
		if (target.view === "board") {
			this.show_board();
			return;
		}
		if (this.created && target.step === "review") {
			this.show_created();
			return;
		}
		this.created = null;
		const allowed = this.first_unfinished_step();
		if (!target.step || RP.steps.indexOf(target.step) > RP.steps.indexOf(allowed)) {
			// An address for a step whose earlier steps are not done: correct this entry, add none.
			this.go([RP.route, "new", target.step ? allowed : "dates"], true);
			return;
		}
		this.show_step(target.step);
	}

	go(route, replace) {
		if ((frappe.get_route() || [])[0] !== RP.route) return;
		if (replace) frappe.route_flags.replace_route = true;
		frappe.set_route(route);
		// Read by the router's push, which has already run; left set it would turn the next
		// Next into a replace.
		delete frappe.route_flags.replace_route;
	}

	current_screen() {
		const route = frappe.get_route() || [];
		return route[1] === "new" ? route[2] || "dates" : "board";
	}

	// Each push records the screen it was made from, so the flow's Back button knows whether
	// the entry behind is the previous step.
	go_step(step) {
		this.pushed.push({ step, from: this.current_screen() });
		this.go([RP.route, "new", step]);
	}

	// The flow's own Back button: step back through history when the previous step is what is
	// behind us (it is whenever Next led here), so Next, Back, Next leaves no stack to replay.
	step_back(step) {
		const index = RP.steps.indexOf(step);
		const previous = index > 0 ? RP.steps[index - 1] : "board";
		const top = this.pushed[this.pushed.length - 1];
		if (top && top.step === step && top.from === previous) {
			this.pushed.pop();
			window.history.back();
			return;
		}
		if (index > 0) {
			this.go([RP.route, "new", RP.steps[index - 1]]);
		} else {
			this.go([RP.route]);
		}
	}

	first_unfinished_step() {
		const d = this.draft;
		if (!d.delivery_datetime || !d.takedown_datetime || d.takedown_datetime <= d.delivery_datetime) {
			return "dates";
		}
		if (!(d.fountains || []).length && !(d.accessories || []).length) return "fountains";
		if (!d.customer) return "customer";
		return "review";
	}

	// ------------------------------------------------------------------ draft

	blank_draft() {
		return { fountains: [], accessories: [], create_project: 1 };
	}

	load_draft() {
		try {
			const raw = window.sessionStorage.getItem(RP.draft_key);
			if (raw) return Object.assign(this.blank_draft(), JSON.parse(raw));
		} catch (e) {
			// Storage blocked or corrupt: start clean.
		}
		return this.blank_draft();
	}

	save_draft() {
		try {
			window.sessionStorage.setItem(RP.draft_key, JSON.stringify(this.draft));
		} catch (e) {
			// The draft still lives in memory for this page.
		}
	}

	reset_draft(prefill) {
		this.draft = Object.assign(this.blank_draft(), prefill || {});
		this.availability = null;
		this.save_draft();
	}

	draft_started() {
		const d = this.draft;
		return Boolean(d.delivery_datetime || d.customer || (d.fountains || []).length || (d.accessories || []).length);
	}

	start_new(prefill) {
		const begin = (fresh) => {
			if (fresh) this.reset_draft(prefill);
			this.created = null;
			this.go_step(fresh ? "dates" : this.first_unfinished_step());
		};
		if (this.draft_started()) {
			frappe.confirm(
				__("You have a rental in progress. Continue it? Choose No to start over."),
				() => begin(false),
				() => begin(true)
			);
		} else {
			begin(true);
		}
	}

	// ------------------------------------------------------------------ board

	load_days() {
		try {
			const days = parseInt(window.localStorage.getItem(RP.days_key), 10);
			if (RP.ranges.includes(days)) return days;
		} catch (e) {
			// Per-viewer convenience only.
		}
		return 31;
	}

	show_board() {
		this.page.set_title(__("Rental Planner"));
		this.page.clear_actions();
		this.page.set_primary_action(__("New Rental"), () => this.start_new(), "add");
		this.page.set_secondary_action(__("Rental Bookings"), () => frappe.set_route("List", "Rental Booking"));
		this.$body.empty();

		const $bar = $('<div class="rp-toolbar"></div>').appendTo(this.$body);
		const shift = (sign) => {
			const step = this.board_days === 92 ? 30 : this.board_days === 31 ? 14 : 7;
			this.board_start = frappe.datetime.add_days(this.board_start, sign * step);
			this.load_timeline();
		};
		$('<button class="btn btn-default btn-sm">').text("‹").attr("title", __("Earlier")).on("click", () => shift(-1)).appendTo($bar);
		$('<button class="btn btn-default btn-sm">').text(__("Today")).on("click", () => {
			this.board_start = frappe.datetime.get_today();
			this.load_timeline();
		}).appendTo($bar);
		$('<button class="btn btn-default btn-sm">').text("›").attr("title", __("Later")).on("click", () => shift(1)).appendTo($bar);
		this.$range_label = $('<span class="rp-range-label"></span>').appendTo($bar);
		const $range = $('<select class="form-control input-sm" style="width:auto"></select>').appendTo($bar);
		[[14, __("2 weeks")], [31, __("Month")], [92, __("Quarter")]].forEach(([days, label]) =>
			$("<option>").val(days).text(label).prop("selected", days === this.board_days).appendTo($range)
		);
		$range.on("change", () => {
			this.board_days = parseInt($range.val(), 10);
			try {
				window.localStorage.setItem(RP.days_key, String(this.board_days));
			} catch (e) {
				// Remembering the range is a convenience.
			}
			this.load_timeline();
		});

		const $legend = $('<div class="rp-legend"></div>').appendTo(this.$body);
		[
			["#2563eb", __("Confirmed rental")],
			["repeating-linear-gradient(45deg,#93c5fd,#93c5fd 4px,#bfdbfe 4px,#bfdbfe 8px)", __("Held (tentative)")],
			["#94a3b8", __("Prep / cleaning")],
			["#d97706", __("Other booking")],
			["#dc2626", __("Out of service")],
		].forEach(([color, label]) => {
			const $s = $("<span>").appendTo($legend);
			$('<i class="rp-swatch">').css("background", color).appendTo($s);
			$s.append(document.createTextNode(label));
		});
		$('<span>').text(__("Click an empty day on a fountain to start a rental there.")).appendTo($legend);

		this.$timeline = $('<div class="rp-scroll"></div>').appendTo(this.$body);
		this.load_timeline();
	}

	load_timeline() {
		const start = this.board_start;
		const days = this.board_days;
		const end = frappe.datetime.add_days(start, days - 1);
		this.$range_label.text(`${frappe.datetime.str_to_user(start)} – ${frappe.datetime.str_to_user(end)}`);
		this.$timeline.empty().append($('<div class="rp-empty">').text(__("Loading the fleet...")));
		const token = (this._timeline_token = {});
		frappe
			.call({ method: `${RP.api}.get_timeline`, args: { start, days } })
			.then(({ message }) => {
				// A later request (the person paged again) owns the board now.
				if (token !== this._timeline_token || !message) return;
				this.render_timeline(message);
			});
	}

	day_width(days) {
		return days <= 14 ? 72 : days <= 31 ? 38 : 16;
	}

	render_timeline(data) {
		const $t = this.$timeline.empty();
		const start = frappe.datetime.str_to_obj(data.start).getTime();
		const days = data.days;
		const dw = this.day_width(days);
		const width = dw * days;
		const px = (value) => ((frappe.datetime.str_to_obj(value).getTime() - start) / RP.day_ms) * dw;
		const track = () => $('<div class="rp-track"></div>').css({ width: width + "px", "--rp-day": dw + "px" });

		if (!data.fountains.length && !data.pools.length) {
			$t.append(
				$('<div class="rp-empty">').text(
					__("No fountains are marked Available for Event Rental yet. Tick it on each fleet Asset.")
				)
			);
			return;
		}

		const today = frappe.datetime.get_today();
		const today_index = frappe.datetime.get_day_diff(today, rp_ymd(new Date(start)));
		const today_line = ($track) => {
			if (today_index >= 0 && today_index < days) {
				$('<div class="rp-today-line"></div>').css("left", today_index * dw + "px").appendTo($track);
			}
		};

		// Header: one cell per day.
		const $head = $('<div class="rp-row rp-head"></div>').appendTo($t);
		$('<div class="rp-label"></div>').text(__("Fountain")).appendTo($head);
		const $days = track().appendTo($head);
		for (let i = 0; i < days; i++) {
			// moment's calendar-day add, not + 24h: across a DST change that lands on the wrong date.
			const date = moment(start).add(i, "days").toDate();
			const $d = $('<div class="rp-day"></div>').css({ left: i * dw + "px", width: dw + "px" });
			$("<b>").text(date.getDate()).appendTo($d);
			if (dw >= 30) $d.append(document.createTextNode(date.toLocaleDateString(undefined, { weekday: "short" })));
			if (i === today_index) $d.addClass("rp-today");
			$d.attr("title", frappe.datetime.str_to_user(rp_ymd(date)));
			$d.appendTo($days);
		}

		// Fountains.
		data.fountains.forEach((f) => {
			const $row = $('<div class="rp-row"></div>').appendTo($t);
			const $label = $('<div class="rp-label"></div>').appendTo($row);
			$label.append(document.createTextNode(f.asset_name || f.asset));
			$("<small>").text(f.item_code || f.asset).appendTo($label);
			const $track = track().appendTo($row);
			today_line($track);
			$track.attr("title", __("Click an empty day to start a rental of {0}", [f.asset_name || f.asset]));
			$track.on("click", (event) => {
				if (event.target !== event.currentTarget) return;
				const offset = event.pageX - $track.offset().left;
				const day = Math.max(0, Math.min(days - 1, Math.floor(offset / dw)));
				this.start_from_cell(f, rp_ymd(moment(start).add(day, "days").toDate()));
			});
			f.bars.forEach((bar) => {
				const left = Math.max(0, px(bar.from));
				const right = Math.min(width, px(bar.to));
				if (right <= left) return;
				const $bar = $('<div class="rp-bar"></div>')
					.addClass(`rp-${bar.kind}`)
					.toggleClass("rp-held", Boolean(bar.held))
					.css({ left: left + "px", width: Math.max(right - left, 3) + "px" })
					.appendTo($track);
				const text = bar.kind === "prep" ? __("Prep") : bar.kind === "turnaround" ? __("Cleaning") : bar.label;
				if (bar.kind !== "prep" && bar.kind !== "turnaround") $bar.text(text);
				const until = bar.open_ended ? __("until further notice") : frappe.datetime.str_to_user(bar.to);
				$bar.attr(
					"title",
					[
						text,
						bar.held ? __("Held (tentative)") : bar.status,
						`${frappe.datetime.str_to_user(bar.from)} – ${until}`,
						bar.name,
					]
						.filter(Boolean)
						.join("\n")
				);
				$bar.on("click", (event) => {
					event.stopPropagation();
					frappe.set_route("Form", bar.doctype, bar.name);
				});
			});
		});

		// Accessory pools: units out per day, against what can be booked.
		if (data.pools.length) {
			const $section = $('<div class="rp-row rp-section"></div>').appendTo($t);
			$('<div class="rp-label"></div>').text(__("Accessories (out / bookable)")).appendTo($section);
			track().appendTo($section);
		}
		data.pools.forEach((pool) => {
			const $row = $('<div class="rp-row rp-pool"></div>').appendTo($t);
			const $label = $('<div class="rp-label"></div>').appendTo($row);
			$label.append(document.createTextNode(pool.label));
			$("<small>").text(__("{0} bookable", [pool.capacity])).appendTo($label);
			const $track = track().appendTo($row);
			pool.used.forEach((used, i) => {
				if (!used) return;
				const ratio = pool.capacity ? used / pool.capacity : 1;
				$('<div class="rp-cell"></div>')
					.addClass(ratio >= 1 ? "rp-full" : ratio >= 0.6 ? "rp-mid" : "rp-low")
					.css({ left: i * dw + 1 + "px", width: dw - 2 + "px" })
					.text(dw >= 30 ? `${used}/${pool.capacity}` : used)
					.attr("title", __("{0} of {1} out", [used, pool.capacity]))
					.appendTo($track);
			});
		});
	}

	start_from_cell(fountain, date) {
		this.start_new({
			delivery_datetime: `${date} 09:00:00`,
			takedown_datetime: `${frappe.datetime.add_days(date, 1)} 17:00:00`,
			fountains: [{ asset: fountain.asset, asset_name: fountain.asset_name, rate: 0 }],
		});
	}

	// ------------------------------------------------------------------ wizard frame

	show_step(step) {
		this.page.set_title(__("New Rental"));
		this.page.clear_actions();
		this.page.set_secondary_action(__("Planner"), () => this.go([RP.route]));
		this.$body.empty();

		const $steps = $('<div class="rp-steps"></div>').appendTo(this.$body);
		const current = RP.steps.indexOf(step);
		const reachable = RP.steps.indexOf(this.first_unfinished_step());
		RP.steps.forEach((key, i) => {
			const $s = $('<div class="rp-step"></div>').text(`${i + 1}. ${RP_STEP_TITLES[key]}`).appendTo($steps);
			if (i === current) $s.addClass("rp-active");
			else if (i <= reachable) $s.addClass("rp-done").on("click", () => this.go_step(key));
		});

		this.$card = $('<div class="rp-card"></div>').appendTo(this.$body);
		const $nav = $('<div class="rp-nav"></div>').appendTo(this.$body);
		$('<button class="btn btn-default"></button>')
			.text(current === 0 ? __("Back to Planner") : __("Back"))
			.on("click", () => this.leave_step(step, () => this.step_back(step)))
			.appendTo($nav);
		this.$next = $('<div></div>').appendTo($nav);

		this[`render_${step}`]();
	}

	// Keep what was typed on this step before moving, whichever way.
	leave_step(step, then) {
		if (this.fg && this.fg_step === step) {
			Object.assign(this.draft, this.fg.get_values(true) || {});
			this.save_draft();
		}
		then();
	}

	next_button(label, step, on_click) {
		this.$next.empty();
		$('<button class="btn btn-primary"></button>').text(label).on("click", on_click).appendTo(this.$next);
	}

	make_fields(step, fields) {
		this.fg_step = step;
		this.fg = new frappe.ui.FieldGroup({ fields, body: this.$card.get(0) });
		this.fg.make();
		this.fg.set_values(
			Object.fromEntries(fields.filter((f) => f.fieldname && this.draft[f.fieldname] != null).map((f) => [f.fieldname, this.draft[f.fieldname]]))
		);
		return this.fg;
	}

	// ------------------------------------------------------------------ 1. dates

	render_dates() {
		this.$card.append($("<h4>").text(RP_STEP_TITLES.dates));
		this.make_fields("dates", [
			{ fieldname: "event_name", fieldtype: "Data", label: __("Event Name") },
			{ fieldname: "venue_address", fieldtype: "Link", options: "Address", label: __("Venue Address") },
			{ fieldtype: "Section Break", label: __("Schedule") },
			{ fieldname: "delivery_datetime", fieldtype: "Datetime", label: __("Delivery"), reqd: 1 },
			{ fieldname: "setup_datetime", fieldtype: "Datetime", label: __("Setup") },
			{ fieldtype: "Column Break" },
			{ fieldname: "event_start_datetime", fieldtype: "Datetime", label: __("Event Start") },
			{ fieldname: "event_end_datetime", fieldtype: "Datetime", label: __("Event End") },
			{ fieldname: "takedown_datetime", fieldtype: "Datetime", label: __("Take-down"), reqd: 1 },
			{ fieldtype: "Section Break" },
			{ fieldname: "venue_notes", fieldtype: "Small Text", label: __("Venue Notes") },
			{ fieldname: "schedule_notes", fieldtype: "Small Text", label: __("Schedule Notes") },
		]);
		this.next_button(__("Next: Fountains"), "dates", () => {
			const values = this.fg.get_values();
			if (!values) return; // frappe has named the missing field
			if (values.takedown_datetime <= values.delivery_datetime) {
				frappe.msgprint(__("Take-down has to be after delivery."));
				return;
			}
			const changed =
				values.delivery_datetime !== this.draft.delivery_datetime ||
				values.takedown_datetime !== this.draft.takedown_datetime;
			Object.assign(this.draft, this.fg.get_values(true));
			if (changed) this.availability = null;
			this.save_draft();
			this.go_step("fountains");
		});
	}

	// ------------------------------------------------------------------ 2. fountains

	render_fountains() {
		this.fg = null;
		this.$card.append($("<h4>").text(RP_STEP_TITLES.fountains));
		const $when = $('<p class="text-muted"></p>').appendTo(this.$card);
		$when.text(
			__("{0} to {1}. Each fountain is also held for its own prep and cleaning time.", [
				frappe.datetime.str_to_user(this.draft.delivery_datetime),
				frappe.datetime.str_to_user(this.draft.takedown_datetime),
			])
		);

		const $package = $('<div class="rp-package" style="max-width:420px"></div>').appendTo(this.$card);
		const package_field = frappe.ui.form.make_control({
			parent: $package,
			df: {
				fieldname: "rental_package",
				fieldtype: "Link",
				options: "Rental Package",
				label: __("Start from a package (optional)"),
				get_query: () => ({ filters: { disabled: 0 } }),
				change: () => {
					const value = package_field.get_value();
					if (value && value !== this.draft.rental_package) this.apply_package(value);
				},
			},
			render_input: true,
		});
		package_field.set_value(this.draft.rental_package || "");

		this.$lists = $("<div></div>").appendTo(this.$card);
		this.$lists.append($('<div class="rp-empty">').text(__("Checking the fleet...")));
		this.next_button(__("Next: Customer"), "fountains", () => {
			if (!this.draft.fountains.length && !this.draft.accessories.length) {
				frappe.msgprint(__("Pick at least one fountain or accessory."));
				return;
			}
			const taken = this.draft.fountains.filter((row) => {
				const f = this.fountain_info(row.asset);
				return f && !f.available;
			});
			if (taken.length) {
				frappe.msgprint(
					__("These are taken for those dates: {0}", [taken.map((r) => r.asset_name || r.asset).join(", ")])
				);
				return;
			}
			this.save_draft();
			this.go_step("customer");
		});
		this.load_availability().then(() => this.render_pick_lists());
	}

	load_availability() {
		const key = `${this.draft.delivery_datetime}|${this.draft.takedown_datetime}`;
		if (this.availability && this.availability.key === key) return Promise.resolve();
		return frappe
			.call({
				method: `${RP.availability}.get_availability`,
				args: {
					delivery_datetime: this.draft.delivery_datetime,
					takedown_datetime: this.draft.takedown_datetime,
				},
			})
			.then(({ message }) => {
				this.availability = Object.assign({ key }, message || { fountains: [], pools: [] });
			});
	}

	fountain_info(asset) {
		return ((this.availability || {}).fountains || []).find((f) => f.asset === asset);
	}

	render_pick_lists() {
		if ((frappe.get_route() || [])[2] !== "fountains" || !this.$lists) return;
		const $l = this.$lists.empty();
		const data = this.availability || { fountains: [], pools: [] };

		$("<h5>").text(__("Fountains")).appendTo($l);
		if (!data.fountains.length) {
			$l.append($('<p class="text-muted">').text(__("No fountains are marked Available for Event Rental.")));
		}
		data.fountains.forEach((f) => {
			const picked = this.draft.fountains.find((r) => r.asset === f.asset);
			const $row = $('<label class="rp-pick"></label>').toggleClass("rp-taken", !f.available).appendTo($l);
			const $check = $('<input type="checkbox">').prop("checked", Boolean(picked)).prop("disabled", !f.available && !picked);
			$check.appendTo($row);
			const $main = $('<div class="rp-pick-main"></div>').appendTo($row);
			$main.append(document.createTextNode(f.asset_name || f.asset));
			$('<span class="rp-pill"></span>')
				.addClass(f.available ? "rp-free" : "rp-no")
				.text(f.available ? __("Free") : __("Taken"))
				.appendTo($main);
			$('<div class="rp-why"></div>')
				.text(f.available ? f.item_code || "" : f.conflicts.map((c) => c.label).join("; "))
				.appendTo($main);
			const $rate = $('<input type="number" min="0" step="0.01" class="form-control input-sm">')
				.attr("placeholder", __("Rate"))
				.val(picked ? picked.rate || "" : "")
				.prop("disabled", !picked)
				.appendTo($row);
			$check.on("change", () => {
				if ($check.prop("checked")) {
					this.draft.fountains.push({ asset: f.asset, asset_name: f.asset_name, rate: 0 });
				} else {
					this.draft.fountains = this.draft.fountains.filter((r) => r.asset !== f.asset);
				}
				$rate.prop("disabled", !$check.prop("checked"));
				this.save_draft();
			});
			$rate.on("change", () => {
				const row = this.draft.fountains.find((r) => r.asset === f.asset);
				if (row) row.rate = parseFloat($rate.val()) || 0;
				this.save_draft();
			});
		});

		$('<h5 style="margin-top:16px"></h5>').text(__("Accessories")).appendTo($l);
		if (!data.pools.length) {
			$l.append($('<p class="text-muted">').text(__("No accessory pools yet.")));
		}
		data.pools.forEach((p) => {
			const line = this.draft.accessories.find((r) => r.pool === p.pool);
			const $row = $('<div class="rp-pick"></div>').appendTo($l);
			const $main = $('<div class="rp-pick-main"></div>').appendTo($row);
			$main.append(document.createTextNode(p.label));
			$('<div class="rp-why"></div>')
				.text(__("{0} free for these dates ({1} bookable, {2} already out)", [p.available, p.capacity, p.booked]))
				.appendTo($main);
			const $qty = $('<input type="number" min="0" step="1" class="form-control input-sm">')
				.attr({ max: p.available, placeholder: __("Qty") })
				.val(line ? line.qty : "")
				.appendTo($row);
			const $rate = $('<input type="number" min="0" step="0.01" class="form-control input-sm">')
				.attr("placeholder", __("Rate each"))
				.val(line ? line.rate || "" : "")
				.appendTo($row);
			const update = () => {
				const qty = Math.max(0, parseInt($qty.val(), 10) || 0);
				this.draft.accessories = this.draft.accessories.filter((r) => r.pool !== p.pool);
				if (qty) {
					this.draft.accessories.push({ pool: p.pool, label: p.label, qty, rate: parseFloat($rate.val()) || 0 });
				}
				if (qty > p.available) {
					frappe.show_alert({ message: __("Only {0} {1} are free.", [p.available, p.label]), indicator: "orange" });
				}
				this.save_draft();
			};
			$qty.on("change", update);
			$rate.on("change", update);
		});
	}

	apply_package(name) {
		frappe
			.call({
				method: `${RP.availability}.plan_package`,
				args: {
					package: name,
					delivery_datetime: this.draft.delivery_datetime,
					takedown_datetime: this.draft.takedown_datetime,
					current_assets: this.draft.fountains.map((r) => r.asset),
				},
				freeze: true,
			})
			.then(({ message }) => {
				if (!message) return;
				this.draft.rental_package = name;
				message.fountains.forEach((f) => this.draft.fountains.push(f));
				message.accessories.forEach((a) => {
					this.draft.accessories = this.draft.accessories.filter((r) => r.pool !== a.pool);
					this.draft.accessories.push(a);
				});
				Object.entries(message.fees).forEach(([field, value]) => {
					if (value && !this.draft[field]) this.draft[field] = value;
				});
				this.save_draft();
				this.render_pick_lists();
				if (message.shortfalls.length) {
					frappe.msgprint({
						title: __("Package partly applied"),
						message: message.shortfalls.map(frappe.utils.escape_html).join("<br>"),
						indicator: "orange",
					});
				}
			});
	}

	// ------------------------------------------------------------------ 3. customer

	render_customer() {
		this.$card.append($("<h4>").text(RP_STEP_TITLES.customer));
		const fg = this.make_fields("customer", [
			{ fieldname: "customer", fieldtype: "Link", options: "Customer", label: __("Customer"), reqd: 1 },
			{ fieldname: "new_customer", fieldtype: "Button", label: __("New Customer") },
			{
				fieldname: "contact_person",
				fieldtype: "Link",
				options: "Contact",
				label: __("Contact"),
				get_query: () => ({
					query: "frappe.contacts.doctype.contact.contact.contact_query",
					filters: { link_doctype: "Customer", link_name: fg.get_value("customer") },
				}),
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "project",
				fieldtype: "Link",
				options: "Project",
				label: __("Existing Project"),
				get_query: () => ({ filters: { customer: fg.get_value("customer") } }),
			},
			{
				fieldname: "create_project",
				fieldtype: "Check",
				label: __("Create an Events project for this rental"),
				depends_on: "eval:!doc.project",
			},
			{
				fieldname: "opportunity",
				fieldtype: "Link",
				options: "Opportunity",
				label: __("Opportunity"),
				get_query: () => ({ filters: { party_name: fg.get_value("customer") } }),
			},
		]);
		fg.fields_dict.new_customer.$input.on("click", () => {
			frappe.ui.form.make_quick_entry("Customer", (doc) => fg.set_value("customer", doc.name));
		});
		this.next_button(__("Next: Review"), "customer", () => {
			const values = fg.get_values();
			if (!values) return;
			Object.assign(this.draft, fg.get_values(true));
			this.save_draft();
			this.go_step("review");
		});
	}

	// ------------------------------------------------------------------ 4. review

	render_review() {
		const d = this.draft;
		this.$card.append($("<h4>").text(RP_STEP_TITLES.review));
		const $dl = $('<dl class="rp-summary"></dl>').appendTo(this.$card);
		const item = (label, value, step) => {
			const $dt = $("<dt></dt>").text(label).appendTo($dl);
			if (step) {
				$('<a href="#" style="margin-left:8px;font-size:11px"></a>')
					.text(__("Edit"))
					.on("click", (e) => {
						e.preventDefault();
						this.leave_step("review", () => this.go_step(step));
					})
					.appendTo($dt);
			}
			$("<dd></dd>").text(value || "—").appendTo($dl);
		};
		const when = [
			`${__("Delivery")}: ${frappe.datetime.str_to_user(d.delivery_datetime)}`,
			d.event_start_datetime ? `${__("Event")}: ${frappe.datetime.str_to_user(d.event_start_datetime)}` : "",
			`${__("Take-down")}: ${frappe.datetime.str_to_user(d.takedown_datetime)}`,
		].filter(Boolean);
		item(__("Event"), [d.event_name, d.venue_address].filter(Boolean).join(" · "), "dates");
		item(__("Schedule"), when.join(" · "), "dates");
		item(__("Fountains"), d.fountains.map((f) => f.asset_name || f.asset).join(", "), "fountains");
		item(__("Accessories"), d.accessories.map((a) => `${a.qty} × ${a.label || a.pool}`).join(", "), "fountains");
		item(
			__("Customer"),
			[d.customer, d.project || (d.create_project ? __("new Events project") : __("no project"))].join(" · "),
			"customer"
		);

		const fg = this.make_fields("review", [
			{ fieldtype: "Section Break", label: __("Fees") },
			{ fieldname: "delivery_setup_fee", fieldtype: "Currency", label: __("Delivery & Setup Fee") },
			{ fieldname: "pickup_removal_fee", fieldtype: "Currency", label: __("Pickup & Removal Fee") },
			{ fieldtype: "Column Break" },
			{ fieldname: "other_fee", fieldtype: "Currency", label: __("Other Fees") },
			{
				fieldname: "security_deposit",
				fieldtype: "Currency",
				label: __("Security Deposit"),
				description: __("Held separately; not part of the total."),
			},
			{ fieldtype: "Section Break" },
			{ fieldname: "notes", fieldtype: "Small Text", label: __("Internal Notes") },
		]);
		const $total = $('<div class="rp-total"></div>').appendTo(this.$card);
		const show_total = () => {
			const v = fg.get_values(true) || {};
			const total =
				d.fountains.reduce((s, f) => s + (parseFloat(f.rate) || 0), 0) +
				d.accessories.reduce((s, a) => s + (parseFloat(a.rate) || 0) * (a.qty || 0), 0) +
				["delivery_setup_fee", "pickup_removal_fee", "other_fee"].reduce((s, k) => s + (parseFloat(v[k]) || 0), 0);
			$total.text(`${__("Rental total")}: ${format_currency(total)}`);
		};
		RP_FEES.forEach((f) => fg.fields_dict[f].$input.on("change input", show_total));
		show_total();

		this.$next.empty();
		const $buttons = $('<div style="display:flex;gap:8px;flex-wrap:wrap"></div>').appendTo(this.$next);
		$('<button class="btn btn-default"></button>')
			.text(__("Book as Confirmed"))
			.on("click", () => this.create("Confirmed"))
			.appendTo($buttons);
		$('<button class="btn btn-primary"></button>')
			.text(__("Place Hold"))
			.attr("title", __("Holds the fountains for 7 days while the customer decides."))
			.on("click", () => this.create("Tentative"))
			.appendTo($buttons);
	}

	create(status) {
		Object.assign(this.draft, this.fg.get_values(true) || {});
		this.save_draft();
		const payload = Object.assign({}, this.draft, { status });
		frappe
			.call({
				method: `${RP.api}.create_rental`,
				type: "POST",
				args: { data: payload },
				freeze: true,
				freeze_message: status === "Tentative" ? __("Placing the hold...") : __("Booking..."),
			})
			.then(({ message }) => {
				if (!message) return;
				this.created = Object.assign({ status }, message);
				this.reset_draft();
				this.pushed = [];
				if ((frappe.get_route() || [])[2] === "review") this.show_created();
			});
	}

	show_created() {
		const c = this.created;
		this.page.set_title(__("Rental Booked"));
		this.page.clear_actions();
		this.$body.empty();
		const $card = $('<div class="rp-card"></div>').appendTo(this.$body);
		$("<h4>")
			.text(c.status === "Tentative" ? __("{0} is on hold", [c.name]) : __("{0} is confirmed", [c.name]))
			.appendTo($card);
		$('<p class="text-muted"></p>')
			.text(
				c.status === "Tentative"
					? __("The fountains are held for 7 days. Confirm the booking to keep them.")
					: __("The fountains are booked and their calendars are firm.")
			)
			.appendTo($card);
		const $buttons = $('<div style="display:flex;gap:8px;flex-wrap:wrap"></div>').appendTo($card);
		$('<button class="btn btn-primary"></button>')
			.text(__("Open Booking"))
			.on("click", () => frappe.set_route("Form", "Rental Booking", c.name))
			.appendTo($buttons);
		if (c.project) {
			$('<button class="btn btn-default"></button>')
				.text(__("Open Project"))
				.on("click", () => frappe.set_route("Form", "Project", c.project))
				.appendTo($buttons);
		}
		$('<button class="btn btn-default"></button>')
			.text(__("Back to Planner"))
			.on("click", () => this.go([RP.route]))
			.appendTo($buttons);
		$('<button class="btn btn-default"></button>')
			.text(__("Book Another"))
			.on("click", () => this.start_new())
			.appendTo($buttons);
	}
}
