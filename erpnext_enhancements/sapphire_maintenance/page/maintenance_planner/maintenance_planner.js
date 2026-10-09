// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Maintenance Planner: a month/week calendar of maintenance visits, moved by dragging cards.
//
//   /desk/maintenance-planner                    this month
//   /desk/maintenance-planner/month/2026-10-01   the month holding that day
//   /desk/maintenance-planner/week/2026-10-05    the week holding that day
//
// Every move between months, weeks and views is a route, so Back and Forward step through them
// (Nik's rule: never break Back/Forward). The page moves only with frappe.set_route; Back and
// Forward re-render through on_page_show -> handle_route.
//
// Cards come from erpnext_enhancements.api.maintenance_planner.get_planner:
//   visit      a Sapphire Maintenance Record. A draft nobody has started can be dragged; that
//              rewrites its Scheduled Visit Date (move_visit).
//   projected  a visit the nightly scheduler has not drafted yet, worked out from the contract.
//              The first one of a series is the contract's stored next visit and can be dragged
//              (move_projected); the ones after it follow whenever that visit actually happens.
// Visits with no date at all wait in the Unscheduled tray until somebody drags them onto a day.
//
// Maintenance and Projects share technicians, so the same response also carries `bookings`: each
// technician's project tasks, rental crew tasks and travel days, and their free hours, all from the
// shared availability engine (project_enhancements.crew_availability). They are read-only here:
// shown as teal / amber / gray cards that open the Task or Travel Trip, never dragged. The free
// hours in a day header are the engine's figure and must match the Project Planner's, so this page
// never works them out itself. Projects moves its own bookings on the Project Planner.
//
// Dragging is done with pointer events rather than HTML5 drag and drop, which phones and
// tablets do not support. A touch has to rest on a card for a moment before it lifts, so a
// swipe still scrolls the page. Clicking or pressing Enter on a card opens it, and its dialog
// can move it too, for anyone who would rather type a date than drag.

const MP = {
	route: "maintenance-planner",
	api: "erpnext_enhancements.api.maintenance_planner",
	tech_key: "ee_maintenance_planner_technician",
	projected_key: "ee_maintenance_planner_projected",
	project_key: "ee_maintenance_planner_project_work",
	hold_ms: 300,
	drag_px: 6,
	// Labels for one-off visits; any other label is a seasonal visit.
	extra_labels: ["Extra Visit", "Chemistry Follow-Up"],
	status_order: { draft: 0, pending: 1, projected: 2, done: 3, booking: 4 },
	// Engine booking kind -> chip text. The engine calls a project task "task".
	booking_kinds: { task: "Project", rental: "Rental", travel: "Travel" },
};

const mp_ymd = (m) => m.format("YYYY-MM-DD");
const mp_esc = (value) => frappe.utils.escape_html(value == null ? "" : String(value));

frappe.pages[MP.route].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Maintenance Planner"),
		single_column: true,
	});
	wrapper.maintenance_planner = new MaintenancePlanner(page);
};

frappe.pages[MP.route].on_page_show = function (wrapper) {
	if (wrapper.maintenance_planner) {
		wrapper.maintenance_planner.handle_route();
	}
};

