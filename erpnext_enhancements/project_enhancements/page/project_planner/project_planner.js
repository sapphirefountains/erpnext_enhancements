// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Project Planner: who is free, and which project tasks they are booked on, moved by dragging.
//
//   /desk/project-planner                     this week
//   /desk/project-planner/week/2026-10-12     the week holding that day
//   /desk/project-planner/month/2026-10-01    the month holding that day
//   /desk/project-planner/crew/2026-10-12     the crew timeline for the week holding that day
//   /desk/project-planner/route/<person>/2026-10-12
//                                             one person's driving day: stops in order, drive
//                                             time between them, and a map
//
// Every move between weeks, months and views is a route, so Back and Forward step through them
// (Nik's rule: never break Back/Forward). The page moves only with frappe.set_route; Back and
// Forward re-render through on_page_show -> handle_route, exactly like the Maintenance Planner.
//
// Everything comes from erpnext_enhancements.api.project_planner.get_planner, which reads the
// shared availability engine (project_enhancements/crew_availability.py), which also pads each
// person-day with the driving it takes (shop -> stops -> shop). get_route draws one such day;
// suggest_dates ranks the days a task could go by how little driving it adds. The Maintenance Planner
// reads the same engine, so a person's free hours are the same number in both planners.
// Maintenance visits, rental crew tasks and travel use people's hours too; they show here
// read-only ("Maintenance, rentals, travel") and are moved in their own planners.
//
// What a planner does here:
//   - drag a task card to another day             save_task(start)   (duration is kept)
//   - drag a person from Resources available
//     onto a task card                            add_crew
//   - in the crew view, drag a task chip to
//     another day on the same row                 save_task(start)
//     or onto another person's row                swap_crew          (and the day, if it changed)
//   - click a card                                a dialog for dates, hours, crew and
//                                                 qualifications                       save_task
//   - "Suggest dates" on a card or its dialog     suggest_dates, then Book -> save_task
//   - the route icon / drive line on a person's
//     day                                         the route view (get_route)
// Overbooking warns and never blocks: when a change would put someone over their hours, on a day
// off or in two places at once, the server answers needs_reason instead of saving, and the page
// asks for a reason and sends the change again with it. The reason lands on the task's timeline.
//
// Dragging is done with pointer events rather than HTML5 drag and drop, which phones and
// tablets do not support. A touch has to rest on a card for a moment before it lifts, so a
// swipe still scrolls the page.

const PP = {
	route: "project-planner",
	api: "erpnext_enhancements.api.project_planner",
	views: ["week", "month", "crew"],
	route_view: "route",
	groups: ["Field", "PM", "Design", "Subcontractor"],
	prefs: {
		project: "ee_project_planner_project",
		pm: "ee_project_planner_pm",
		group: "ee_project_planner_group",
		foreign: "ee_project_planner_foreign",
		panel: "ee_project_planner_panel",
	},
	hold_ms: 300,
	drag_px: 6,
	undo_max: 20,
	tray_limit: 60,
	default_color: "#2563eb",
	undo_reason: "Undo on the Project Planner",
};

const pp_ymd = (m) => m.format("YYYY-MM-DD");
const pp_esc = (value) => frappe.utils.escape_html(value == null ? "" : String(value));
// 2 -> "2", 2.5 -> "2.5", 1.25 -> "1.25": the engine's fmt_hours, so both sides print alike.
const pp_hours = (value) => String(Math.round((Number(value) || 0) * 100) / 100);
// Colors come from records people edit; only a plain hex value goes into a style attribute.
const pp_color = (value, fallback) => (/^#[0-9a-fA-F]{3,8}$/.test(String(value || "")) ? value : fallback);
const pp_when = (ymd) => moment(ymd, "YYYY-MM-DD").format("ddd, MMM D");
// 70 -> "1h 10m", 25 -> "25m", 120 -> "2h": drive time as people say it.
const pp_drive = (minutes) => {
	const total = Math.max(0, Math.round(Number(minutes) || 0));
	const h = Math.floor(total / 60);
	const m = total % 60;
	if (!h) return `${m}m`;
	return m ? `${h}h ${m}m` : `${h}h`;
};
const pp_km = (value) => String(Math.round((Number(value) || 0) * 10) / 10);
const pp_valid_point = (lat, lng) =>
	Number.isFinite(Number(lat)) && Number.isFinite(Number(lng)) && (Number(lat) !== 0 || Number(lng) !== 0);

frappe.pages[PP.route].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Project Planner"),
		single_column: true,
	});
	wrapper.project_planner = new ProjectPlanner(page);
};

frappe.pages[PP.route].on_page_show = function (wrapper) {
	if (wrapper.project_planner) {
		wrapper.project_planner.handle_route();
	}
};

