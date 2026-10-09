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
//   visit      a Sapphire Maintenance Record. It carries `technician` (the lead, who fills it in),
//              `crew` ([{user, name, hours}]: the others booked on it), `planned_hours`, `full_day` and
//              `hours` (the length each person is booked for). A draft nobody has started can be dragged; that
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
//     visit card                                  add_crew (the visit's crew grows); a visit
//                                                 with nobody on it gets them as technician
//   - a multi-person visit                        shows in every crew member's row; dragging
//                                                 one person's chip to another row moves only
//                                                 that person (move_visit with from_user when
//                                                 the dragged person is a helper)
//   - the card's dialog                           technician, crew (with optional hours each),
//                                                 planned hours and Full day
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
	// Adding a helper to a visit's crew is its own POST: dropping a chip on a visit sends it.
	add_crew: `${MP.api}.add_crew`,
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
.mp-techs{flex:0 0 auto;display:inline-flex;align-items:center;gap:3px;}
.mp-tech.mp-lead{box-shadow:0 0 0 1.5px var(--card-bg),0 0 0 3px var(--text-color);margin-right:2px;}
.mp-more{font-size:10px;color:var(--text-muted);}
.mp-cm-list{display:flex;flex-direction:column;gap:4px;max-height:220px;overflow:auto;margin-bottom:6px;}
.mp-cm-row{display:flex;align-items:center;gap:8px;}
.mp-cm-row label{flex:1 1 auto;margin:0;font-weight:normal;display:flex;align-items:center;gap:6px;}
.mp-cm-row input[type=number]{width:84px;flex:0 0 auto;}
.mp-cm-row.mp-cm-lead{opacity:.5;}
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
.mp-month .mp-card-sub,.mp-month .mp-tech,.mp-month .mp-techs{display:none;}
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
		this.init_phase6a();
		this.init_phase6b();
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
		this.p6a_route(() => frappe.set_route(MP.route, view, anchor), true);
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
		[].concat(data.visits || [], data.unscheduled || [], data.projected || []).forEach((card) => {
			if (card.modified) this.modified[card.name] = card.modified;
			// A helper who is not on the technician list still has a name on the card.
			(card.crew || []).forEach((member) => {
				if (member && member.user && member.name && !this.tech_names[member.user]) {
					this.tech_names[member.user] = member.name;
				}
			});
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
		return this.visit_people(card).includes(this.technician);
	}

	// Everyone booked on a visit: the technician (the lead) first, then the crew, each once.
	visit_people(card) {
		const out = [];
		const add = (user) => {
			if (user && !out.includes(user)) out.push(user);
		};
		add(card.technician);
		(card.crew || []).forEach((member) => add(member && member.user));
		return out;
	}

	crew_has(card, user) {
		return !!user && (card.crew || []).some((member) => member && member.user === user);
	}

	// The crew as move_visit takes it: the users besides the lead, with their own hours when set.
	crew_rows(card) {
		return (card.crew || [])
			.filter((member) => member && member.user && member.user !== card.technician)
			.map((member) => ({ user: member.user, hours: Number(member.hours) > 0 ? Number(member.hours) : null }));
	}

	// Order does not matter to the crew, so compare it sorted.
	crew_key(rows) {
		return JSON.stringify(
			(rows || [])
				.map((row) => [row.user, row.hours == null ? null : Number(row.hours)])
				.sort((a, b) => String(a[0]).localeCompare(String(b[0])))
		);
	}

	// ------------------------------------------------------------------ render

	render() {
		if (!this.data) return;
		this.by_key = {};
		this.day_cards = {};
		const cards = this.all_cards();
		cards.forEach((card) => {
			this.by_key[card.key] = card;
			if ((card.kind === "visit" || card.kind === "projected") && card.date) {
				// A multi-person visit is on every crew member's day.
				this.visit_people(card).forEach((user) => {
					(this.day_cards[`${user}|${card.date}`] = this.day_cards[`${user}|${card.date}`] || []).push(card);
				});
			}
		});
		this.$tech.toggle(this.view !== "crew");
		this.render_panel();
		if (this.view === "crew") this.render_crew(cards);
		else this.render_calendar(cards);
		this.$hint.text(
			this.view === "crew"
				? __(
						"Drag a visit to another day on the same row to move it, or onto another technician's row to hand it over. On a touch screen, hold for a moment first. A visit with a crew shows in every crew member's row: drag one person's card to another row to swap only that person. Drag a technician onto a visit to add them to its crew. Dashed visits follow the technician on the site's Maintenance Profile, so they only change day."
				  )
				: __(
						"Drag a card to another day to move the visit. Drag a technician from Resources available onto a visit to add them to its crew (a visit with nobody on it gets them as its technician). The ringed initials are the technician who fills the visit in. On a touch screen, hold the card for a moment first. A dashed card with a blue edge is a contract's next visit: moving it moves the contract's next visit date, and the dashed cards after it follow. Teal, amber and gray cards are project, rental and travel bookings of the same people: they open the task but are moved on the Project Planner."
				  )
		);
		this.render_phase6a();
		this.render_phase6b();
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
			? __("Drag {0} onto a visit to add them to its crew", [person.name])
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
			// A multi-person visit sits in every crew member's row.
			const owners = card.kind === "booking" ? [card.technician || ""] : this.visit_people(card);
			(owners.length ? owners : [""]).forEach((user) => {
				const key = `${user}|${card.date}`;
				(by_cell[key] = by_cell[key] || []).push(card);
			});
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
						${list.map((card) => this.card_row_html(card, user)).join("")}
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
		return this.card_row_html(card, undefined);
	}

	// `row_user` is the crew-view row the card sits in: the same visit shows in every crew member's
	// row, and a drag needs to know which person's chip was picked up. The calendar passes none.
	card_row_html(card, row_user) {
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
		// How long it books each person, when that is not just the default.
		const people = this.visit_people(card);
		if (card.full_day) chips.push(__("Full day"));
		else if (Number(card.planned_hours) > 0 || (people.length > 1 && Number(card.hours) > 0)) {
			chips.push(__("{0}h each", [mp_hours(Number(card.planned_hours) > 0 ? card.planned_hours : card.hours)]));
		}
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
		const who = people.length ? people.map((user) => this.tech_name(user)).join(", ") : __("Unassigned");
		const crew_line = people.length > 1 ? `${__("Crew")}: ${who}` : "";
		const tip = [
			card.site_full,
			sub.join(" · "),
			crew_line || who,
			day_cell && this.is_off(day_cell) ? this.off_label(day_cell) : "",
		]
			.filter(Boolean)
			.join("\n");
		const row_attr = row_user === undefined ? "" : ` data-row-user="${mp_esc(row_user)}"`;
		return `
			<div class="mp-card ${this.kind_class(card)}" data-key="${mp_esc(card.key)}"${row_attr} tabindex="0" title="${mp_esc(tip)}">
				<div class="mp-card-top">
					<span class="mp-card-title">${mp_esc(card.site || card.project || card.name)}</span>
					${this.people_badges(card, in_row)}
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

	// The initials of everyone on a visit, the lead first and ringed when there is a crew. In a crew-view
	// row a lone person is the row, so only a multi-person visit shows them.
	people_badges(card, in_row) {
		const people = this.visit_people(card);
		if (!people.length || (in_row && people.length < 2)) return "";
		const shown = people.slice(0, 4);
		const badges = shown
			.map((user) => {
				const lead = people.length > 1 && user === card.technician;
				return this.tech_badge(user, lead ? "mp-lead" : "", lead ? __("Technician") : "");
			})
			.join("");
		const more = people.length > shown.length ? `<span class="mp-more">+${people.length - shown.length}</span>` : "";
		return `<span class="mp-techs">${badges}${more}</span>`;
	}

	tech_badge(user, extra, role) {
		if (!user) return "";
		const name = this.tech_name(user);
		const parts = String(name).split(/\s+/).filter(Boolean);
		const initials = (parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "");
		return `<span class="mp-tech${extra ? ` ${mp_esc(extra)}` : ""}" style="background:${mp_esc(
			this.tech_color(user)
		)}" title="${mp_esc(role ? `${name} (${role})` : name)}">${mp_esc(initials.toUpperCase())}</span>`;
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
		this.p6a_route(() => frappe.set_route(MP.project_route, "route", resource, ymd), false);
		return true;
	}

	// What is under the pointer when it went down: a visit card, or a technician.
	drag_source(el) {
		if (!this.data) return null;
		if (el.closest(".pk-drawer")) return this.p6a_drag_source(el);
		const person_el = el.closest(".mp-person[data-user]");
		if (person_el) {
			if (!this.data.can_move_visits) return null;
			return { kind: "person", el: person_el, user: person_el.getAttribute("data-user") };
		}
		const card_el = el.closest(".mp-card");
		if (!card_el) return null;
		const card = this.by_key[card_el.getAttribute("data-key")];
		if (!card || !card.movable || card.saving) return null;
		// In the crew view the same visit sits in each crew member's row: remember whose chip it is.
		const row = card_el.getAttribute("data-row-user");
		return { kind: "card", el: card_el, card, from_user: row == null ? null : row };
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
		this.p6a_edge_scroll(x, y);
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
		this.p6b_lift(drag);
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

	// Work out what a drop means. `{ card, change }` is a move to send (`change` holds a date, a
	// technician, and for a helper's chip the `from_user` it replaces), `{ card, add_crew }` a person
	// added to the visit's crew, `{ refuse }` a reason it cannot be done, and null changes nothing.
	plan_drop(source, target) {
		if (source.kind === "person") {
			const card = this.by_key[target.key];
			const name = this.tech_name(source.user);
			if (!card || card.kind === "booking") return { refuse: __("Drop {0} onto a visit.", [name]) };
			const site = this.site_of(card);
			if (card.kind === "projected") {
				return {
					refuse: __(
						"{0} is a projected visit: it follows the technician on the site's Maintenance Profile, so {1} cannot be added to it here. Set the site's default crew on its Maintenance Profile, or add them once the visit is drafted.",
						[site, name]
					),
				};
			}
			if (!card.movable) {
				return { refuse: __("{0} is started or finished, so its crew stays as it is.", [site]) };
			}
			if ((card.technician || "") === source.user || this.crew_has(card, source.user)) {
				return { refuse: __("{0} is already on {1}.", [name, site]) };
			}
			// Nobody on the visit yet: the person becomes its technician. Otherwise they join the crew.
			if (!card.technician) return { card, change: { technician: source.user } };
			return { card, add_crew: source.user };
		}

		const card = source.card;
		if (target.date !== card.date && target.date < this.today()) {
			return { refuse: __("Visits can only be moved to today or a later day.") };
		}
		const change = {};
		if (target.date !== card.date) change.date = target.date;
		// The row the chip was picked up from: a helper's chip sits in the helper's own row.
		const row_user = source.from_user == null ? card.technician || "" : source.from_user;
		const helper = !!row_user && row_user !== (card.technician || "") && this.crew_has(card, row_user);
		const hands_over = target.row && (target.user || "") !== row_user;
		if (hands_over && card.kind === "projected") {
			return {
				refuse: __(
					"{0} is a projected visit: it follows the technician on the site's Maintenance Profile, so it cannot move to another technician's row. Drop it on a day of its own row to change the date.",
					[this.site_of(card)]
				),
			};
		}
		if (hands_over && helper) {
			if (!target.user) {
				return {
					refuse: __("Drop {0} on a technician's row to hand their place on the visit over.", [
						this.tech_name(row_user),
					]),
				};
			}
			if (target.user === (card.technician || "") || this.crew_has(card, target.user)) {
				return { refuse: __("{0} is already on {1}.", [this.tech_name(target.user), this.site_of(card)]) };
			}
			// Only the helper whose chip it is changes; the technician and the rest of the crew stay.
			change.technician = target.user;
			change.from_user = row_user;
		} else if (target.row && (target.user || "") !== (card.technician || "") && !helper) {
			change.technician = target.user;
		}
		return Object.keys(change).length ? { card, change } : null;
	}

	drop(source, target) {
		if (this.p6b_drop(source, target)) return;
		const plan = this.plan_drop(source, target);
		if (!plan) return;
		if (plan.refuse) {
			frappe.show_alert({ message: plan.refuse, indicator: "orange" }, 6);
			return;
		}
		if (plan.add_crew) {
			this.add_to_crew(plan.card, plan.add_crew);
			return;
		}
		this.apply(plan.card, plan.change);
	}

	// What a visit looks like before a change, for Undo: date, technician, crew, length and Full day.
	snapshot_of(card) {
		const before = { date: card.date || null, technician: card.technician || "" };
		before.crew = this.crew_rows(card);
		before.planned_hours = Number(card.planned_hours) || 0;
		before.full_day = card.full_day ? 1 : 0;
		return before;
	}

	// Show the card on its new day straight away; the reload afterwards is the truth.
	apply(card, change) {
		const before = { date: card.date || null, technician: card.technician || "" };
		Object.assign(before, this.snapshot_of(card));
		if (change.date) card.date = change.date;
		if (change.from_user) {
			// A helper's place is handed over: the technician and the rest of the crew stay.
			card.crew = (card.crew || []).map((member) =>
				member.user === change.from_user
					? Object.assign({}, member, { user: change.technician, name: this.tech_name(change.technician) })
					: member
			);
		} else if (change.technician !== undefined) {
			card.technician = change.technician || null;
			card.crew = (card.crew || []).filter((member) => member.user !== change.technician);
		}
		if (change.crew !== undefined) {
			card.crew = change.crew.map((row) => ({ user: row.user, name: this.tech_name(row.user), hours: row.hours }));
		}
		if (change.planned_hours !== undefined) card.planned_hours = change.planned_hours || null;
		if (change.full_day !== undefined) card.full_day = change.full_day ? 1 : 0;
		card.saving = true;
		this.render();
		return this.save_move(card, change, before).finally(() => this.load());
	}

	// A technician chip dropped on a visit that has a technician already: add_crew. The conflict
	// check, the reason dialog and Undo are the same as for a move.
	add_to_crew(card, user) {
		const before = this.snapshot_of(card);
		const site = this.site_of(card);
		card.crew = (card.crew || []).concat([{ user, name: this.tech_name(user), hours: null }]);
		card.saving = true;
		this.render();
		return this.send(
			"add_crew",
			{ record: card.name, user, modified: this.modified[card.name] || card.modified },
			{
				snapshot: Object.assign({ kind: "visit", site, record: card.name }, before),
				message: __("{0} added to {1}", [this.tech_name(user), site]),
				noun: "visit",
			}
		).finally(() => this.load());
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
		// The crew goes as a JSON list of {user, hours} rows; Full day as 1 or 0.
		if (visit && args.crew !== undefined) args.crew = JSON.stringify(args.crew);
		if (visit && args.full_day !== undefined) args.full_day = args.full_day ? 1 : 0;
		const snapshot = visit
			? {
					kind: "visit",
					site,
					record: card.name,
					date: before.date, technician: before.technician,
					crew: before.crew,
					planned_hours: before.planned_hours,
					full_day: before.full_day,
			  }
			: {
					kind: "projected",
					site,
					contract: card.contract,
					serial_no: card.serial_no || null,
					from_date: change.date,
					to_date: before.date,
			  };
		let message;
		if (visit && change.from_user) {
			message = __("{0}: {1} handed over to {2}", [
				site,
				this.tech_name(change.from_user),
				this.tech_name(change.technician),
			]);
			if (change.date) message += `, ${__("moved to {0}", [mp_when(change.date)])}`;
		} else if (change.date && change.technician !== undefined) {
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
				let message = opts.message;
				if (message && result.drafted) message += ". " + __("The visit is drafted and on the technician's list.");
				let toasted = false;
				if (opts.snapshot) {
					// A projected visit moved into the drafting window becomes a record at once, and its
					// own undo is moving that record, which the page cannot yet name.
					toasted = this.push_undo(
						result.drafted && opts.snapshot.kind === "projected"
							? { kind: "drafted", site: opts.snapshot.site }
							: opts.snapshot,
						message
					);
				}
				if (result.name && result.modified) this.modified[result.name] = result.modified;
				if (message && !toasted) frappe.show_alert({ message, indicator: "green" }, 5);
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

	push_undo(snapshot, message) {
		this.undo_stack.push(snapshot);
		while (this.undo_stack.length > MP.undo_max) this.undo_stack.shift();
		this.update_undo_button();
		return this.p6a_undo_toast(snapshot, message);
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
		this.p6a_close_toast();
		const snap = this.undo_stack.pop();
		this.update_undo_button();
		if (!snap) {
			frappe.show_alert({ message: __("Nothing to undo"), indicator: "blue" }, 4);
			return;
		}
		if (this.p6b_undo(snap)) return;
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
		// Crew, length and Full day go back too, but only what differs now (everything, when the visit is
		// not on screen to compare with).
		if (snap.crew && (!card || this.crew_key(this.crew_rows(card)) !== this.crew_key(snap.crew))) {
			args.crew = JSON.stringify(snap.crew);
		}
		if (snap.planned_hours !== undefined && (!card || (Number(card.planned_hours) || 0) !== snap.planned_hours)) {
			args.planned_hours = snap.planned_hours;
		}
		if (snap.full_day !== undefined && (!card || (card.full_day ? 1 : 0) !== snap.full_day)) {
			args.full_day = snap.full_day;
		}
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
		const helpers = (card.crew || []).filter((member) => member && member.user && member.user !== card.technician);
		add(
			__("Crew"),
			helpers
				.map((member) => {
					const hours = Number(member.hours) > 0 ? ` (${__("{0}h", [mp_hours(member.hours)])})` : "";
					return `${this.tech_name(member.user)}${hours}`;
				})
				.join(", ")
		);
		if (card.full_day) add(__("Length"), __("Full day for each person"));
		else if (Number(card.planned_hours) > 0) add(__("Length"), __("{0}h for each person", [mp_hours(card.planned_hours)]));
		else if (this.visit_people(card).length > 1 && Number(card.hours) > 0) {
			add(__("Length"), __("{0}h for each person", [mp_hours(card.hours)]));
		}
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
		links.push(...this.phase5_links(card));
		links.push(...this.p6a_links(card));

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
				// Who else goes: filled in below as a checkbox and optional hours for each person.
				fields.push(
					{ fieldtype: "Section Break", label: __("Crew") },
					{ fieldtype: "HTML", fieldname: "crew_editor", options: '<div class="mp-cm-list"></div>' },
					{ fieldtype: "Section Break" },
					{
						fieldtype: "Float",
						fieldname: "planned_hours",
						label: __("Planned hours"),
						default: Number(card.planned_hours) || 0,
						description: __("Hours each person is booked. Blank or 0: the site's default length."),
					},
					{ fieldtype: "Column Break" },
					{
						fieldtype: "Check",
						fieldname: "full_day",
						label: __("Full day"),
						default: card.full_day ? 1 : 0,
						description: __("The visit takes each person's whole day."),
					}
				);
			}
		}

		const dialog = this.p6a_dialog({ title: card.site || card.site_full || card.name, fields }, card);
		if (card.movable) {
			dialog.set_primary_action(card.kind === "visit" ? __("Save") : __("Move next visit"), (values) => {
				const change = {};
				if (values.date && values.date !== card.date) change.date = values.date;
				if (card.kind === "visit" && (values.technician || "") !== (card.technician || "")) {
					change.technician = values.technician || "";
				}
				if (card.kind === "visit") {
					// Only what changed goes to the server.
					const lead = values.technician || "";
					const crew = this.read_crew_editor(dialog, lead);
					if (this.crew_key(crew) !== this.crew_key(this.crew_rows(card))) change.crew = crew;
					const planned = Math.max(0, Number(values.planned_hours) || 0);
					if (planned !== (Number(card.planned_hours) || 0)) change.planned_hours = planned;
					const full_day = values.full_day ? 1 : 0;
					if (full_day !== (card.full_day ? 1 : 0)) change.full_day = full_day;
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
		dialog.show();
		if (card.movable && card.kind === "visit") this.build_crew_editor(dialog, card);
		dialog.$wrapper.find("[data-action]").on("click", (e) => {
			const action = e.currentTarget.getAttribute("data-action");
			dialog.hide();
			if (this.p6a_action(action, card)) return;
			if (action === "form") frappe.set_route("Form", "Sapphire Maintenance Record", card.name);
			else if (action === "wizard") frappe.set_route("visit-wizard", { record: card.name });
			else if (action === "contract") frappe.set_route("Form", "Sapphire Maintenance Contract", card.contract);
			else this.phase5_action(action, card);
		});
	}

	// The people who can go on a visit: every active technician and Field helper, plus anyone already
	// on it. A checkbox for each, and an hours box that is blank when they just share the visit's length.
	build_crew_editor(dialog, card) {
		const $list = dialog.$wrapper.find(".mp-cm-list");
		const on = {};
		(card.crew || []).forEach((member) => {
			if (member && member.user) on[member.user] = member;
		});
		const people = [];
		const seen = {};
		const add = (user, name) => {
			if (!user || seen[user]) return;
			seen[user] = true;
			people.push({ user, name: name || this.tech_name(user) });
		};
		((this.data && this.data.technicians) || []).forEach((person) => {
			if (person.enabled || on[person.user]) add(person.user, person.name);
		});
		Object.keys(on).forEach((user) => add(user, on[user].name));
		if (!people.length) {
			$("<div class='mp-empty-note'></div>").text(__("No technicians to add.")).appendTo($list);
			return;
		}
		people.forEach((person) => {
			const $row = $("<div class='mp-cm-row'></div>").attr("data-user", person.user).appendTo($list);
			const $label = $("<label></label>").appendTo($row);
			$("<input type='checkbox' class='mp-cm-on'>").prop("checked", !!on[person.user]).appendTo($label);
			$("<span></span>").text(person.name).appendTo($label);
			const hours = on[person.user] && Number(on[person.user].hours) > 0 ? Number(on[person.user].hours) : "";
			$("<input type='number' min='0' step='0.25' class='form-control input-sm mp-cm-hours'>")
				.attr("placeholder", __("hours"))
				.attr("aria-label", __("{0}: hours", [person.name]))
				.val(hours)
				.appendTo($row);
		});
		// The technician is not also a crew member: grey that row out as the technician changes.
		const sync = () => {
			const lead = dialog.get_value("technician") || "";
			$list.find(".mp-cm-row").each((i, el) => {
				const is_lead = el.getAttribute("data-user") === lead;
				$(el).toggleClass("mp-cm-lead", is_lead);
				$(el).find("input").prop("disabled", is_lead);
			});
		};
		const technician = dialog.fields_dict.technician;
		if (technician && technician.$input) technician.$input.on("change", sync);
		sync();
	}

	// The crew as ticked in the dialog, without the technician: [{user, hours}].
	read_crew_editor(dialog, lead) {
		const rows = [];
		dialog.$wrapper.find(".mp-cm-list .mp-cm-row").each((i, el) => {
			const user = el.getAttribute("data-user");
			if (!user || user === lead) return;
			if (!$(el).find(".mp-cm-on").prop("checked")) return;
			const hours = Number($(el).find(".mp-cm-hours").val());
			rows.push({ user, hours: hours > 0 ? hours : null });
		});
		return rows;
	}
}

// ====================================================================== Project Planner Phase 5
//
// "Preview customer email" in a visit's dialog: what the customer date confirmation would say for
// this visit, and to whom, rendered by the Project Planner's preview endpoint (the confirmation
// covers both planners). It never sends, works while confirmations are switched off (they ship
// off), and opens in a sandboxed frame so the email's styles and links stay inside it. Only stored
// visits have one: a projected visit has no record yet. The endpoint lives in the Project Planner's
// API, so it is named here rather than in MP.methods, which lists this page's own endpoints.
// The hooks into the class above are one line each: phase5_links and phase5_action (open_card).

const MP5 = {
	preview_method: "erpnext_enhancements.api.project_planner.preview_customer_confirmation",
	preview_roles: ["System Manager", "Projects Manager"],
	record: "Sapphire Maintenance Record",
};

const MP5_METHODS = {
	phase5_links(card) {
		const can = MP5.preview_roles.some((role) => frappe.user.has_role(role));
		return card.kind === "visit" && card.name && can ? [["customer_preview", __("Preview customer email")]] : [];
	},

	phase5_action(action, card) {
		if (action === "customer_preview") this.preview_customer_email(card.name);
	},

	preview_customer_email(name) {
		return Promise.resolve(
			frappe.call({
				method: MP5.preview_method,
				args: { doctype: MP5.record, name },
				freeze: true,
				freeze_message: __("Rendering the customer email…"),
			})
		)
			.then((r) => this.show_customer_preview((r && r.message) || {}))
			.catch(() => null);
	},

	show_customer_preview(answer) {
		const status = answer.would_send ? __("This email would be sent.") : __("This email would not be sent now.");
		const to = answer.recipient ? __("To: {0}", [answer.recipient]) : __("To: nobody (no email address found)");
		const notes = (answer.notes || []).map((text) => `<li>${mp_esc(text)}</li>`).join("");
		const parts = [
			`<div class="mp-p5-meta"><b>${mp_esc(status)}</b></div>`,
			`<div class="mp-p5-meta">${mp_esc(to)}</div>`,
		];
		if (answer.subject) parts.push(`<div class="mp-p5-meta">${mp_esc(__("Subject: {0}", [answer.subject]))}</div>`);
		if (notes) parts.push(`<ul class="mp-p5-meta">${notes}</ul>`);
		if (answer.error) parts.push(`<div class="mp-p5-meta"><b>${mp_esc(answer.error)}</b></div>`);
		parts.push(
			`<iframe class="mp-p5-frame" sandbox="" title="${mp_esc(__("Email preview"))}" ` +
				'style="display:block;width:100%;min-height:420px;border:1px solid var(--border-color);border-radius:8px;background:#ffffff"></iframe>'
		);
		const dialog = new frappe.ui.Dialog({
			title: __("Customer email preview"),
			size: "large",
			fields: [{ fieldtype: "HTML", fieldname: "preview", options: parts.join("") }],
		});
		dialog.show();
		const $frame = dialog.$wrapper.find("iframe.mp-p5-frame");
		if (answer.html) $frame.attr("srcdoc", answer.html);
		else $frame.hide();
	},
};

Object.assign(MaintenancePlanner.prototype, MP5_METHODS);

// ====================================================================== Phase 6A: quick looks and polish
//
// The Maintenance Planner's half of Phase 6A (the Project Planner has the same, see its own block).
// Nik, 2026-10-09: "click the Technicians name or something and see what their specific schedule is
// in a pop up or something so as not to lose context overall." The quick looks open in the planner
// kit's side drawer (public/js/planner_kit, loaded with frappe.require below); the calendar stays
// visible and usable behind it, and Esc or Back closes it without leaving the planner.
//
//   - click a technician's name (Resources available, a crew-view row, the initials on a visit, an
//     "off" badge)                            their week (planner_views.get_person_schedule, by user)
//   - click a date (day numbers, Resources available's day heads, crew-view column heads)
//                                             every technician's day side by side (get_day_overview)
//   - "Site at a glance" in a visit's panel  the site's upcoming, projected and recent visits, its
//                                             default technician and crew (get_site_overview)
//   - click a visit                          its editor opens in the side panel (planner_kit.panel
//                                             hosts the same fields; the crew editor is unchanged)
//   - drag a visit out of a person, day or site drawer onto a day or a technician's row: the page's
//     own drag_source / plan_drop / drop, so a conflict asks for a reason and Undo works
//   - every change that can be undone shows a toast with Undo; hovering a name lights up that
//     person's visits and bookings (pk-glow); the crew view's and the week view's headers stay put
//     while scrolling; "?" opens the legend
//
// Hooks into the class above, one line each: init_phase6a (constructor), render_phase6a (render),
// p6a_route (go, open_route), p6a_drag_source (drag_source), p6a_edge_scroll (edge_scroll),
// p6a_undo_toast (push_undo, whose answer send() reads so the alert is not shown twice),
// p6a_close_toast (undo), and p6a_dialog, p6a_links and p6a_action (open_card).

const MP6A = {
	kit: "planner_kit.bundle.js",
	site_method: "erpnext_enhancements.api.planner_views.get_site_overview",
	owner: "mp",
	widths: { person: 460, day: 760, site: 520, panel: 540 },
	hints: [
		["mp-person-peek", "Click a technician's name to see their week without leaving the calendar."],
		["mp-day-peek", "Click a date to see every technician's day side by side."],
		["mp-glow", "Hover over a name to light up all of that person's visits."],
	],
	status: { draft: "Scheduled", pending: "Pending review", done: "Done", projected: "Projected" },
};

const MP6A_STYLE = `
.mp-p6a-hints{display:flex;flex-direction:column;}
.mp-p6a-help{font-weight:700;min-width:30px;}
.mp-p6a-name{cursor:pointer;}
.mp-p6a-name:hover .mp-person-name{text-decoration:underline;}
.mp-tech[data-pk-person]{cursor:pointer;}
.mp-day-num[data-pp6a-day],.mp-crew-head[data-pp6a-day],.mp-res-head[data-pp6a-day]{cursor:pointer;}
.mp-res-head[data-pp6a-day]:hover,.mp-crew-head[data-pp6a-day]:hover{color:var(--primary,#2490ef);}
.mp-p6a-sample{display:inline-block;font-size:11px;padding:1px 6px;border-radius:6px;border:1px solid var(--border-color);}
.mp-p6a-bar{display:inline-block;width:90px;}
.mp-p6a-bar .mp-bar{display:block;}
.mp-p6a-row{cursor:pointer;}
@media (min-width:761px){
.mp-crew-view .mp-scroll{max-height:calc(100vh - 150px);overflow:auto;}
.mp-crew-view .mp-crew-head,.mp-crew-view .mp-crew-corner{position:sticky;top:0;z-index:3;background:var(--card-bg);}
.mp-crew-view .mp-crew-corner{left:0;z-index:4;}
.mp-week .mp-grid{max-height:calc(100vh - 150px);overflow:auto;}
.mp-week .mp-day-head{position:sticky;top:0;z-index:2;background:var(--card-bg);padding-top:2px;padding-bottom:2px;}
.mp-week .mp-day.mp-out .mp-day-head{background:var(--control-bg);}
.mp-res .mp-scroll{max-height:46vh;overflow:auto;}
.mp-res .mp-res-head{position:sticky;top:0;z-index:2;background:var(--card-bg);}
.mp-res .mp-res-grid > .mp-res-head:first-child{left:0;z-index:3;}
}
`;

const MP6A_METHODS = {
	init_phase6a() {
		if (!document.getElementById("mp-style-6a")) {
			$("<style id='mp-style-6a'>").text(MP6A_STYLE).appendTo(document.head);
		}
		this.p6a = { kit: null, peek: null, panel: null, panel_card: null, seen: null, glow: [] };
		const $bar = this.$body.find(".mp-toolbar").first();
		this.$p6a_hints = $('<div class="mp-p6a-hints"></div>').insertAfter($bar);
		this.$p6a_help = $('<button type="button" class="btn btn-default btn-sm mp-p6a-help">?</button>')
			.attr("title", __("How to read the planner"))
			.attr("aria-label", __("How to read the planner"))
			.on("click", () => this.p6a_legend())
			.insertAfter(this.$undo);
		const root = this.$body[0];
		// Capture phase: a name or a date inside a card or a header is a quick look, not the card's
		// own click (which opens the visit) and not the day number's old "show this week".
		root.addEventListener("click", (e) => this.p6a_click(e), true);
		root.addEventListener(
			"keydown",
			(e) => {
				if (e.key !== "Enter" && e.key !== " ") return;
				const el = e.target && e.target.closest ? e.target.closest(".mp-p6a-name, [data-pp6a-day]") : null;
				if (!el || e.target !== el) return;
				e.preventDefault();
				e.stopPropagation();
				el.click();
			},
			true
		);
		frappe.require(MP6A.kit, () => this.p6a_ready());
	},

	p6a_ready() {
		const kit = window.planner_kit;
		if (!kit || (this.p6a && this.p6a.kit)) return;
		this.p6a.kit = kit;
		kit.drawer.element().addEventListener("pointerdown", (e) => {
			const current = kit.drawer.current();
			if (current && current.owner === MP6A.owner) this.on_down(e);
		});
		this.p6a.glow = [kit.hover_glow(this.$body[0], "person")];
		this.p6a_show_hints();
	},

	// ------------------------------------------------------------------ hooks

	p6a_route(fn, keep) {
		const kit = this.p6a && this.p6a.kit;
		return kit ? kit.drawer.route(fn, keep) : fn();
	},

	render_phase6a() {
		if (!this.p6a) return;
		this.p6a_decorate();
		this.p6a_refresh_open();
	},

	p6a_undo_toast(snapshot, message) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !message) return false;
		// A projected visit drafted by its move cannot be undone from here (undo() says why).
		if (snapshot && snapshot.kind === "drafted") {
			kit.toast(message, { tone: "success" });
			return true;
		}
		kit.toast(message, {
			action_label: __("Undo"),
			tone: "success",
			on_action: () => {
				if (this.undo_stack[this.undo_stack.length - 1] !== snapshot) {
					frappe.show_alert({ message: __("That change has already been undone."), indicator: "blue" }, 5);
					return;
				}
				this.undo();
			},
		});
		return true;
	},

	p6a_close_toast() {
		const kit = this.p6a && this.p6a.kit;
		if (kit) kit.toast.close();
	},

	p6a_dialog(opts, card) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit) return new frappe.ui.Dialog(opts);
		const people = this.visit_people(card).map((user) => this.tech_name(user));
		const panel = kit.panel(
			Object.assign({}, opts, {
				subtitle: [card.date ? mp_when(card.date) : __("Not scheduled"), people.join(", ")].filter(Boolean).join(" · "),
				width: MP6A.widths.panel,
				key: `visit:${card.key}`,
				owner: MP6A.owner,
				push: this.$body[0],
				reopen: () => {
					const fresh = this.by_key[card.key];
					if (fresh) this.open_card(fresh);
				},
			})
		);
		this.p6a.panel = panel;
		this.p6a.panel_card = card;
		this.p6a.peek = null;
		panel.onhide = () => {
			if (this.p6a.panel !== panel) return;
			this.p6a.panel = null;
			this.p6a.panel_card = null;
		};
		return panel;
	},

	p6a_links(card) {
		if (!this.p6a || !this.p6a.kit) return [];
		const out = [];
		if (card.project) out.push(["p6a_site", __("Site at a glance")]);
		if (card.technician) out.push(["p6a_person", __("{0}'s week", [this.tech_name(card.technician)])]);
		return out;
	},

	p6a_action(action, card) {
		if (action === "p6a_site") {
			this.p6a_open_site(card);
			return true;
		}
		if (action === "p6a_person") {
			this.p6a_open_person(card.technician, card.date);
			return true;
		}
		return false;
	},

	// A drag that starts in a drawer this page opened: a visit on the calendar that may move.
	p6a_drag_source(el) {
		const kit = this.p6a && this.p6a.kit;
		const current = kit && kit.drawer.current();
		if (!current || current.owner !== MP6A.owner) return null;
		const item = el.closest("[data-pk-drag]");
		if (!item || el.closest("button, a, input, select, textarea")) return null;
		if (item.getAttribute("data-pk-kind") !== "visit") return null;
		const card = this.by_key[item.getAttribute("data-pk-key")];
		if (!card || !card.movable || card.saving) return null;
		return { kind: "card", el: item, card, from_user: item.getAttribute("data-pk-user") || null };
	},

	p6a_edge_scroll(x, y) {
		const el = document.elementFromPoint(x, y);
		const box = el && el.closest ? el.closest(".mp-crew-view .mp-scroll, .mp-week .mp-grid, .mp-res .mp-scroll") : null;
		if (!box || box.scrollHeight <= box.clientHeight) return;
		const rect = box.getBoundingClientRect();
		if (y < rect.top + 40) box.scrollTop -= 14;
		else if (y > rect.bottom - 40) box.scrollTop += 14;
	},

	// ------------------------------------------------------------------ markup the quick looks need

	p6a_decorate() {
		if (!this.data) return;
		const root = this.$body[0];
		const key = (value) => encodeURIComponent(String(value == null ? "" : value));
		root.querySelectorAll(".mp-person[data-user]").forEach((el) => {
			el.setAttribute("data-pk-person", key(el.getAttribute("data-user")));
			el.classList.add("mp-p6a-name");
			el.setAttribute("role", "button");
			el.setAttribute("tabindex", "0");
			if (!el.getAttribute("data-pp6a-tip")) {
				el.setAttribute("data-pp6a-tip", "1");
				el.setAttribute("title", `${el.getAttribute("title") || ""}\n${__("Click to see their week.")}`.trim());
			}
		});
		root.querySelectorAll(".mp-card[data-key]").forEach((el) => {
			const card = this.by_key[el.getAttribute("data-key")];
			if (!card) return;
			const row = el.getAttribute("data-row-user");
			const people = card.kind === "booking" ? [card.technician] : row ? [row] : this.visit_people(card);
			el.setAttribute("data-pk-persons", people.filter(Boolean).map(key).join(" "));
			if (card.kind === "booking") {
				const badge = el.querySelector(".mp-tech");
				if (badge && card.technician) badge.setAttribute("data-pk-person", key(card.technician));
				return;
			}
			const everyone = this.visit_people(card);
			el.querySelectorAll(".mp-techs .mp-tech").forEach((badge, index) => {
				if (everyone[index]) badge.setAttribute("data-pk-person", key(everyone[index]));
			});
		});
		const planned = (this.data.technicians || []).filter((person) => person.enabled && person.resource);
		root.querySelectorAll(".mp-day[data-date]").forEach((el) => {
			const ymd = el.getAttribute("data-date");
			const num = el.querySelector(".mp-day-num");
			if (num) {
				num.setAttribute("data-pp6a-day", ymd);
				num.setAttribute("title", __("See everyone's day"));
				num.setAttribute("tabindex", "0");
			}
			const off = planned.filter((person) => this.is_off(this.cell_of(person.user, ymd)));
			el.querySelectorAll(".mp-off-badges .mp-tech").forEach((badge, index) => {
				if (off[index]) badge.setAttribute("data-pk-person", key(off[index].user));
			});
		});
		root.querySelectorAll(".mp-crew-head[data-week]").forEach((el) => {
			el.setAttribute("data-pp6a-day", el.getAttribute("data-week"));
			el.setAttribute("title", __("See everyone's day"));
			el.setAttribute("tabindex", "0");
		});
		const days = this.range_days();
		root.querySelectorAll(".mp-res-grid > .mp-res-head").forEach((el, index) => {
			const ymd = days[index - 1];
			if (!ymd) return;
			el.setAttribute("data-pp6a-day", ymd);
			el.setAttribute("title", __("See everyone's day"));
		});
	},

	// ------------------------------------------------------------------ clicks

	p6a_click(e) {
		if (!this.p6a || !this.p6a.kit || Date.now() < this.click_blocked_until) return;
		const target = e.target;
		if (!target || !target.closest) return;
		if (target.closest("button, a, input, select, textarea, .mp-route, [data-route-resource]")) return;
		const take = () => {
			e.stopPropagation();
			e.preventDefault();
		};
		const person = target.closest("[data-pk-person]");
		if (person) {
			take();
			this.p6a_open_person(decodeURIComponent(person.getAttribute("data-pk-person")));
			return;
		}
		const day = target.closest("[data-pp6a-day]");
		if (day) {
			take();
			this.p6a_open_day(day.getAttribute("data-pp6a-day"));
		}
	},

	// ------------------------------------------------------------------ the quick looks

	p6a_week_start() {
		const today = this.today();
		const { start, end } = this.range();
		let base = this.anchor;
		if (this.view === "month" && today >= mp_ymd(start) && today <= mp_ymd(end)) base = today;
		const day = moment(base || today, "YYYY-MM-DD");
		return mp_ymd(day.clone().subtract((day.day() - this.first_weekday() + 7) % 7, "days"));
	},

	// The page owns visits: only a visit card on the calendar that may move leaves a drawer by drag.
	p6a_can_drag(booking) {
		if (booking.kind !== "visit") return false;
		const card = this.by_key[booking.key];
		return !!(card && card.movable && card.kind !== "booking");
	},

	p6a_open_person(user, selected) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !user) return;
		this.p6a.seen = this.data;
		this.p6a.peek = kit.peeks.person({
			user,
			label: this.tech_name(user),
			start: this.p6a_week_start(),
			selected: selected || null,
			days: 7,
			owner: MP6A.owner,
			push: this.$body[0],
			width: MP6A.widths.person,
			can_drag: (booking) => this.p6a_can_drag(booking),
			on_full_route: (resource, ymd) =>
				kit.drawer.navigate(() => frappe.set_route(MP.project_route, "route", resource, ymd)),
		});
	},

	p6a_open_day(ymd) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !ymd) return;
		const listed = new Set(((this.data && this.data.technicians) || []).map((person) => person.user));
		this.p6a.seen = this.data;
		this.p6a.peek = kit.peeks.day({
			date: ymd,
			owner: MP6A.owner,
			push: this.$body[0],
			width: MP6A.widths.day,
			only: (person) => !listed.size || (person.user && listed.has(person.user)),
			person_key: (person) => person.user || person.resource,
			can_drag: (booking) => this.p6a_can_drag(booking),
			on_person: (person) => {
				if (person.user) this.p6a_open_person(person.user, ymd);
			},
			on_week: (date) => kit.drawer.navigate(() => this.go("week", date)),
		});
	},

	p6a_open_site(card) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !card || !card.project) return;
		const project = card.project;
		const state = { data: null, token: 0 };
		this.p6a.seen = this.data;
		const handle = kit.drawer.open({
			title: card.site || card.site_full || project,
			subtitle: __("Loading…"),
			body: `<p class="pk-empty">${mp_esc(__("Loading…"))}</p>`,
			width: MP6A.widths.site,
			key: `site:${project}`,
			owner: MP6A.owner,
			push: this.$body[0],
			reopen: () => this.p6a_open_site(card),
			on_click: (e) => {
				const row = e.target && e.target.closest ? e.target.closest("[data-mp6a-visit]") : null;
				if (!row || Date.now() < this.click_blocked_until) return;
				const found = this.by_key[row.getAttribute("data-mp6a-visit")];
				if (found) this.open_card(found);
				else if (row.getAttribute("data-mp6a-record")) {
					const name = row.getAttribute("data-mp6a-record");
					kit.drawer.navigate(() => frappe.set_route("Form", "Sapphire Maintenance Record", name));
				}
			},
		});
		const load = () => {
			const token = ++state.token;
			if (state.data) handle.body.classList.add("pk-busy");
			return Promise.resolve(frappe.call({ method: MP6A.site_method, args: { project } }))
				.then((r) => {
					if (token !== state.token || !handle.is_open()) return;
					handle.body.classList.remove("pk-busy");
					state.data = (r && r.message) || null;
					const data = state.data || {};
					handle.set_title(data.site || data.title || card.site || project);
					handle.set_subtitle([data.customer_name, project].filter(Boolean).join(" · "));
					handle.set_body(this.p6a_site_html(state.data));
					const actions = [];
					if (data.profile && data.profile.name) {
						actions.push({
							label: __("Open Maintenance Profile"),
							on_click: () =>
								kit.drawer.navigate(() => frappe.set_route("Form", "Sapphire Maintenance Profile", data.profile.name)),
						});
					}
					const contract = (data.contracts || [])[0];
					if (contract && contract.name) {
						actions.push({
							label: __("Open contract"),
							on_click: () =>
								kit.drawer.navigate(() => frappe.set_route("Form", "Sapphire Maintenance Contract", contract.name)),
						});
					}
					handle.set_actions(actions);
				})
				.catch(() => {
					if (token !== state.token || !handle.is_open()) return;
					handle.body.classList.remove("pk-busy");
					handle.set_body(this.p6a_site_html(null));
				});
		};
		this.p6a.peek = { data: () => state.data, refresh: load, is_open: () => handle.is_open() };
		load();
	},

	p6a_visit_row(visit) {
		const card = this.by_key[visit.key];
		const grab = !!(card && card.movable && card.kind !== "booking");
		const people = [visit.technician_name || __("Unassigned")].concat((visit.crew || []).map((member) => member.name));
		const what = visit.label ? __(visit.label) : visit.feature || (visit.kind === "projected" ? __("Projected visit") : __("Regular visit"));
		const status = __(MP6A.status[visit.status] || visit.status || "");
		const meta = [status, people.filter(Boolean).join(", ")].filter(Boolean).join(" · ");
		const chips = visit.overdue ? `<span class="pk-chip pk-chip-red">${mp_esc(__("Overdue"))}</span>` : "";
		const drag = grab
			? ` data-pk-drag="1" data-pk-kind="visit" data-pk-ref="${mp_esc(visit.name || "")}" data-pk-key="${mp_esc(
					visit.key
			  )}" data-pk-date="${mp_esc(visit.date || "")}" data-pk-user=""`
			: "";
		const record = visit.kind === "visit" && visit.name ? ` data-mp6a-record="${mp_esc(visit.name)}"` : "";
		const tip = [what, meta, grab ? __("Drag onto the calendar to move it; click to open it.") : ""].filter(Boolean).join("\n");
		return (
			`<li class="pk-item pk-k-visit mp-p6a-row${grab ? " pk-grab" : ""}" data-mp6a-visit="${mp_esc(visit.key)}"${record}${drag}` +
			` title="${mp_esc(tip)}"><span class="pk-kind">${mp_esc(visit.date ? mp_when(visit.date) : __("No date"))}</span>` +
			`<span class="pk-item-main"><span class="pk-item-label">${mp_esc(what)}</span>` +
			`<span class="pk-item-meta">${mp_esc(meta)}</span></span>${chips}</li>`
		);
	},

	p6a_site_html(data) {
		if (!data) return `<p class="pk-empty">${mp_esc(__("The site could not be loaded. Try again."))}</p>`;
		const facts = [];
		const fact = (label, value) => {
			if (value) facts.push(`<tr><th>${mp_esc(label)}</th><td>${mp_esc(value)}</td></tr>`);
		};
		fact(__("Site"), data.title);
		fact(__("Account"), data.customer_name);
		fact(
			__("Contract"),
			(data.contracts || []).map((row) => `${row.name}${row.status ? ` (${__(row.status)})` : ""}`).join(", ")
		);
		const profile = data.profile;
		if (profile) {
			fact(__("Default technician"), profile.default_technician_name);
			fact(
				__("Default crew"),
				(profile.crew || [])
					.map((member) => (Number(member.hours) > 0 ? `${member.name} (${mp_hours(member.hours)}h)` : member.name))
					.join(", ")
			);
			fact(
				__("Visit length"),
				profile.full_day ? __("Full day") : Number(profile.visit_hours) > 0 ? __("{0}h", [mp_hours(profile.visit_hours)]) : ""
			);
		} else {
			fact(__("Maintenance Profile"), __("None yet"));
		}
		const section = (title, visits, empty) =>
			`<div class="pk-section-title">${mp_esc(title)}</div>` +
			(visits.length
				? `<ul class="pk-items pk-day">${visits.map((visit) => this.p6a_visit_row(visit)).join("")}</ul>`
				: `<p class="pk-empty">${mp_esc(empty)}</p>`);
		return (
			`<div class="mp-p6a-site"><table class="pk-facts">${facts.join("")}</table>` +
			section(__("Coming up"), data.upcoming || [], __("No visit is scheduled.")) +
			section(__("Projected from the contract"), data.projected || [], __("Nothing projected in the next 90 days.")) +
			section(__("Recent visits"), data.recent || [], __("No visits yet.")) +
			`</div>`
		);
	},

	// After a load: the visit in the side panel is reopened from its new card (fresh `modified`) when
	// it changed; one no longer on the calendar (another week on screen) stays as it is. An open
	// person, day or site drawer asks for its numbers again.
	p6a_refresh_open() {
		const p6a = this.p6a;
		if (!p6a.kit || !this.data) return;
		if (p6a.panel && p6a.panel.is_open() && p6a.panel_card) {
			const fresh = this.by_key[p6a.panel_card.key];
			if (fresh && !fresh.saving && this.p6a_signature(fresh) !== this.p6a_signature(p6a.panel_card)) {
				this.open_card(fresh);
				return;
			}
		}
		if (p6a.peek && p6a.peek.is_open() && p6a.seen !== this.data) {
			p6a.seen = this.data;
			p6a.peek.refresh();
		}
	},

	p6a_signature(card) {
		return JSON.stringify([
			card.modified,
			card.date,
			card.technician,
			card.planned_hours,
			card.full_day,
			(card.crew || []).map((member) => [member.user, member.hours]),
		]);
	},

	// ------------------------------------------------------------------ legend and hints

	p6a_show_hints() {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !this.$p6a_hints) return;
		let shown = 0;
		MP6A.hints.forEach(([key, text]) => {
			if (shown >= 2 || kit.hint.seen(key)) return;
			if (kit.hint(key, __(text), { container: this.$p6a_hints[0] })) shown += 1;
		});
	},

	p6a_legend() {
		const kit = this.p6a && this.p6a.kit;
		if (!kit) return;
		const swatch = (color, text, style, fill) => ({ swatch: { color, style: style || "solid", fill }, text: __(text) });
		const chip = (cls, text) => `<span class="mp-chip${cls ? ` ${cls}` : ""}">${mp_esc(__(text))}</span>`;
		const item = (sample_html, text) => ({ sample_html, text: __(text) });
		const bar = (cls, width) => `<span class="mp-p6a-bar ${cls}"><span class="mp-bar"><i style="width:${width}%"></i></span></span>`;
		kit.legend(
			[
				{
					title: __("Quick looks"),
					items: [
						item("", "Click a technician's name (in the panel, a crew row or the initials on a visit) to see their week: visits, project work, free hours, days off and the day's stops in driving order."),
						item("", "Click a date to see every technician's day side by side."),
						item("", "In a visit's side panel, Site at a glance shows the site's coming, projected and recent visits and its default crew."),
						item("", "Drag a visit out of any of these onto a day or a technician's row to move it. Esc or Back closes the side panel."),
						item("", "Hover over a name to light up all of that person's visits and bookings."),
					],
				},
				{
					title: __("Visit cards"),
					items: [
						swatch("#2563eb", "A scheduled visit. Drag it to another day; only a draft nobody has started moves."),
						swatch("#d97706", "A seasonal visit (startup, winterization). Before it is drafted it sits on the 1st of its month."),
						swatch("#7c3aed", "An extra visit or a chemistry follow-up."),
						swatch("#ca8a04", "Pending review: the technician has sent it to the office.", "solid", "#fef9c3"),
						swatch("#16a34a", "Done.", "solid", "#dcfce7"),
						swatch("#94a3b8", "Projected from the contract, not drafted yet. It follows whenever the visit before it is done.", "dashed"),
						swatch("#2563eb", "The contract's next visit (dashed, blue edge): moving it moves the contract's next visit date.", "dashed"),
						swatch("#dc2626", "Overdue: a draft whose day has passed."),
						swatch("#0d9488", "A project task of the same person (read-only here; moved on the Project Planner)."),
						swatch("#b45309", "A rental crew task (read-only here)."),
						swatch("#6b7280", "A travel day (read-only here)."),
					],
				},
				{
					title: __("Chips on a visit"),
					items: [
						item(chip("", "Next visit"), "The contract's stored next visit date."),
						item(chip("", "Pending review"), "Waiting for the office to approve it."),
						item(chip("", "Full day"), "It takes each person's whole day."),
						item(chip("", "2h each"), "How long it books each person, when that is not the default."),
						item(chip("mp-red", "On hold"), "The account is on a service hold."),
						item(chip("mp-red", "Chemistry"), "A reading on the visit was out of range."),
						item(chip("mp-red", "Day off: Holiday"), "The technician does not work that day: move it or give it to someone else."),
					],
				},
				{
					title: __("People"),
					items: [
						item(`<span class="mp-tech" style="background:#2563eb">AH</span>`, "Everyone on the visit. Click one to see their week."),
						item(`<span class="mp-tech mp-lead" style="background:#16a34a">JD</span>`, "Ringed: the technician who fills the visit in."),
						item(`<span class="mp-tech mp-tech-off" style="background:#64748b">KF</span>`, "Faded and striped: off that day (shown with All technicians)."),
					],
				},
				{
					title: __("A technician's day"),
					items: [
						item(bar("mp-green", 50), "Up to 75% of their hours booked."),
						item(bar("mp-amber", 90), "More than 75% booked."),
						item(bar("mp-red", 100), "Over their hours."),
						item(`<span class="mp-p6a-sample mp-offday">${mp_esc(__("Holiday"))}</span>`, "Not working: a holiday, time off or not a work day. The type and reason of time off are never shown."),
						item(`<span class="mp-p6a-sample mp-conflict">${mp_esc(__("1h free"))}</span>`, "Red outline: a conflict on that day."),
						item(chip("mp-red", "Long drive"), "More driving than the Settings limit."),
						item(`<span class="mp-p6a-sample">${MP_ROUTE_ICON}</span>`, "Open the day's route on the Project Planner. A ~ before a drive time means it is an estimate."),
					],
				},
				{
					title: __("Dragging"),
					items: [
						item(`<span class="mp-p6a-sample mp-over">${mp_esc(__("Drop here"))}</span>`, "Where the visit will land."),
						item(`<span class="mp-p6a-sample mp-over-no">${mp_esc(__("Past day"))}</span>`, "A day before today: visits cannot move there."),
						item("", "On a touch screen, hold a card for a moment before you drag it. Drag a technician's name onto a visit to add them to its crew."),
						item("", "A change that causes a conflict asks for a reason, never blocks. Undo puts the last change back."),
					],
				},
			].concat(this.p6b_legend_sections()),
			{ title: __("How to read the Maintenance Planner"), owner: MP6A.owner }
		);
	},
};