const MP_STYLE = `
.mp-wrap{padding-bottom:48px;color:var(--text-color);}
.mp-toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:4px 0 10px;}
.mp-title{font-weight:600;font-size:16px;margin:0 6px;min-width:150px;}
.mp-seg{display:inline-flex;border:1px solid var(--border-color);border-radius:8px;overflow:hidden;}
.mp-seg button{border:none;background:var(--card-bg);padding:4px 12px;font-size:13px;color:var(--text-color);}
.mp-seg button + button{border-left:1px solid var(--border-color);}
.mp-seg button.mp-on{background:var(--primary,#2490ef);color:#fff;}
.mp-spacer{flex:1 1 auto;}
.mp-toolbar select{width:auto;max-width:220px;}
.mp-toolbar label{display:inline-flex;align-items:center;gap:5px;margin:0;font-size:13px;font-weight:normal;}
.mp-legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--text-muted);margin:0 0 10px;align-items:center;}
.mp-legend span{display:inline-flex;align-items:center;gap:5px;}
.mp-swatch{display:inline-block;width:16px;height:11px;border-radius:3px;border:1px solid var(--border-color);border-left-width:4px;}
.mp-tray{border:1px dashed var(--border-color);border-radius:10px;padding:8px 10px;margin-bottom:10px;}
.mp-tray h5{margin:0 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);}
.mp-tray-cards{display:flex;flex-wrap:wrap;gap:6px;}
.mp-tray .mp-card{width:230px;}
.mp-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));border:1px solid var(--border-color);border-radius:10px;overflow:hidden;background:var(--card-bg);}
.mp-dow{padding:6px 8px;font-size:11px;font-weight:600;color:var(--text-muted);border-bottom:1px solid var(--border-color);text-transform:uppercase;letter-spacing:.04em;}
.mp-day{min-height:118px;padding:4px;border-right:1px solid var(--border-color);border-bottom:1px solid var(--border-color);display:flex;flex-direction:column;gap:4px;min-width:0;}
.mp-day:nth-child(7n){border-right:none;}
.mp-week .mp-day{min-height:440px;}
.mp-week .mp-dow{display:none;}
.mp-day-head{font-size:12px;color:var(--text-muted);display:flex;align-items:center;justify-content:space-between;gap:4px;padding:0 2px;}
.mp-day-num{border-radius:10px;padding:0 6px;cursor:pointer;}
.mp-day-num:hover{background:var(--control-bg);}
.mp-day.mp-today .mp-day-num{background:var(--primary,#2490ef);color:#fff;font-weight:600;}
.mp-day.mp-out{background:var(--control-bg);}
.mp-day.mp-out .mp-day-head{opacity:.55;}
.mp-day.mp-past .mp-day-head{opacity:.6;}
.mp-day-count{font-size:11px;}
.mp-day.mp-over{outline:2px solid var(--primary,#2490ef);outline-offset:-2px;background:rgba(36,144,239,.08);}
.mp-day.mp-over-no{outline:2px dashed #dc2626;outline-offset:-2px;}
.mp-card{position:relative;border:1px solid var(--border-color);border-left:4px solid #2563eb;border-radius:6px;background:var(--card-bg);padding:3px 6px;font-size:12px;line-height:1.3;cursor:pointer;min-width:0;}
.mp-card:hover,.mp-card:focus{box-shadow:0 1px 4px rgba(0,0,0,.18);outline:none;}
.mp-card.mp-movable{cursor:grab;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none;}
.mp-card-top{display:flex;align-items:center;gap:4px;}
.mp-card-title{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1 1 auto;min-width:0;}
.mp-card-sub{color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:11px;}
.mp-card-who{color:var(--text-muted);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.mp-month .mp-card-who{display:none;}
.mp-tech{flex:0 0 auto;width:20px;height:20px;border-radius:50%;font-size:9px;font-weight:700;color:#fff;display:inline-flex;align-items:center;justify-content:center;}
.mp-chip{display:inline-block;font-size:10px;border-radius:8px;padding:0 6px;margin-right:3px;background:var(--control-bg);color:var(--text-muted);}
.mp-chip.mp-red{background:rgba(220,38,38,.12);color:#b91c1c;}
.mp-card.mp-seasonal{border-left-color:#d97706;}
.mp-card.mp-extra{border-left-color:#7c3aed;}
.mp-card.mp-pending{border-left-color:#ca8a04;background:rgba(234,179,8,.10);}
.mp-card.mp-done{border-left-color:#16a34a;background:rgba(22,163,74,.08);}
.mp-card.mp-projected{border-style:dashed;border-left:4px dashed #94a3b8;background:transparent;}
.mp-card.mp-projected .mp-card-title{font-weight:500;}
.mp-card.mp-projected.mp-movable{border-left-color:#2563eb;}
.mp-card.mp-projected.mp-seasonal{border-left-color:#d97706;}
.mp-card.mp-overdue{border-left-color:#dc2626;}
.mp-card.mp-saving{opacity:.55;pointer-events:none;}
.mp-card.mp-booking{background:rgba(13,148,136,.07);}
.mp-card.mp-booking.mp-bk-task{border-left-color:#0d9488;}
.mp-card.mp-booking.mp-bk-rental{border-left-color:#b45309;background:rgba(180,83,9,.08);}
.mp-card.mp-booking.mp-bk-travel{border-left-color:#6b7280;background:rgba(107,114,128,.10);}
.mp-day-free{font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;color:#15803d;}
.mp-day-free.mp-free-full{color:#b45309;}
.mp-day-free.mp-free-over{color:#b91c1c;font-weight:600;}
.mp-day-free.mp-free-off{color:var(--text-muted);}
.mp-card.mp-dragging{opacity:.3;}
.mp-ghost{position:fixed;z-index:1100;pointer-events:none;box-shadow:0 10px 28px rgba(0,0,0,.28);transform:rotate(2deg);opacity:.96;margin:0;}
body.mp-drag-active,body.mp-drag-active *{cursor:grabbing !important;-webkit-user-select:none;user-select:none;}
.mp-loading .mp-grid,.mp-loading .mp-tray{opacity:.6;}
.mp-hint{font-size:12px;color:var(--text-muted);margin-top:8px;}
.mp-summary{width:100%;font-size:13px;margin-bottom:6px;}
.mp-summary th{color:var(--text-muted);font-weight:normal;padding:3px 10px 3px 0;vertical-align:top;white-space:nowrap;width:1%;}
.mp-summary td{padding:3px 0;}
.mp-links{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 2px;}
@media (max-width:760px){
.mp-day{min-height:84px;padding:2px;}
.mp-month .mp-card{padding:1px 3px;font-size:10px;border-left-width:3px;}
.mp-month .mp-card-sub,.mp-month .mp-tech{display:none;}
.mp-week .mp-grid{grid-template-columns:1fr;}
.mp-week .mp-day{min-height:0;border-right:none;}
.mp-tray .mp-card{width:100%;}
.mp-title{min-width:0;}
}
`;