const PP_STYLE = `
.pp-wrap{padding-bottom:48px;color:var(--text-color);}
.pp-toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:4px 0 10px;}
.pp-title{font-weight:600;font-size:16px;margin:0 6px;min-width:150px;}
.pp-seg{display:inline-flex;border:1px solid var(--border-color);border-radius:8px;overflow:hidden;}
.pp-seg button{border:none;background:var(--card-bg);padding:4px 12px;font-size:13px;color:var(--text-color);}
.pp-seg button + button{border-left:1px solid var(--border-color);}
.pp-seg button.pp-on{background:var(--primary,#2490ef);color:#fff;}
.pp-spacer{flex:1 1 auto;}
.pp-toolbar select{width:auto;max-width:200px;}
.pp-toolbar label{display:inline-flex;align-items:center;gap:5px;margin:0;font-size:13px;font-weight:normal;}
.pp-undo.pp-empty{opacity:.55;}
.pp-section{border:1px solid var(--border-color);border-radius:10px;background:var(--card-bg);margin-bottom:10px;}
.pp-section-head{display:flex;align-items:center;gap:8px;padding:6px 10px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);font-weight:600;}
.pp-section-head .btn{text-transform:none;letter-spacing:0;}
.pp-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;}
.pp-res-grid{display:grid;min-width:0;}
.pp-res-grid > div{border-top:1px solid var(--border-color);}
.pp-res-head{font-size:11px;color:var(--text-muted);padding:3px 4px;text-align:center;white-space:nowrap;}
.pp-res-head.pp-today{color:var(--primary,#2490ef);font-weight:700;}
.pp-person{display:flex;align-items:center;gap:6px;padding:3px 8px;font-size:12px;min-width:0;cursor:grab;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none;position:sticky;left:0;background:var(--card-bg);z-index:1;}
.pp-person:hover{background:var(--control-bg);}
.pp-person-name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600;}
.pp-person-group{font-size:10px;color:var(--text-muted);white-space:nowrap;}
.pp-dot{flex:0 0 auto;width:10px;height:10px;border-radius:50%;}
.pp-avail{padding:3px 4px;font-size:11px;min-width:0;display:flex;flex-direction:column;justify-content:center;gap:2px;border-left:1px solid var(--border-color);}
.pp-avail-text{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-bar{height:4px;border-radius:2px;background:var(--control-bg);overflow:hidden;}
.pp-bar i{display:block;height:100%;}
.pp-green .pp-bar i{background:#16a34a;}
.pp-amber .pp-bar i{background:#d97706;}
.pp-red .pp-bar i{background:#dc2626;}
.pp-red .pp-avail-text,.pp-red .pp-cap-text{color:#b91c1c;font-weight:600;}
.pp-offday{background:repeating-linear-gradient(135deg,transparent 0 6px,var(--control-bg) 6px 8px);color:var(--text-muted);}
.pp-conflict{box-shadow:inset 0 0 0 2px rgba(220,38,38,.55);}
.pp-mini{padding:0;min-height:22px;}
.pp-mini.pp-green{background:rgba(22,163,74,.22);}
.pp-mini.pp-amber{background:rgba(217,119,6,.28);}
.pp-mini.pp-red{background:rgba(220,38,38,.38);}
.pp-mini.pp-idle{background:transparent;}
.pp-tray{border:1px dashed var(--border-color);border-radius:10px;padding:8px 10px;margin-bottom:10px;}
.pp-tray h5{margin:0 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);display:flex;align-items:center;gap:8px;}
.pp-tray h5 .btn{text-transform:none;letter-spacing:0;}
.pp-tray-cards{display:flex;flex-wrap:wrap;gap:6px;}
.pp-tray .pp-card{width:230px;}
.pp-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));border:1px solid var(--border-color);border-radius:10px;overflow:hidden;background:var(--card-bg);}
.pp-dow{padding:6px 8px;font-size:11px;font-weight:600;color:var(--text-muted);border-bottom:1px solid var(--border-color);text-transform:uppercase;letter-spacing:.04em;}
.pp-day{min-height:118px;padding:4px;border-right:1px solid var(--border-color);border-bottom:1px solid var(--border-color);display:flex;flex-direction:column;gap:4px;min-width:0;}
.pp-day:nth-child(7n){border-right:none;}
.pp-week .pp-day{min-height:380px;}
.pp-week .pp-dow{display:none;}
.pp-day-head{font-size:12px;color:var(--text-muted);display:flex;align-items:center;justify-content:space-between;gap:4px;padding:0 2px;}
.pp-day-num{border-radius:10px;padding:0 6px;cursor:pointer;}
.pp-day-num:hover{background:var(--control-bg);}
.pp-day.pp-today .pp-day-num{background:var(--primary,#2490ef);color:#fff;font-weight:600;}
.pp-day.pp-out{background:var(--control-bg);}
.pp-day.pp-out .pp-day-head{opacity:.55;}
.pp-day.pp-past .pp-day-head{opacity:.6;}
.pp-day-free{font-size:11px;white-space:nowrap;}
.pp-over{outline:2px solid var(--primary,#2490ef);outline-offset:-2px;background:rgba(36,144,239,.08);}
.pp-over-past{outline:2px dashed #d97706;outline-offset:-2px;background:rgba(217,119,6,.08);}
.pp-card{position:relative;border:1px solid var(--border-color);border-left:4px solid #2563eb;border-radius:6px;background:var(--card-bg);padding:3px 6px;font-size:12px;line-height:1.3;cursor:pointer;min-width:0;}
.pp-card:hover,.pp-card:focus{box-shadow:0 1px 4px rgba(0,0,0,.18);outline:none;}
.pp-card.pp-movable{cursor:grab;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none;}
.pp-card.pp-over{outline:2px solid var(--primary,#2490ef);outline-offset:1px;}
.pp-card-top{display:flex;align-items:center;gap:4px;}
.pp-card-title{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1 1 auto;min-width:0;}
.pp-card-sub{color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:11px;}
.pp-card-chips{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-card-chips:empty{display:none;}
.pp-badges{display:inline-flex;flex:0 0 auto;}
.pp-badge{width:20px;height:20px;border-radius:50%;font-size:9px;font-weight:700;color:#fff;display:inline-flex;align-items:center;justify-content:center;border:1px solid var(--card-bg);margin-left:-4px;}
.pp-badge:first-child{margin-left:0;}
.pp-badge.pp-more{background:var(--gray-500,#64748b);}
.pp-badge.pp-lead{box-shadow:0 0 0 2px #f59e0b;}
.pp-chip{display:inline-block;font-size:10px;border-radius:8px;padding:0 6px;margin-right:3px;background:var(--control-bg);color:var(--text-muted);}
.pp-chip.pp-red{background:rgba(220,38,38,.12);color:#b91c1c;}
.pp-chip.pp-amber{background:rgba(217,119,6,.14);color:#b45309;}
.pp-card.pp-overdue{border-left-color:#dc2626 !important;}
.pp-card.pp-saving{opacity:.55;pointer-events:none;}
.pp-card.pp-dragging{opacity:.3;}
.pp-card.pp-dim{opacity:.45;}
.pp-fcard{border:1px dashed var(--border-color);border-left:4px solid #94a3b8;border-radius:6px;background:var(--control-bg);padding:3px 6px;font-size:11px;line-height:1.3;color:var(--text-muted);cursor:pointer;min-width:0;}
.pp-fcard.pp-k-visit{border-left-color:#64748b;}
.pp-fcard.pp-k-rental{border-left-color:#d97706;background:rgba(217,119,6,.08);}
.pp-fcard.pp-k-travel{border-left-color:#a16207;background:rgba(234,179,8,.10);}
.pp-fcard-title{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600;}
.pp-fcard-sub{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-crew{display:grid;grid-template-columns:160px repeat(7,minmax(118px,1fr));border:1px solid var(--border-color);border-radius:10px;background:var(--card-bg);min-width:980px;}
.pp-crew > div{border-top:1px solid var(--border-color);}
.pp-crew-head{padding:6px 8px;font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em;border-left:1px solid var(--border-color);cursor:pointer;border-top:none !important;}
.pp-crew-head.pp-today{color:var(--primary,#2490ef);}
.pp-crew-corner{border-top:none !important;}
.pp-crew .pp-person{align-items:flex-start;flex-direction:column;gap:0;padding:6px 8px;}
.pp-cell{border-left:1px solid var(--border-color);padding:4px;display:flex;flex-direction:column;gap:3px;min-width:0;min-height:64px;}
.pp-cell.pp-past{background:rgba(0,0,0,.02);}
.pp-cap{display:flex;flex-direction:column;gap:2px;font-size:11px;}
.pp-cap-text{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text-muted);}
.pp-cell .pp-card,.pp-cell .pp-fcard{font-size:11px;padding:2px 5px;}
.pp-ghost{position:fixed;z-index:1100;pointer-events:none;box-shadow:0 10px 28px rgba(0,0,0,.28);transform:rotate(2deg);opacity:.96;margin:0;}
body.pp-drag-active,body.pp-drag-active *{cursor:grabbing !important;-webkit-user-select:none;user-select:none;}
.pp-loading .pp-grid,.pp-loading .pp-tray,.pp-loading .pp-crew,.pp-loading .pp-res-grid{opacity:.6;}
.pp-hint{font-size:12px;color:var(--text-muted);margin-top:8px;}
.pp-summary{width:100%;font-size:13px;margin-bottom:6px;}
.pp-summary th{color:var(--text-muted);font-weight:normal;padding:3px 10px 3px 0;vertical-align:top;white-space:nowrap;width:1%;}
.pp-summary td{padding:3px 0;}
.pp-links{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 2px;}
.pp-why{margin:6px 0;}
.pp-why ul{margin:2px 0 0;padding-left:18px;}
.pp-empty-note{font-size:12px;color:var(--text-muted);padding:8px 10px;}
.pp-drive{display:flex;flex-wrap:wrap;align-items:center;gap:3px;font-size:10px;color:var(--text-muted);min-width:0;}
.pp-drive-text{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-route{display:inline-flex;align-items:center;gap:3px;cursor:pointer;color:var(--text-muted);border-radius:6px;padding:0 3px;}
.pp-route:hover,.pp-route:focus{color:var(--primary,#2490ef);background:var(--control-bg);outline:none;}
.pp-route svg{width:12px;height:12px;flex:0 0 auto;}
.pp-suggest-row{margin-top:3px;}
.pp-route-view{padding-bottom:24px;}
.pp-route-head{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:4px 0 6px;}
.pp-route-title{font-weight:600;font-size:16px;margin:0 6px;}
.pp-route-totals{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:13px;margin:0 0 10px;}
.pp-route-note{font-size:12px;color:var(--text-muted);border:1px dashed var(--border-color);border-radius:8px;padding:6px 10px;margin-bottom:10px;}
.pp-route-cols{display:grid;grid-template-columns:minmax(300px,420px) minmax(0,1fr);gap:12px;align-items:start;}
.pp-stops{border:1px solid var(--border-color);border-radius:10px;background:var(--card-bg);}
.pp-stop{display:flex;gap:10px;padding:8px 10px;border-top:1px solid var(--border-color);font-size:13px;}
.pp-stop:first-child{border-top:none;}
.pp-stop-num{flex:0 0 auto;width:24px;height:24px;border-radius:50%;background:#2563eb;color:#fff;font-size:12px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;}
.pp-stop-num.pp-shop{background:#16a34a;}
.pp-stop-body{min-width:0;flex:1 1 auto;}
.pp-stop-time{font-size:12px;color:var(--text-muted);}
.pp-stop-label{font-weight:600;}
.pp-stop-sub{font-size:12px;color:var(--text-muted);}
.pp-stop-leg{font-size:11px;color:var(--text-muted);margin-top:2px;}
.pp-stop-link{cursor:pointer;color:var(--primary,#2490ef);}
.pp-stop.pp-unlocated{background:var(--control-bg);}
.pp-stop.pp-unlocated .pp-stop-num{background:var(--gray-500,#64748b);}
.pp-stop.pp-unlocated .pp-stop-label,.pp-stop.pp-unlocated .pp-stop-sub{color:var(--text-muted);}
.pp-stop-missing{font-size:12px;color:#b45309;margin-top:2px;}
.pp-route-cols.pp-nomap{grid-template-columns:minmax(0,640px);}
.pp-map{height:460px;border:1px solid var(--border-color);border-radius:10px;overflow:hidden;background:var(--control-bg);}
.pp-sug{width:100%;font-size:13px;border-collapse:collapse;}
.pp-sug td{padding:6px 8px 6px 0;border-top:1px solid var(--border-color);vertical-align:top;}
.pp-sug tr:first-child td{border-top:none;}
.pp-sug-when{white-space:nowrap;font-weight:600;}
.pp-sug-reason{color:var(--text-muted);font-size:12px;}
@media (max-width:760px){
.pp-title{min-width:0;width:100%;order:-1;margin:0;}
.pp-toolbar select{max-width:none;flex:1 1 40%;}
.pp-spacer{display:none;}
.pp-day{min-height:84px;padding:2px;}
.pp-month .pp-card{padding:1px 3px;font-size:10px;border-left-width:3px;}
.pp-month .pp-card-sub,.pp-month .pp-badges,.pp-month .pp-card-chips,.pp-month .pp-fcard-sub{display:none;}
.pp-week .pp-grid{grid-template-columns:1fr;}
.pp-week .pp-day{min-height:0;border-right:none;}
.pp-tray .pp-card{width:100%;}
.pp-crew{grid-template-columns:110px repeat(7,minmax(104px,1fr));min-width:840px;}
.pp-route-cols{grid-template-columns:minmax(0,1fr);}
.pp-map{height:320px;}
.pp-route-title{min-width:0;width:100%;order:-1;margin:0;}
.pp-sug td{display:block;border-top:none;padding:2px 0;}
.pp-sug tr{display:block;border-top:1px solid var(--border-color);padding:6px 0;}
.pp-sug tr:first-child{border-top:none;}
}
`;

class ProjectPlanner {
	constructor(page) {
		this.page = page;
		if (!document.getElementById("pp-style")) {
			$("<style id='pp-style'>").text(PP_STYLE).appendTo(document.head);
		}
		this.view = "week";
		this.anchor = frappe.datetime.get_today();
		this.project = this.load_pref(PP.prefs.project, "");
		this.pm = this.load_pref(PP.prefs.pm, "");
		this.group = this.load_pref(PP.prefs.group, "");
		this.show_foreign = this.load_pref(PP.prefs.foreign, "1") !== "0";
		this.panel_open = this.load_pref(PP.prefs.panel, "1") !== "0";
		this.show_all_unscheduled = false;
		this.data = null;
		this.by_task = {};
		this.by_resource = {};
		this.by_project = {};
		this.by_foreign = {};
		this.task_days = {};
		this.needs_crew = new Set();
		// The newest `modified` seen for each task: a save answers with one, and Undo needs it
		// even after the task has moved out of the range on screen.
		this.modified = {};
		this.undo_stack = [];
		this.request = 0;
		this.drag = null;
		this.click_blocked_until = 0;
		// The route view: one person's day. `route_map` is the live Google map (rebuilt on every
		// render; a theme flip is picked up the next time the view opens).
		this.route_resource = "";
		this.route_date = "";
		this.route_data = null;
		this.route_map = null;

		this.page.add_menu_item(__("Planner Resources"), () => frappe.set_route("List", "Planner Resource"));
		this.page.add_menu_item(__("Project Planner Settings"), () =>
			frappe.set_route("Form", "Project Planner Settings")
		);
		this.$body = $('<div class="pp-wrap"></div>').appendTo(page.main);
		this.build_shell();
		this.bind_drag();
	}

	// ------------------------------------------------------------------ routing

	route_target() {
		const route = frappe.get_route() || [];
		if (route[0] !== PP.route) return null;
		// /project-planner/route/<person>/<date>
		if (route[1] === PP.route_view && route[2]) {
			const date = route[3] && moment(route[3], "YYYY-MM-DD", true).isValid() ? route[3] : frappe.datetime.get_today();
			return { view: PP.route_view, resource: route[2], anchor: date };
		}
		const view = PP.views.includes(route[1]) ? route[1] : "week";
		const day = route[2] && moment(route[2], "YYYY-MM-DD", true).isValid() ? route[2] : frappe.datetime.get_today();
		return { view, anchor: day };
	}

	handle_route() {
		const target = this.route_target();
		if (!target) return;
		this.view = target.view;
		this.anchor = target.anchor;
		if (target.view === PP.route_view) {
			this.route_resource = target.resource;
			this.route_date = target.anchor;
			this.set_mode(true);
			this.load_route();
			return;
		}
		this.set_mode(false);
		this.load();
	}

	// The route view replaces the toolbar, panel, trays and calendar; the others replace it.
	// (The panel is shown and hidden by render_panel, so leaving this view does not reveal a stale one.)
	set_mode(route) {
		this.$toolbar.toggle(!route);
		this.$trays.toggle(!route);
		this.$grid_wrap.toggle(!route);
		this.$hint.toggle(!route);
		this.$route.toggle(!!route);
		if (route) this.$panel.hide();
		else this.clear_route_map();
	}

	go_route(resource, ymd) {
		const route = frappe.get_route() || [];
		if (route[0] === PP.route && route[1] === PP.route_view && route[2] === resource && route[3] === ymd) {
			this.load_route();
			return;
		}
		frappe.set_route(PP.route, PP.route_view, resource, ymd);
	}

