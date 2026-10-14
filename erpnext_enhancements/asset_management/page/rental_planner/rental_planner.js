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
// Back/Forward). The page moves between screens only with frappe.set_route; frappe's router owns
// history, and Back/Forward re-render the route through on_page_show -> handle_route. There is no
// popstate listener.
//
// The board's first day is in its address (v1.662.0): /desk/rental-planner?start=YYYY-MM-DD, and
// the bare address is today. ‹ › and Today each push an entry of their own, so Back returns to the
// dates looked at before and a reload or a pasted link opens the same ones. That is the page's one
// history write: the same screen with another query, written with history.pushState because
// routing to the page it is on would make older v16 routers drop the query. Back lands through
// the router like any entry: it copies the query into frappe.route_options, and handle_route reads
// `start` there and consumes it. A start that is not a real calendar day is ignored.
//
// The draft lives in memory and in sessionStorage, so leaving the page, Back past step 1 or a
// reload loses nothing; an address for a step whose earlier steps are not done yet is corrected
// in place (replace, not a new entry) to the first unfinished step.
//
// The timeline is drawn here rather than on the Gantt widget: DHTMLX Gantt Standard draws one
// bar per row, and a fleet timeline is several bookings on each fountain's row (split tasks and
// the resource view are PRO-only). The server is still the authority on availability — the
// booking's own save re-checks with the fountains row-locked — so nothing here enforces anything.
//
// The page's styles are rental_planner.css beside this file (custom pages S3.4f, v1.670.0), which
// Frappe's page loader sends with the page. The script writes no stylesheet and no color: a bar's
// kind, a pool cell's load and the legend are classes, and what stays inline is layout only (a
// bar's left and width, a cell's, the day width as --rp-day).

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

// "YYYY-MM-DD" when the value is a real calendar day, else null: 2026-02-30 is not one.
function rp_valid_day(value) {
	const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value == null ? "" : value).trim());
	if (!m || +m[1] < 1900 || +m[1] > 2999) return null;
	const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
	return d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3] ? m[0] : null;
}

// sf_kit (v1.645.0): the board's loading, error and no-access panels and its quiet server call,
// from the shared loader (public/js/sf_kit_loader.js, in the global bundle). It never rejects: a
// kit that never arrived resolves to a plain stand-in, so the board does not sit on "Loading" for
// good. A Desk opened before the deploy that added the loader has no sf_kit_ready yet, and asks
// frappe.require for the kit itself.
function rp_kit() {
	if (typeof window.sf_kit_ready === "function") return window.sf_kit_ready();
	return new Promise((resolve) =>
		frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css"], () =>
			resolve(window.sf_kit && window.sf_kit.state ? window.sf_kit : rp_bare_kit())
		)
	);
}

// Neither the loader nor the kit: a Desk opened before this deploy whose old kit file is gone too.
// A line of text and a button, so the page still says something instead of throwing.
function rp_bare_kit() {
	const failed = (x) => ({ status: (x && x.status) || 0, message: "", exc_type: "", session_expired: (x && x.status) === 401 });
	const render = (el, kind, o) => {
		const $el = $(el).empty().append($('<div class="text-muted"></div>').text((o && o.title) || ""));
		if (o && o.on_action) $('<button class="btn btn-default btn-sm"></button>').text(o.action_label || __("Try again")).on("click", o.on_action).appendTo($el);
	};
	const call = (method, args) => Promise.resolve(frappe.call({ method, args: args || {} })).then((r) => r && r.message, (x) => Promise.reject(failed(x)));
	return { esc: (v) => frappe.utils.escape_html(v == null ? "" : String(v)), call, state: { render }, toast: (message) => frappe.show_alert({ message, indicator: "orange" }) };
}

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