class MaintenancePlanner {
	constructor(page) {
		this.page = page;
		if (!document.getElementById("mp-style")) {
			$("<style id='mp-style'>").text(MP_STYLE).appendTo(document.head);
		}
		this.view = "month";
		this.anchor = frappe.datetime.get_today();
		this.technician = this.load_pref(MP.tech_key, "");
		this.show_projected = this.load_pref(MP.projected_key, "1") !== "0";
		this.show_project_work = this.load_pref(MP.project_key, "1") !== "0";
		this.data = null;
		this.by_key = {};
		this.request = 0;
		this.drag = null;
		this.click_blocked_until = 0;

		this.page.set_secondary_action(__("Day Board"), () => frappe.set_route("maintenance-day-board"));
		this.$body = $('<div class="mp-wrap"></div>').appendTo(page.main);
		this.build_shell();
		this.bind_drag();
	}

	// ------------------------------------------------------------------ routing

	route_target() {
		const route = frappe.get_route() || [];
		if (route[0] !== MP.route) return null;
		const view = route[1] === "week" ? "week" : "month";
		const day = route[2] && moment(route[2], "YYYY-MM-DD", true).isValid() ? route[2] : frappe.datetime.get_today();
		return { view, anchor: day };
	}

	handle_route() {
		const target = this.route_target();
		if (!target) return;
		this.view = target.view;
		this.anchor = target.anchor;
		this.load();
	}

	go(view, anchor) {
		const route = frappe.get_route() || [];
		if (route[0] === MP.route && route[1] === view && route[2] === anchor) {
			this.load();
			return;
		}
		frappe.set_route(MP.route, view, anchor);
	}

	// ------------------------------------------------------------------ prefs

	load_pref(key, fallback) {
		try {
			const value = window.localStorage.getItem(key);
			return value == null ? fallback : value;
		} catch (e) {
			return fallback;
		}
	}

	save_pref(key, value) {
		try {
			window.localStorage.setItem(key, value);
		} catch (e) {
			// A remembered filter is a convenience.
		}
	}

	// ------------------------------------------------------------------ dates

	first_weekday() {
		const name = (frappe.boot.sysdefaults && frappe.boot.sysdefaults.first_day_of_the_week) || "Sunday";
		const index = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"].indexOf(name);
		return index < 0 ? 0 : index;
	}

	range() {
		const first = this.first_weekday();
		const anchor = moment(this.anchor, "YYYY-MM-DD");
		if (this.view === "week") {
			const start = anchor.clone().subtract((anchor.day() - first + 7) % 7, "days");
			return { start, end: start.clone().add(6, "days") };
		}
		const month_start = anchor.clone().startOf("month");
		const month_end = anchor.clone().endOf("month").startOf("day");
		return {
			start: month_start.clone().subtract((month_start.day() - first + 7) % 7, "days"),
			end: month_end.clone().add((first + 6 - month_end.day() + 7) % 7, "days"),
		};
	}

	shift(sign) {
		const anchor = moment(this.anchor, "YYYY-MM-DD");
		const next =
			this.view === "week" ? anchor.add(sign * 7, "days") : anchor.startOf("month").add(sign, "months");
		this.go(this.view, mp_ymd(next));
	}

	title() {
		const { start, end } = this.range();
		if (this.view === "month") return moment(this.anchor, "YYYY-MM-DD").format("MMMM YYYY");
		if (start.year() !== end.year()) return `${start.format("MMM D, YYYY")} – ${end.format("MMM D, YYYY")}`;
		if (start.month() !== end.month()) return `${start.format("MMM D")} – ${end.format("MMM D, YYYY")}`;
		return `${start.format("MMM D")} – ${end.format("D, YYYY")}`;
	}

	// ------------------------------------------------------------------ shell