	go(view, anchor) {
		const route = frappe.get_route() || [];
		if (route[0] === PP.route && route[1] === view && route[2] === anchor) {
			this.load();
			return;
		}
		frappe.set_route(PP.route, view, anchor);
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
		for (let day = start.clone(); !day.isAfter(end, "day"); day.add(1, "days")) days.push(pp_ymd(day));
		return days;
	}

	shift(sign) {
		const anchor = moment(this.anchor, "YYYY-MM-DD");
		const next =
			this.view === "month" ? anchor.startOf("month").add(sign, "months") : anchor.add(sign * 7, "days");
		this.go(this.view, pp_ymd(next));
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
		const $bar = $('<div class="pp-toolbar"></div>').appendTo(this.$body);
		this.$toolbar = $bar;
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
		this.$title = $('<span class="pp-title"></span>').appendTo($bar);

		const $seg = $('<span class="pp-seg"></span>').appendTo($bar);
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

		$('<span class="pp-spacer"></span>').appendTo($bar);
		this.$project = this.make_select($bar, __("Project"), (value) => {
			this.project = value;
			this.save_pref(PP.prefs.project, value);
			this.show_all_unscheduled = false;
			this.render();
		});
		this.$pm = this.make_select($bar, __("Project manager"), (value) => {
			this.pm = value;
			this.save_pref(PP.prefs.pm, value);
			this.render();
		});
		this.$group = this.make_select($bar, __("Group"), (value) => {
			this.group = value;
			this.save_pref(PP.prefs.group, value);
			this.render();
		});
		$("<option></option>").val("").text(__("All groups")).appendTo(this.$group);
		PP.groups.forEach((group) => $("<option></option>").val(group).text(__(group)).appendTo(this.$group));
		this.$group.val(PP.groups.includes(this.group) ? this.group : "");
		this.group = this.$group.val() || "";

		const $foreign = $("<label></label>").appendTo($bar);
		$('<input type="checkbox">')
			.prop("checked", this.show_foreign)
			.on("change", (e) => {
				this.show_foreign = e.target.checked;
				this.save_pref(PP.prefs.foreign, this.show_foreign ? "1" : "0");
				this.render();
			})
			.appendTo($foreign);
		$foreign.append(document.createTextNode(__("Maintenance, rentals, travel")));

		this.$undo = $('<button class="btn btn-default btn-sm pp-undo pp-empty"></button>')
			.text(__("Undo"))
			.on("click", () => this.undo())
			.appendTo($bar);
		$('<button class="btn btn-default btn-sm">↻</button>')
			.attr("title", __("Refresh"))
			.on("click", () => this.load())
			.appendTo($bar);
		this.update_undo_button();

		this.$panel = $('<div class="pp-section pp-res"></div>').appendTo(this.$body);
		this.$trays = $("<div></div>").appendTo(this.$body);
		this.$grid_wrap = $("<div></div>").appendTo(this.$body);
		this.$hint = $('<div class="pp-hint"></div>').appendTo(this.$body);
		this.$route = $('<div class="pp-route-view"></div>').hide().appendTo(this.$body);
	}

	make_select($bar, label, on_change) {
		const $select = $('<select class="form-control input-sm"></select>')
			.attr("aria-label", label)
			.attr("title", label)
			.appendTo($bar);
		$select.on("change", () => on_change($select.val() || ""));
		return $select;
	}

	// ------------------------------------------------------------------ data

	load() {
		const { start, end } = this.range();
		const token = ++this.request;
		this.$title.text(this.title());
		Object.entries(this.$view_buttons).forEach(([view, $button]) => $button.toggleClass("pp-on", view === this.view));
		this.$body.addClass("pp-loading");
		return Promise.resolve(
			frappe.call({ method: `${PP.api}.get_planner`, args: { start: pp_ymd(start), end: pp_ymd(end) } })
		)
			.then((r) => {
				if (token !== this.request) return;
				this.data = (r && r.message) || {};
				this.$body.removeClass("pp-loading");
				this.index();
				this.fill_filters();
				this.render();
			})
			.catch(() => {
				if (token === this.request) this.$body.removeClass("pp-loading");
			});
	}

	index() {
		const data = this.data;
		this.by_task = {};
		this.by_resource = {};
		this.by_project = {};
		this.by_foreign = {};
		this.task_days = {};
		[].concat(data.tasks || [], data.unscheduled || []).forEach((card) => {
			this.by_task[card.name] = card;
			if (card.modified) this.modified[card.name] = card.modified;
		});
		(data.resources || []).forEach((resource) => (this.by_resource[resource.name] = resource));
		(data.projects || []).forEach((project) => (this.by_project[project.name] = project));
		(data.foreign || []).forEach((item) => (this.by_foreign[item.key] = item));
		// Which person-days each task is booked on, so a card can say it is in a conflict.
		Object.entries(data.days || {}).forEach(([resource, days]) => {
			Object.entries(days || {}).forEach(([ymd, day]) => {
				(day.bookings || []).forEach((booking) => {
					if (!booking.ref || !this.by_task[booking.ref]) return;
					(this.task_days[booking.ref] = this.task_days[booking.ref] || []).push({ resource, ymd, day, booking });
				});
			});
		});
		this.needs_crew = new Set(data.needs_crew || []);
	}

	fill_filters() {
		const projects = (this.data.projects || [])
			.slice()
			.sort((a, b) => String(a.title || a.name).localeCompare(String(b.title || b.name)));
		this.$project.empty();
		$("<option></option>").val("").text(__("All projects")).appendTo(this.$project);
		projects.forEach((project) => {
			const label = project.title && project.title !== project.name ? `${project.title} (${project.name})` : project.name;
			$("<option></option>").val(project.name).text(label).appendTo(this.$project);
		});
		if (this.project && !this.by_project[this.project]) this.project = "";
		this.$project.val(this.project);

		const pms = [...new Set(projects.map((project) => project.pm).filter(Boolean))].sort();
		this.$pm.empty();
		$("<option></option>").val("").text(__("All project managers")).appendTo(this.$pm);
		pms.forEach((pm) => $("<option></option>").val(pm).text(pm).appendTo(this.$pm));
		if (this.pm && !pms.includes(this.pm)) this.pm = "";
		this.$pm.val(this.pm);
	}

	// ------------------------------------------------------------------ filters

	task_visible(card) {
		if (this.project && card.project !== this.project) return false;
		if (this.pm) {
			const project = card.project && this.by_project[card.project];
			if (!project || project.pm !== this.pm) return false;
		}
		return true;
	}

	resources() {
		const order = (group) => {
			const index = PP.groups.indexOf(group);
			return index < 0 ? PP.groups.length : index;
		};
		return (this.data.resources || [])
			.filter((resource) => !this.group || resource.group === this.group)
			.slice()
			.sort((a, b) => order(a.group) - order(b.group) || String(a.label).localeCompare(String(b.label)));
	}

	resource_label(name, fallback) {
		const resource = this.by_resource[name];
		return (resource && resource.label) || fallback || name;
	}

	resource_color(name) {
		const resource = this.by_resource[name];
		if (resource && pp_color(resource.color, "")) return resource.color;
		let hash = 0;
		for (const ch of String(name || "")) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
		return `hsl(${hash},55%,42%)`;
	}

	day_of(resource, ymd) {
		const days = (this.data.days || {})[resource];
		return (days && days[ymd]) || null;
	}

	// ------------------------------------------------------------------ render

	render() {
		if (!this.data) return;
		this.render_panel();
		this.render_trays();
		if (this.view === "crew") this.render_crew();
		else this.render_calendar();
		this.$hint.text(
			this.view === "crew"
				? __(
						"Drag a task to another day on the same row to move it, or onto someone else's row to hand their place on the crew to that person. On a touch screen, hold for a moment first."
				  )
				: __(
						"Drag a task card to another day to move it; it keeps its length. Drag a person from Resources available onto a task to add them to its crew. On a touch screen, hold for a moment first. Click a card to change its dates, hours and crew."
				  )
		);
	}

	// The availability of one person on one day, as a class and a short text.
	avail_state(day) {
		if (!day) return { cls: "pp-idle", text: "", ratio: 0 };
		const capacity = Number(day.capacity) || 0;
		const booked = Number(day.booked) || 0;
		const over = booked - capacity;
		const conflict = (day.conflicts || []).length > 0;
		let state;
		if (capacity <= 0 && booked <= 0) {
			const holiday = String(day.off || "").startsWith("Holiday");
			state = { cls: "pp-offday", text: holiday ? __("Holiday") : __("Off"), ratio: 0 };
		} else if (capacity <= 0) {
			state = { cls: "pp-red pp-offday", text: __("Off, {0}h booked", [pp_hours(booked)]), ratio: 1 };
		} else if (over > 0.01) {
			state = { cls: "pp-red", text: __("Over {0}h", [pp_hours(over)]), ratio: 1 };
		} else {
			const ratio = booked / capacity;
			const free = Math.max(0, Number(day.free != null ? day.free : capacity - booked));
			state = {
				cls: ratio <= 0.75 ? "pp-green" : "pp-amber",
				text: free > 0.01 ? __("{0}h free", [pp_hours(free)]) : __("Full"),
				ratio,
			};
			if (day.off) state.text += ` · ${__("½ day off")}`;
		}
		if (conflict) state.cls += " pp-conflict";
		return state;
	}

	day_tip(resource, ymd, day) {
		const lines = [`${this.resource_label(resource)} · ${pp_when(ymd)}`];
		if (!day) return lines.join("\n");
		lines.push(__("{0}h of {1}h booked", [pp_hours(day.booked), pp_hours(day.capacity)]));
		if (day.off) lines.push(__(day.off));
		if (Number(day.drive_minutes) > 0) {
			lines.push(
				`${__("Driving")}: ${pp_drive(day.drive_minutes)} (${this.source_text(day.drive_source)})${
					day.long_drive ? ` · ${__("Long drive")}` : ""
				}`
			);
		}
		if (Number(day.unlocated) > 0) {
			lines.push(__("{0} stop(s) with no location: not counted in the drive", [day.unlocated]));
		}
		(day.bookings || []).forEach((booking) => {
			// The drive is summarized above; it is only listed when the day carries no figure for it.
			if (booking.kind === "drive" && Number(day.drive_minutes) > 0) return;
			const parts = [`${pp_hours(booking.hours)}h`];
			if (booking.slot) parts.push(booking.slot.join("–"));
			if (booking.estimated) parts.push(__("estimated"));
			lines.push(`• ${booking.label || booking.ref} (${parts.join(", ")})`);
		});
		(day.conflicts || []).forEach((text) => lines.push(`${__("Conflict")}: ${text}`));
		(day.warnings || []).forEach((text) => lines.push(`${__("Note")}: ${text}`));
		return lines.join("\n");
	}

	// "Google", "Estimate", "Google + estimate": where a drive time came from.
	source_text(source) {
		if (source === "google") return __("Google");
		if (source === "mixed") return __("Google + estimate");
		return __("estimate");
	}

	// Does this person-day have anything to drive to (so a route is worth opening)?
	has_route(day) {
		if (!day) return false;
		if (Number(day.drive_minutes) > 0) return true;
		return (day.bookings || []).some((b) => b.kind && b.kind !== "drive" && b.kind !== "travel");
	}