Object.assign(MaintenancePlanner.prototype, MP6A_METHODS);

// ====================================================================== Phase 6B: faster scheduling
//
// The Maintenance Planner's half of Phase 6B (TASK-2026-02468; the Project Planner has the rest, see
// its own block). What applies to visits:
//
//   - right-click a visit, an empty spot or a name (a long press of about half a second on a touch
//     screen, when the finger does not move)  a menu (planner_kit.menu): Edit, Assign to..., Move to
//                                            next free day, Site at a glance, the technician's week;
//                                            Everyone's day on an empty spot; See their week on a
//                                            name. Other phases add items through this.p6_menu_providers
//   - Shift-, Ctrl- or Cmd-click draft visits a selection (this.p6_selection, visit keys); drag one and
//                                            they all move by the same number of calendar days, one
//                                            move_visit each after a single confirmation, one reason
//                                            for the lot and one Undo
//   - the search box (or /)                  visits, sites and technicians on screen first, then the
//                                            server (search_planner, planner="maintenance")
//   - T, the arrows, 1 2 3, Ctrl/Cmd+Z, ?, Esc keyboard shortcuts, listed in the legend
//
// Left out on purpose: Duplicate, Split and Pencil (a visit has none of them), resizing (no visit
// spans several days) and "Add visit here" (every way a visit is created today belongs to the
// contract, the scheduler or the Visit Wizard on site; see the README).
//
// Nothing here picks a person: Assign to... lists the technicians who are free that day
// (project_planner.who_is_free) and the planner clicks one. Every write is the page's own: move_visit
// and add_crew through send(), so the reason prompt and Undo apply.
//
// Hooks into the class above, one line each: init_phase6b (constructor), render_phase6b (render),
// p6b_lift (lift), p6b_drop (drop), p6b_undo (undo), and p6b_legend_sections (p6a_legend).