	build_shell() {
		const $bar = $('<div class="mp-toolbar"></div>').appendTo(this.$body);
		$('<button class="btn btn-default btn-sm">‹</button>')
			.attr("title", __("Earlier"))
			.on("click", () => this.shift(-1))
			.appendTo($bar);
		$('<button class="btn btn-default btn-sm"></button>')
			.text(__("Today"))
			.on("click", () => this.go(this.view, frappe.datetime.get_today()))
			.appendTo($bar);
		$('<button class="btn btn-default btn-sm">›</button>')
			.attr("title", __("Later"))
			.on("click", () => this.shift(1))
			.appendTo($bar);
		this.$title = $('<span class="mp-title"></span>').appendTo($bar);

		const $seg = $('<span class="mp-seg"></span>').appendTo($bar);
		this.$view_buttons = {};
		[
			["month", __("Month")],
			["week", __("Week")],
		].forEach(([view, label]) => {
			this.$view_buttons[view] = $('<button type="button"></button>')
				.text(label)
				.on("click", () => this.go(view, this.anchor))
				.appendTo($seg);
		});

		$('<span class="mp-spacer"></span>').appendTo($bar);
		this.$tech = $('<select class="form-control input-sm"></select>')
			.attr("aria-label", __("Technician"))
			.on("change", () => {
				this.technician = this.$tech.val() || "";
				this.save_pref(MP.tech_key, this.technician);
				this.render();
			})
			.appendTo($bar);
		const $projected = $("<label></label>").appendTo($bar);
		$('<input type="checkbox">')
			.prop("checked", this.show_projected)
			.on("change", (e) => {
				this.show_projected = e.target.checked;
				this.save_pref(MP.projected_key, this.show_projected ? "1" : "0");
				this.render();
			})
			.appendTo($projected);
		$projected.append(document.createTextNode(__("Projected visits")));
		const $project_work = $("<label></label>")
			.attr("title", __("Project tasks, rental crew tasks and travel, read-only. Move them on the Project Planner."))
			.appendTo($bar);
		$('<input type="checkbox">')
			.prop("checked", this.show_project_work)
			.on("change", (e) => {
				this.show_project_work = e.target.checked;
				this.save_pref(MP.project_key, this.show_project_work ? "1" : "0");
				this.render();
			})
			.appendTo($project_work);
		$project_work.append(document.createTextNode(__("Project work")));
		$('<button class="btn btn-default btn-sm">↻</button>')
			.attr("title", __("Refresh"))
			.on("click", () => this.load())
			.appendTo($bar);

		const $legend = $('<div class="mp-legend"></div>').appendTo(this.$body);
		[
			["#2563eb", "solid", __("Scheduled visit")],
			["#d97706", "solid", __("Seasonal")],
			["#7c3aed", "solid", __("Extra / follow-up")],
			["#ca8a04", "solid", __("Pending review")],
			["#16a34a", "solid", __("Done")],
			["#94a3b8", "dashed", __("Projected from the contract")],
			["#dc2626", "solid", __("Overdue")],
			["#0d9488", "solid", __("Project task (read-only)")],
			["#b45309", "solid", __("Rental crew (read-only)")],
			["#6b7280", "solid", __("Travel (read-only)")],
		].forEach(([color, style, label]) => {
			const $item = $("<span></span>").appendTo($legend);
			$('<i class="mp-swatch"></i>').css({ "border-left-color": color, "border-style": style }).appendTo($item);
			$item.append(document.createTextNode(label));
		});

		this.$tray = $('<div class="mp-tray"></div>').hide().appendTo(this.$body);
		this.$grid_wrap = $("<div></div>").appendTo(this.$body);
		$('<div class="mp-hint"></div>')
			.text(
				__(
					"Drag a card to another day to move the visit. On a touch screen, hold the card for a moment first. A dashed card with a blue edge is a contract's next visit: moving it moves the contract's next visit date, and the dashed cards after it follow. Teal, amber and gray cards are project, rental and travel bookings of the same people: they open the task but are moved on the Project Planner."
				)
			)
			.appendTo(this.$body);
	}

	// ------------------------------------------------------------------ data

	load() {
		const { start, end } = this.range();
		const token = ++this.request;
		this.$title.text(this.title());
		Object.entries(this.$view_buttons).forEach(([view, $button]) => $button.toggleClass("mp-on", view === this.view));
		this.$body.addClass("mp-loading");
		frappe
			.call({ method: `${MP.api}.get_planner`, args: { start: mp_ymd(start), end: mp_ymd(end) } })
			.then((r) => {
				if (token !== this.request) return;
				this.data = r.message || {};
				this.$body.removeClass("mp-loading");
				this.fill_technicians();
				this.render();
			})
			.catch(() => {
				if (token === this.request) this.$body.removeClass("mp-loading");
			});
	}

	fill_technicians() {
		const people = (this.data && this.data.technicians) || [];
		this.tech_names = {};
		people.forEach((person) => (this.tech_names[person.user] = person.name));
		const keep = this.technician;
		this.$tech.empty();
		$("<option></option>").val("").text(__("All technicians")).appendTo(this.$tech);
		$("<option></option>").val("__none__").text(__("Unassigned")).appendTo(this.$tech);
		people.forEach((person) => {
			$("<option></option>")
				.val(person.user)
				.text(person.enabled ? person.name : `${person.name} (${__("disabled")})`)
				.appendTo(this.$tech);
		});
		const known = !keep || keep === "__none__" || people.some((person) => person.user === keep);
		this.technician = known ? keep : "";
		this.$tech.val(this.technician);
	}

	all_cards() {
		const data = this.data || {};
		return [].concat(data.visits || [], data.unscheduled || [], data.projected || [], this.booking_cards());
	}

	// The engine's read-only bookings as cards, one per booking per day. Never movable: they carry
	// no `movable` flag, which is also what keeps them out of the drag path (on_down).
	booking_cards() {
		const out = [];
		const bookings = (this.data && this.data.bookings) || {};
		Object.keys(bookings).forEach((user) => {
			Object.keys(bookings[user] || {}).forEach((ymd) => {
				((bookings[user][ymd] || {}).items || []).forEach((item, index) => {
					out.push({
						kind: "booking",
						booking_kind: item.kind,
						key: `bk|${user}|${ymd}|${item.kind}|${item.ref}|${index}`,
						date: ymd,
						technician: user,
						ref: item.ref,
						name: item.ref,
						project: item.project,
						label: item.label,
						hours: item.hours,
						slot: item.slot,
					});
				});
			});
		});
		return out;
	}