	// The drive line of a person-day: "1h 10m drive", a Long drive chip when it is long, and the
	// route icon. Clicking either opens the route view. Empty when there is nothing to show.
	drive_html(resource, ymd, day) {
		if (!this.has_route(day)) return "";
		const minutes = Number(day.drive_minutes) || 0;
		// With no figure for the day the icon stands alone: it still opens the route.
		const text = minutes > 0 ? __("{0} drive", [pp_drive(minutes)]) : "";
		const tip = minutes > 0 ? `${pp_drive(minutes)} · ${this.source_text(day.drive_source)}` : __("Open this day's route");
		const long = day.long_drive ? `<span class="pp-chip pp-red">${pp_esc(__("Long drive"))}</span>` : "";
		const icon =
			'<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5">' +
			'<circle cx="3.5" cy="12.5" r="1.8"/><circle cx="12.5" cy="3.5" r="1.8"/>' +
			'<path d="M5.3 12.5H10a2.5 2.5 0 0 0 0-5H6a2.5 2.5 0 0 1 0-4h4.7"/></svg>';
		return `
			<span class="pp-drive">
				<span class="pp-route" role="button" tabindex="0" data-route-resource="${pp_esc(resource)}"
					data-route-date="${pp_esc(ymd)}" title="${pp_esc(tip)}" aria-label="${pp_esc(
			__("Open the route for {0}", [pp_when(ymd)])
		)}">${icon}${text ? `<span class="pp-drive-text">${pp_esc(text)}</span>` : ""}</span>${long}
			</span>`;
	}

	person_html(resource, compact) {
		const color = this.resource_color(resource.name);
		const group = resource.group ? __(resource.group) : "";
		return `
			<div class="pp-person" data-resource="${pp_esc(resource.name)}"
				title="${pp_esc(__("Drag {0} onto a task to add them to its crew", [resource.label]))}">
				${compact ? "" : `<span class="pp-dot" style="background:${pp_esc(color)}"></span>`}
				<span class="pp-person-name">${pp_esc(resource.label)}</span>
				<span class="pp-person-group">${pp_esc(group)}</span>
			</div>`;
	}

	render_panel() {
		this.$panel.toggle(this.view !== "crew").empty();
		if (this.view === "crew") return;
		const resources = this.resources();
		const $head = $('<div class="pp-section-head"></div>').appendTo(this.$panel);
		$("<span></span>").text(__("Resources available")).appendTo($head);
		$('<span class="pp-spacer"></span>').appendTo($head);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(this.panel_open ? __("Hide") : __("Show"))
			.on("click", () => {
				this.panel_open = !this.panel_open;
				this.save_pref(PP.prefs.panel, this.panel_open ? "1" : "0");
				this.render_panel();
			})
			.appendTo($head);
		if (!this.panel_open) return;
		if (!resources.length) {
			$('<div class="pp-empty-note"></div>')
				.text(
					this.group
						? __("Nobody in this group. Add people under Planner Resources.")
						: __("No planner resources yet. Add the people you schedule under Planner Resources.")
				)
				.appendTo(this.$panel);
			return;
		}
		const days = this.range_days();
		const today = this.today();
		const month = this.view === "month";
		const columns = month ? `150px repeat(${days.length},minmax(20px,1fr))` : "170px repeat(7,minmax(74px,1fr))";
		const html = [];
		html.push(`<div class="pp-res-head"></div>`);
		days.forEach((ymd) => {
			const m = moment(ymd, "YYYY-MM-DD");
			const label = month ? m.format("D") : m.format("ddd D");
			html.push(`<div class="pp-res-head${ymd === today ? " pp-today" : ""}">${pp_esc(label)}</div>`);
		});
		resources.forEach((resource) => {
			html.push(this.person_html(resource, false));
			days.forEach((ymd) => {
				const day = this.day_of(resource.name, ymd);
				const state = this.avail_state(day);
				const tip = this.day_tip(resource.name, ymd, day);
				if (month) {
					html.push(`<div class="pp-avail pp-mini ${state.cls}" title="${pp_esc(tip)}"></div>`);
					return;
				}
				const width = Math.round(Math.min(1, state.ratio) * 100);
				html.push(`
					<div class="pp-avail ${state.cls}" title="${pp_esc(tip)}">
						<span class="pp-avail-text">${pp_esc(state.text)}</span>
						<div class="pp-bar"><i style="width:${width}%"></i></div>
						${this.drive_html(resource.name, ymd, day)}
					</div>`);
			});
		});
		const $scroll = $('<div class="pp-scroll"></div>').appendTo(this.$panel);
		$('<div class="pp-res-grid"></div>')
			.css("grid-template-columns", columns)
			.css("min-width", month ? `${150 + days.length * 22}px` : "700px")
			.html(html.join(""))
			.appendTo($scroll);
	}

	render_trays() {
		this.$trays.empty();
		const needs = (this.data.tasks || [])
			.filter((card) => this.needs_crew.has(card.name) && this.task_visible(card))
			.sort((a, b) => this.compare(a, b));
		if (needs.length) {
			const $tray = $('<div class="pp-tray pp-tray-needs"></div>').appendTo(this.$trays);
			$("<h5></h5>")
				.text(__("Needs crew ({0}): drag a person onto a task", [needs.length]))
				.appendTo($tray);
			const $cards = $('<div class="pp-tray-cards"></div>').appendTo($tray);
			$cards.html(needs.map((card) => this.card_html(card, null)).join(""));
		}

		const unscheduled = (this.data.unscheduled || [])
			.filter((card) => this.task_visible(card))
			.sort((a, b) => this.compare(a, b));
		if (unscheduled.length) {
			const $tray = $('<div class="pp-tray pp-tray-unscheduled"></div>').appendTo(this.$trays);
			const $h = $("<h5></h5>")
				.append(document.createTextNode(__("Unscheduled ({0}): drag onto a day", [unscheduled.length])))
				.appendTo($tray);
			const shown = this.show_all_unscheduled ? unscheduled : unscheduled.slice(0, PP.tray_limit);
			if (unscheduled.length > shown.length) {
				$('<button type="button" class="btn btn-default btn-xs"></button>')
					.text(__("Show all"))
					.on("click", () => {
						this.show_all_unscheduled = true;
						this.render_trays();
					})
					.appendTo($h);
			}
			const $cards = $('<div class="pp-tray-cards"></div>').appendTo($tray);
			$cards.html(shown.map((card) => this.card_html(card, null)).join(""));
		}
	}

	// The days of the range a dated task covers.
	covered(card, days) {
		if (!card.start) return [];
		const end = card.end && card.end >= card.start ? card.end : card.start;
		return days.filter((ymd) => ymd >= card.start && ymd <= end);
	}

	render_calendar() {
		const days = this.range_days();
		const today = this.today();
		const month = moment(this.anchor, "YYYY-MM-DD").month();
		const resources = this.resources();
		const visible_people = new Set(resources.map((resource) => resource.name));
		const by_day = {};
		(this.data.tasks || []).forEach((card) => {
			if (!this.task_visible(card)) return;
			this.covered(card, days).forEach((ymd) => (by_day[ymd] = by_day[ymd] || []).push(card));
		});
		const foreign_by_day = {};
		if (this.show_foreign) {
			(this.data.foreign || []).forEach((item) => {
				// A rental crew task is a Task: when it is on the board as a card, it is not repeated.
				if (item.ref && this.by_task[item.ref]) return;
				if (item.resource && !visible_people.has(item.resource)) return;
				if (item.date) (foreign_by_day[item.date] = foreign_by_day[item.date] || []).push(item);
			});
		}

		const html = [];
		const first = this.first_weekday();
		for (let i = 0; i < 7; i++) {
			html.push(`<div class="pp-dow">${pp_esc(moment().day((first + i) % 7).format("ddd"))}</div>`);
		}
		days.forEach((ymd) => {
			const day = moment(ymd, "YYYY-MM-DD");
			const classes = ["pp-day"];
			if (ymd === today) classes.push("pp-today");
			if (ymd < today) classes.push("pp-past");
			if (this.view === "month" && day.month() !== month) classes.push("pp-out");
			const label =
				this.view === "week" ? day.format("ddd, MMM D") : day.date() === 1 ? day.format("MMM D") : day.format("D");
			let free = 0;
			resources.forEach((resource) => {
				const data = this.day_of(resource.name, ymd);
				if (data) free += Math.max(0, Number(data.free) || 0);
			});
			const cards = (by_day[ymd] || []).sort((a, b) => this.compare(a, b));
			html.push(`
				<div class="${classes.join(" ")}" data-date="${pp_esc(ymd)}">
					<div class="pp-day-head">
						<span class="pp-day-num" data-week="${pp_esc(ymd)}" title="${pp_esc(__("Show this week"))}">${pp_esc(label)}</span>
						${
							this.view === "week" && resources.length
								? `<span class="pp-day-free" title="${pp_esc(__("Free hours of the people shown"))}">${pp_esc(
										__("{0}h free", [pp_hours(free)])
								  )}</span>`
								: ""
						}
					</div>
					${cards.map((card) => this.card_html(card, ymd)).join("")}
					${(foreign_by_day[ymd] || []).map((item) => this.foreign_html(item)).join("")}
				</div>`);
		});
		const $grid = $('<div class="pp-grid"></div>').html(html.join(""));
		$grid.find("[data-week]").on("click", (e) => this.go("week", e.currentTarget.getAttribute("data-week")));
		this.$grid_wrap.empty().removeClass("pp-month pp-week pp-crew-view").addClass(`pp-${this.view}`).append($grid);
	}

	render_crew() {
		const days = this.range_days();
		const today = this.today();
		const resources = this.resources();
		const html = [`<div class="pp-crew-corner"></div>`];
		days.forEach((ymd) => {
			html.push(
				`<div class="pp-crew-head${ymd === today ? " pp-today" : ""}" data-week="${pp_esc(ymd)}" title="${pp_esc(
					__("Show this week")
				)}">${pp_esc(pp_when(ymd))}</div>`
			);
		});
		resources.forEach((resource) => {
			html.push(this.person_html(resource, true));
			days.forEach((ymd) => {
				const day = this.day_of(resource.name, ymd);
				const state = this.avail_state(day);
				const width = Math.round(Math.min(1, state.ratio) * 100);
				const chips = ((day && day.bookings) || [])
					.map((booking) => this.booking_html(resource.name, ymd, booking))
					.join("");
				html.push(`
					<div class="pp-cell${ymd < today ? " pp-past" : ""}" data-date="${pp_esc(ymd)}" data-resource="${pp_esc(
					resource.name
				)}">
						<div class="pp-cap ${state.cls}" title="${pp_esc(this.day_tip(resource.name, ymd, day))}">
							<span class="pp-cap-text">${pp_esc(state.text)}</span>
							<div class="pp-bar"><i style="width:${width}%"></i></div>
							${this.drive_html(resource.name, ymd, day)}
						</div>
						${chips}
					</div>`);
			});
		});
		this.$grid_wrap.empty().removeClass("pp-month pp-week").addClass("pp-crew-view");
		if (!resources.length) {
			$('<div class="pp-section pp-empty-note"></div>')
				.text(__("No planner resources to show. Add the people you schedule under Planner Resources."))
				.appendTo(this.$grid_wrap);
			return;
		}
		const $scroll = $('<div class="pp-scroll"></div>').appendTo(this.$grid_wrap);
		const $crew = $('<div class="pp-crew"></div>').html(html.join("")).appendTo($scroll);
		$crew.find("[data-week]").on("click", (e) => this.go("week", e.currentTarget.getAttribute("data-week")));
	}

