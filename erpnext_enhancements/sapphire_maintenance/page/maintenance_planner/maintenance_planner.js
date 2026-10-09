// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Maintenance Planner: a month/week calendar of maintenance visits, moved by dragging cards.
//
//   /desk/maintenance-planner                    this month
//   /desk/maintenance-planner/month/2026-10-01   the month holding that day
//   /desk/maintenance-planner/week/2026-10-05    the week holding that day
//   /desk/maintenance-planner/crew/2026-10-05    the crew timeline for the week holding that day
//
// Every move between months, weeks and views is a route, so Back and Forward step through them
// (Nik's rule: never break Back/Forward). The page moves only with frappe.set_route; Back and
// Forward re-render through on_page_show -> handle_route.
//
// Cards come from erpnext_enhancements.api.maintenance_planner.get_planner:
//   visit      a Sapphire Maintenance Record. A draft nobody has started can be dragged; that
//              rewrites its Scheduled Visit Date and/or its technician (move_visit).
//   projected  a visit the nightly scheduler has not drafted yet, worked out from the contract.
//              The first one of a series is the contract's stored next visit and can be dragged
//              (move_projected); the ones after it follow whenever that visit actually happens.
//              A projected visit follows the technician on the site's Maintenance Profile, so only
//              its day moves.
// Visits with no date at all wait in the Unscheduled tray until somebody drags them onto a day.
//
// Maintenance and Projects share technicians, so the same response also carries `bookings`: each
// technician's project tasks, rental crew tasks and travel days, their free hours and days off,
// and (when the engine provides them) their drive time, all from the shared availability engine
// (project_enhancements.crew_availability). The bookings are read-only here: shown as teal /
// amber / gray cards that open the Task or Travel Trip, never dragged. The free hours are the
// engine's figure and must match the Project Planner's, so this page never works them out itself.
// Projects moves its own bookings on the Project Planner.
//
// What a scheduler sees and can do, the same as on the Project Planner:
//   - "Resources available" above the calendar    each technician's hours free, Full, Over,
//                                                 Holiday, Time off or Not a work day, per day
//   - days off on the calendar                    one technician picked: their days off are shaded
//                                                 and labeled; All: a badge for each person off
//   - Crew view (a route, /crew/<date>)           a row per technician, a column per day of the
//                                                 week, their visits, bookings and hours in each
//   - drag a visit to another day                 move_visit(date)
//   - in the crew view, drag a visit to another
//     technician's row                            move_visit(technician and date)
//   - drag a technician from the panel onto a
//     visit card                                  move_visit(technician)
//   - the route icon on a technician's day        the Project Planner's route view for that day
// Overbooking warns and never blocks: when a move would put a technician over their hours, on a
// day off or in two places at once, the server answers needs_reason instead of saving, and the
// page asks for a reason and sends the move again with it. The reason lands on the visit's (or,
// for a projected visit, the contract's) timeline. Undo steps back through the last 20 moves.
//
// Dragging is done with pointer events rather than HTML5 drag and drop, which phones and
// tablets do not support. A touch has to rest on a card for a moment before it lifts, so a
// swipe still scrolls the page. Clicking or pressing Enter on a card opens it, and its dialog
// can move it too, for anyone who would rather type a date than drag.

const MP = {
	route: "maintenance-planner",
	api: "erpnext_enhancements.api.maintenance_planner",
	views: ["month", "week", "crew"],
	tech_key: "ee_maintenance_planner_technician",
	projected_key: "ee_maintenance_planner_projected",
	project_key: "ee_maintenance_planner_project_work",
	panel_key: "ee_maintenance_planner_panel",
	hold_ms: 300,
	drag_px: 6,
	undo_max: 20,
	undo_reason: "Undo on the Maintenance Planner",
	// The Project Planner page the route icon opens: /project-planner/route/<resource>/<date>.
	project_route: "project-planner",
	// Labels for one-off visits; any other label is a seasonal visit.
	extra_labels: ["Extra Visit", "Chemistry Follow-Up"],
	status_order: { draft: 0, pending: 1, projected: 2, done: 3, booking: 4 },
	// Engine booking kind -> chip text. The engine calls a project task "task".
	booking_kinds: { task: "Project", rental: "Rental", travel: "Travel" },
};

// The endpoints the page calls, and no others.
MP.methods = {
	get_planner: `${MP.api}.get_planner`,
	move_visit: `${MP.api}.move_visit`,
	move_projected: `${MP.api}.move_projected`,
};