	// 3 -> "3", 2.5 -> "2.5", 1.25 -> "1.25"
	fmt_hours(hours) {
		return String(+Number(hours || 0).toFixed(2));
	}

	// The day header's free-hours text for the selected technician, from the engine's numbers.
	free_info(user, ymd) {
		const cell = (((this.data && this.data.bookings) || {})[user] || {})[ymd];
		if (!cell) return null;
		const lines = [];
		let text;
		let cls = "";
		if (cell.off) {
			text = __(cell.off);
			cls = "mp-free-off";
		} else if (!(cell.capacity > 0)) {
			text = __("Not a work day");
			cls = "mp-free-off";
		} else if (cell.booked - cell.capacity > 0.01) {
			text = __("Over {0}h", [this.fmt_hours(cell.booked - cell.capacity)]);
			cls = "mp-free-over";
		} else if (!(cell.free > 0.005)) {
			text = __("Full");
			cls = "mp-free-full";
		} else {
			text = __("{0}h free", [this.fmt_hours(cell.free)]);
		}
		lines.push(
			__("{0}h of {1}h booked on projects, rentals, travel and visits", [
				this.fmt_hours(cell.booked),
				this.fmt_hours(cell.capacity),
			])
		);
		(cell.conflicts || []).forEach((conflict) => lines.push(conflict));
		return { text, cls, title: lines.join("\n") };
	}

	visible(card) {
		if (card.kind === "booking") {
			if (!this.show_project_work || this.technician === "__none__") return false;
			return !this.technician || card.technician === this.technician;
		}
		if (card.kind === "projected" && !this.show_projected) return false;
		if (!this.technician) return true;
		if (this.technician === "__none__") return !card.technician;
		return card.technician === this.technician;
	}

	// ------------------------------------------------------------------ render

	render() {
		if (!this.data) return;
		const { start, end } = this.range();
		const today = this.data.today || frappe.datetime.get_today();
		const month = moment(this.anchor, "YYYY-MM-DD").month();
		this.by_key = {};
		const by_day = {};
		const unscheduled = [];
		this.all_cards().forEach((card) => {
			this.by_key[card.key] = card;
			if (!this.visible(card)) return;
			if (!card.date) unscheduled.push(card);
			else (by_day[card.date] = by_day[card.date] || []).push(card);
		});

		this.render_tray(unscheduled);

		const $grid = $('<div class="mp-grid"></div>');
		const first = this.first_weekday();
		for (let i = 0; i < 7; i++) {
			$('<div class="mp-dow"></div>')
				.text(moment().day((first + i) % 7).format("ddd"))
				.appendTo($grid);
		}
		for (let day = start.clone(); !day.isAfter(end, "day"); day.add(1, "days")) {
			const ymd = mp_ymd(day);
			const cards = (by_day[ymd] || []).sort((a, b) => this.compare(a, b));
			const $day = $('<div class="mp-day"></div>')
				.attr("data-date", ymd)
				.toggleClass("mp-today", ymd === today)
				.toggleClass("mp-past", ymd < today)
				.toggleClass("mp-out", this.view === "month" && day.month() !== month)
				.appendTo($grid);
			const $head = $('<div class="mp-day-head"></div>').appendTo($day);
			const label =
				this.view === "week"
					? day.format("ddd, MMM D")
					: day.date() === 1
					? day.format("MMM D")
					: day.format("D");
			$('<span class="mp-day-num"></span>')
				.text(label)
				.attr("title", __("Show this week"))
				.on("click", () => this.go("week", ymd))
				.appendTo($head);
			const free = this.technician && this.technician !== "__none__" ? this.free_info(this.technician, ymd) : null;
			if (free) {
				$('<span class="mp-day-free"></span>')
					.addClass(free.cls)
					.text(free.text)
					.attr("title", free.title)
					.appendTo($head);
			}
			const visit_count = cards.filter((card) => card.kind !== "booking").length;
			if (visit_count > 1) {
				$('<span class="mp-day-count"></span>').text(__("{0} visits", [visit_count])).appendTo($head);
			}
			cards.forEach((card) => $day.append(this.card_html(card)));
		}
		this.$grid_wrap.empty().removeClass("mp-month mp-week").addClass(`mp-${this.view}`).append($grid);
	}

	render_tray(cards) {
		this.$tray.empty().toggle(cards.length > 0);
		if (!cards.length) return;
		$("<h5></h5>")
			.text(__("Unscheduled ({0}): drag onto a day", [cards.length]))
			.appendTo(this.$tray);
		const $cards = $('<div class="mp-tray-cards"></div>').appendTo(this.$tray);
		cards.sort((a, b) => this.compare(a, b)).forEach((card) => $cards.append(this.card_html(card)));
	}

	compare(a, b) {
		const order = (card) =>
			MP.status_order[card.kind === "projected" || card.kind === "booking" ? card.kind : card.status] || 0;
		return order(a) - order(b) || String(a.site || "").localeCompare(String(b.site || ""));
	}