	// One booking in a crew-view cell: a task the planner can move, or someone else's (static).
	booking_html(resource, ymd, booking) {
		// Driving is padding on the day's hours, not a thing to open: the cell's drive line shows it.
		if (booking.kind === "drive") return "";
		const card = booking.ref && this.by_task[booking.ref];
		const hours = booking.slot ? booking.slot.join("–") : `${pp_hours(booking.hours)}h`;
		if (card) {
			const classes = ["pp-card"];
			if (card.movable && this.data.can_edit) classes.push("pp-movable");
			if (card.saving) classes.push("pp-saving");
			if (!this.task_visible(card)) classes.push("pp-dim");
			if (card.overdue) classes.push("pp-overdue");
			const color = pp_color(card.color, PP.default_color);
			const tip = [card.subject, card.project_title || card.project, hours].filter(Boolean).join("\n");
			return `
				<div class="${classes.join(" ")}" data-task="${pp_esc(card.name)}" data-date="${pp_esc(ymd)}"
					data-resource="${pp_esc(resource)}" tabindex="0" title="${pp_esc(tip)}" style="border-left-color:${pp_esc(color)}">
					<div class="pp-card-title">${pp_esc(card.subject || card.name)}</div>
					<div class="pp-card-sub">${pp_esc(hours)}</div>
				</div>`;
		}
		if (!this.show_foreign) return "";
		return `
			<div class="pp-fcard pp-k-${pp_esc(booking.kind || "other")}" data-ref="${pp_esc(booking.ref || "")}"
				data-kind="${pp_esc(booking.kind || "")}" data-date="${pp_esc(ymd)}" tabindex="0"
				title="${pp_esc([booking.label, hours].filter(Boolean).join("\n"))}">
				<div class="pp-fcard-title">${pp_esc(booking.label || booking.ref)}</div>
				<div class="pp-fcard-sub">${pp_esc(hours)}</div>
			</div>`;
	}

	compare(a, b) {
		return (
			String(a.project_title || a.project || "").localeCompare(String(b.project_title || b.project || "")) ||
			String(a.subject || "").localeCompare(String(b.subject || ""))
		);
	}

	// What is wrong on the days this task is booked: "Austin, Tue Oct 13: Over by 2h".
	task_conflicts(card) {
		const out = [];
		(this.task_days[card.name] || []).forEach(({ resource, ymd, day }) => {
			(day.conflicts || []).forEach((text) => out.push(`${this.resource_label(resource)}, ${pp_when(ymd)}: ${text}`));
		});
		return out;
	}

	hours_text(card) {
		if (card.slot && card.slot.length === 2) return card.slot.join("–");
		if (Number(card.expected_time) > 0) return __("{0}h", [pp_hours(card.expected_time)]);
		return __("Full day");
	}

	badges_html(card) {
		const crew = card.crew || [];
		const shown = crew.slice(0, 4);
		const badges = shown.map((member) => {
			const name = this.resource_label(member.resource, member.label);
			const parts = String(name).split(/\s+/).filter(Boolean);
			const initials = ((parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
			const tip = member.is_lead ? `${name} (${__("lead")})` : name;
			const cls = member.is_lead ? "pp-badge pp-lead" : "pp-badge";
			return `<span class="${cls}" style="background:${pp_esc(
				this.resource_color(member.resource)
			)}" title="${pp_esc(tip)}">${pp_esc(initials)}</span>`;
		});
		if (crew.length > shown.length) {
			badges.push(`<span class="pp-badge pp-more">+${pp_esc(crew.length - shown.length)}</span>`);
		}
		return badges.length ? `<span class="pp-badges">${badges.join("")}</span>` : "";
	}

	// A task card. `ymd` is the day it is drawn on (null in a tray): a drag measures the move from it.
	card_html(card, ymd) {
		const chips = [];
		const red = (text) => `<span class="pp-chip pp-red">${pp_esc(text)}</span>`;
		const plain = (text) => `<span class="pp-chip">${pp_esc(text)}</span>`;
		if (ymd && card.start && card.end && card.end > card.start) {
			const length = moment(card.end, "YYYY-MM-DD").diff(moment(card.start, "YYYY-MM-DD"), "days") + 1;
			const index = moment(ymd, "YYYY-MM-DD").diff(moment(card.start, "YYYY-MM-DD"), "days") + 1;
			chips.push(plain(__("Day {0} of {1}", [index, length])));
		}
		if (Number(card.short) > 0) chips.push(`<span class="pp-chip pp-amber">${pp_esc(__("Needs {0} more", [card.short]))}</span>`);
		else if (!(card.crew || []).length && this.needs_crew.has(card.name)) {
			chips.push(`<span class="pp-chip pp-amber">${pp_esc(__("No crew"))}</span>`);
		}
		const conflicts = this.task_conflicts(card);
		if (conflicts.length) chips.push(red(__("Conflict")));
		if (card.overdue) chips.push(red(__("Overdue")));
		if (card.rental_kind) chips.push(plain(__(card.rental_kind)));
		if (!card.start) chips.push(plain(__("No dates")));

		const classes = ["pp-card"];
		if (card.movable && this.data.can_edit) classes.push("pp-movable");
		if (card.overdue) classes.push("pp-overdue");
		if (card.saving) classes.push("pp-saving");
		const color = pp_color(card.color, PP.default_color);
		const sub = [card.project_title || card.project, this.hours_text(card)].filter(Boolean).join(" · ");
		const crew_names = (card.crew || []).map((member) => this.resource_label(member.resource, member.label));
		const tip = [card.subject, sub, crew_names.join(", "), ...conflicts].filter(Boolean).join("\n");
		// Cards in the Needs crew and Unscheduled trays offer to find a day (ymd is null there).
		const suggest =
			!ymd && card.movable && this.data.can_edit
				? `<div class="pp-suggest-row"><button type="button" class="btn btn-default btn-xs pp-suggest" data-suggest="${pp_esc(
						card.name
				  )}">${pp_esc(__("Suggest dates"))}</button></div>`
				: "";
		return `
			<div class="${classes.join(" ")}" data-task="${pp_esc(card.name)}" data-date="${pp_esc(ymd || "")}"
				tabindex="0" title="${pp_esc(tip)}" style="border-left-color:${pp_esc(color)}">
				<div class="pp-card-top">
					<span class="pp-card-title">${pp_esc(card.subject || card.name)}</span>
					${this.badges_html(card)}
				</div>
				<div class="pp-card-sub">${pp_esc(sub)}</div>
				<div class="pp-card-chips">${chips.join("")}</div>
				${suggest}
			</div>`;
	}

	foreign_html(item) {
		const kinds = { visit: __("Maintenance"), rental: __("Rental"), travel: __("Travel") };
		const who = item.resource ? this.resource_label(item.resource) : "";
		const sub = [kinds[item.kind] || item.kind, who, item.hours ? `${pp_hours(item.hours)}h` : ""]
			.filter(Boolean)
			.join(" · ");
		return `
			<div class="pp-fcard pp-k-${pp_esc(item.kind || "other")}" data-fkey="${pp_esc(item.key)}" tabindex="0"
				title="${pp_esc([item.label, sub].filter(Boolean).join("\n"))}">
				<div class="pp-fcard-title">${pp_esc(item.label || item.ref)}</div>
				<div class="pp-fcard-sub">${pp_esc(sub)}</div>
			</div>`;
	}

	open_foreign(kind, ref, ymd) {
		if (kind === "travel" && ref) frappe.set_route("Form", "Travel Trip", ref);
		else if (kind === "rental" && ref) frappe.set_route("Form", "Task", ref);
		else if (kind === "visit") {
			// The Maintenance Planner page is not open to Projects Users.
			const roles = ["System Manager", "Projects Manager", "Maintenance Supervisor", "Maintenance User"];
			if (frappe.user && frappe.user.has_role && frappe.user.has_role(roles)) {
				frappe.set_route("maintenance-planner", "week", ymd || this.anchor);
			} else {
				frappe.show_alert({ message: __("A maintenance visit: it moves on the Maintenance Planner."), indicator: "blue" });
			}
		}
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
			this.activate(e.target);
		});
		root.addEventListener("keydown", (e) => {
			if (e.key !== "Enter" && e.key !== " ") return;
			if (!e.target.closest || !e.target.closest(".pp-card, .pp-fcard, .pp-route")) return;
			e.preventDefault();
			this.activate(e.target);
		});
	}

	activate(target) {
		if (!target || !target.closest) return;
		const route_el = target.closest(".pp-route[data-route-resource]");
		if (route_el) {
			this.go_route(route_el.getAttribute("data-route-resource"), route_el.getAttribute("data-route-date"));
			return;
		}
		const suggest_el = target.closest(".pp-suggest[data-suggest]");
		if (suggest_el) {
			const task = this.by_task[suggest_el.getAttribute("data-suggest")];
			if (task) this.suggest_dates(task);
			return;
		}
		const card_el = target.closest(".pp-card[data-task]");
		if (card_el) {
			const card = this.by_task[card_el.getAttribute("data-task")];
			if (card) this.open_card(card);
			return;
		}
		const foreign_el = target.closest(".pp-fcard");
		if (!foreign_el) return;
		const item = this.by_foreign[foreign_el.getAttribute("data-fkey")];
		if (item) this.open_foreign(item.kind, item.ref, item.date);
		else
			this.open_foreign(
				foreign_el.getAttribute("data-kind"),
				foreign_el.getAttribute("data-ref"),
				foreign_el.getAttribute("data-date")
			);
	}

	// What is under the pointer when it went down: a task card, or a person.
	drag_source(el) {
		if (!this.data || !this.data.can_edit) return null;
		const person_el = el.closest(".pp-person[data-resource]");
		if (person_el) return { kind: "person", el: person_el, resource: person_el.getAttribute("data-resource") };
		const card_el = el.closest(".pp-card[data-task]");
		if (!card_el) return null;
		const card = this.by_task[card_el.getAttribute("data-task")];
		if (!card || !card.movable || card.saving) return null;
		return {
			kind: "card",
			el: card_el,
			card,
			from_date: card_el.getAttribute("data-date") || null,
			from_resource: card_el.getAttribute("data-resource") || null,
		};
	}

	on_down(e) {
		if (e.button > 0 || this.drag || !e.target.closest) return;
		const source = this.drag_source(e.target);
		if (!source) return;
		this.drag = { source, el: source.el, x: e.clientX, y: e.clientY, id: e.pointerId, touch: e.pointerType === "touch" };
		if (this.drag.touch) {
			this.drag.timer = setTimeout(() => this.lift(), PP.hold_ms);
		} else {
			// No mousedown, so a drag does not select the text of every day it crosses. The
			// click that opens the card still fires.
			e.preventDefault();
		}
	}