const MP6B = {
	who_is_free: "erpnext_enhancements.api.project_planner.who_is_free",
	next_free_day: "erpnext_enhancements.api.planner_conflicts.get_next_free_day",
	search: "erpnext_enhancements.api.planner_actions.search_planner",
	long_press_ms: 500,
	search_min: 2,
	search_wait_ms: 250,
	search_limit: 20,
	flash_ms: 2600,
	default_visit_hours: 2,
	widths: { assign: 440, panel: 460 },
};

// Pure helpers, the same as the Project Planner's PP6B_PURE (tests/test_planner_phase6b.py runs the
// same cases through both under node).
const MP6B_PURE = {
	ymd_add(ymd, days) {
		const date = new Date(`${ymd}T00:00:00Z`);
		date.setUTCDate(date.getUTCDate() + (Number(days) || 0));
		return date.toISOString().slice(0, 10);
	},
	ymd_diff(a, b) {
		return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86400000);
	},
	toggle(set, key) {
		if (set.has(key)) {
			set.delete(key);
			return false;
		}
		set.add(key);
		return true;
	},
	shortcut(info) {
		if (!info || info.editable || info.dialog) return null;
		const key = String(info.key || "");
		if ((info.ctrl || info.meta) && !info.alt && !info.shift && key.toLowerCase() === "z") return "undo";
		// Ctrl+S, Ctrl+K, Ctrl+G, Alt+... stay frappe's.
		if (info.ctrl || info.meta || info.alt) return null;
		if (key === "Escape" || key === "Esc") return "escape";
		if (key === "?") return "legend";
		// Shift+T is frappe's console.
		if (info.shift) return null;
		if (key === "t" || key === "T") return "today";
		if (key === "/") return "search";
		if (key === "ArrowLeft") return "prev";
		if (key === "ArrowRight") return "next";
		if (key === "1" || key === "2" || key === "3") return `view${key}`;
		return null;
	},
	with_providers(items, providers, target) {
		const out = (items || []).slice();
		(providers || []).forEach((provider) => {
			let group = [];
			try {
				group = (provider(target) || []).filter(Boolean);
			} catch (e) {
				group = [];
			}
			if (!group.length) return;
			if (out.length && !out[out.length - 1].divider) out.push({ divider: true });
			out.push(...group);
		});
		return out;
	},
	search_local(q, entries, limit) {
		const words = String(q || "")
			.toLowerCase()
			.split(/\s+/)
			.filter(Boolean);
		if (!words.length) return [];
		const order = { person: 0, project: 1, site: 1, task: 2, visit: 2 };
		const rank = (kind) => (kind in order ? order[kind] : 3);
		const found = [];
		(entries || []).forEach((entry) => {
			const hay = [entry.label].concat(entry.keys || []).join(" ").toLowerCase();
			if (!words.every((word) => hay.includes(word))) return;
			const starts = String(entry.label || "").toLowerCase().startsWith(words[0]) ? 0 : 1;
			found.push({ entry, starts });
		});
		found.sort(
			(a, b) =>
				a.starts - b.starts ||
				rank(a.entry.kind) - rank(b.entry.kind) ||
				String(a.entry.label || "").localeCompare(String(b.entry.label || ""))
		);
		return found.slice(0, limit || 20).map((item) => item.entry);
	},
};