	kind_class(card) {
		const classes = [card.kind === "projected" ? "mp-projected" : `mp-${card.status}`];
		if (card.label) classes.push(MP.extra_labels.includes(card.label) ? "mp-extra" : "mp-seasonal");
		if (card.overdue && card.kind === "visit") classes.push("mp-overdue");
		if (card.movable) classes.push("mp-movable");
		if (card.saving) classes.push("mp-saving");
		return classes.join(" ");
	}

	// A project task, rental crew task or travel day of a technician. Read-only: no mp-movable.
	booking_html(card) {
		const kind = card.booking_kind in MP.booking_kinds ? card.booking_kind : "task";
		const sub = [];
		if (card.project) sub.push(card.project);
		sub.push(card.slot ? card.slot.join("–") : `${this.fmt_hours(card.hours)}h`);
		const who = card.technician ? this.tech_name(card.technician) : "";
		const tip = [card.label, sub.join(" · "), who, __("Read-only. Move it on the Project Planner.")]
			.filter(Boolean)
			.join("\n");
		const badge = this.technician ? "" : this.tech_badge(card.technician);
		return `
			<div class="mp-card mp-booking mp-bk-${kind}" data-key="${mp_esc(card.key)}" tabindex="0" title="${mp_esc(tip)}">
				<div class="mp-card-top">
					<span class="mp-card-title">${mp_esc(card.label || card.ref)}</span>
					${badge}
				</div>
				<div class="mp-card-sub">${mp_esc(sub.join(" · "))}</div>
				<div class="mp-card-who"><span class="mp-chip">${mp_esc(__(MP.booking_kinds[kind]))}</span></div>
			</div>`;
	}

	card_html(card) {
		if (card.kind === "booking") return this.booking_html(card);
		const sub = [];
		if (card.label) sub.push(__(card.label));
		if (card.feature) sub.push(card.feature);
		if (!sub.length) sub.push(card.kind === "projected" ? __(card.frequency || "") : __("Regular visit"));

		const chips = [];
		if (card.kind === "projected") chips.push(card.movable ? __("Next visit") : __("Projected"));
		if (card.status === "pending") chips.push(__("Pending review"));
		if (card.status === "done") chips.push(__("Done"));
		if (card.overdue) chips.push(`<span class="mp-chip mp-red">${mp_esc(__("Overdue"))}</span>`);
		if (card.on_hold) chips.push(`<span class="mp-chip mp-red">${mp_esc(__("On hold"))}</span>`);
		if (card.flagged) chips.push(`<span class="mp-chip mp-red">${mp_esc(__("Chemistry"))}</span>`);

		const who = card.technician ? this.tech_name(card.technician) : __("Unassigned");
		const tip = [card.site_full, sub.join(" · "), who].filter(Boolean).join("\n");
		return `
			<div class="mp-card ${this.kind_class(card)}" data-key="${mp_esc(card.key)}" tabindex="0" title="${mp_esc(tip)}">
				<div class="mp-card-top">
					<span class="mp-card-title">${mp_esc(card.site || card.project || card.name)}</span>
					${this.tech_badge(card.technician)}
				</div>
				<div class="mp-card-sub">${mp_esc(sub.join(" · "))}</div>
				<div class="mp-card-who">${mp_esc(who)} ${chips
					.map((chip) => (chip.startsWith("<span") ? chip : `<span class="mp-chip">${mp_esc(chip)}</span>`))
					.join("")}</div>
			</div>`;
	}

	tech_name(user) {
		return (this.tech_names && this.tech_names[user]) || user;
	}

	tech_badge(user) {
		if (!user) return "";
		const name = this.tech_name(user);
		const parts = String(name).split(/\s+/).filter(Boolean);
		const initials = (parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "");
		let hash = 0;
		for (const ch of String(user)) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
		return `<span class="mp-tech" style="background:hsl(${hash},55%,42%)" title="${mp_esc(name)}">${mp_esc(
			initials.toUpperCase()
		)}</span>`;
	}

	// ------------------------------------------------------------------ dragging

	bind_drag() {
		const root = this.$body[0];
		root.addEventListener("pointerdown", (e) => this.on_down(e));
		document.addEventListener("pointermove", (e) => this.on_move(e));
		document.addEventListener("pointerup", (e) => this.on_up(e));
		document.addEventListener("pointercancel", (e) => this.on_cancel(e));
		// Once a card has lifted, a finger moving it must not scroll the page. Non-passive, or
		// the browser ignores preventDefault here.
		document.addEventListener(
			"touchmove",
			(e) => {
				if (this.drag && this.drag.active) e.preventDefault();
			},
			{ passive: false }
		);
		root.addEventListener("contextmenu", (e) => {
			if (this.drag) e.preventDefault();
		});
		root.addEventListener("click", (e) => {
			const card_el = e.target.closest(".mp-card");
			if (!card_el || Date.now() < this.click_blocked_until) return;
			const card = this.by_key[card_el.getAttribute("data-key")];
			if (card) this.open_card(card);
		});
		root.addEventListener("keydown", (e) => {
			if (e.key !== "Enter" && e.key !== " ") return;
			const card_el = e.target.closest && e.target.closest(".mp-card");
			if (!card_el) return;
			e.preventDefault();
			const card = this.by_key[card_el.getAttribute("data-key")];
			if (card) this.open_card(card);
		});
	}