	on_move(e) {
		const drag = this.drag;
		if (!drag || e.pointerId !== drag.id) return;
		const far = Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > PP.drag_px;
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
		const scroller = el && el.closest(".pp-scroll");
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
		drag.ghost.classList.add("pp-ghost");
		drag.ghost.style.width = `${Math.max(rect.width, 120)}px`;
		document.body.appendChild(drag.ghost);
		drag.el.classList.add("pp-dragging");
		document.body.classList.add("pp-drag-active");
		this.place_ghost(drag.x, drag.y);
		if (drag.touch && navigator.vibrate) navigator.vibrate(12);
	}

	place_ghost(x, y) {
		const drag = this.drag;
		if (!drag || !drag.ghost) return;
		drag.ghost.style.left = `${x - drag.dx}px`;
		drag.ghost.style.top = `${y - drag.dy}px`;
	}

	// Where a drag would land. A person lands on a task card; a task lands on a crew-view cell
	// (a person and a day) or a calendar day.
	find_target(x, y) {
		const drag = this.drag;
		const el = document.elementFromPoint(x, y);
		if (!drag || !el || !el.closest || !this.$body[0].contains(el)) return null;
		if (drag.source.kind === "person") {
			const card_el = el.closest(".pp-card[data-task]");
			return card_el ? { el: card_el, task: card_el.getAttribute("data-task") } : null;
		}
		const cell = el.closest(".pp-cell[data-date][data-resource]");
		if (cell) return { el: cell, date: cell.getAttribute("data-date"), resource: cell.getAttribute("data-resource") };
		const day = el.closest(".pp-day[data-date]");
		if (day) return { el: day, date: day.getAttribute("data-date") };
		return null;
	}

	mark_target(target) {
		const drag = this.drag;
		if (!drag) return;
		if (drag.target_el && (!target || drag.target_el !== target.el)) {
			drag.target_el.classList.remove("pp-over", "pp-over-past");
		}
		drag.target_el = target ? target.el : null;
		if (!target) return;
		target.el.classList.add(target.date && target.date < this.today() ? "pp-over-past" : "pp-over");
	}

	end_drag() {
		const drag = this.drag;
		this.drag = null;
		if (!drag) return;
		clearTimeout(drag.timer);
		if (drag.ghost) drag.ghost.remove();
		if (drag.target_el) drag.target_el.classList.remove("pp-over", "pp-over-past");
		drag.el.classList.remove("pp-dragging");
		document.body.classList.remove("pp-drag-active");
	}

	// ------------------------------------------------------------------ dropping

	// A task dragged from the day it was drawn on to another keeps its length and moves by the
	// same number of days, so dragging day 2 of a 3-day job one day later moves the whole job
	// one day later. From a tray there is no "from" day; the task starts on the drop day.
	shifted_start(card, from_date, to_date) {
		if (!card.start || !from_date) return to_date;
		const delta = moment(to_date, "YYYY-MM-DD").diff(moment(from_date, "YYYY-MM-DD"), "days");
		return pp_ymd(moment(card.start, "YYYY-MM-DD").add(delta, "days"));
	}

	crew_has(card, resource) {
		return (card.crew || []).some((member) => member.resource === resource);
	}

	// The crew as save_task takes it. Blank and 0 hours mean the same thing to the engine (an
	// even share, or a full day), and the server stores a blank as 0, so both become null here.
	crew_rows(card) {
		return (card.crew || []).map((member) => ({
			resource: member.resource,
			hours: Number(member.hours) > 0 ? Number(member.hours) : null,
			is_lead: member.is_lead ? 1 : 0,
		}));
	}

	// Work out what a drop means, as one API call. Returns null when it changes nothing.
	plan_drop(source, target) {
		if (source.kind === "person") {
			const card = this.by_task[target.task];
			if (!card) return null;
			const name = this.resource_label(source.resource);
			if (!card.movable) {
				return { refuse: __("{0} cannot be changed from the planner.", [card.subject || card.name]) };
			}
			if (this.crew_has(card, source.resource)) {
				return { refuse: __("{0} is already on {1}.", [name, card.subject || card.name]) };
			}
			return {
				card,
				method: "add_crew",
				args: { task: card.name, resource: source.resource },
				message: __("{0} added to {1}", [name, card.subject || card.name]),
			};
		}

		const card = source.card;
		const start = this.shifted_start(card, source.from_date, target.date);
		const moved = start !== card.start;
		const subject = card.subject || card.name;
		const moved_message = __("{0} moved to {1}", [subject, pp_when(start)]);

		if (target.resource && source.from_resource && target.resource !== source.from_resource) {
			const from = this.resource_label(source.from_resource);
			const to = this.resource_label(target.resource);
			if (this.crew_has(card, target.resource)) {
				return { refuse: __("{0} is already on {1}.", [to, subject]) };
			}
			const args = { task: card.name, from_resource: source.from_resource, to_resource: target.resource };
			if (moved) args.date = start;
			return {
				card,
				method: "swap_crew",
				args,
				past: moved && target.date < this.today() ? target.date : null,
				message: moved
					? __("{0}: {1} in place of {2}, moved to {3}", [subject, to, from, pp_when(start)])
					: __("{0}: {1} in place of {2}", [subject, to, from]),
			};
		}

		const args = { task: card.name, start };
		// A task dropped from a tray onto a person's row in the crew view is booked for them too.
		const adds = target.resource && !source.from_resource && !this.crew_has(card, target.resource);
		if (adds) {
			args.crew = JSON.stringify(
				this.crew_rows(card).concat([{ resource: target.resource, hours: null, is_lead: 0 }])
			);
		}
		if (!moved && !adds) return null;
		return {
			card,
			method: "save_task",
			args,
			past: moved && target.date < this.today() ? target.date : null,
			message: adds
				? __("{0} booked for {1} on {2}", [subject, this.resource_label(target.resource), pp_when(start)])
				: moved_message,
		};
	}

	drop(source, target) {
		const plan = this.plan_drop(source, target);
		if (!plan) return;
		if (plan.refuse) {
			frappe.show_alert({ message: plan.refuse, indicator: "orange" }, 6);
			return;
		}
		const run = () => this.commit(plan.card, plan.method, plan.args, plan.message);
		if (plan.past) {
			// Tasks, unlike visits, may be recorded after the fact; a past day only needs a yes.
			frappe.confirm(__("{0} is before today. Schedule the task on a past day?", [pp_when(plan.past)]), run);
			return;
		}
		run();
	}

	// ------------------------------------------------------------------ saving

	snapshot(card) {
		return {
			task: card.name,
			subject: card.subject || card.name,
			start: card.start || null,
			end: card.end || null,
			expected_time: Number(card.expected_time) || 0,
			crew_size: Number(card.crew_size) || 0,
			crew: this.crew_rows(card),
			credentials: (card.credentials || []).slice(),
		};
	}

	// Save one change to a task: fade its card, send it, ask for a reason if the server reports
	// conflicts, then reload, which is the truth either way.
	commit(card, method, args, message) {
		const snapshot = this.snapshot(card);
		card.saving = true;
		this.render();
		return this.send(method, Object.assign({ modified: this.modified[card.name] || card.modified }, args), {
			snapshot,
			message,
		}).finally(() => this.load());
	}