const mp_ymd = (m) => m.format("YYYY-MM-DD");
const mp_esc = (value) => frappe.utils.escape_html(value == null ? "" : String(value));
// 3 -> "3", 2.5 -> "2.5", 1.25 -> "1.25": the engine's fmt_hours, so both sides print alike.
const mp_hours = (value) => String(Math.round((Number(value) || 0) * 100) / 100);
const mp_when = (ymd) => moment(ymd, "YYYY-MM-DD").format("ddd, MMM D");
// 70 -> "1h 10m", 25 -> "25m", 120 -> "2h": drive time as people say it.
const mp_drive = (minutes) => {
	const total = Math.max(0, Math.round(Number(minutes) || 0));
	const h = Math.floor(total / 60);
	const m = total % 60;
	if (!h) return `${m}m`;
	return m ? `${h}h ${m}m` : `${h}h`;
};
// Colors come from records people edit; only a plain hex value goes into a style attribute.
const mp_color = (value, fallback) => (/^#[0-9a-fA-F]{3,8}$/.test(String(value || "")) ? value : fallback);
// The same pin-and-route glyph the Project Planner uses on a person's day.
const MP_ROUTE_ICON =
	'<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5">' +
	'<circle cx="3.5" cy="12.5" r="1.8"/><circle cx="12.5" cy="3.5" r="1.8"/>' +
	'<path d="M5.3 12.5H10a2.5 2.5 0 0 0 0-5H6a2.5 2.5 0 0 1 0-4h4.7"/></svg>';

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
.mp-undo.mp-empty{opacity:.55;}
.mp-legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--text-muted);margin:0 0 10px;align-items:center;}
.mp-legend span{display:inline-flex;align-items:center;gap:5px;}
.mp-swatch{display:inline-block;width:16px;height:11px;border-radius:3px;border:1px solid var(--border-color);border-left-width:4px;}
.mp-section{border:1px solid var(--border-color);border-radius:10px;background:var(--card-bg);margin-bottom:10px;}
.mp-section-head{display:flex;align-items:center;gap:8px;padding:6px 10px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);font-weight:600;}
.mp-section-head .btn{text-transform:none;letter-spacing:0;}
.mp-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;}
.mp-res-grid{display:grid;min-width:0;}
.mp-res-grid > div{border-top:1px solid var(--border-color);}
.mp-res-head{font-size:11px;color:var(--text-muted);padding:3px 4px;text-align:center;white-space:nowrap;}
.mp-res-head.mp-today{color:var(--primary,#2490ef);font-weight:700;}
.mp-person{display:flex;align-items:center;gap:6px;padding:3px 8px;font-size:12px;min-width:0;cursor:grab;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none;position:sticky;left:0;background:var(--card-bg);z-index:1;}
.mp-person:hover{background:var(--control-bg);}
.mp-person.mp-selected .mp-person-name{color:var(--primary,#2490ef);}
.mp-person-name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600;}
.mp-person-group{font-size:10px;color:var(--text-muted);white-space:nowrap;}
.mp-dot{flex:0 0 auto;width:10px;height:10px;border-radius:50%;}
.mp-avail{padding:3px 4px;font-size:11px;min-width:0;display:flex;flex-direction:column;justify-content:center;gap:2px;border-left:1px solid var(--border-color);}
.mp-avail-row{display:flex;align-items:center;justify-content:space-between;gap:2px;min-width:0;}
.mp-avail-text{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.mp-bar{height:4px;border-radius:2px;background:var(--control-bg);overflow:hidden;}
.mp-bar i{display:block;height:100%;}
.mp-green .mp-bar i{background:#16a34a;}
.mp-amber .mp-bar i{background:#d97706;}
.mp-red .mp-bar i{background:#dc2626;}
.mp-red .mp-avail-text,.mp-red .mp-cap-text{color:#b91c1c;font-weight:600;}
.mp-offday{background:repeating-linear-gradient(135deg,transparent 0 6px,var(--control-bg) 6px 8px);color:var(--text-muted);}
.mp-conflict{box-shadow:inset 0 0 0 2px rgba(220,38,38,.55);}
.mp-mini{padding:0;min-height:22px;}
.mp-mini.mp-green{background:rgba(22,163,74,.22);}
.mp-mini.mp-amber{background:rgba(217,119,6,.28);}
.mp-mini.mp-red{background:rgba(220,38,38,.38);}
.mp-mini.mp-idle{background:transparent;}
.mp-mini[data-route-resource]{cursor:pointer;}
.mp-drive{display:flex;flex-wrap:wrap;align-items:center;gap:3px;font-size:10px;color:var(--text-muted);min-width:0;}
.mp-drive-text{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.mp-route{display:inline-flex;align-items:center;gap:3px;cursor:pointer;color:var(--text-muted);border:none;background:transparent;border-radius:6px;padding:0 3px;line-height:1;}
.mp-route:hover,.mp-route:focus{color:var(--primary,#2490ef);background:var(--control-bg);outline:none;}
.mp-route svg{width:12px;height:12px;flex:0 0 auto;}
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
.mp-day.mp-off-day,.mp-cell.mp-off-day{background:repeating-linear-gradient(135deg,transparent 0 6px,var(--control-bg) 6px 8px);}
.mp-off-label{font-size:11px;color:var(--text-muted);padding:0 2px;}
.mp-off-badges{display:flex;flex-wrap:wrap;gap:2px;padding:0 2px;}
.mp-day-count{font-size:11px;}
.mp-day.mp-over,.mp-cell.mp-over{outline:2px solid var(--primary,#2490ef);outline-offset:-2px;background:rgba(36,144,239,.08);}
.mp-day.mp-over-no,.mp-cell.mp-over-no{outline:2px dashed #dc2626;outline-offset:-2px;}
.mp-card.mp-over{outline:2px solid var(--primary,#2490ef);outline-offset:1px;}
.mp-card{position:relative;border:1px solid var(--border-color);border-left:4px solid #2563eb;border-radius:6px;background:var(--card-bg);padding:3px 6px;font-size:12px;line-height:1.3;cursor:pointer;min-width:0;}
.mp-card:hover,.mp-card:focus{box-shadow:0 1px 4px rgba(0,0,0,.18);outline:none;}
.mp-card.mp-movable{cursor:grab;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none;}
.mp-card-top{display:flex;align-items:center;gap:4px;}
.mp-card-title{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1 1 auto;min-width:0;}
.mp-card-sub{color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:11px;}
.mp-card-who{color:var(--text-muted);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.mp-card-who:empty{display:none;}
.mp-month .mp-card-who{display:none;}
.mp-tech{flex:0 0 auto;width:20px;height:20px;border-radius:50%;font-size:9px;font-weight:700;color:#fff;display:inline-flex;align-items:center;justify-content:center;}
.mp-tech.mp-tech-off{opacity:.45;background-image:repeating-linear-gradient(135deg,transparent 0 3px,rgba(255,255,255,.55) 3px 5px);}
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
.mp-card.mp-dragging,.mp-person.mp-dragging{opacity:.3;}
.mp-ghost{position:fixed;z-index:1100;pointer-events:none;box-shadow:0 10px 28px rgba(0,0,0,.28);transform:rotate(2deg);opacity:.96;margin:0;}
body.mp-drag-active,body.mp-drag-active *{cursor:grabbing !important;-webkit-user-select:none;user-select:none;}
.mp-loading .mp-grid,.mp-loading .mp-tray,.mp-loading .mp-crew,.mp-loading .mp-res-grid{opacity:.6;}
.mp-crew{display:grid;grid-template-columns:160px repeat(7,minmax(118px,1fr));border:1px solid var(--border-color);border-radius:10px;background:var(--card-bg);min-width:980px;}
.mp-crew > div{border-top:1px solid var(--border-color);}
.mp-crew-head{padding:6px 8px;font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em;border-left:1px solid var(--border-color);cursor:pointer;border-top:none !important;}
.mp-crew-head.mp-today{color:var(--primary,#2490ef);}
.mp-crew-corner{border-top:none !important;}
.mp-crew .mp-person{align-items:flex-start;flex-direction:column;gap:0;padding:6px 8px;}
.mp-cell{border-left:1px solid var(--border-color);padding:4px;display:flex;flex-direction:column;gap:3px;min-width:0;min-height:64px;}
.mp-cell.mp-past{background:rgba(0,0,0,.02);}
.mp-cap{display:flex;flex-direction:column;gap:2px;font-size:11px;}
.mp-cap-row{display:flex;align-items:center;justify-content:space-between;gap:2px;min-width:0;}
.mp-cap-text{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text-muted);}
.mp-cell .mp-card{font-size:11px;padding:2px 5px;}
.mp-hint{font-size:12px;color:var(--text-muted);margin-top:8px;}
.mp-summary{width:100%;font-size:13px;margin-bottom:6px;}
.mp-summary th{color:var(--text-muted);font-weight:normal;padding:3px 10px 3px 0;vertical-align:top;white-space:nowrap;width:1%;}
.mp-summary td{padding:3px 0;}
.mp-links{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 2px;}
.mp-why{margin:6px 0;}
.mp-why ul{margin:2px 0 0;padding-left:18px;}
.mp-empty-note{font-size:12px;color:var(--text-muted);padding:8px 10px;}
@media (max-width:760px){
.mp-day{min-height:84px;padding:2px;}
.mp-month .mp-card{padding:1px 3px;font-size:10px;border-left-width:3px;}
.mp-month .mp-card-sub,.mp-month .mp-tech{display:none;}
.mp-month .mp-off-badges{display:none;}
.mp-week .mp-grid{grid-template-columns:1fr;}
.mp-week .mp-day{min-height:0;border-right:none;}
.mp-tray .mp-card{width:100%;}
.mp-title{min-width:0;}
.mp-crew{grid-template-columns:110px repeat(7,minmax(104px,1fr));min-width:840px;}
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
		this.panel_open = this.load_pref(MP.panel_key, "1") !== "0";
		this.data = null;
		this.by_key = {};
		this.tech_by_user = {};
		this.tech_names = {};
		this.day_cards = {};
		// The newest `modified` seen for each visit: a save answers with one, and Undo needs it even
		// after the visit has moved out of the range on screen.
		this.modified = {};
		this.undo_stack = [];
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
		const view = MP.views.includes(route[1]) ? route[1] : "month";
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

	today() {
		return (this.data && this.data.today) || frappe.datetime.get_today();
	}

	first_weekday() {
		const name = (frappe.boot.sysdefaults && frappe.boot.sysdefaults.first_day_of_the_week) || "Sunday";
		const index = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"].indexOf(name);
		return index < 0 ? 0 : index;
	}

	// The week and crew views show the week holding the anchor; the month view, the six weeks around it.
	range() {
		const first = this.first_weekday();
		const anchor = moment(this.anchor, "YYYY-MM-DD");
		if (this.view !== "month") {
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

	range_days() {
		const { start, end } = this.range();
		const days = [];
		for (let day = start.clone(); !day.isAfter(end, "day"); day.add(1, "days")) days.push(mp_ymd(day));
		return days;
	}

	shift(sign) {
		const anchor = moment(this.anchor, "YYYY-MM-DD");
		const next =
			this.view === "month" ? anchor.startOf("month").add(sign, "months") : anchor.add(sign * 7, "days");
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
			["crew", __("Crew")],
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
		this.$undo = $('<button class="btn btn-default btn-sm mp-undo mp-empty"></button>')
			.text(__("Undo"))
			.on("click", () => this.undo())
			.appendTo($bar);
		$('<button class="btn btn-default btn-sm">↻</button>')
			.attr("title", __("Refresh"))
			.on("click", () => this.load())
			.appendTo($bar);
		this.update_undo_button();

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

		this.$panel = $('<div class="mp-section mp-res"></div>').appendTo(this.$body);
		this.$tray = $('<div class="mp-tray"></div>').hide().appendTo(this.$body);
		this.$grid_wrap = $("<div></div>").appendTo(this.$body);
		this.$hint = $('<div class="mp-hint"></div>').appendTo(this.$body);
	}

	// ------------------------------------------------------------------ data

	load() {
		const { start, end } = this.range();
		const token = ++this.request;
		this.$title.text(this.title());
		Object.entries(this.$view_buttons).forEach(([view, $button]) => $button.toggleClass("mp-on", view === this.view));
		this.$body.addClass("mp-loading");
		return Promise.resolve(
			frappe.call({ method: `${MP.api}.get_planner`, args: { start: mp_ymd(start), end: mp_ymd(end) } })
		)
			.then((r) => {
				if (token !== this.request) return;
				this.data = (r && r.message) || {};
				this.$body.removeClass("mp-loading");
				this.index();
				this.fill_technicians();
				this.render();
			})
			.catch(() => {
				if (token === this.request) this.$body.removeClass("mp-loading");
			});
	}

	index() {
		const data = this.data || {};
		this.tech_by_user = {};
		this.tech_names = {};
		(data.technicians || []).forEach((person) => {
			this.tech_by_user[person.user] = person;
			this.tech_names[person.user] = person.name;
		});
		[].concat(data.visits || [], data.unscheduled || []).forEach((card) => {
			if (card.modified) this.modified[card.name] = card.modified;
		});
	}

	fill_technicians() {
		const people = (this.data && this.data.technicians) || [];
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

	cell_of(user, ymd) {
		return (((this.data && this.data.bookings) || {})[user] || {})[ymd] || null;
	}

	// No hours that day: a holiday, time off, or a day their work pattern leaves out.
	is_off(cell) {
		return !!cell && !(Number(cell.capacity) > 0);
	}

	// The short name of a day off: "Holiday", "Time off" or "Not a work day".
	off_label(cell) {
		const off = String((cell && cell.off) || "");
		if (off.startsWith("Holiday")) return __("Holiday");
		if (off === "Time off") return __("Time off");
		return __("Not a work day");
	}

	// The availability of one technician on one day, as a class and a short text. `kind` is one of
	// off, over, full and free. `ratio` fills the capacity bar.
	avail_state(cell) {
		if (!cell) return { kind: "none", cls: "mp-idle", text: "", ratio: 0 };
		const capacity = Number(cell.capacity) || 0;
		const booked = Number(cell.booked) || 0;
		const over = booked - capacity;
		let state;
		if (capacity <= 0) {
			const label = this.off_label(cell);
			state =
				booked > 0.01
					? { kind: "off", cls: "mp-red mp-offday", text: __("Off, {0}h booked", [this.fmt_hours(booked)]), ratio: 1 }
					: { kind: "off", cls: "mp-offday", text: label, ratio: 0 };
		} else if (over > 0.01) {
			state = { kind: "over", cls: "mp-red", text: __("Over {0}h", [this.fmt_hours(over)]), ratio: 1 };
		} else {
			const ratio = booked / capacity;
			const free = Math.max(0, Number(cell.free != null ? cell.free : capacity - booked));
			state = {
				kind: free > 0.01 ? "free" : "full",
				cls: ratio <= 0.75 ? "mp-green" : "mp-amber",
				text: free > 0.01 ? __("{0}h free", [this.fmt_hours(free)]) : __("Full"),
				ratio,
			};
			if (cell.off) state.text += ` · ${__("½ day off")}`;
		}
		if ((cell.conflicts || []).length) state.cls += " mp-conflict";
		return state;
	}

	// "~1h 10m drive": the engine's drive time for the day. A "~" marks an estimate.
	drive_line(cell) {
		const minutes = Number(cell && cell.drive_minutes) || 0;
		if (minutes <= 0) return "";
		const approx = cell.drive_source === "estimate" || cell.drive_source === "mixed" ? "~" : "";
		return __("{0} drive", [approx + mp_drive(minutes)]);
	}

	drive_source_text(source) {
		if (source === "google") return __("Google");
		if (source === "mixed") return __("Google + estimate");
		return __("estimate");
	}

	// Does this technician-day have anything to drive to (so a route is worth opening)?
	has_route(user, ymd, cell) {
		if (!cell) return false;
		if (Number(cell.drive_minutes) > 0) return true;
		if ((this.day_cards[`${user}|${ymd}`] || []).length) return true;
		return (cell.items || []).some((item) => item.kind && item.kind !== "travel");
	}

	// The drive text, a Long drive chip, and the route icon of one technician-day. Clicking the icon
	// opens the Project Planner's route view for that person and day.
	drive_html(person, ymd, cell) {
		if (!person || !person.resource || !this.has_route(person.user, ymd, cell)) return "";
		const line = this.drive_line(cell);
		const tip = line
			? `${line} · ${this.drive_source_text(cell.drive_source)}`
			: __("Open this day's route");
		const long = cell.long_drive ? `<span class="mp-chip mp-red">${mp_esc(__("Long drive"))}</span>` : "";
		return `
			<span class="mp-drive">
				<button type="button" class="mp-route" data-route-resource="${mp_esc(person.resource)}"
					data-route-date="${mp_esc(ymd)}" title="${mp_esc(tip)}" aria-label="${mp_esc(
			__("Open the route for {0}", [mp_when(ymd)])
		)}">${MP_ROUTE_ICON}<span class="mp-drive-text">${mp_esc(line || __("Route"))}</span></button>${long}
			</span>`;
	}

	// The tooltip of one technician-day: hours, the day off, what is booked, drive and conflicts.
	cell_tip(user, ymd, cell) {
		const lines = [`${this.tech_name(user)} · ${mp_when(ymd)}`];
		if (!cell) return lines.join("\n");
		lines.push(
			__("{0}h of {1}h booked on projects, rentals, travel and visits", [
				this.fmt_hours(cell.booked),
				this.fmt_hours(cell.capacity),
			])
		);
		if (cell.off) lines.push(cell.off);
		(this.day_cards[`${user}|${ymd}`] || []).forEach((card) => {
			const what = card.kind === "projected" ? __("projected visit") : __("visit");
			lines.push(`• ${card.site || card.project || card.name} (${what})`);
		});
		(cell.items || []).forEach((item) => {
			const parts = [item.slot ? item.slot.join("–") : `${this.fmt_hours(item.hours)}h`];
			lines.push(`• ${item.label || item.ref} (${parts.join(", ")})`);
		});
		const drive = this.drive_line(cell);
		if (drive) lines.push(`${__("Driving")}: ${drive} (${this.drive_source_text(cell.drive_source)})`);
		if (cell.long_drive) lines.push(__("Long drive"));
		if (Number(cell.unlocated) > 0) {
			lines.push(__("{0} stop(s) with no location: not counted in the drive", [cell.unlocated]));
		}
		(cell.conflicts || []).forEach((text) => lines.push(`${__("Conflict")}: ${text}`));
		(cell.warnings || []).forEach((text) => lines.push(`${__("Note")}: ${text}`));
		return lines.join("\n");
	}

	// The day header's free-hours text for the selected technician, from the engine's numbers.
	free_info(user, ymd) {
		const cell = this.cell_of(user, ymd);
		if (!cell) return null;
		const state = this.avail_state(cell);
		const cls = { off: "mp-free-off", over: "mp-free-over", full: "mp-free-full" }[state.kind] || "";
		const lines = [
			__("{0}h of {1}h booked on projects, rentals, travel and visits", [
				this.fmt_hours(cell.booked),
				this.fmt_hours(cell.capacity),
			]),
		];
		if (cell.off) lines.push(cell.off);
		const drive = this.drive_line(cell);
		if (drive) lines.push(`${__("Driving")}: ${drive}`);
		(cell.conflicts || []).forEach((conflict) => lines.push(conflict));
		return { text: state.text, cls, title: lines.join("\n") };
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
		this.by_key = {};
		this.day_cards = {};
		const cards = this.all_cards();
		cards.forEach((card) => {
			this.by_key[card.key] = card;
			if ((card.kind === "visit" || card.kind === "projected") && card.technician && card.date) {
				(this.day_cards[`${card.technician}|${card.date}`] = this.day_cards[`${card.technician}|${card.date}`] || []).push(card);
			}
		});
		this.$tech.toggle(this.view !== "crew");
		this.render_panel();
		if (this.view === "crew") this.render_crew(cards);
		else this.render_calendar(cards);
		this.$hint.text(
			this.view === "crew"
				? __(
						"Drag a visit to another day on the same row to move it, or onto another technician's row to hand it over. On a touch screen, hold for a moment first. Dashed visits follow the technician on the site's Maintenance Profile, so they only change day."
				  )
				: __(
						"Drag a card to another day to move the visit. Drag a technician from Resources available onto a visit to assign them. On a touch screen, hold the card for a moment first. A dashed card with a blue edge is a contract's next visit: moving it moves the contract's next visit date, and the dashed cards after it follow. Teal, amber and gray cards are project, rental and travel bookings of the same people: they open the task but are moved on the Project Planner."
				  )
		);
	}

	render_calendar(cards) {
		const { start, end } = this.range();
		const today = this.today();
		const month = moment(this.anchor, "YYYY-MM-DD").month();
		const by_day = {};
		const unscheduled = [];
		cards.forEach((card) => {
			if (!this.visible(card)) return;
			if (!card.date) unscheduled.push(card);
			else (by_day[card.date] = by_day[card.date] || []).push(card);
		});

		this.render_tray(unscheduled);

		const single = !!this.technician && this.technician !== "__none__";
		const everyone = !this.technician;
		const planned = (this.data.technicians || []).filter((person) => person.enabled && person.resource);
		const $grid = $('<div class="mp-grid"></div>');
		const first = this.first_weekday();
		for (let i = 0; i < 7; i++) {
			$('<div class="mp-dow"></div>')
				.text(moment().day((first + i) % 7).format("ddd"))
				.appendTo($grid);
		}
		for (let day = start.clone(); !day.isAfter(end, "day"); day.add(1, "days")) {
			const ymd = mp_ymd(day);
			const day_cards = (by_day[ymd] || []).sort((a, b) => this.compare(a, b));
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
			const cell = single ? this.cell_of(this.technician, ymd) : null;
			const free = single ? this.free_info(this.technician, ymd) : null;
			if (free) {
				$('<span class="mp-day-free"></span>')
					.addClass(free.cls)
					.text(free.text)
					.attr("title", free.title)
					.appendTo($head);
			}
			if (cell && cell.long_drive) {
				$(`<span class="mp-chip mp-red"></span>`)
					.text(__("Long drive"))
					.attr("title", this.drive_line(cell))
					.appendTo($head);
			}
			const visit_count = day_cards.filter((card) => card.kind !== "booking").length;
			if (visit_count > 1) {
				$('<span class="mp-day-count"></span>').text(__("{0} visits", [visit_count])).appendTo($head);
			}
			// Days off: shaded and labeled for one technician; a badge for each person off for All.
			if (this.is_off(cell)) {
				$day.addClass("mp-off-day");
				$('<div class="mp-off-label"></div>')
					.text(this.off_label(cell))
					.attr("title", cell.off || "")
					.appendTo($day);
			} else if (everyone) {
				this.append_off_badges($day, planned, ymd);
			}
			day_cards.forEach((card) => $day.append(this.card_html(card)));
		}
		this.$grid_wrap.empty().removeClass("mp-month mp-week mp-crew-view").addClass(`mp-${this.view}`).append($grid);
	}

	// Everyone with hours on the planner who is off on `ymd`, as small badges; one label when nobody works.
	append_off_badges($day, planned, ymd) {
		const off = planned.filter((person) => this.is_off(this.cell_of(person.user, ymd)));
		if (!off.length) return;
		if (planned.length > 1 && off.length === planned.length) {
			$day.addClass("mp-off-day");
			$('<div class="mp-off-label"></div>').text(__("Everyone off")).appendTo($day);
			return;
		}
		const $row = $('<div class="mp-off-badges"></div>').appendTo($day);
		off.forEach((person) => {
			const cell = this.cell_of(person.user, ymd);
			const badge = $(this.tech_badge(person.user, "mp-tech-off")).attr(
				"title",
				`${person.name}: ${this.off_label(cell)}`
			);
			badge.appendTo($row);
		});
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

	// ------------------------------------------------------------------ resources panel

	tech_color(user) {
		const person = this.tech_by_user[user];
		const own = person && mp_color(person.color, "");
		if (own) return own;
		let hash = 0;
		for (const ch of String(user || "")) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
		return `hsl(${hash},55%,42%)`;
	}

	// A technician's name chip. Dragging it onto a visit card assigns them to the visit.
	person_html(person, compact) {
		const group = person.group ? __(person.group) : "";
		const tip = this.data.can_move_visits
			? __("Drag {0} onto a visit to assign them", [person.name])
			: person.name;
		const selected = this.technician === person.user ? " mp-selected" : "";
		return `
			<div class="mp-person${selected}" data-user="${mp_esc(person.user)}" title="${mp_esc(tip)}">
				${compact ? "" : `<span class="mp-dot" style="background:${mp_esc(this.tech_color(person.user))}"></span>`}
				<span class="mp-person-name">${mp_esc(person.name)}</span>
				<span class="mp-person-group">${mp_esc(group)}</span>
			</div>`;
	}

	render_panel() {
		this.$panel.toggle(this.view !== "crew").empty();
		if (this.view === "crew") return;
		const people = (this.data.technicians || []).filter((person) => person.enabled);
		const $head = $('<div class="mp-section-head"></div>').appendTo(this.$panel);
		$("<span></span>").text(__("Resources available")).appendTo($head);
		$('<span class="mp-spacer"></span>').appendTo($head);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(this.panel_open ? __("Hide") : __("Show"))
			.on("click", () => {
				this.panel_open = !this.panel_open;
				this.save_pref(MP.panel_key, this.panel_open ? "1" : "0");
				this.render_panel();
			})
			.appendTo($head);
		if (!this.panel_open) return;
		if (!people.length) {
			$('<div class="mp-empty-note"></div>')
				.text(__("No technicians yet. Technicians are active employees with a Technician designation."))
				.appendTo(this.$panel);
			return;
		}
		const days = this.range_days();
		const today = this.today();
		const month = this.view === "month";
		const columns = month ? `150px repeat(${days.length},minmax(20px,1fr))` : "170px repeat(7,minmax(74px,1fr))";
		const html = [`<div class="mp-res-head"></div>`];
		days.forEach((ymd) => {
			const m = moment(ymd, "YYYY-MM-DD");
			const label = month ? m.format("D") : m.format("ddd D");
			html.push(`<div class="mp-res-head${ymd === today ? " mp-today" : ""}">${mp_esc(label)}</div>`);
		});
		people.forEach((person) => {
			html.push(this.person_html(person, false));
			days.forEach((ymd) => {
				const cell = this.cell_of(person.user, ymd);
				const state = this.avail_state(cell);
				const tip = this.cell_tip(person.user, ymd, cell);
				if (month) {
					// Too small for an icon: the whole mini cell opens the route when there is one.
					const route =
						person.resource && this.has_route(person.user, ymd, cell)
							? ` data-route-resource="${mp_esc(person.resource)}" data-route-date="${mp_esc(ymd)}"`
							: "";
					html.push(`<div class="mp-avail mp-mini ${state.cls}"${route} title="${mp_esc(tip)}"></div>`);
					return;
				}
				const width = Math.round(Math.min(1, state.ratio) * 100);
				html.push(`
					<div class="mp-avail ${state.cls}" title="${mp_esc(tip)}">
						<span class="mp-avail-text">${mp_esc(state.text)}</span>
						<div class="mp-bar"><i style="width:${width}%"></i></div>
						${this.drive_html(person, ymd, cell)}
					</div>`);
			});
		});
		const $scroll = $('<div class="mp-scroll"></div>').appendTo(this.$panel);
		$('<div class="mp-res-grid"></div>')
			.css("grid-template-columns", columns)
			.css("min-width", month ? `${150 + days.length * 22}px` : "700px")
			.html(html.join(""))
			.appendTo($scroll);
	}

	// ------------------------------------------------------------------ crew view

	render_crew(cards) {
		const days = this.range_days();
		const today = this.today();
		const by_cell = {};
		const unscheduled = [];
		cards.forEach((card) => {
			if (card.kind === "booking" && !this.show_project_work) return;
			if (card.kind === "projected" && !this.show_projected) return;
			if (!card.date) {
				if (card.kind === "visit") unscheduled.push(card);
				return;
			}
			const key = `${card.technician || ""}|${card.date}`;
			(by_cell[key] = by_cell[key] || []).push(card);
		});
		this.render_tray(unscheduled);

		// Every technician, plus a disabled one who still has visits this week, plus Unassigned when
		// any visit has nobody. The technician filter is for the calendar: here every row is a drop target.
		const people = (this.data.technicians || []).filter(
			(person) => person.enabled || days.some((ymd) => (by_cell[`${person.user}|${ymd}`] || []).length)
		);
		const rows = people.map((person) => ({ person, user: person.user }));
		if (days.some((ymd) => (by_cell[`|${ymd}`] || []).length)) rows.push({ person: null, user: "" });

		const html = [`<div class="mp-crew-corner"></div>`];
		days.forEach((ymd) => {
			html.push(
				`<div class="mp-crew-head${ymd === today ? " mp-today" : ""}" data-week="${mp_esc(ymd)}" title="${mp_esc(
					__("Show this week")
				)}">${mp_esc(mp_when(ymd))}</div>`
			);
		});
		rows.forEach(({ person, user }) => {
			html.push(
				person
					? this.person_html(person, true)
					: `<div class="mp-person" style="cursor:default"><span class="mp-person-name">${mp_esc(__("Unassigned"))}</span></div>`
			);
			days.forEach((ymd) => {
				const cell = person ? this.cell_of(user, ymd) : null;
				const state = this.avail_state(cell);
				const width = Math.round(Math.min(1, state.ratio) * 100);
				const list = (by_cell[`${user}|${ymd}`] || []).sort((a, b) => this.compare(a, b));
				const cap = person
					? `<div class="mp-cap ${state.cls}" title="${mp_esc(this.cell_tip(user, ymd, cell))}">
							<span class="mp-cap-text">${mp_esc(state.text)}</span>
							<div class="mp-bar"><i style="width:${width}%"></i></div>
							${this.drive_html(person, ymd, cell)}
						</div>`
					: "";
				html.push(`
					<div class="mp-cell${ymd < today ? " mp-past" : ""}${this.is_off(cell) ? " mp-off-day" : ""}"
						data-date="${mp_esc(ymd)}" data-user="${mp_esc(user)}">
						${cap}
						${list.map((card) => this.card_html(card)).join("")}
					</div>`);
			});
		});
		this.$grid_wrap.empty().removeClass("mp-month mp-week").addClass("mp-crew-view");
		if (!rows.length) {
			$('<div class="mp-section mp-empty-note"></div>')
				.text(__("No technicians to show."))
				.appendTo(this.$grid_wrap);
			return;
		}
		const $scroll = $('<div class="mp-scroll"></div>').appendTo(this.$grid_wrap);
		const $crew = $('<div class="mp-crew"></div>').html(html.join("")).appendTo($scroll);
		$crew.find("[data-week]").on("click", (e) => this.go("week", e.currentTarget.getAttribute("data-week")));
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
		// In a technician's row the row says who it is.
		const badge = this.technician || this.view === "crew" ? "" : this.tech_badge(card.technician);
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
		// A visit on a day its technician does not work still gets done by somebody: say so.
		const day_cell =
			card.technician && card.date && card.status !== "done" && card.status !== "pending"
				? this.cell_of(card.technician, card.date)
				: null;
		if (this.is_off(day_cell)) {
			chips.push(
				`<span class="mp-chip mp-red" title="${mp_esc(day_cell.off || "")}">${mp_esc(__("Day off"))}: ${mp_esc(
					this.off_label(day_cell)
				)}</span>`
			);
		}

		// In a technician's row the row says who it is.
		const in_row = this.view === "crew" && !!card.date;
		const who = card.technician ? this.tech_name(card.technician) : __("Unassigned");
		const tip = [card.site_full, sub.join(" · "), who, day_cell && this.is_off(day_cell) ? this.off_label(day_cell) : ""]
			.filter(Boolean)
			.join("\n");
		return `
			<div class="mp-card ${this.kind_class(card)}" data-key="${mp_esc(card.key)}" tabindex="0" title="${mp_esc(tip)}">
				<div class="mp-card-top">
					<span class="mp-card-title">${mp_esc(card.site || card.project || card.name)}</span>
					${in_row ? "" : this.tech_badge(card.technician)}
				</div>
				<div class="mp-card-sub">${mp_esc(sub.join(" · "))}</div>
				<div class="mp-card-who">${in_row ? "" : mp_esc(who)} ${chips
					.map((chip) => (chip.startsWith("<span") ? chip : `<span class="mp-chip">${mp_esc(chip)}</span>`))
					.join("")}</div>
			</div>`;
	}

	tech_name(user) {
		return (this.tech_names && this.tech_names[user]) || user;
	}

	tech_badge(user, extra) {
		if (!user) return "";
		const name = this.tech_name(user);
		const parts = String(name).split(/\s+/).filter(Boolean);
		const initials = (parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "");
		return `<span class="mp-tech${extra ? ` ${mp_esc(extra)}` : ""}" style="background:${mp_esc(
			this.tech_color(user)
		)}" title="${mp_esc(name)}">${mp_esc(initials.toUpperCase())}</span>`;
	}

	// ------------------------------------------------------------------ dragging

	bind_drag() {
		const root = this.$body[0];
		root.addEventListener("pointerdown", (e) => this.on_down(e));
		document.addEventListener("pointermove", (e) => this.on_move(e));
		document.addEventListener("pointerup", (e) => this.on_up(e));
		document.addEventListener("pointercancel", (e) => this.on_cancel(e));
		document.addEventListener("keydown", (e) => {
			if (e.key === "Escape" && this.drag) this.end_drag();
		});
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
			if (Date.now() < this.click_blocked_until) return;
			if (this.open_route(e.target)) return;
			const card_el = e.target.closest(".mp-card");
			if (!card_el) return;
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

	// The route icon (or a month-panel mini cell) of a technician-day: the Project Planner's route view.
	open_route(target) {
		const el = target && target.closest && target.closest("[data-route-resource]");
		if (!el) return false;
		const resource = el.getAttribute("data-route-resource");
		const ymd = el.getAttribute("data-route-date");
		if (!resource || !ymd) return false;
		frappe.set_route(MP.project_route, "route", resource, ymd);
		return true;
	}

	// What is under the pointer when it went down: a visit card, or a technician.
	drag_source(el) {
		if (!this.data) return null;
		const person_el = el.closest(".mp-person[data-user]");
		if (person_el) {
			if (!this.data.can_move_visits) return null;
			return { kind: "person", el: person_el, user: person_el.getAttribute("data-user") };
		}
		const card_el = el.closest(".mp-card");
		if (!card_el) return null;
		const card = this.by_key[card_el.getAttribute("data-key")];
		if (!card || !card.movable || card.saving) return null;
		return { kind: "card", el: card_el, card };
	}

	on_down(e) {
		if (e.button > 0 || this.drag || !e.target.closest) return;
		const source = this.drag_source(e.target);
		if (!source) return;
		this.drag = { source, el: source.el, x: e.clientX, y: e.clientY, id: e.pointerId, touch: e.pointerType === "touch" };
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
		this.mark_target(this.find_target(e.clientX, e.clientY));
		this.edge_scroll(e.clientX, e.clientY);
	}

	// Near the top or bottom of the window, scroll so a far week is reachable; near the side of
	// a sideways-scrolling grid (the crew view on a phone), scroll that.
	edge_scroll(x, y) {
		const edge = 56;
		if (y < edge) window.scrollBy(0, -14);
		else if (y > window.innerHeight - edge) window.scrollBy(0, 14);
		const el = document.elementFromPoint(x, y);
		const scroller = el && el.closest(".mp-scroll");
		if (!scroller) return;
		const rect = scroller.getBoundingClientRect();
		if (x < rect.left + 40) scroller.scrollLeft -= 14;
		else if (x > rect.right - 40) scroller.scrollLeft += 14;
	}

	on_up(e) {
		const drag = this.drag;
		if (!drag || e.pointerId !== drag.id) return;
		const was_active = drag.active;
		const target = was_active ? this.find_target(e.clientX, e.clientY) : null;
		this.end_drag();
		if (!was_active) return;
		this.click_blocked_until = Date.now() + 400;
		if (target) this.drop(drag.source, target);
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
		drag.ghost.style.width = `${Math.max(rect.width, 120)}px`;
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

	// Where a drag would land. A technician lands on a visit card; a visit lands on a crew-view cell
	// (a technician and a day) or a calendar day.
	find_target(x, y) {
		const drag = this.drag;
		const el = document.elementFromPoint(x, y);
		if (!drag || !el || !el.closest || !this.$body[0].contains(el)) return null;
		if (drag.source.kind === "person") {
			const card_el = el.closest(".mp-card[data-key]");
			return card_el ? { el: card_el, key: card_el.getAttribute("data-key") } : null;
		}
		const cell = el.closest(".mp-cell[data-date]");
		if (cell) return { el: cell, date: cell.getAttribute("data-date"), user: cell.getAttribute("data-user") || "", row: true };
		const day = el.closest(".mp-grid .mp-day[data-date]");
		if (day) return { el: day, date: day.getAttribute("data-date") };
		return null;
	}

	mark_target(target) {
		const drag = this.drag;
		if (!drag) return;
		if (drag.target_el && (!target || drag.target_el !== target.el)) {
			drag.target_el.classList.remove("mp-over", "mp-over-no");
		}
		drag.target_el = target ? target.el : null;
		if (!target) return;
		target.el.classList.add(target.date && target.date < this.today() ? "mp-over-no" : "mp-over");
	}

	end_drag() {
		const drag = this.drag;
		this.drag = null;
		if (!drag) return;
		clearTimeout(drag.timer);
		if (drag.ghost) drag.ghost.remove();
		if (drag.target_el) drag.target_el.classList.remove("mp-over", "mp-over-no");
		drag.el.classList.remove("mp-dragging");
		document.body.classList.remove("mp-drag-active");
	}

	// ------------------------------------------------------------------ moving

	site_of(card) {
		return card.site || card.site_full || card.name;
	}

	// Work out what a drop means. `{ card, change }` is a move to send (`change` holds a date and/or
	// a technician), `{ refuse }` a reason it cannot be done, and null changes nothing.
	plan_drop(source, target) {
		if (source.kind === "person") {
			const card = this.by_key[target.key];
			const name = this.tech_name(source.user);
			if (!card || card.kind === "booking") return { refuse: __("Drop {0} onto a visit.", [name]) };
			const site = this.site_of(card);
			if (card.kind === "projected") {
				return {
					refuse: __(
						"{0} is a projected visit: it follows the technician on the site's Maintenance Profile, so it cannot be handed to {1} here. It can once the visit is drafted.",
						[site, name]
					),
				};
			}
			if (!card.movable) {
				return { refuse: __("{0} is started or finished, so it keeps its technician.", [site]) };
			}
			if ((card.technician || "") === source.user) return { refuse: __("{0} is already on {1}.", [name, site]) };
			return { card, change: { technician: source.user } };
		}

		const card = source.card;
		if (target.date !== card.date && target.date < this.today()) {
			return { refuse: __("Visits can only be moved to today or a later day.") };
		}
		const change = {};
		if (target.date !== card.date) change.date = target.date;
		if (target.row && (target.user || "") !== (card.technician || "")) {
			if (card.kind === "projected") {
				return {
					refuse: __(
						"{0} is a projected visit: it follows the technician on the site's Maintenance Profile, so it cannot move to another technician's row. Drop it on a day of its own row to change the date.",
						[this.site_of(card)]
					),
				};
			}
			change.technician = target.user;
		}
		return Object.keys(change).length ? { card, change } : null;
	}

	drop(source, target) {
		const plan = this.plan_drop(source, target);
		if (!plan) return;
		if (plan.refuse) {
			frappe.show_alert({ message: plan.refuse, indicator: "orange" }, 6);
			return;
		}
		this.apply(plan.card, plan.change);
	}

	// Show the card on its new day straight away; the reload afterwards is the truth.
	apply(card, change) {
		const before = { date: card.date || null, technician: card.technician || "" };
		if (change.date) card.date = change.date;
		if (change.technician !== undefined) card.technician = change.technician || null;
		card.saving = true;
		this.render();
		return this.save_move(card, change, before).finally(() => this.load());
	}

	// Resolves either way: a refusal has already shown the server's own message.
	save_move(card, change, before) {
		const site = this.site_of(card);
		const visit = card.kind === "visit";
		const args = visit
			? Object.assign({ record: card.name, modified: this.modified[card.name] || card.modified }, change)
			: {
					contract: card.contract,
					from_date: card.from_date,
					to_date: change.date,
					serial_no: card.serial_no || null,
			  };
		const snapshot = visit
			? { kind: "visit", site, record: card.name, date: before.date, technician: before.technician }
			: {
					kind: "projected",
					site,
					contract: card.contract,
					serial_no: card.serial_no || null,
					from_date: change.date,
					to_date: before.date,
			  };
		let message;
		if (change.date && change.technician !== undefined) {
			message = __("{0} moved to {1}, assigned to {2}", [
				site,
				mp_when(change.date),
				change.technician ? this.tech_name(change.technician) : __("nobody"),
			]);
		} else if (change.date) {
			message = __("{0} moved to {1}", [site, mp_when(change.date)]);
		} else if (change.technician !== undefined) {
			message = change.technician
				? __("{0} assigned to {1}", [site, this.tech_name(change.technician)])
				: __("{0} is now unassigned", [site]);
		} else {
			message = __("{0} updated", [site]);
		}
		return this.send(visit ? "move_visit" : "move_projected", args, {
			snapshot,
			message,
			noun: visit ? "visit" : "contract",
		});
	}

	// Send one call. When the server answers needs_reason, ask why and send it again with the reason.
	// Resolves with the result, or null when it was refused, backed out of or failed.
	send(method, args, opts) {
		opts = opts || {};
		return Promise.resolve(frappe.call({ method: MP.methods[method], args }))
			.then((r) => {
				const result = (r && r.message) || {};
				if (result.needs_reason) {
					if (args.reason) {
						// The server asked again although a reason was sent: stop rather than loop.
						frappe.msgprint(__("The change was not saved. Refresh the planner and try again."));
						return null;
					}
					const ask = opts.auto_reason
						? Promise.resolve(opts.auto_reason)
						: this.ask_reason(result.conflicts || {}, opts.noun);
					return ask.then((reason) =>
						reason ? this.send(method, Object.assign({}, args, { reason }), opts) : null
					);
				}
				if (opts.snapshot) {
					// A projected visit moved into the drafting window becomes a record at once, and its
					// own undo is moving that record, which the page cannot yet name.
					this.push_undo(
						result.drafted && opts.snapshot.kind === "projected"
							? { kind: "drafted", site: opts.snapshot.site }
							: opts.snapshot
					);
				}
				if (result.name && result.modified) this.modified[result.name] = result.modified;
				if (opts.message) {
					let message = opts.message;
					if (result.drafted) message += ". " + __("The visit is drafted and on the technician's list.");
					frappe.show_alert({ message, indicator: "green" }, 5);
				}
				(result.warnings || []).forEach((warning) =>
					frappe.show_alert({ message: warning, indicator: "orange" }, 10)
				);
				return result;
			})
			.catch(() => null);
	}

	// The conflicts a move would cause, per technician, and a required reason. Resolves with the
	// reason, or null when the planner backs out.
	ask_reason(conflicts, noun) {
		return new Promise((resolve) => {
			let settled = false;
			const settle = (value) => {
				if (settled) return;
				settled = true;
				resolve(value);
			};
			const people = Object.entries(conflicts || {})
				.map(
					([who, list]) =>
						`<div class="mp-why"><b>${mp_esc(who)}</b><ul>${(list || [])
							.map((text) => `<li>${mp_esc(text)}</li>`)
							.join("")}</ul></div>`
				)
				.join("");
			const where = noun === "contract" ? __("the contract's timeline") : __("the visit's timeline");
			const dialog = new frappe.ui.Dialog({
				title: __("This booking has conflicts"),
				fields: [
					{
						fieldtype: "HTML",
						fieldname: "conflicts",
						options: `<p>${mp_esc(
							__("You can save it anyway. Say why: the reason is added to {0}.", [where])
						)}</p>${people}`,
					},
					{ fieldtype: "Small Text", fieldname: "reason", label: __("Reason"), reqd: 1 },
				],
				primary_action_label: __("Save anyway"),
				primary_action: (values) => {
					const reason = String((values && values.reason) || "").trim();
					if (!reason) return;
					settle(reason);
					dialog.hide();
				},
			});
			dialog.onhide = () => settle(null);
			dialog.show();
		});
	}

	// ------------------------------------------------------------------ undo

	push_undo(snapshot) {
		this.undo_stack.push(snapshot);
		while (this.undo_stack.length > MP.undo_max) this.undo_stack.shift();
		this.update_undo_button();
	}

	update_undo_button() {
		if (!this.$undo) return;
		const last = this.undo_stack[this.undo_stack.length - 1];
		this.$undo
			.toggleClass("mp-empty", !last)
			.attr("title", last ? __("Undo the last change to {0}", [last.site]) : __("Nothing to undo"));
	}

	// Put the visit back the way it was before the last change: a visit goes back through move_visit
	// (its date and technician), a projected visit through move_projected. A date already in the past
	// cannot be returned to, and a visit cannot be unscheduled, so those stay and the planner says so.
	undo() {
		const snap = this.undo_stack.pop();
		this.update_undo_button();
		if (!snap) {
			frappe.show_alert({ message: __("Nothing to undo"), indicator: "blue" }, 4);
			return;
		}
		const today = this.today();
		const reason = __(MP.undo_reason);
		if (snap.kind === "drafted") {
			frappe.show_alert(
				{
					message: __("{0} was drafted when it moved: move the visit card to change it.", [snap.site]),
					indicator: "orange",
				},
				8
			);
			return;
		}
		if (snap.kind === "projected") {
			if (!snap.to_date || snap.to_date < today) {
				frappe.show_alert(
					{ message: __("{0} was on a day that has passed, so it stays where it is.", [snap.site]), indicator: "orange" },
					8
				);
				return;
			}
			this.send(
				"move_projected",
				{
					contract: snap.contract,
					from_date: snap.from_date,
					to_date: snap.to_date,
					serial_no: snap.serial_no || null,
				},
				{ message: __("Undone: {0}", [snap.site]), auto_reason: reason, noun: "contract" }
			).finally(() => this.load());
			return;
		}
		const card = this.by_key[snap.record];
		const args = {
			record: snap.record,
			modified: this.modified[snap.record] || (card && card.modified) || "",
			technician: snap.technician || "",
		};
		const date_back = !!(snap.date && snap.date >= today);
		if (date_back) args.date = snap.date;
		if (card) {
			card.saving = true;
			this.render();
		}
		this.send("move_visit", args, {
			message: __("Undone: {0}", [snap.site]),
			auto_reason: reason,
			noun: "visit",
		})
			.then((result) => {
				if (result && !date_back && card && card.date !== snap.date) {
					frappe.show_alert(
						{
							message: __("{0} keeps its day: a visit cannot go back to a day that has passed or to unscheduled.", [
								snap.site,
							]),
							indicator: "orange",
						},
						10
					);
				}
			})
			.finally(() => this.load());
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
		// How that technician's day looks: free hours, a day off, anything wrong with it.
		const cell = card.technician && card.date ? this.cell_of(card.technician, card.date) : null;
		if (cell) {
			add(__("Their day"), this.avail_state(cell).text);
			(cell.conflicts || []).forEach((text) => add(__("Conflict"), text));
		}
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
				const today = this.today();
				if (change.date && change.date < today) {
					frappe.msgprint(__("Pick today or a later day."));
					return;
				}
				if (card.kind === "projected" && !change.date) {
					dialog.hide();
					return;
				}
				dialog.hide();
				this.apply(card, change);
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