	on_down(e) {
		if (e.button > 0 || this.drag) return;
		const card_el = e.target.closest(".mp-card");
		if (!card_el) return;
		const card = this.by_key[card_el.getAttribute("data-key")];
		if (!card || !card.movable || card.saving) return;
		this.drag = { card, el: card_el, x: e.clientX, y: e.clientY, id: e.pointerId, touch: e.pointerType === "touch" };
		if (this.drag.touch) {
			this.drag.timer = setTimeout(() => this.lift(), MP.hold_ms);
		} else {
			// No mousedown, so a drag does not select the text of every day it crosses. The
			// click that opens the card still fires.
			e.preventDefault();
		}
	}

	on_move(e) {
		const drag = this.drag;
		if (!drag || e.pointerId !== drag.id) return;
		const far = Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > MP.drag_px;
		if (!drag.active) {
			if (drag.touch) {
				// Moved before the hold finished: that is a scroll, not a drag.
				if (far) this.end_drag();
				return;
			}
			if (!far) return;
			this.lift();
		}
		e.preventDefault();
		this.place_ghost(e.clientX, e.clientY);
		this.mark_target(this.target_day(e.clientX, e.clientY));
		// Near the top or bottom of the window, scroll so a far week is reachable.
		const edge = 56;
		if (e.clientY < edge) window.scrollBy(0, -14);
		else if (e.clientY > window.innerHeight - edge) window.scrollBy(0, 14);
	}

	on_up(e) {
		const drag = this.drag;
		if (!drag || e.pointerId !== drag.id) return;
		const was_active = drag.active;
		const target = was_active ? this.target_day(e.clientX, e.clientY) : null;
		this.end_drag();
		if (!was_active) return;
		this.click_blocked_until = Date.now() + 400;
		if (target && target !== drag.card.date) this.drop(drag.card, target);
	}

	on_cancel(e) {
		if (this.drag && e.pointerId === this.drag.id) this.end_drag();
	}

	lift() {
		const drag = this.drag;
		if (!drag || drag.active) return;
		drag.active = true;
		const rect = drag.el.getBoundingClientRect();
		drag.dx = drag.x - rect.left;
		drag.dy = drag.y - rect.top;
		drag.ghost = drag.el.cloneNode(true);
		drag.ghost.classList.add("mp-ghost");
		drag.ghost.style.width = `${rect.width}px`;
		document.body.appendChild(drag.ghost);
		drag.el.classList.add("mp-dragging");
		document.body.classList.add("mp-drag-active");
		this.place_ghost(drag.x, drag.y);
		if (drag.touch && navigator.vibrate) navigator.vibrate(12);
	}

	place_ghost(x, y) {
		const drag = this.drag;
		if (!drag || !drag.ghost) return;
		drag.ghost.style.left = `${x - drag.dx}px`;
		drag.ghost.style.top = `${y - drag.dy}px`;
	}

	target_day(x, y) {
		const el = document.elementFromPoint(x, y);
		const day_el = el && el.closest(".mp-grid .mp-day[data-date]");
		return day_el ? day_el.getAttribute("data-date") : null;
	}

	mark_target(ymd) {
		if (this.drag) this.drag.target = ymd;
		const today = (this.data && this.data.today) || frappe.datetime.get_today();
		this.$grid_wrap.find(".mp-over, .mp-over-no").removeClass("mp-over mp-over-no");
		if (!ymd) return;
		this.$grid_wrap
			.find(`.mp-day[data-date="${ymd}"]`)
			.addClass(ymd < today ? "mp-over-no" : "mp-over");
	}

	end_drag() {
		const drag = this.drag;
		this.drag = null;
		if (!drag) return;
		clearTimeout(drag.timer);
		if (drag.ghost) drag.ghost.remove();
		drag.el.classList.remove("mp-dragging");
		document.body.classList.remove("mp-drag-active");
		this.$grid_wrap.find(".mp-over, .mp-over-no").removeClass("mp-over mp-over-no");
	}

	// ------------------------------------------------------------------ moving

	drop(card, ymd) {
		const today = (this.data && this.data.today) || frappe.datetime.get_today();
		if (ymd < today) {
			frappe.show_alert({ message: __("Visits can only be moved to today or a later day."), indicator: "orange" });
			return;
		}
		// Show the card on its new day straight away; the reload afterwards is the truth.
		card.date = ymd;
		card.saving = true;
		this.render();
		this.save_move(card, { date: ymd }).finally(() => this.load());
	}