const MP6B_STYLE = `
.mp-card.mp-p6b-selected{outline:2px solid #7c3aed;outline-offset:1px;}
.mp-p6b-bar{position:fixed;left:50%;bottom:72px;transform:translateX(-50%);z-index:1020;display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;max-width:calc(100vw - 24px);padding:6px 10px;border:1px solid #7c3aed;border-radius:10px;background:var(--card-bg);color:var(--text-color);box-shadow:0 8px 24px rgba(0,0,0,.18);font-size:13px;}
.mp-p6b-bar-count{font-weight:600;}
.mp-p6b-bar-note{flex:1 1 100%;font-size:11px;color:var(--text-muted);}
.mp-p6b-count{position:absolute;top:-9px;right:-9px;min-width:20px;height:20px;padding:0 5px;border-radius:10px;background:#7c3aed;color:#fff;font-size:11px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;}
.mp-card.mp-p6b-flash{animation:mp-p6b-flash .8s ease-in-out 3;}
@keyframes mp-p6b-flash{0%,100%{box-shadow:none;}50%{box-shadow:0 0 0 4px rgba(245,158,11,.85);}}
.mp-p6b-search-wrap{position:relative;display:inline-block;}
.mp-toolbar input.mp-p6b-search{width:210px;}
.mp-p6b-results{position:absolute;top:100%;left:0;z-index:1030;margin-top:4px;min-width:300px;max-width:min(420px,calc(100vw - 24px));max-height:60vh;overflow-y:auto;padding:4px 0;border:1px solid var(--border-color);border-radius:8px;background:var(--card-bg);box-shadow:0 10px 28px rgba(0,0,0,.2);}
.mp-p6b-result{display:flex;flex-direction:column;padding:5px 10px;cursor:pointer;font-size:13px;}
.mp-p6b-result.mp-p6b-active,.mp-p6b-result:hover{background:var(--control-bg);}
.mp-p6b-result-kind{font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);}
.mp-p6b-result-label{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.mp-p6b-result-sub{font-size:11px;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.mp-p6b-results-note{padding:4px 10px;font-size:11px;color:var(--text-muted);}
.mp-p6b-modes{margin:4px 0 8px;}
.mp-p6b-modes label{display:flex;align-items:center;gap:6px;margin:2px 0;font-weight:normal;}
.mp-p6b-pick{display:flex;justify-content:space-between;align-items:center;gap:8px;width:100%;margin-bottom:4px;padding:6px 8px;border:1px solid var(--border-color);border-radius:8px;background:var(--card-bg);color:var(--text-color);text-align:left;cursor:pointer;}
.mp-p6b-pick:hover,.mp-p6b-pick:focus{background:var(--control-bg);outline:none;}
.mp-p6b-pick-busy{border-style:dashed;}
.mp-p6b-pick-why{font-size:11px;color:var(--text-muted);}
.mp-p6b-kbd{display:inline-block;min-width:18px;margin-right:3px;padding:0 5px;border:1px solid var(--border-color);border-bottom-width:2px;border-radius:4px;background:var(--control-bg);font-size:11px;font-family:inherit;text-align:center;}
@media (max-width:760px){
.mp-p6b-search-wrap{flex:1 1 100%;}
.mp-toolbar input.mp-p6b-search{width:100%;}
.mp-p6b-bar{bottom:64px;}
}
`;