class RentalPlanner {
	constructor(page) {
		this.page = page;
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
		const start = this.take_route_start();
		// Steps the flow pushed that the browser has since gone back past are no longer behind us.
		while (this.pushed.length && this.pushed[this.pushed.length - 1].step !== target.step) {
			this.pushed.pop();
		}
		if (target.view === "board") {
			// The address names the dates, every time: Back onto the bare address is today again.
			this.board_start = start || frappe.datetime.get_today();
			// Today's date or a malformed one leaves the bare address: corrected in place.
			this.write_board_address(false);
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

	// The board's start from frappe.route_options (where the router copies the address's query, and
	// where a link from another page puts its options), taken out as the list view takes its
	// filters: the key goes, and an emptied object goes too, or a later plain set_route to this page
	// would replay it. Null when there is none or it is not a real day.
	take_route_start() {
		const opts = frappe.route_options;
		if (!opts || !Object.prototype.hasOwnProperty.call(opts, "start")) return null;
		const start = rp_valid_day(opts.start);
		delete opts.start;
		if (!Object.keys(opts).length) frappe.route_options = null;
		return start;
	}

	// The route back to the board at the dates it shows, for the flow's "Planner" buttons: a new
	// entry, and it should not quietly jump back to today.
	board_route() {
		const start = this.board_start;
		return start && start !== frappe.datetime.get_today() ? [RP.route, { start }] : [RP.route];
	}

	// The board's address on the path the router put on screen (a workspace shell may lead it). push:
	// dates the person moved to, a new entry; otherwise the entry on screen is corrected in place.
	// Nothing is written off the board, or when the address already says it.
	write_board_address(push) {
		if (this.current_screen() !== "board" || (frappe.get_route() || [])[0] !== RP.route) return;
		const start = this.board_start;
		const url = window.location.pathname + (start && start !== frappe.datetime.get_today() ? `?start=${encodeURIComponent(start)}` : "");
		if (url === window.location.pathname + window.location.search) return;
		if (push) window.history.pushState(null, "", url);
		else window.history.replaceState(window.history.state, "", url);
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
			this.go(this.board_route());
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
			this.move_board(frappe.datetime.add_days(this.board_start, sign * step));
		};
		$('<button type="button" class="btn btn-default btn-sm">').text("‹").attr({ title: __("Earlier"), "aria-label": __("Earlier") }).on("click", () => shift(-1)).appendTo($bar);
		$('<button class="btn btn-default btn-sm">').text(__("Today")).on("click", () => this.move_board(frappe.datetime.get_today())).appendTo($bar);
		$('<button type="button" class="btn btn-default btn-sm">').text("›").attr({ title: __("Later"), "aria-label": __("Later") }).on("click", () => shift(1)).appendTo($bar);
		this.$range_label = $('<span class="rp-range-label"></span>').appendTo($bar);
		const $range = $('<select class="form-control input-sm rp-range"></select>').appendTo($bar);
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
		// Each swatch carries its bar's own classes, so the rule that paints a bar paints its key.
		[
			["rp-rental", __("Confirmed rental")],
			["rp-rental rp-held", __("Held (tentative)")],
			["rp-prep", __("Prep / cleaning")],
			["rp-booking", __("Other booking")],
			["rp-out_of_service", __("Out of service")],
		].forEach(([kind, label]) => {
			const $s = $("<span>").appendTo($legend);
			$('<i class="rp-swatch" aria-hidden="true"></i>').addClass(kind).appendTo($s);
			$s.append(document.createTextNode(label));
		});
		$('<span>').text(__("Click an empty day on a fountain to start a rental there.")).appendTo($legend);

		this.$timeline = $('<div class="rp-scroll"></div>').appendTo(this.$body);
		this.load_timeline();
	}

	// Dates the person moved the board to: an entry of their own (none when nothing moved, as Today
	// pressed on today), then the fleet for them.
	move_board(start) {
		this.board_start = start;
		this.write_board_address(true);
		this.load_timeline();
	}

	load_timeline() {
		const start = this.board_start;
		const days = this.board_days;
		const end = frappe.datetime.add_days(start, days - 1);
		this.$range_label.text(`${frappe.datetime.str_to_user(start)} – ${frappe.datetime.str_to_user(end)}`);
		const $timeline = this.$timeline;
		const token = (this._timeline_token = {});
		// Quiet (v1.645.0): a failed load says so in the board's own panel, with Try again, rather
		// than leaving "Loading the fleet" up for good behind a frappe dialog. Each page of the
		// range is a fresh load (the old range is already off the board), so each gets the panel.
		rp_kit().then((sf) => {
			// A later request (the person paged again) owns the board now.
			if (token !== this._timeline_token) return;
			sf.state.render($timeline, "loading", { title: __("Loading the fleet…") });
			sf.call(`${RP.api}.get_timeline`, { start, days }, { quiet: true }).then(
				(message) => {
					if (token !== this._timeline_token) return;
					if (!message) {
						sf.state.render($timeline, "empty", {
							title: __("Nothing came back for these dates."),
							body: __("Try another range, or Today."),
						});
						return;
					}
					this.render_timeline(message);
				},
				(err) => {
					if (token !== this._timeline_token) return;
					this.timeline_failed(sf, $timeline, err || {});
				}
			);
		});
	}

	// No access on a 403; a way back in when the session has lapsed (the quiet path puts up no
	// sign-in dialog of frappe's); otherwise what went wrong, with Try again for the same range.
	timeline_failed(sf, $timeline, err) {
		if (err.session_expired) {
			sf.state.render($timeline, "error", {
				title: __("You have been signed out."),
				body: __("Reload the page to sign in again."),
				action_label: __("Reload"),
				on_action: () => window.location.reload(),
			});
		} else if (err.status === 403) {
			sf.state.render($timeline, "no-access", {
				title: __("You do not have access to the rental fleet."),
				body: err.message,
			});
		} else {
			sf.state.render($timeline, "error", {
				title: __("Could not load the fleet."),
				body: err.message,
				on_action: () => this.load_timeline(),
			});
		}
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
			// Today is marked by a bar and its weight, and says so to a screen reader.
			if (i === today_index) $d.addClass("rp-today").append($('<span class="rp-sr"></span>').text(__("Today")));
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
				// A real button (S3.6): Tab reaches it, and Enter or Space opens it as a click does.
				const $bar = $('<button type="button" class="rp-bar"></button>')
					.addClass(`rp-${bar.kind}`)
					.toggleClass("rp-held", Boolean(bar.held))
					.css({ left: left + "px", width: Math.max(right - left, 3) + "px" })
					.appendTo($track);
				const text = bar.kind === "prep" ? __("Prep") : bar.kind === "turnaround" ? __("Cleaning") : bar.label;
				// The label truncates on an inner span: some engines ignore text-overflow on a <button>.
				if (bar.kind !== "prep" && bar.kind !== "turnaround") $('<span class="rp-bar-text"></span>').text(text).appendTo($bar);
				const until = bar.open_ended ? __("until further notice") : frappe.datetime.str_to_user(bar.to);
				const facts = [
					text,
					bar.held ? __("Held (tentative)") : bar.status,
					`${frappe.datetime.str_to_user(bar.from)} – ${until}`,
					bar.name,
				].filter(Boolean);
				// The prep and cleaning bars draw no text, so the label is all a screen reader has.
				$bar.attr({ title: facts.join("\n"), "aria-label": facts.join(", ") });
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
				const $cell = $('<div class="rp-cell"></div>')
					.addClass(ratio >= 1 ? "rp-full" : ratio >= 0.6 ? "rp-mid" : "rp-low")
					.css({ left: i * dw + 1 + "px", width: dw - 2 + "px" })
					.text(dw >= 30 ? `${used}/${pool.capacity}` : used)
					.attr("title", __("{0} of {1} out", [used, pool.capacity]))
					.appendTo($track);
				// The shade is not the only thing that says a pool is full or nearly so.
				if (ratio >= 0.6) $('<span class="rp-sr"></span>').text(ratio >= 1 ? __("full") : __("nearly full")).appendTo($cell);
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
		this.page.set_secondary_action(__("Planner"), () => this.go(this.board_route()));
		this.$body.empty();

		const $steps = $('<div class="rp-steps"></div>').appendTo(this.$body);
		const current = RP.steps.indexOf(step);
		const reachable = RP.steps.indexOf(this.first_unfinished_step());
		RP.steps.forEach((key, i) => {
			// A step that can be gone back to is a real button (S3.6); the others are only words.
			const done = i !== current && i <= reachable;
			const $s = $(done ? '<button type="button" class="rp-step"></button>' : '<div class="rp-step"></div>')
				.text(`${i + 1}. ${RP_STEP_TITLES[key]}`)
				.appendTo($steps);
			if (i === current) $s.addClass("rp-active").attr("aria-current", "step");
			else if (done) {
				$s.addClass("rp-done").on("click", (event) => {
					// Enter or Space on a button is a click with detail 0. The step that comes next
					// replaces the rail, so focus would fall to <body>: put it on the step's heading.
					if (event && event.detail === 0) this.focus_heading_until = Date.now() + 2000;
					this.go_step(key);
				});
			}
		});

		this.$card = $('<div class="rp-card"></div>').appendTo(this.$body);
		const $nav = $('<div class="rp-nav"></div>').appendTo(this.$body);
		$('<button class="btn btn-default"></button>')
			.text(current === 0 ? __("Back to Planner") : __("Back"))
			.on("click", () => this.leave_step(step, () => this.step_back(step)))
			.appendTo($nav);
		this.$next = $('<div></div>').appendTo($nav);

		this[`render_${step}`]();
		// A step reached from the keyboard (S3.6): its heading takes focus, a mouse click leaves it.
		if (this.focus_heading_until > Date.now()) {
			this.focus_heading_until = 0;
			const heading = this.$card.find("h4").first().attr("tabindex", "-1")[0];
			if (heading && typeof heading.focus === "function") heading.focus();
		}
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

		const $package = $('<div class="rp-package"></div>').appendTo(this.$card);
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

		$('<h5 class="rp-subhead"></h5>').text(__("Accessories")).appendTo($l);
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
				$('<a href="#" class="rp-edit"></a>')
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
		const $buttons = $('<div class="rp-actions"></div>').appendTo(this.$next);
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
		const $buttons = $('<div class="rp-actions"></div>').appendTo($card);
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
			.on("click", () => this.go(this.board_route()))
			.appendTo($buttons);
		$('<button class="btn btn-default"></button>')
			.text(__("Book Another"))
			.on("click", () => this.start_new())
			.appendTo($buttons);
	}
}