	// Resolves either way: a refusal has already shown the server's own message.
	save_move(card, change) {
		const site = card.site || card.site_full || card.name;
		const when = (ymd) => moment(ymd, "YYYY-MM-DD").format("ddd, MMM D");
		const call =
			card.kind === "visit"
				? frappe.call({
						method: `${MP.api}.move_visit`,
						args: Object.assign({ record: card.name, modified: card.modified }, change),
				  })
				: frappe.call({
						method: `${MP.api}.move_projected`,
						args: {
							contract: card.contract,
							from_date: card.from_date,
							to_date: change.date,
							serial_no: card.serial_no || null,
						},
				  });
		return Promise.resolve(call)
			.then((r) => {
				const result = (r && r.message) || {};
				let message = change.date
					? __("{0} moved to {1}", [site, when(change.date)])
					: __("{0} updated", [site]);
				if (result.drafted) message += ". " + __("The visit is drafted and on the technician's list.");
				frappe.show_alert({ message, indicator: "green" }, 5);
				(result.warnings || []).forEach((warning) =>
					frappe.show_alert({ message: warning, indicator: "orange" }, 10)
				);
			})
			.catch(() => {});
	}

	// ------------------------------------------------------------------ dialog

	open_card(card) {
		if (card.kind === "booking") {
			// Travel days open their trip; project and rental bookings are Tasks.
			if (card.booking_kind === "travel") frappe.set_route("Form", "Travel Trip", card.ref);
			else frappe.set_route("Form", "Task", card.ref);
			return;
		}
		const rows = [];
		const add = (label, value) => {
			if (value) rows.push(`<tr><th>${mp_esc(label)}</th><td>${mp_esc(value)}</td></tr>`);
		};
		const status = {
			draft: __("Scheduled"),
			pending: __("Pending review"),
			done: __("Done"),
		};
		add(__("Site"), card.site_full);
		add(__("Visit"), card.label ? __(card.label) : card.kind === "visit" ? __("Regular visit") : "");
		add(__("Water feature"), card.feature);
		add(__("Technician"), card.technician ? this.tech_name(card.technician) : __("Unassigned"));
		add(
			__("Status"),
			card.kind === "projected"
				? card.movable
					? __("Not drafted yet. This is the contract's next visit.")
					: __("Projected. It follows whenever the visit before it is done.")
				: status[card.status]
		);
		add(__("Date"), card.date ? frappe.datetime.str_to_user(card.date) : __("Not scheduled"));
		if (card.kind === "projected") add(__("Frequency"), card.frequency);
		if (card.kind === "visit" && card.status !== "done") add(__("Filled in"), `${Math.round(card.completion || 0)}%`);
		if (card.started) add(__("Note"), __("This visit has been started, so its date is changed on the visit form."));
		add(__("Contract"), card.contract);

		const links = [];
		if (card.kind === "visit") {
			links.push(["form", __("Open visit form")]);
			if (card.status !== "done") links.push(["wizard", __("Open in Visit Wizard")]);
		}
		if (card.contract) links.push(["contract", __("Open contract")]);

		const fields = [
			{
				fieldtype: "HTML",
				fieldname: "summary",
				options:
					`<table class="mp-summary">${rows.join("")}</table>` +
					`<div class="mp-links">${links
						.map(
							([action, label]) =>
								`<button type="button" class="btn btn-default btn-xs" data-action="${action}">${mp_esc(label)}</button>`
						)
						.join("")}</div>`,
			},
		];
		if (card.movable) {
			fields.push({
				fieldtype: "Date",
				fieldname: "date",
				label: card.kind === "visit" ? __("Scheduled date") : __("Next visit date"),
				default: card.date || "",
				reqd: card.kind === "projected" ? 1 : 0,
			});
			if (card.kind === "visit") {
				fields.push({
					fieldtype: "Select",
					fieldname: "technician",
					label: __("Technician"),
					options: [{ label: __("Unassigned"), value: "" }].concat(
						((this.data && this.data.technicians) || [])
							.filter((person) => person.enabled || person.user === card.technician)
							.map((person) => ({ label: person.name, value: person.user }))
					),
					default: card.technician || "",
				});
			}
		}

		const dialog = new frappe.ui.Dialog({ title: card.site || card.site_full || card.name, fields });
		if (card.movable) {
			dialog.set_primary_action(card.kind === "visit" ? __("Save") : __("Move next visit"), (values) => {
				const change = {};
				if (values.date && values.date !== card.date) change.date = values.date;
				if (card.kind === "visit" && (values.technician || "") !== (card.technician || "")) {
					change.technician = values.technician || "";
				}
				if (!Object.keys(change).length) {
					dialog.hide();
					return;
				}
				const today = (this.data && this.data.today) || frappe.datetime.get_today();
				if (change.date && change.date < today) {
					frappe.msgprint(__("Pick today or a later day."));
					return;
				}
				if (card.kind === "projected" && !change.date) {
					dialog.hide();
					return;
				}
				dialog.hide();
				card.saving = true;
				this.render();
				this.save_move(card, change).finally(() => this.load());
			});
		}
		dialog.$wrapper.find("[data-action]").on("click", (e) => {
			const action = e.currentTarget.getAttribute("data-action");
			dialog.hide();
			if (action === "form") frappe.set_route("Form", "Sapphire Maintenance Record", card.name);
			else if (action === "wizard") frappe.set_route("visit-wizard", { record: card.name });
			else if (action === "contract") frappe.set_route("Form", "Sapphire Maintenance Contract", card.contract);
		});
		dialog.show();
	}
}