const MP6B_METHODS = {
	init_phase6b() {
		if (!document.getElementById("mp-style-6b")) {
			$("<style id='mp-style-6b'>").text(MP6B_STYLE).appendTo(document.head);
		}
		// The registries other phases add to (phase6-wave2): menu items and legend sections.
		this.p6_menu_providers = this.p6_menu_providers || [];
		this.p6_legend_providers = this.p6_legend_providers || [];
		// The multi-selection: visit keys. 6C's print reads it.
		this.p6_selection = this.p6_selection || new Set();
		this.p6b = {
			press: null,
			flash: null,
			seen: null,
			next_free: {},
			keys_on: false,
			search: { q: "", token: 0, timer: null, local: [], remote: [], loading: false, active: 0, shown: [], list_id: "" },
		};
		this.$p6b_toolbar = this.$body.find(".mp-toolbar").first();
		this.p6b_build_search();
		this.$p6b_bar = $('<div class="mp-p6b-bar" role="toolbar"></div>')
			.attr("aria-label", __("Selected visits"))
			.hide()
			.appendTo(this.$body);
		const root = this.$body[0];
		// Capture on the page's own container: a modified click on a visit selects it, ahead of the
		// card's own click (which opens it) and of 6A's quick-look clicks.
		(root.parentElement || root).addEventListener("click", (e) => this.p6b_modifier_click(e), true);
		root.addEventListener("click", (e) => this.p6b_plain_click(e));
		root.addEventListener("contextmenu", (e) => this.p6b_contextmenu(e));
		root.addEventListener("pointerdown", (e) => this.p6b_pointer_down(e), true);
		document.addEventListener("pointermove", (e) => this.p6b_pointer_move(e));
		document.addEventListener("pointerup", (e) => this.p6b_pointer_up(e));
		document.addEventListener("pointercancel", (e) => this.p6b_pointer_cancel(e));
		this.p6b_bind_keys();
	},

	render_phase6b() {
		if (!this.p6b) return;
		if (this.p6b.seen !== this.data) {
			this.p6b.seen = this.data;
			this.p6b.next_free = {};
		}
		this.p6b_prune_selection();
		this.p6b_decorate_selection();
		this.p6b_render_bar();
		this.p6b_flash_pending();
	},

	p6b_kit() {
		return (this.p6a && this.p6a.kit) || null;
	},

	p6b_navigate(fn) {
		const kit = this.p6b_kit();
		return kit ? kit.drawer.navigate(fn) : fn();
	},

	p6b_panel(opts) {
		const kit = this.p6b_kit();
		if (!kit) return new frappe.ui.Dialog({ title: opts.title, fields: opts.fields });
		return kit.panel({
			title: opts.title,
			subtitle: opts.subtitle || "",
			fields: opts.fields,
			width: MP6B.widths.panel,
			key: opts.key || null,
			owner: MP6A.owner,
			push: this.$body[0],
		});
	},

	p6b_resource(user) {
		const person = user && this.tech_by_user[user];
		return (person && person.resource) || null;
	},

	// The hours one person spends on a visit: the lead's effective length, else Planned hours, else 2.
	p6b_visit_hours(card) {
		return Number(card.hours) > 0
			? Number(card.hours)
			: Number(card.planned_hours) > 0
			? Number(card.planned_hours)
			: MP6B.default_visit_hours;
	},

	// ------------------------------------------------------------------ the right-click menu

	p6b_target(el) {
		if (!el || !el.closest || !this.$body[0].contains(el)) return null;
		if (el.closest("button, a, input, select, textarea, .mp-toolbar, .mp-legend, .mp-route, [data-route-resource], .mp-p6b-bar")) {
			return null;
		}
		const card_el = el.closest(".mp-card[data-key]");
		if (card_el) {
			const card = this.by_key[card_el.getAttribute("data-key")];
			if (!card) return null;
			const row = card_el.getAttribute("data-row-user");
			const user = row == null ? card.technician || null : row || null;
			return { kind: "card", card, ymd: card.date || null, user, resource: this.p6b_resource(user), el: card_el };
		}
		const person_el = el.closest(".mp-person[data-user]");
		if (person_el) {
			const user = person_el.getAttribute("data-user");
			return { kind: "person", user, resource: this.p6b_resource(user), el: person_el };
		}
		const cell = el.closest(".mp-cell[data-date]");
		if (cell) {
			const user = cell.getAttribute("data-user") || null;
			return { kind: "cell", user, resource: this.p6b_resource(user), ymd: cell.getAttribute("data-date"), el: cell };
		}
		const day = el.closest(".mp-grid .mp-day[data-date]");
		if (day) return { kind: "cell", user: null, resource: null, ymd: day.getAttribute("data-date"), el: day };
		return null;
	},

	p6b_contextmenu(e) {
		if (this.drag) return;
		if (!this.p6b_open_for(e.target, { x: e.clientX, y: e.clientY }, !!this.p6b.press)) return;
		e.preventDefault();
		if (this.p6b.press) this.p6b.press.opened = true;
	},

	p6b_open_for(el, anchor, from_touch) {
		if (!this.p6b_kit() || !this.data) return false;
		const target = this.p6b_target(el);
		if (!target) return false;
		const items = this.p6b_menu_items(target);
		if (!items.length) return false;
		if (from_touch) {
			this.click_blocked_until = Date.now() + 600;
			this.p6b_swallow_click();
		}
		this.p6b_open_menu(target, items, anchor);
		return true;
	},

	p6b_swallow_click() {
		// The click a lifting finger makes comes at once; a later one is the planner's own.
		const until = Date.now() + 350;
		const swallow = (e) => {
			document.removeEventListener("click", swallow, true);
			if (Date.now() < until) {
				e.stopPropagation();
				e.preventDefault();
			}
		};
		document.addEventListener("click", swallow, true);
		setTimeout(() => document.removeEventListener("click", swallow, true), 500);
	},

	p6b_menu_title(target) {
		if (target.kind === "card") return this.site_of(target.card);
		if (target.kind === "person") return this.tech_name(target.user);
		return [target.user ? this.tech_name(target.user) : "", target.ymd ? mp_when(target.ymd) : ""].filter(Boolean).join(" · ");
	},

	p6b_menu_items(target) {
		if (target.kind === "card") return this.p6b_card_items(target);
		if (target.kind === "person") {
			return [{ label: __("See their week"), on_click: () => this.p6a_open_person(target.user) }];
		}
		if (target.kind === "cell" && target.ymd) {
			return [{ label: __("Everyone's day"), on_click: () => this.p6a_open_day(target.ymd) }];
		}
		return [];
	},

	p6b_open_menu(target, items, anchor) {
		const kit = this.p6b_kit();
		const all = MP6B_PURE.with_providers(items, this.p6_menu_providers, target);
		const handle = kit.menu({ anchor, items: all, title: this.p6b_menu_title(target), owner: MP6A.owner });
		all.forEach((item) => {
			if (!item || !item.p6b_lookup) return;
			item.p6b_lookup.then((answer) => {
				const state = this.p6b_free_state(answer);
				this.p6b_menu_hint(handle, item.label, state.hint, state.disabled);
			});
		});
		return handle;
	},

	p6b_menu_hint(handle, label, hint, disabled) {
		if (!handle || !handle.el || !handle.el.isConnected) return;
		const button = Array.from(handle.el.querySelectorAll(".pk-menu-item")).find(
			(node) => node.firstChild && node.firstChild.textContent === label
		);
		if (!button) return;
		let span = button.querySelector(".pk-menu-hint");
		if (!span) {
			span = document.createElement("span");
			span.className = "pk-menu-hint";
			button.appendChild(span);
		}
		span.textContent = hint || "";
		button.disabled = !!disabled;
	},

	// A visit's items. A project task, rental crew task or travel day shown here is read-only and
	// gets the browser's own menu.
	p6b_card_items(target) {
		const card = target.card;
		if (card.kind === "booking") return [];
		const visit = card.kind === "visit";
		const can = !!(this.data && this.data.can_move_visits);
		const movable = !!(visit && card.movable && can);
		const why = !visit
			? __("Follows the site's Maintenance Profile")
			: !can
			? __("You cannot move visits")
			: card.started
			? __("Started")
			: card.status !== "draft"
			? __("Finished")
			: __("Read-only");
		const items = [
			{ label: __("Edit"), on_click: () => this.open_card(card) },
			{ label: __("Assign to…"), hint: movable ? "" : why, disabled: !movable, on_click: () => this.p6b_assign(card) },
			this.p6b_next_free_item(card, movable, why),
		];
		if (card.project || card.technician) items.push({ divider: true });
		if (card.project) items.push({ label: __("Site at a glance"), on_click: () => this.p6a_open_site(card) });
		if (card.technician) {
			items.push({
				label: __("{0}'s week", [this.tech_name(card.technician)]),
				on_click: () => this.p6a_open_person(card.technician, card.date),
			});
		}
		return items;
	},

	p6b_next_free(card, resource, hours) {
		const key = [card.key, resource, hours, this.modified[card.name] || card.modified].join("|");
		if (!this.p6b.next_free[key]) {
			const args = { resource, hours, after: card.date || this.today() };
			this.p6b.next_free[key] = Promise.resolve(frappe.call({ method: MP6B.next_free_day, args }))
				.then((r) => (r && r.message) || null)
				.catch(() => null);
		}
		return this.p6b.next_free[key];
	},

	p6b_free_state(answer) {
		if (!answer) return { hint: __("Could not check"), disabled: false };
		if (answer.date) return { hint: mp_when(answer.date), disabled: false };
		return { hint: __("Nothing free in {0} days", [answer.horizon || 30]), disabled: true };
	},

	// The visit's technician's next day with its hours free (planner_conflicts.get_next_free_day), then
	// move_visit through apply(): draft visits nobody has started only.
	p6b_next_free_item(card, movable, why) {
		const item = { label: __("Move to next free day"), hint: __("Looking…"), disabled: false };
		if (!movable) return Object.assign(item, { hint: why, disabled: true });
		if (!card.technician) return Object.assign(item, { hint: __("No technician"), disabled: true });
		const resource = this.p6b_resource(card.technician);
		if (!resource) return Object.assign(item, { hint: __("Not a Planner Resource"), disabled: true });
		const lookup = this.p6b_next_free(card, resource, this.p6b_visit_hours(card));
		item.p6b_lookup = lookup;
		item.on_click = () =>
			lookup.then((answer) => {
				if (answer && answer.date) {
					this.apply(card, { date: answer.date });
					return;
				}
				frappe.show_alert(
					{
						message: (answer && answer.note) || __("No free day found for {0}.", [this.tech_name(card.technician)]),
						indicator: "orange",
					},
					7
				);
			});
		return item;
	},

	// ------------------------------------------------------------------ assign to...

	p6b_assign(card) {
		const kit = this.p6b_kit();
		if (!kit) {
			this.open_card(card);
			return;
		}
		if (!card.date) {
			frappe.msgprint(__("Give the visit a date first: who is free depends on the day."));
			return;
		}
		const state = { mode: card.technician ? "add" : "tech", data: null };
		const handle = kit.drawer.open({
			title: __("Assign to…"),
			subtitle: [this.site_of(card), mp_when(card.date)].join(" · "),
			body: `<p class="pk-empty">${mp_esc(__("Finding who is free…"))}</p>`,
			width: MP6B.widths.assign,
			key: `assign:${card.key}`,
			owner: MP6A.owner,
			push: this.$body[0],
			reopen: () => this.p6b_assign(this.by_key[card.key] || card),
			on_click: (e, h) => this.p6b_assign_click(e, h, card, state),
		});
		Promise.resolve(
			frappe.call({ method: MP6B.who_is_free, args: { start: card.date, hours: this.p6b_visit_hours(card) } })
		)
			.then((r) => {
				if (!handle.is_open()) return;
				state.data = (r && r.message) || null;
				handle.set_body(this.p6b_assign_html(card, state));
			})
			.catch(() => {
				if (handle.is_open()) handle.set_body(this.p6b_assign_html(card, state));
			});
	},

	p6b_assign_html(card, state) {
		const on = new Set(this.visit_people(card));
		const listed = new Set(((this.data && this.data.technicians) || []).filter((person) => person.enabled).map((person) => person.user));
		const parts = [
			`<p class="pk-note">${mp_esc(
				__("Pick who should go: nobody is chosen for you. Someone who is not free can still be booked; you will be asked for a reason.")
			)}</p>`,
		];
		if (card.technician) {
			const option = (value, text) =>
				`<label><input type="radio" name="mp-p6b-mode" value="${mp_esc(value)}"${state.mode === value ? " checked" : ""}> ${mp_esc(text)}</label>`;
			parts.push(
				`<div class="mp-p6b-modes" role="radiogroup" aria-label="${mp_esc(__("How to assign"))}">${option(
					"add",
					__("Add to the crew")
				)}${option("tech", __("Make them the technician, in place of {0}", [this.tech_name(card.technician)]))}</div>`
			);
		} else {
			parts.push(`<p class="pk-note">${mp_esc(__("The visit has no technician: the person you pick fills it in."))}</p>`);
		}
		const data = state.data;
		if (!data) {
			parts.push(`<p class="pk-empty">${mp_esc(__("Who is free could not be loaded. Close this and try again."))}</p>`);
			return parts.join("");
		}
		const entry = (data.days || [])[0] || { free: [], not_free: [] };
		// Technicians only (the people this planner can put on a visit), by user, not already on it.
		const keep = (person) => person.user && listed.has(person.user) && !on.has(person.user);
		const row = (person, busy) =>
			`<button type="button" class="mp-p6b-pick${busy ? " mp-p6b-pick-busy" : ""}" data-mp-p6b-pick="${mp_esc(person.user)}">` +
			`<span><b>${mp_esc(this.tech_name(person.user) || person.label)}</b></span>` +
			`<span class="mp-p6b-pick-why">${mp_esc(busy ? person.reason || "" : __("{0}h free", [mp_hours(person.free_hours)]))}</span></button>`;
		const free = (entry.free || []).filter(keep);
		const busy = (entry.not_free || []).filter(keep);
		parts.push(
			`<div class="pk-section-title">${mp_esc(__("Free on {0} ({1}h or more)", [mp_when(entry.date || card.date), mp_hours(data.hours)]))}</div>`
		);
		parts.push(
			free.length
				? free.map((person) => row(person, false)).join("")
				: `<p class="pk-empty">${mp_esc(__("No technician has those hours free that day."))}</p>`
		);
		if (busy.length) {
			parts.push(`<div class="pk-section-title">${mp_esc(__("Not free"))}</div>`);
			parts.push(busy.map((person) => row(person, true)).join(""));
		}
		if (on.size) {
			parts.push(
				`<div class="pk-section-title">${mp_esc(__("Already on it"))}</div><p class="pk-note">${mp_esc(
					[...on].map((user) => this.tech_name(user)).join(", ")
				)}</p>`
			);
		}
		return parts.join("");
	},

	p6b_assign_click(e, handle, card, state) {
		const radio = e.target && e.target.closest ? e.target.closest("input[name='mp-p6b-mode']") : null;
		if (radio) {
			state.mode = radio.value;
			return;
		}
		const pick = e.target && e.target.closest ? e.target.closest("[data-mp-p6b-pick]") : null;
		if (!pick) return;
		const user = pick.getAttribute("data-mp-p6b-pick");
		const mode = state.mode;
		handle.close();
		const fresh = this.by_key[card.key] || card;
		if (!fresh.movable) return;
		if (mode === "add" && fresh.technician) this.add_to_crew(fresh, user);
		else this.apply(fresh, { technician: user });
	},

	// ------------------------------------------------------------------ long press

	p6b_pointer_down(e) {
		this.p6b.press =
			e.pointerType === "touch" && !(e.button > 0)
				? { id: e.pointerId, x: e.clientX, y: e.clientY, at: Date.now(), target: e.target, moved: false, opened: false }
				: null;
	},

	p6b_pointer_move(e) {
		const press = this.p6b.press;
		if (press && e.pointerId === press.id && Math.hypot(e.clientX - press.x, e.clientY - press.y) > MP.drag_px) {
			press.moved = true;
		}
	},

	p6b_pointer_up(e) {
		const press = this.p6b.press;
		this.p6b.press = null;
		if (!press || e.pointerId !== press.id || press.moved || press.opened) return;
		if (Date.now() - press.at < MP6B.long_press_ms) return;
		this.p6b_open_for(press.target, { x: e.clientX, y: e.clientY }, true);
	},

	p6b_pointer_cancel(e) {
		if (this.p6b.press && e.pointerId === this.p6b.press.id) this.p6b.press = null;
	},

	// ------------------------------------------------------------------ selection

	p6b_selectable(card) {
		return !!(card && card.kind === "visit" && card.movable && card.date && this.data && this.data.can_move_visits);
	},

	p6b_selected_cards() {
		return [...this.p6_selection].map((key) => this.by_key[key]).filter((card) => this.p6b_selectable(card));
	},

	p6b_modifier_click(e) {
		if (!(e.shiftKey || e.ctrlKey || e.metaKey) || e.button > 0 || Date.now() < this.click_blocked_until) return;
		const el = e.target && e.target.closest ? e.target.closest(".mp-card[data-key]") : null;
		if (!el || !this.$body[0].contains(el) || e.target.closest("button, a, input, select, textarea")) return;
		e.preventDefault();
		e.stopPropagation();
		const key = el.getAttribute("data-key");
		const card = this.by_key[key];
		if (!card) return;
		if (!this.p6_selection.has(key) && !this.p6b_selectable(card)) {
			frappe.show_alert(
				{
					message: __("Only draft visits nobody has started can be moved together; {0} cannot.", [this.site_of(card)]),
					indicator: "orange",
				},
				6
			);
			return;
		}
		MP6B_PURE.toggle(this.p6_selection, key);
		this.p6b_selection_changed();
	},

	p6b_plain_click(e) {
		if (!this.p6_selection.size || e.shiftKey || e.ctrlKey || e.metaKey) return;
		const el = e.target;
		if (!el || !el.closest) return;
		if (el.closest(".mp-card, .mp-person, .mp-toolbar, .mp-p6b-bar, button, a, input, select, textarea, label")) return;
		this.p6b_clear_selection();
	},

	p6b_clear_selection() {
		if (!this.p6_selection.size) return false;
		this.p6_selection.clear();
		this.p6b_selection_changed();
		return true;
	},

	p6b_selection_changed() {
		this.p6b_decorate_selection();
		this.p6b_render_bar();
		$(document).trigger("p6-selection-changed", [this]);
	},

	p6b_prune_selection() {
		if (!this.p6_selection.size || !this.data) return;
		let changed = false;
		[...this.p6_selection].forEach((key) => {
			if (!this.p6b_selectable(this.by_key[key])) {
				this.p6_selection.delete(key);
				changed = true;
			}
		});
		if (changed) $(document).trigger("p6-selection-changed", [this]);
	},

	p6b_decorate_selection() {
		this.$body[0].querySelectorAll(".mp-card[data-key]").forEach((el) => {
			el.classList.toggle("mp-p6b-selected", this.p6_selection.has(el.getAttribute("data-key")));
		});
	},

	p6b_render_bar() {
		const $bar = this.$p6b_bar;
		if (!$bar) return;
		const cards = this.p6b_selected_cards();
		if (!cards.length) {
			$bar.hide().empty();
			return;
		}
		$bar.empty().show();
		$('<span class="mp-p6b-bar-count"></span>').text(__("{0} selected", [cards.length])).appendTo($bar);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(__("Move…"))
			.on("click", () => this.p6b_move_panel())
			.appendTo($bar);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(__("Clear"))
			.on("click", () => this.p6b_clear_selection())
			.appendTo($bar);
		$('<span class="mp-p6b-bar-note"></span>')
			.text(
				__("Drag any of them and they all move by the same number of calendar days. Each visit is saved on its own. Esc clears the selection.")
			)
			.appendTo($bar);
	},

	p6b_lift(drag) {
		if (!drag || !drag.ghost || !drag.source || drag.source.kind !== "card") return;
		if (!this.p6_selection.has(drag.source.card.key)) return;
		const count = this.p6b_selected_cards().length;
		if (count < 2) return;
		const badge = document.createElement("span");
		badge.className = "mp-p6b-count";
		badge.textContent = String(count);
		drag.ghost.appendChild(badge);
	},

	// A drop of one selected visit when several are selected. True when it was handled here.
	p6b_drop(source, target) {
		if (!source || source.kind !== "card" || !source.card || this.p6_selection.size < 2) return false;
		if (!this.p6_selection.has(source.card.key) || this.p6b_selected_cards().length < 2) return false;
		const row_user = source.from_user == null ? source.card.technician || "" : source.from_user;
		if (target.row && (target.user || "") !== row_user) {
			frappe.show_alert(
				{
					message: __(
						"Several visits are selected: drop them on the same technician's row to change their days. To hand one visit to someone else, clear the selection first."
					),
					indicator: "orange",
				},
				8
			);
			return true;
		}
		if (!source.card.date || !target.date) return true;
		const days = MP6B_PURE.ymd_diff(source.card.date, target.date);
		if (days) this.p6b_move_selection(days);
		return true;
	},

	p6b_move_panel() {
		const cards = this.p6b_selected_cards();
		if (!cards.length) return;
		const panel = this.p6b_panel({
			title: __("Move {0} visits", [cards.length]),
			key: "mp-p6b-move",
			fields: [
				{
					fieldtype: "Int",
					fieldname: "days",
					label: __("Move by (days)"),
					reqd: 1,
					default: 7,
					description: __("Calendar days; a negative number moves them earlier. Visits cannot move to a day that has passed."),
				},
			],
		});
		panel.set_primary_action(__("Move"), (values) => {
			const days = parseInt(values && values.days, 10) || 0;
			if (!days) return;
			panel.hide();
			this.p6b_move_selection(days);
		});
		panel.show();
	},

	// One confirmation, then one move_visit per visit through send(): the first conflict asks for a
	// reason and that reason answers the rest; backing out stops the rest. A visit that fails is listed
	// at the end and the others stay moved (each save is its own).
	p6b_move_selection(days) {
		const cards = this.p6b_selected_cards();
		if (!cards.length || !days) return;
		const today = this.today();
		const plans = cards.map((card) => ({ card, date: MP6B_PURE.ymd_add(card.date, days) }));
		if (plans.some((plan) => plan.date < today)) {
			frappe.show_alert({ message: __("Visits can only be moved to today or a later day."), indicator: "orange" }, 6);
			return;
		}
		const question =
			days > 0
				? __("Move {0} visits {1} day(s) later?", [plans.length, days])
				: __("Move {0} visits {1} day(s) earlier?", [plans.length, -days]);
		frappe.confirm(
			`<p>${mp_esc(question)}</p><p class="text-muted">${mp_esc(
				__("Each visit is saved on its own: if one cannot move, the others still do, and you are told which.")
			)}</p>`,
			() => this.p6b_run_moves(plans)
		);
	},

	p6b_run_moves(plans) {
		plans.forEach((plan) => (plan.card.saving = true));
		this.render();
		const moved = [];
		const missed = [];
		return this.p6b_one_reason((batch) =>
			plans.reduce(
				(chain, plan) =>
					chain.then(() => {
						if (batch.declined) {
							missed.push(plan);
							return null;
						}
						const before = plan.card.date;
						return this.send(
							"move_visit",
							{ record: plan.card.name, modified: this.modified[plan.card.name] || plan.card.modified, date: plan.date },
							{ noun: "visit" }
						).then((result) => {
							if (result) moved.push({ record: plan.card.name, site: this.site_of(plan.card), date: before });
							else missed.push(plan);
						});
					}),
				Promise.resolve()
			)
		)
			.then(() => {
				if (moved.length) {
					const message = __("{0} visits moved", [moved.length]);
					const snapshot = { p6b: "many", site: __("{0} visits", [moved.length]), items: moved };
					if (!this.push_undo(snapshot, message)) frappe.show_alert({ message, indicator: "green" }, 5);
				}
				if (missed.length) {
					frappe.msgprint({
						title: __("Moved {0} of {1}", [moved.length, plans.length]),
						message:
							`<p>${mp_esc(__("These visits were not moved; the others were saved:"))}</p>` +
							`<ul>${missed.map((plan) => `<li>${mp_esc(this.site_of(plan.card))}</li>`).join("")}</ul>`,
						indicator: "orange",
					});
				}
			})
			.finally(() => this.load());
	},

	// While `work` runs, the first reason given answers every later conflict of the same batch, and
	// backing out of it stops the rest. Only for the batch: the page's own ask_reason (the prototype's)
	// is back as soon as it ends, and send() itself is untouched.
	p6b_one_reason(work) {
		const base = MaintenancePlanner.prototype.ask_reason;
		const batch = { reason: null, declined: false };
		this.ask_reason = (conflicts, noun) => {
			if (batch.declined) return Promise.resolve(null);
			if (batch.reason) return Promise.resolve(batch.reason);
			return base.call(this, conflicts, noun).then((reason) => {
				if (reason) batch.reason = reason;
				else batch.declined = true;
				return reason;
			});
		};
		return Promise.resolve()
			.then(() => work(batch))
			.finally(() => {
				delete this.ask_reason;
			});
	},

	// ------------------------------------------------------------------ undo

	p6b_undo(snap) {
		if (!snap || snap.p6b !== "many") return false;
		const today = this.today();
		const back = (snap.items || []).filter((item) => item.date && item.date >= today);
		const kept = (snap.items || []).length - back.length;
		back.reduce(
			(chain, item) =>
				chain.then(() =>
					this.send(
						"move_visit",
						{
							record: item.record,
							modified: this.modified[item.record] || (this.by_key[item.record] || {}).modified || "",
							date: item.date,
						},
						{ auto_reason: __(MP.undo_reason), noun: "visit" }
					)
				),
			Promise.resolve()
		)
			.then(() => {
				frappe.show_alert({ message: __("Undone: {0}", [snap.site]), indicator: "green" }, 5);
				if (kept) {
					frappe.show_alert(
						{ message: __("{0} visit(s) were on a day that has passed, so they stay where they are.", [kept]), indicator: "orange" },
						8
					);
				}
			})
			.finally(() => this.load());
		return true;
	},

	// ------------------------------------------------------------------ search

	p6b_build_search() {
		const $wrap = $('<span class="mp-p6b-search-wrap"></span>');
		const $seg = this.$p6b_toolbar.children(".mp-seg").first();
		if ($seg.length) $wrap.insertAfter($seg);
		else $wrap.appendTo(this.$p6b_toolbar);
		const list_id = `mp-p6b-results-${Math.random().toString(36).slice(2, 9)}`;
		this.p6b.search.list_id = list_id;
		this.$p6b_search = $('<input type="search" class="form-control input-sm mp-p6b-search" autocomplete="off" spellcheck="false">')
			.attr({
				placeholder: __("Search  ( / )"),
				title: __("Find a visit, site or technician. Press / to come here."),
				"aria-label": __("Search visits, sites and technicians"),
				role: "combobox",
				"aria-autocomplete": "list",
				"aria-expanded": "false",
				"aria-controls": list_id,
			})
			.appendTo($wrap);
		this.$p6b_results = $('<div class="mp-p6b-results" role="listbox"></div>').attr("id", list_id).hide().appendTo($wrap);
		this.$p6b_search.on("input", () => this.p6b_search_input());
		this.$p6b_search.on("keydown", (e) => this.p6b_search_key(e));
		this.$p6b_search.on("focus", () => {
			if (this.p6b.search.q.length >= MP6B.search_min) this.p6b_render_results();
		});
		this.$p6b_search.on("blur", () => setTimeout(() => this.p6b_close_results(), 150));
		this.$p6b_results.on("mousedown", (e) => e.preventDefault());
		this.$p6b_results.on("click", "[data-mp-p6b-result]", (e) => {
			const item = this.p6b.search.shown[Number(e.currentTarget.getAttribute("data-mp-p6b-result"))];
			if (item) this.p6b_pick(item);
		});
	},

	p6b_search_input() {
		const search = this.p6b.search;
		search.q = String(this.$p6b_search.val() || "").trim();
		search.active = 0;
		search.remote = [];
		clearTimeout(search.timer);
		const token = ++search.token;
		if (search.q.length < MP6B.search_min) {
			search.local = [];
			search.loading = false;
			this.p6b_close_results();
			return;
		}
		search.local = MP6B_PURE.search_local(search.q, this.p6b_local_entries(), MP6B.search_limit);
		search.loading = true;
		this.p6b_render_results();
		const q = search.q;
		search.timer = setTimeout(() => this.p6b_remote_search(q, token), MP6B.search_wait_ms);
	},

	// What is on screen: every visit (drafted, unscheduled, projected), their sites and the technicians.
	p6b_local_entries() {
		const data = this.data || {};
		const out = [];
		const sites = {};
		[].concat(data.visits || [], data.unscheduled || [], data.projected || []).forEach((card) => {
			const site = this.site_of(card);
			out.push({
				kind: "visit",
				key: card.key,
				name: card.name,
				label: site,
				keys: [card.name, card.site_full || "", card.feature || "", card.label || "", ...this.visit_people(card).map((user) => this.tech_name(user))],
				sub: [card.date ? mp_when(card.date) : __("Not scheduled"), card.label ? __(card.label) : ""].filter(Boolean).join(" · "),
				date: card.date || null,
			});
			if (card.project && !sites[card.project]) {
				sites[card.project] = true;
				out.push({ kind: "site", project: card.project, label: site, keys: [card.project, card.site_full || ""], sub: card.project });
			}
		});
		(data.technicians || [])
			.filter((person) => person.enabled)
			.forEach((person) => {
				out.push({ kind: "person", user: person.user, label: person.name, keys: [], sub: person.group ? __(person.group) : "" });
			});
		return out;
	},

	p6b_result_key(item) {
		return `${item.kind}|${item.key || item.name || item.project || item.user || ""}`;
	},

	p6b_from_server(item) {
		if (!item || !item.kind) return null;
		if (item.kind === "visit") {
			return {
				kind: "visit",
				key: item.name,
				name: item.name,
				label: item.label || item.name,
				sub: [item.date ? mp_when(item.date) : __("Not scheduled"), item.visit_label ? __(item.visit_label) : "", item.name]
					.filter(Boolean)
					.join(" · "),
				date: item.date || null,
			};
		}
		if (item.kind === "site") return { kind: "site", project: item.project, label: item.label || item.project, sub: item.project };
		if (item.kind === "person" && item.user) {
			return { kind: "person", user: item.user, label: item.label || item.user, sub: item.group ? __(item.group) : "" };
		}
		return null;
	},

	p6b_remote_search(q, token) {
		return Promise.resolve(
			frappe.call({ method: MP6B.search, args: { q, start: this.anchor, planner: "maintenance" } })
		)
			.then((r) => {
				const search = this.p6b.search;
				if (token !== search.token) return;
				const seen = new Set(search.local.map((item) => this.p6b_result_key(item)));
				search.remote = (((r && r.message) || {}).results || [])
					.map((item) => this.p6b_from_server(item))
					.filter((item) => item && !seen.has(this.p6b_result_key(item)));
				search.loading = false;
				if (this.$p6b_search.is(":focus")) this.p6b_render_results();
			})
			.catch(() => {
				if (token !== this.p6b.search.token) return;
				this.p6b.search.loading = false;
				if (this.$p6b_search.is(":focus")) this.p6b_render_results();
			});
	},

	p6b_render_results() {
		const search = this.p6b.search;
		if (search.q.length < MP6B.search_min) {
			this.p6b_close_results();
			return;
		}
		const shown = search.local.concat(search.remote).slice(0, MP6B.search_limit);
		search.shown = shown;
		if (search.active >= shown.length) search.active = 0;
		const kinds = { visit: __("Visit"), site: __("Site"), person: __("Technician") };
		const rows = shown.map(
			(item, index) =>
				`<div class="mp-p6b-result${index === search.active ? " mp-p6b-active" : ""}" role="option" id="${mp_esc(
					`${search.list_id}-${index}`
				)}" aria-selected="${index === search.active ? "true" : "false"}" data-mp-p6b-result="${index}">` +
				`<span class="mp-p6b-result-kind">${mp_esc(kinds[item.kind] || item.kind)}</span>` +
				`<span class="mp-p6b-result-label">${mp_esc(item.label)}</span>` +
				(item.sub ? `<span class="mp-p6b-result-sub">${mp_esc(item.sub)}</span>` : "") +
				`</div>`
		);
		const note = search.loading ? __("Searching the rest…") : shown.length ? "" : __("Nothing found.");
		this.$p6b_results.html(rows.join("") + (note ? `<div class="mp-p6b-results-note">${mp_esc(note)}</div>` : "")).show();
		this.$p6b_search.attr("aria-expanded", "true");
		if (shown.length) this.$p6b_search.attr("aria-activedescendant", `${search.list_id}-${search.active}`);
		else this.$p6b_search.removeAttr("aria-activedescendant");
	},

	p6b_close_results() {
		if (!this.$p6b_results) return;
		this.$p6b_results.hide().empty();
		this.$p6b_search.attr("aria-expanded", "false").removeAttr("aria-activedescendant");
	},

	p6b_search_key(e) {
		const search = this.p6b.search;
		if (e.key === "ArrowDown" || e.key === "ArrowUp") {
			if (!search.shown.length) return;
			e.preventDefault();
			search.active = (search.active + (e.key === "ArrowDown" ? 1 : -1) + search.shown.length) % search.shown.length;
			this.p6b_render_results();
		} else if (e.key === "Enter") {
			e.preventDefault();
			const item = search.shown[search.active] || search.shown[0];
			if (item) this.p6b_pick(item);
		} else if (e.key === "Escape") {
			e.preventDefault();
			e.stopPropagation();
			if (this.$p6b_results.is(":visible")) this.p6b_close_results();
			else this.$p6b_search.val("").trigger("blur");
		}
	},

	p6b_pick(item) {
		this.p6b_close_results();
		this.$p6b_search.trigger("blur");
		if (item.kind === "person") this.p6a_open_person(item.user);
		else if (item.kind === "site") this.p6a_open_site({ project: item.project, site: item.label, site_full: item.label });
		else if (item.kind === "visit") this.p6b_jump_to_visit(item);
	},

	// A visit: the calendar goes to its week (a real route) and the card flashes.
	p6b_jump_to_visit(item) {
		const card = this.by_key[item.key];
		const date = item.date || (card && card.date) || null;
		if (!date) {
			if (card) {
				this.p6b.flash = { key: card.key, date: null, wait: false };
				this.p6b_flash_pending();
				return;
			}
			this.p6b_navigate(() => frappe.set_route("Form", "Sapphire Maintenance Record", item.name));
			return;
		}
		this.p6b.flash = { key: item.key, date, wait: true };
		if (this.range_days().includes(date)) {
			this.p6b_flash_pending();
			return;
		}
		this.go(this.view === "week" || this.view === "crew" ? this.view : "week", date);
	},

	p6b_flash_pending() {
		const flash = this.p6b.flash;
		if (!flash || !this.data) return;
		if (flash.date && !(this.data.start <= flash.date && flash.date <= this.data.end)) {
			if (!flash.wait) this.p6b.flash = null;
			return;
		}
		this.p6b.flash = null;
		const el = Array.from(this.$body[0].querySelectorAll(".mp-card[data-key]")).find(
			(node) => node.getAttribute("data-key") === flash.key
		);
		if (!el) {
			if (flash.wait) {
				frappe.show_alert({ message: __("That visit is hidden by the filters on screen."), indicator: "orange" }, 6);
			}
			return;
		}
		el.classList.add("mp-p6b-flash");
		try {
			el.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
		} catch (e) {
			el.scrollIntoView();
		}
		setTimeout(() => el.classList.remove("mp-p6b-flash"), MP6B.flash_ms);
	},

	// ------------------------------------------------------------------ keyboard

	// One keydown listener on the document (capture), added while the page shows and removed when it
	// hides; see the Project Planner's p6b_bind_keys for why not frappe.ui.keys.
	p6b_bind_keys() {
		const handler = (e) => this.p6b_key(e);
		const on = () => {
			if (this.p6b.keys_on) return;
			document.addEventListener("keydown", handler, true);
			this.p6b.keys_on = true;
		};
		const off = () => {
			if (!this.p6b.keys_on) return;
			document.removeEventListener("keydown", handler, true);
			this.p6b.keys_on = false;
			this.p6b_close_results();
		};
		const wrapper = this.page && this.page.wrapper;
		if (wrapper && typeof wrapper.on === "function") {
			// frappe triggers these on the page itself; a Bootstrap dropdown's or collapse's show/hide
			// inside the page bubbles up as the same event name and must not switch the keys off.
			wrapper.on("show", (e) => e.target === wrapper[0] && on()).on("hide", (e) => e.target === wrapper[0] && off());
		}
		on();
	},

	p6b_page_live() {
		const route = frappe.get_route() || [];
		if (route[0] !== MP.route) return false;
		const wrapper = this.page && this.page.wrapper;
		return !wrapper || typeof wrapper.is !== "function" || wrapper.is(":visible");
	},

	p6b_key(e) {
		if (e.defaultPrevented || !this.p6b_page_live()) return;
		const target = e.target;
		const editable = !!(
			target &&
			((target.closest && target.closest("input, textarea, select, [contenteditable]:not([contenteditable='false'])")) ||
				target.isContentEditable)
		);
		const action = MP6B_PURE.shortcut({
			key: e.key,
			ctrl: e.ctrlKey,
			meta: e.metaKey,
			shift: e.shiftKey,
			alt: e.altKey,
			editable,
			dialog: !!(window.cur_dialog && window.cur_dialog.display),
		});
		if (!action) return;
		if (target && target.closest && target.closest(".pk-menu")) return;
		if (action === "escape") {
			this.p6b_escape();
			return;
		}
		if (!this.p6b_run_key(action)) return;
		e.preventDefault();
		e.stopPropagation();
	},

	p6b_run_key(action) {
		if (action === "today") {
			this.go(this.view, frappe.datetime.get_today());
			return true;
		}
		if (action === "prev" || action === "next") {
			this.shift(action === "prev" ? -1 : 1);
			return true;
		}
		if (action.startsWith("view")) {
			// The Maintenance Planner's own order: month, week, crew.
			const view = MP.views[Number(action.slice(4)) - 1];
			if (!view) return false;
			this.go(view, this.anchor);
			return true;
		}
		if (action === "undo") {
			this.undo();
			return true;
		}
		if (action === "legend") {
			if (!this.p6b_kit()) return false;
			this.p6a_legend();
			return true;
		}
		if (action === "search") {
			if (!this.$p6b_search || !this.$p6b_search.is(":visible")) return false;
			this.$p6b_search.trigger("focus").trigger("select");
			return true;
		}
		return false;
	},

	p6b_escape() {
		if (this.drag) return;
		const kit = this.p6b_kit();
		if (kit && kit.guard && typeof kit.guard.top === "function" && kit.guard.top()) return;
		this.p6b_clear_selection();
	},

	p6b_legend_sections() {
		const key = (text) => `<kbd class="mp-p6b-kbd">${mp_esc(text)}</kbd>`;
		const item = (sample_html, text) => ({ sample_html, text: __(text) });
		const own = [
			{
				title: __("Keyboard"),
				note: __("Not while typing in a box or with a dialog open."),
				items: [
					item(key("T"), "Go to today."),
					item(key("←") + key("→"), "The previous or next month or week."),
					item(key("1") + key("2") + key("3"), "Month, week or crew view."),
					item(key("Ctrl") + key("Z"), "Undo the last change (⌘Z on a Mac)."),
					item(key("/"), "Search visits, sites and technicians."),
					item(key("?"), "This help."),
					item(key("Esc"), "Close a menu or side panel, or clear the selection."),
				],
			},
			{
				title: __("Faster scheduling"),
				items: [
					item("", "Right-click a visit, an empty spot or a name (on a touch screen, hold it still for a moment) for a menu: assign someone, move to the technician's next free day, the site at a glance."),
					item(
						`<span class="mp-p6a-sample mp-p6b-selected" style="outline:2px solid #7c3aed">${mp_esc(__("Visit"))}</span>`,
						"Shift-click (or Ctrl- or ⌘-click) draft visits to select several. Drag one and they all move by the same number of calendar days."
					),
					item("", "Assign to… lists the technicians who are free that day. Nobody is picked for you."),
				],
			},
		];
		const extra = (this.p6_legend_providers || []).flatMap((provider) => {
			try {
				return provider() || [];
			} catch (e) {
				return [];
			}
		});
		return own.concat(extra);
	},
};

Object.assign(MaintenancePlanner.prototype, MP6B_METHODS);