	// Resolves either way: a refusal has already shown the server's own message.
	send(method, args, opts) {
		opts = opts || {};
		return Promise.resolve(frappe.call({ method: `${PP.api}.${method}`, args }))
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
						: this.ask_reason(result.conflicts || {});
					return ask.then((reason) =>
						reason ? this.send(method, Object.assign({}, args, { reason }), opts) : null
					);
				}
				if (opts.snapshot) this.push_undo(opts.snapshot);
				if (result.name && result.modified) this.modified[result.name] = result.modified;
				if (opts.message) frappe.show_alert({ message: opts.message, indicator: "green" }, 5);
				(result.warnings || []).forEach((warning) =>
					frappe.show_alert({ message: warning, indicator: "orange" }, 10)
				);
				return result;
			})
			.catch(() => null);
	}

	// The conflicts a change would cause, per person, and a required reason. Resolves with the
	// reason, or null when the planner backs out.
	ask_reason(conflicts) {
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
						`<div class="pp-why"><b>${pp_esc(who)}</b><ul>${(list || [])
							.map((text) => `<li>${pp_esc(text)}</li>`)
							.join("")}</ul></div>`
				)
				.join("");
			const dialog = new frappe.ui.Dialog({
				title: __("This booking has conflicts"),
				fields: [
					{
						fieldtype: "HTML",
						fieldname: "conflicts",
						options: `<p>${pp_esc(
							__("You can save it anyway. Say why: the reason is added to the task's timeline.")
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

	push_undo(snapshot) {
		this.undo_stack.push(snapshot);
		while (this.undo_stack.length > PP.undo_max) this.undo_stack.shift();
		this.update_undo_button();
	}

	update_undo_button() {
		if (!this.$undo) return;
		const last = this.undo_stack[this.undo_stack.length - 1];
		this.$undo
			.toggleClass("pp-empty", !last)
			.attr("title", last ? __("Undo the last change to {0}", [last.subject]) : __("Nothing to undo"));
	}

	// Put the task back the way it was before the last change. Every field is sent, so an undo
	// restores dates, hours and crew together. save_task reads a blank date as "unchanged", so a
	// task dragged out of the Unscheduled tray keeps its new dates and the planner says so.
	undo() {
		const snap = this.undo_stack.pop();
		this.update_undo_button();
		if (!snap) {
			frappe.show_alert({ message: __("Nothing to undo"), indicator: "blue" }, 4);
			return;
		}
		const card = this.by_task[snap.task];
		if (card) {
			card.saving = true;
			this.render();
		}
		const args = {
			task: snap.task,
			modified: this.modified[snap.task] || (card && card.modified) || "",
			expected_time: snap.expected_time,
			crew_size: snap.crew_size,
			crew: JSON.stringify(snap.crew),
			credentials: JSON.stringify(snap.credentials),
		};
		if (snap.start) {
			args.start = snap.start;
			args.end = snap.end || snap.start;
		}
		this.send("save_task", args, {
			message: __("Undone: {0}", [snap.subject]),
			auto_reason: __(PP.undo_reason),
		})
			.then((result) => {
				if (result && !snap.start && card && card.start) {
					frappe.show_alert(
						{
							message: __("{0} keeps its dates: clear them on the task form to unschedule it.", [snap.subject]),
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
		const editable = !!(card.movable && this.data && this.data.can_edit);
		const rows = [];
		const add = (label, value) => {
			if (value) rows.push(`<tr><th>${pp_esc(label)}</th><td>${pp_esc(value)}</td></tr>`);
		};
		add(__("Project"), card.project_title && card.project ? `${card.project_title} (${card.project})` : card.project);
		add(__("Status"), card.status ? __(card.status) : "");
		if (card.start) {
			const span =
				card.end && card.end !== card.start
					? `${frappe.datetime.str_to_user(card.start)} – ${frappe.datetime.str_to_user(card.end)}`
					: frappe.datetime.str_to_user(card.start);
			add(__("Dates"), span);
		} else {
			add(__("Dates"), __("Not scheduled"));
		}
		if (card.slot && card.slot.length === 2) add(__("Time"), card.slot.join("–"));
		if (card.rental_kind) add(__("Rental"), __(card.rental_kind));
		const booked = (card.crew || [])
			.map((member) => {
				const name = this.resource_label(member.resource, member.label);
				const lead = member.is_lead ? ` (${__("lead")})` : "";
				return member.booked != null ? `${name}${lead}: ${pp_hours(member.booked)}h` : `${name}${lead}`;
			})
			.join(", ");
		add(__("Booked"), booked || __("Nobody yet"));
		if (Number(card.short) > 0) add(__("Short"), __("Needs {0} more", [card.short]));
		this.task_conflicts(card).forEach((text) => add(__("Conflict"), text));
		if (card.overdue) add(__("Overdue"), __("The task's end date has passed."));
		if (!editable) add(__("Note"), __("This task is read-only here."));

		const links = [["task", __("Open task")]];
		if (card.project) links.push(["project", __("Open project")]);
		if (editable) links.push(["suggest", __("Suggest dates")]);
		const fields = [
			{
				fieldtype: "HTML",
				fieldname: "summary",
				options:
					`<table class="pp-summary">${rows.join("")}</table>` +
					`<div class="pp-links">${links
						.map(
							([action, label]) =>
								`<button type="button" class="btn btn-default btn-xs" data-action="${action}">${pp_esc(label)}</button>`
						)
						.join("")}</div>`,
			},
		];
		if (editable) {
			fields.push(
				{ fieldtype: "Section Break", label: __("Schedule") },
				{ fieldtype: "Date", fieldname: "start", label: __("Start date"), default: card.start || "" },
				{ fieldtype: "Date", fieldname: "end", label: __("End date"), default: card.end || "" },
				{ fieldtype: "Column Break" },
				{
					fieldtype: "Float",
					fieldname: "expected_time",
					label: __("Expected hours"),
					default: Number(card.expected_time) || 0,
					description: __("Blank or 0: each person is booked a full day for each day of the task."),
				},
				{
					fieldtype: "Int",
					fieldname: "crew_size",
					label: __("Crew needed"),
					default: Number(card.crew_size) || 0,
				},
				{ fieldtype: "Section Break", label: __("Crew") },
				{
					fieldtype: "Table",
					fieldname: "crew",
					label: __("Crew"),
					cannot_add_rows: false,
					in_place_edit: true,
					data: this.crew_rows(card).map((row) => Object.assign({}, row, { hours: row.hours == null ? "" : row.hours })),
					fields: [
						{
							fieldtype: "Link",
							fieldname: "resource",
							options: "Planner Resource",
							label: __("Person"),
							in_list_view: 1,
							reqd: 1,
							columns: 5,
						},
						{
							fieldtype: "Float",
							fieldname: "hours",
							label: __("Hours"),
							in_list_view: 1,
							columns: 3,
							description: __("Blank: an even share of the expected hours, or a full day."),
						},
						{ fieldtype: "Check", fieldname: "is_lead", label: __("Lead"), in_list_view: 1, columns: 2 },
					],
				},
				{
					fieldtype: "MultiSelectPills",
					fieldname: "credentials",
					label: __("Qualifications needed"),
					get_data: (txt) => frappe.db.get_link_options("Credential Type", txt),
				}
			);
		}

		const dialog = new frappe.ui.Dialog({ title: card.subject || card.name, fields, size: "large" });
		if (editable) {
			dialog.set_primary_action(__("Save"), (values) => this.save_dialog(card, dialog, values));
		}
		dialog.$wrapper.find("[data-action]").on("click", (e) => {
			const action = e.currentTarget.getAttribute("data-action");
			dialog.hide();
			if (action === "task") frappe.set_route("Form", "Task", card.name);
			else if (action === "project") frappe.set_route("Form", "Project", card.project);
			else if (action === "suggest") this.suggest_dates(card);
		});
		dialog.show();
		if (editable && (card.credentials || []).length) dialog.set_value("credentials", card.credentials.slice());
	}

	save_dialog(card, dialog, values) {
		values = values || {};
		const args = { task: card.name };
		const start = values.start || "";
		const end = values.end || "";
		// save_task reads a blank date as "unchanged", so a dated task cannot be sent back to
		// the Unscheduled tray from here; say so rather than appear to save it.
		if (!start && card.start) {
			frappe.msgprint(__("To unschedule a task, clear its dates on the task form."));
			return;
		}
		if (!start && end) {
			frappe.msgprint(__("Set a start date as well."));
			return;
		}
		if (start && end && end < start) {
			frappe.msgprint(__("The end date is before the start date."));
			return;
		}
		// The dialog shows both dates, so both are sent when either changed: what the planner
		// typed is what is saved, rather than the end following the start as it does on a drag.
		const last = start ? end || start : "";
		if (start !== (card.start || "") || last !== (card.end || card.start || "")) {
			args.start = start;
			args.end = last;
		}
		const expected = Number(values.expected_time) || 0;
		if (expected !== (Number(card.expected_time) || 0)) args.expected_time = expected;
		const crew_size = Math.max(0, parseInt(values.crew_size, 10) || 0);
		if (crew_size !== (Number(card.crew_size) || 0)) args.crew_size = crew_size;

		const crew = (values.crew || [])
			.filter((row) => row && row.resource)
			.map((row) => ({
				resource: row.resource,
				hours: Number(row.hours) > 0 ? Number(row.hours) : null,
				is_lead: row.is_lead ? 1 : 0,
			}));
		if (JSON.stringify(crew) !== JSON.stringify(this.crew_rows(card))) args.crew = JSON.stringify(crew);
		const credentials = (values.credentials || []).filter(Boolean);
		const before = (card.credentials || []).slice().sort();
		if (JSON.stringify(credentials.slice().sort()) !== JSON.stringify(before)) {
			args.credentials = JSON.stringify(credentials);
		}

		dialog.hide();
		if (Object.keys(args).length === 1) return;
		this.commit(card, "save_task", args, __("{0} updated", [card.subject || card.name]));
	}

	// ------------------------------------------------------------------ suggestions

	// Ask the server which days suit this task by drive time, then offer them in a dialog.
	suggest_dates(card) {
		return Promise.resolve(
			frappe.call({
				method: `${PP.api}.suggest_dates`,
				args: { task: card.name },
				freeze: true,
				freeze_message: __("Looking for the best days…"),
			})
		)
			.then((r) => this.show_suggestions(card, (r && r.message) || {}))
			.catch(() => null);
	}

	show_suggestions(card, result) {
		const suggestions = result.suggestions || [];
		const subject = card.subject || card.name;
		const parts = [];
		if (result.note) parts.push(`<div class="pp-route-note">${pp_esc(result.note)}</div>`);
		if (result.site && result.site.label) {
			parts.push(`<p class="pp-stop-sub">${pp_esc(__("Site: {0}", [result.site.label]))}</p>`);
		}
		if (!suggestions.length) {
			parts.push(
				`<p>${pp_esc(
					__("No day in the next few weeks has someone free for this task. Try another crew or shorten the task.")
				)}</p>`
			);
		} else {
			const rows = suggestions.map((item, index) => {
				const added = Number(item.added_minutes);
				const drive =
					item.added_minutes == null || !Number.isFinite(added)
						? ""
						: `<span class="pp-chip">${pp_esc(__("+{0} driving", [pp_drive(added)]))}</span>`;
				const long = item.long_drive ? `<span class="pp-chip pp-red">${pp_esc(__("Long drive"))}</span>` : "";
				const free = item.free_hours == null ? "" : pp_esc(__("{0}h free", [pp_hours(item.free_hours)]));
				return `
					<tr>
						<td class="pp-sug-when">${pp_esc(pp_when(item.date))}</td>
						<td>
							<div><b>${pp_esc(item.label || item.resource)}</b> <span class="pp-stop-sub">${free}</span></div>
							<div class="pp-sug-reason">${pp_esc(item.reason || "")}</div>
							<div>${drive}${long}</div>
						</td>
						<td><button type="button" class="btn btn-primary btn-xs" data-book="${pp_esc(index)}">${pp_esc(
					__("Book")
				)}</button></td>
					</tr>`;
			});
			parts.push(`<table class="pp-sug">${rows.join("")}</table>`);
		}
		const dialog = new frappe.ui.Dialog({
			title: __("Suggested days for {0}", [subject]),
			fields: [{ fieldtype: "HTML", fieldname: "suggestions", options: parts.join("") }],
		});
		dialog.$wrapper.find("[data-book]").on("click", (e) => {
			const item = suggestions[parseInt(e.currentTarget.getAttribute("data-book"), 10)];
			if (!item) return;
			dialog.hide();
			this.book_suggestion(card, item);
		});
		dialog.show();
	}

	// Book one suggestion exactly as a drag from a tray onto a person's row would: one save_task
	// for the day (the person is added to the crew in the same call when they are not on it), so a
	// conflict asks for a reason once, through the same flow.
	book_suggestion(card, item) {
		const args = { task: card.name, start: item.date };
		if (item.resource && !this.crew_has(card, item.resource)) {
			args.crew = JSON.stringify(this.crew_rows(card).concat([{ resource: item.resource, hours: null, is_lead: 0 }]));
		}
		const who = this.resource_label(item.resource, item.label);
		return this.commit(
			card,
			"save_task",
			args,
			__("{0} booked for {1} on {2}", [card.subject || card.name, who, pp_when(item.date)])
		);
	}

	// ------------------------------------------------------------------ route view

	load_route() {
		const token = ++this.request;
		this.$title.text("");
		this.$body.addClass("pp-loading");
		return Promise.resolve(
			frappe.call({
				method: `${PP.api}.get_route`,
				args: { resource: this.route_resource, date: this.route_date },
			})
		)
			.then((r) => {
				if (token !== this.request) return;
				this.$body.removeClass("pp-loading");
				this.route_data = (r && r.message) || null;
				this.render_route(token);
			})
			.catch(() => {
				if (token !== this.request) return;
				this.$body.removeClass("pp-loading");
				this.route_data = null;
				this.render_route(token);
			});
	}

	clear_route_map() {
		const state = this.route_map;
		this.route_map = null;
		if (!state) return;
		(Object.values(state.markers || {}) || []).forEach((marker) => {
			if (marker && marker.setMap) marker.setMap(null);
		});
		if (state.line && state.line.setMap) state.line.setMap(null);
	}

	route_back_to_week() {
		this.go("week", this.route_date || frappe.datetime.get_today());
	}

	render_route(token) {
		this.clear_route_map();
		const data = this.route_data;
		const date = this.route_date;
		const label = (data && data.label) || this.route_resource;
		const stops = (data && data.stops) || [];
		const head = [];
		const step = (sign) =>
			this.go_route(this.route_resource, pp_ymd(moment(date, "YYYY-MM-DD").add(sign, "days")));

		this.$route.empty();
		const $head = $('<div class="pp-route-head"></div>').appendTo(this.$route);
		$('<button type="button" class="btn btn-default btn-sm">‹</button>')
			.attr("title", __("Previous day"))
			.on("click", () => step(-1))
			.appendTo($head);
		$('<button type="button" class="btn btn-default btn-sm">›</button>')
			.attr("title", __("Next day"))
			.on("click", () => step(1))
			.appendTo($head);
		$('<span class="pp-route-title"></span>')
			.text(`${label} · ${moment(date, "YYYY-MM-DD").format("dddd, MMM D, YYYY")}`)
			.appendTo($head);
		$('<span class="pp-spacer"></span>').appendTo($head);
		$('<button type="button" class="btn btn-default btn-sm"></button>')
			.text(__("Back to week"))
			.on("click", () => this.route_back_to_week())
			.appendTo($head);
		const maps_url = data && data.maps_url;
		if (maps_url && /^https:\/\/www\.google\.com\/maps\//.test(maps_url)) {
			$('<button type="button" class="btn btn-primary btn-sm"></button>')
				.text(__("Open in Google Maps"))
				.on("click", () => window.open(maps_url, "_blank", "noopener"))
				.appendTo($head);
		}

		if (!data) {
			$('<div class="pp-route-note"></div>')
				.text(__("The route could not be loaded. Check that this person is on the Planner Resources list, then refresh."))
				.appendTo(this.$route);
			return;
		}

		// Totals.
		const sum_legs = stops.reduce((total, stop) => total + (Number(stop.drive_minutes) || 0), 0);
		const totals = [];
		totals.push(`<b>${pp_esc(__("Driving"))}</b> ${pp_esc(pp_drive(data.drive_minutes))}`);
		if (Number(data.km) > 0) totals.push(pp_esc(`${pp_km(data.km)} km`));
		if (Number(data.drive_minutes) > 0 && data.source) totals.push(pp_esc(this.source_text(data.source)));
		totals.push(pp_esc(__("{0} stop(s)", [stops.length])));
		const long = data.long_drive ? `<span class="pp-chip pp-red">${pp_esc(__("Long drive"))}</span>` : "";
		$(`<div class="pp-route-totals">${totals.join(" · ")}${long}</div>`).appendTo(this.$route);

		// Notices.
		if (data.travel) head.push(data.travel);
		if (data.off) head.push(data.off);
		if (!data.start) head.push(__("The shop address could not be located, so the route starts at the first stop."));
		if (data.source && data.source !== "google" && Number(data.drive_minutes) > 0) {
			head.push(__("Some drive times are straight-line estimates, not Google's."));
		}
		if (!stops.length) head.push(__("Nothing to drive to on this day."));
		head.forEach((text) => $('<div class="pp-route-note"></div>').text(text).appendTo(this.$route));
		const $map_note = $('<div class="pp-route-note"></div>').hide().appendTo(this.$route);

		// The ordered list, then the map: on a phone the list comes first.
		const $cols = $('<div class="pp-route-cols"></div>').appendTo(this.$route);
		const html = [];
		if (data.start) {
			html.push(`
				<div class="pp-stop">
					<span class="pp-stop-num pp-shop">S</span>
					<div class="pp-stop-body">
						<div class="pp-stop-label">${pp_esc(data.start.label || __("Shop"))}</div>
						<div class="pp-stop-sub">${pp_esc(data.start.address || "")}</div>
					</div>
				</div>`);
		}
		let previous_located = !!data.start;
		stops.forEach((stop) => {
			const located = !!stop.located && pp_valid_point(stop.lat, stop.lng);
			const when =
				stop.arrive || stop.depart ? [stop.arrive, stop.depart].filter(Boolean).join("–") : "";
			const sub = [
				stop.project_title || stop.project,
				stop.slot && stop.slot.length === 2 ? stop.slot.join("–") : "",
				Number(stop.hours) > 0 ? `${pp_hours(stop.hours)}h` : "",
			]
				.filter(Boolean)
				.join(" · ");
			let leg = "";
			if (located && previous_located && Number(stop.drive_minutes) >= 0 && stop.drive_minutes != null) {
				const km = Number(stop.km) > 0 ? ` · ${pp_km(stop.km)} km` : "";
				leg = `<div class="pp-stop-leg">${pp_esc(`${pp_drive(stop.drive_minutes)}${km}`)}</div>`;
			}
			if (located) previous_located = true;
			const waits =
				Number(stop.wait_minutes) >= 5
					? `<div class="pp-stop-leg">${pp_esc(__("Waits {0} for the start time", [pp_drive(stop.wait_minutes)]))}</div>`
					: "";
			const missing = located
				? ""
				: `<div class="pp-stop-missing">${pp_esc(__("No location — set the task's address"))}</div>`;
			const open =
				(stop.kind === "task" || stop.kind === "rental") && stop.ref
					? `<span class="pp-stop-link" role="button" tabindex="0" data-open-task="${pp_esc(stop.ref)}">${pp_esc(
							__("Open task")
					  )}</span>`
					: "";
			html.push(`
				<div class="pp-stop${located ? "" : " pp-unlocated"}" data-marker="${pp_esc(located ? stop.order : "")}">
					<span class="pp-stop-num">${pp_esc(stop.order)}</span>
					<div class="pp-stop-body">
						${when ? `<div class="pp-stop-time">${pp_esc(when)}</div>` : ""}
						<div class="pp-stop-label">${pp_esc(stop.label || stop.ref)}</div>
						${sub ? `<div class="pp-stop-sub">${pp_esc(sub)}</div>` : ""}
						${stop.address ? `<div class="pp-stop-sub">${pp_esc(stop.address)}</div>` : ""}
						${leg}${waits}${missing}${open ? `<div class="pp-stop-leg">${open}</div>` : ""}
					</div>
				</div>`);
		});
		// The way home: the server prices it (end.drive_minutes); the difference is the fallback.
		const back =
			data.end && data.end.drive_minutes != null
				? Number(data.end.drive_minutes) || 0
				: (Number(data.drive_minutes) || 0) - sum_legs;
		if (data.end && stops.some((stop) => stop.located) && back > 0.5) {
			html.push(`
				<div class="pp-stop">
					<span class="pp-stop-num pp-shop">S</span>
					<div class="pp-stop-body">
						<div class="pp-stop-label">${pp_esc(__("Back at {0}", [data.end.label || __("Shop")]))}</div>
						<div class="pp-stop-leg">${pp_esc(pp_drive(back))}</div>
					</div>
				</div>`);
		}
		const $list = $('<div class="pp-stops"></div>').html(html.join("")).appendTo($cols);
		$list.find("[data-open-task]").on("click keydown", (e) => {
			if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
			e.stopPropagation();
			frappe.set_route("Form", "Task", e.currentTarget.getAttribute("data-open-task"));
		});
		$list.find(".pp-stop[data-marker]").on("click", (e) => {
			const order = e.currentTarget.getAttribute("data-marker");
			if (order) this.focus_marker(order);
		});
		if (!stops.length && !data.start) $list.hide();

		const $map = $('<div class="pp-map"></div>').appendTo($cols);
		this.draw_route_map(token, data, $map, $map_note, $cols);
	}

	// Never throws, never leaves a blank pane: when Google cannot be used the list stands alone
	// with a one-line notice.
	draw_route_map(token, data, $map, $note, $cols) {
		const fail = (text) => {
			if (token !== this.request) return;
			$map.hide();
			$cols.addClass("pp-nomap");
			$note.text(text).show();
		};
		const points = (data.stops || []).filter((stop) => stop.located && pp_valid_point(stop.lat, stop.lng));
		if (!(data.stops || []).length) {
			$map.hide();
			$cols.addClass("pp-nomap");
			return;
		}
		if (!points.length) {
			fail(__("No stop on this route has a location yet, so there is nothing to draw on a map."));
			return;
		}
		if (!data.maps_key) {
			fail(__("Add a Google Maps API key in Travel Settings to see this route on a map."));
			return;
		}
		if (!window.EEGoogleMaps) {
			fail(__("The map could not be loaded, so only the list is shown."));
			return;
		}
		const start = data.start && pp_valid_point(data.start.lat, data.start.lng) ? data.start : null;
		const theme = (document.documentElement.dataset && document.documentElement.dataset.theme) || "light";
		window.EEGoogleMaps.load({ apiKey: data.maps_key, libraries: ["maps"] })
			.then((maps) => {
				if (token !== this.request || !document.body.contains($map[0])) return;
				const first = start || points[0];
				const options = Object.assign(
					{
						zoom: 10,
						center: { lat: Number(first.lat), lng: Number(first.lng) },
						mapTypeControl: false,
						streetViewControl: false,
						fullscreenControl: true,
						gestureHandling: "cooperative",
					},
					window.EEGoogleMaps.mapOptions(data.map_ids || {}, theme)
				);
				const map = new maps.Map($map[0], options);
				const info = new maps.InfoWindow();
				const bounds = new maps.LatLngBounds();
				const markers = {};
				const add = (key, point, text, color, title, detail) => {
					const position = { lat: Number(point.lat), lng: Number(point.lng) };
					const marker = new maps.Marker({
						position,
						map,
						title,
						label: { text: String(text), color: "#ffffff", fontWeight: "700", fontSize: "12px" },
						icon: {
							path: maps.SymbolPath.CIRCLE,
							scale: 13,
							fillColor: color,
							fillOpacity: 1,
							strokeColor: "#ffffff",
							strokeWeight: 2,
						},
					});
					marker.addListener("click", () => {
						// Text nodes only: stop and task names are typed by people.
						const box = document.createElement("div");
						const head = document.createElement("b");
						head.textContent = title;
						box.appendChild(head);
						if (detail) {
							const line = document.createElement("div");
							line.textContent = detail;
							box.appendChild(line);
						}
						info.setContent(box);
						info.open({ map, anchor: marker });
					});
					bounds.extend(position);
					markers[key] = marker;
				};
				const path = [];
				if (start) {
					add("shop", start, "S", "#16a34a", start.label || __("Shop"), start.address || "");
					path.push({ lat: Number(start.lat), lng: Number(start.lng) });
				}
				points.forEach((stop) => {
					add(
						String(stop.order),
						stop,
						stop.order,
						PP.default_color,
						stop.label || stop.ref || "",
						[stop.arrive, stop.address].filter(Boolean).join(" · ")
					);
					path.push({ lat: Number(stop.lat), lng: Number(stop.lng) });
				});
				if (start) path.push({ lat: Number(start.lat), lng: Number(start.lng) });
				const line = new maps.Polyline({
					path,
					map,
					strokeColor: PP.default_color,
					strokeOpacity: 0.8,
					strokeWeight: 4,
				});
				if (path.length > 1) map.fitBounds(bounds, 56);
				else map.setZoom(14);
				this.route_map = { map, info, markers, line };
			})
			.catch(() => fail(__("The map could not be loaded, so only the list is shown.")));
	}

	// Clicking a stop in the list shows it on the map.
	focus_marker(order) {
		const state = this.route_map;
		const marker = state && state.markers[String(order)];
		if (!marker) return;
		state.map.panTo(marker.getPosition());
		window.google.maps.event.trigger(marker, "click");
	}
}
