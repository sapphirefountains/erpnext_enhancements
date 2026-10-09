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
//   /desk/project-planner/heatmap/2026-10-12  eight weeks of load per person, one cell per week
//                                             (the arrows step four weeks; a cell opens its week)
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
// Planning helpers (Phase 3A):
//   - a Tentative (pencil) task is hatched; its hours are "soft" load, drawn apart from the firm
//     hours, and "Firm up" turns it into a real booking (checked like any other change)
//   - a task that starts before a predecessor ends carries a warning, and moving a task later
//     offers to shift the tasks that follow it by the same working days (shift_successors)
//   - a task whose required qualifications nobody on the crew holds says so (never a block)
//   - the Overdue tray lists late open tasks by project: reschedule, mark done or cancel them in
//     bulk (bulk_update), or drag one onto a day
//   - "Copy week..." copies chosen tasks into another week as new tasks (copy_week), previewed first
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
	heatmap_view: "heatmap",
	heatmap_weeks: 8,
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
.pp-bar{position:relative;height:4px;border-radius:2px;background:var(--control-bg);overflow:hidden;}
.pp-bar i{display:block;height:100%;}
.pp-bar b{position:absolute;top:0;bottom:0;background-image:repeating-linear-gradient(135deg,rgba(100,116,139,.85) 0 2px,transparent 2px 4px);}
.pp-green .pp-bar i{background:#16a34a;}
.pp-amber .pp-bar i{background:#d97706;}
.pp-red .pp-bar i{background:#dc2626;}
.pp-red .pp-avail-text,.pp-red .pp-cap-text{color:#b91c1c;font-weight:600;}
.pp-offday{background:repeating-linear-gradient(135deg,transparent 0 6px,var(--control-bg) 6px 8px);color:var(--text-muted);}
.pp-conflict{box-shadow:inset 0 0 0 2px rgba(220,38,38,.55);}
.pp-softover{box-shadow:inset 0 0 0 2px rgba(217,119,6,.55);}
.pp-mini{padding:0;min-height:22px;}
.pp-mini.pp-green{background:rgba(22,163,74,.22);}
.pp-mini.pp-amber{background:rgba(217,119,6,.28);}
.pp-mini.pp-red{background:rgba(220,38,38,.38);}
.pp-mini.pp-idle{background:transparent;}
.pp-mini.pp-soft-mini{background-image:repeating-linear-gradient(135deg,rgba(100,116,139,.5) 0 2px,transparent 2px 5px);}
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
.pp-chip.pp-pencil{background:rgba(100,116,139,.2);color:var(--text-color);font-style:italic;}
.pp-card.pp-tentative{border-top-style:dashed;border-right-style:dashed;border-bottom-style:dashed;background-color:var(--card-bg);background-image:repeating-linear-gradient(135deg,transparent 0 6px,rgba(100,116,139,.16) 6px 8px);}
.pp-firm-row{margin-top:3px;}
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
.pp-hm-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;}
.pp-hm{display:grid;min-width:640px;}
.pp-hm > div{border-top:1px solid var(--border-color);padding:2px;min-width:0;}
.pp-hm > .pp-hm-head{border-top:none;font-size:11px;color:var(--text-muted);text-align:center;white-space:nowrap;padding:5px 2px;}
.pp-hm > .pp-hm-head.pp-today{color:var(--primary,#2490ef);font-weight:700;}
.pp-hm > .pp-hm-name{display:flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:2px 8px;min-width:0;position:sticky;left:0;background:var(--card-bg);z-index:1;}
.pp-hm-name span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-hm-cell{border-radius:6px;padding:4px 6px;min-height:46px;cursor:pointer;font-size:11px;display:flex;flex-direction:column;justify-content:center;gap:2px;min-width:0;overflow:hidden;border:1px solid var(--border-color);}
.pp-hm-cell:hover,.pp-hm-cell:focus{box-shadow:0 1px 4px rgba(0,0,0,.25);outline:none;}
.pp-hm-cell.pp-green{background:rgba(22,163,74,.22);}
.pp-hm-cell.pp-amber{background:rgba(217,119,6,.28);}
.pp-hm-cell.pp-red{background:rgba(220,38,38,.38);}
.pp-hm-cell.pp-idle{background:transparent;color:var(--text-muted);}
.pp-hm-pct{font-weight:600;white-space:nowrap;}
.pp-hm-sub{color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-hm-legend{display:flex;flex-wrap:wrap;align-items:center;gap:12px;font-size:12px;color:var(--text-muted);margin:8px 0 0;}
.pp-hm-key{display:inline-flex;align-items:center;gap:5px;}
.pp-hm-key i{display:inline-block;width:14px;height:14px;border-radius:4px;border:1px solid var(--border-color);}
.pp-hm-key i.pp-green{background:rgba(22,163,74,.35);}
.pp-hm-key i.pp-amber{background:rgba(217,119,6,.4);}
.pp-hm-key i.pp-red{background:rgba(220,38,38,.5);}
.pp-hm-key i.pp-soft-key{background-image:repeating-linear-gradient(135deg,rgba(100,116,139,.85) 0 2px,transparent 2px 4px);}
.pp-od{border:1px solid rgba(220,38,38,.4);border-radius:10px;padding:8px 10px;margin-bottom:10px;}
.pp-od h5{margin:0;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#b91c1c;display:flex;align-items:center;gap:8px;}
.pp-od h5 .btn{text-transform:none;letter-spacing:0;}
.pp-od-actions{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:8px 0 2px;}
.pp-od-count{font-size:12px;color:var(--text-muted);}
.pp-od-list{max-height:360px;overflow-y:auto;}
.pp-od-project{display:flex;align-items:center;gap:6px;font-weight:600;font-size:12px;margin:8px 0 3px;}
.pp-od-project input,.pp-od-card input{flex:0 0 auto;margin:0;}
.pp-od-card{display:flex;align-items:center;gap:8px;border:1px solid var(--border-color);border-left:4px solid #dc2626;border-radius:6px;background:var(--card-bg);padding:3px 8px;font-size:12px;margin-bottom:3px;cursor:grab;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none;min-width:0;}
.pp-od-card.pp-dragging{opacity:.3;}
.pp-od-main{flex:1 1 auto;min-width:0;}
.pp-od-subject{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-od-sub{font-size:11px;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-od-end{font-size:11px;color:#b91c1c;white-space:nowrap;}
.pp-copy-list{max-height:300px;overflow-y:auto;border:1px solid var(--border-color);border-radius:8px;padding:4px 8px;}
.pp-copy-row{display:flex;align-items:flex-start;gap:8px;padding:3px 0;font-size:13px;}
.pp-copy-row input{margin-top:3px;flex:0 0 auto;}
.pp-copy-sub{font-size:11px;color:var(--text-muted);}
.pp-copy-table{width:100%;font-size:13px;border-collapse:collapse;}
.pp-copy-table td{padding:4px 8px 4px 0;border-top:1px solid var(--border-color);vertical-align:top;}
.pp-copy-table tr:first-child td{border-top:none;}
@media (max-width:760px){
.pp-hm{min-width:560px;}
.pp-hm-cell{padding:3px 4px;}
.pp-od-card{flex-wrap:wrap;}
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
		// Phase 3A: the Overdue tray (collapsed until opened; the tasks ticked in it), and the
		// heatmap view's data.
		this.overdue = null;
		this.overdue_open = false;
		this.overdue_selected = new Set();
		this.overdue_request = 0;
		this.heatmap = null;

		this.page.add_menu_item(__("Planner Resources"), () => frappe.set_route("List", "Planner Resource"));
		this.page.add_menu_item(__("Project Planner Settings"), () =>
			frappe.set_route("Form", "Project Planner Settings")
		);
		this.$body = $('<div class="pp-wrap"></div>').appendTo(page.main);
		this.build_shell();
		this.init_phase3b();
		this.init_phase4();
		this.init_phase5();
		this.init_phase6a();
		this.init_phase6b();
		this.init_phase6c();
		this.init_phase6d();
		this.bind_drag();
	}

	// ------------------------------------------------------------------ routing

	route_target() {
		const route = frappe.get_route() || [];
		if (route[0] !== PP.route) return null;
		const mine = this.my_week_target(route);
		if (mine) return mine;
		// /project-planner/route/<person>/<date>
		if (route[1] === PP.route_view && route[2]) {
			const date = route[3] && moment(route[3], "YYYY-MM-DD", true).isValid() ? route[3] : frappe.datetime.get_today();
			return { view: PP.route_view, resource: route[2], anchor: date };
		}
		// /project-planner/heatmap/<date>
		if (route[1] === PP.heatmap_view) {
			const date = route[2] && moment(route[2], "YYYY-MM-DD", true).isValid() ? route[2] : frappe.datetime.get_today();
			return { view: PP.heatmap_view, anchor: date };
		}
		const view = PP.views.includes(route[1]) ? route[1] : "week";
		const day = route[2] && moment(route[2], "YYYY-MM-DD", true).isValid() ? route[2] : frappe.datetime.get_today();
		return { view, anchor: day };
	}

	handle_route() {
		if (this.p6c_before_route()) return;
		const target = this.route_target();
		if (!target) return;
		this.view = target.view;
		this.anchor = target.anchor;
		if (target.view === PP3B.my_week) {
			this.show_my_week(target.anchor);
			return;
		}
		if (target.view === PP.route_view) {
			this.route_resource = target.resource;
			this.route_date = target.anchor;
			this.set_mode("route");
			this.load_route();
			return;
		}
		if (target.view === PP.heatmap_view) {
			this.set_mode("heatmap");
			this.load_heatmap();
			return;
		}
		this.set_mode("");
		this.load();
	}

	// The route view replaces the toolbar, panel, trays and calendar; the others replace it.
	// The heatmap keeps the toolbar but swaps the calendar for its grid, and drops the controls
	// that only mean something on the calendar. (The panel is shown and hidden by render_panel,
	// so leaving a view does not reveal a stale one.)
	set_mode(mode) {
		const route = mode === "route";
		const heatmap = mode === PP.heatmap_view;
		this.$toolbar.toggle(!route);
		this.$trays.toggle(!route && !heatmap);
		this.$grid_wrap.toggle(!route);
		this.$hint.toggle(!route);
		this.$route.toggle(!!route);
		if (this.$my_week) this.$my_week.hide();
		this.p4_set_mode(mode);
		this.p6c_set_mode(mode);
		this.p6d_set_mode(mode);
		if (route && this.$draft_bar) this.$draft_bar.hide();
		[this.$project, this.$pm, this.$foreign, this.$undo].forEach(($el) => $el && $el.toggle(!heatmap));
		if (heatmap && this.$copy) this.$copy.hide();
		if (route || heatmap) this.$panel.hide();
		if (!route) this.clear_route_map();
	}

	go_route(resource, ymd) {
		const route = frappe.get_route() || [];
		if (route[0] === PP.route && route[1] === PP.route_view && route[2] === resource && route[3] === ymd) {
			this.load_route();
			return;
		}
		this.p6a_route(() => frappe.set_route(PP.route, PP.route_view, resource, ymd), false);
	}

	go(view, anchor) {
		const route = frappe.get_route() || [];
		if (route[0] === PP.route && route[1] === view && route[2] === anchor) {
			this.load();
			return;
		}
		this.p6a_route(() => frappe.set_route(PP.route, view, anchor), true);
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
		if (this.view === PP.heatmap_view) {
			// The same weeks as the calendar: they begin on the site's first weekday, which is also
			// what get_heatmap and copy_week snap to.
			const start = anchor.clone().subtract((anchor.day() - first + 7) % 7, "days");
			return { start, end: start.clone().add(PP.heatmap_weeks * 7 - 1, "days") };
		}
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
		// The heatmap steps half its span, so each step keeps four weeks in common.
		const next =
			this.view === "month"
				? anchor.startOf("month").add(sign, "months")
				: anchor.add(sign * (this.view === PP.heatmap_view ? (PP.heatmap_weeks / 2) * 7 : 7), "days");
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
			[PP.heatmap_view, __("Heatmap")],
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
		this.$foreign = $foreign;
		$('<input type="checkbox">')
			.prop("checked", this.show_foreign)
			.on("change", (e) => {
				this.show_foreign = e.target.checked;
				this.save_pref(PP.prefs.foreign, this.show_foreign ? "1" : "0");
				this.render();
			})
			.appendTo($foreign);
		$foreign.append(document.createTextNode(__("Maintenance, rentals, travel")));

		this.$copy = $('<button type="button" class="btn btn-default btn-sm pp-copy"></button>')
			.text(__("Copy week…"))
			.attr("title", __("Copy tasks from this week into another week"))
			.hide()
			.on("click", () => this.open_copy_week())
			.appendTo($bar);
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
		if (this.view === PP.heatmap_view) return this.load_heatmap();
		const { start, end } = this.range();
		const token = ++this.request;
		this.$title.text(this.title());
		Object.entries(this.$view_buttons).forEach(([view, $button]) => $button.toggleClass("pp-on", view === this.view));
		this.$body.addClass("pp-loading");
		return Promise.resolve(
			frappe.call({
				method: `${PP.api}.get_planner`,
				args: Object.assign({ start: pp_ymd(start), end: pp_ymd(end) }, this.draft_args()),
			})
		)
			.then((r) => {
				if (token !== this.request) return;
				this.data = (r && r.message) || {};
				this.$body.removeClass("pp-loading");
				this.index();
				this.fill_filters();
				this.load_phase4();
				this.render();
				this.load_overdue();
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
		if (this.over_only && !card.over_plan) return false;
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
		if (this.view === PP.heatmap_view) {
			this.render_heatmap();
			return;
		}
		if (!this.data) return;
		this.$copy.toggle(this.view !== "month" && !!this.data.can_edit);
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
		this.render_phase3b();
		this.render_phase4();
		this.render_phase6a();
		this.render_phase6b();
		this.render_phase6c();
		this.render_phase6d();
	}

	// The availability of one person on one day, as a class and a short text.
	avail_state(day) {
		if (!day) return { cls: "pp-idle", text: "", ratio: 0, soft: 0, soft_ratio: 0 };
		const capacity = Number(day.capacity) || 0;
		const booked = Number(day.booked) || 0;
		// Pencilled (tentative) hours: soft load. `booked`, `free` and the colors count firm work only.
		const soft = Math.max(0, Number(day.soft_booked) || 0);
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
		this.p6d_state(day, state);
		if (conflict) state.cls += " pp-conflict";
		state.soft = soft;
		state.soft_ratio = capacity > 0 ? Math.min(Math.max(0, 1 - state.ratio), soft / capacity) : 0;
		if (soft > 0.01) {
			state.text += ` · ${__("+{0}h pencil", [pp_hours(soft)])}`;
			if (capacity > 0 && booked + soft - capacity > 0.01) state.cls += " pp-softover";
		}
		return state;
	}

	// The bar under a person-day: firm hours solid, pencilled hours hatched after them.
	bar_html(state) {
		const width = Math.round(Math.min(1, state.ratio) * 100);
		const soft = Math.round(Math.min(1, state.soft_ratio || 0) * 100);
		return `<div class="pp-bar"><i style="width:${width}%"></i>${
			soft > 0 ? `<b style="left:${width}%;width:${soft}%"></b>` : ""
		}</div>`;
	}

	day_tip(resource, ymd, day) {
		const lines = [`${this.resource_label(resource)} · ${pp_when(ymd)}`];
		if (!day) return lines.join("\n");
		lines.push(__("{0}h of {1}h booked", [pp_hours(day.booked), pp_hours(day.capacity)]));
		if (Number(day.soft_booked) > 0) {
			lines.push(__("{0}h pencilled (tentative, not counted as booked)", [pp_hours(day.soft_booked)]));
		}
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
			if (booking.tentative) parts.push(__("pencil"));
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
					const hatch = state.soft > 0.01 ? " pp-soft-mini" : "";
					html.push(`<div class="pp-avail pp-mini ${state.cls}${hatch}" title="${pp_esc(tip)}"></div>`);
					return;
				}
				html.push(`
					<div class="pp-avail ${state.cls}" title="${pp_esc(tip)}">
						<span class="pp-avail-text">${pp_esc(state.text)}</span>
						${this.bar_html(state)}
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
		this.render_overdue_tray();
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
					${this.p6d_day_html(ymd, resources)}
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
		html.push(this.p6d_crew_notes_row(days));
		resources.forEach((resource) => {
			html.push(this.person_html(resource, true));
			days.forEach((ymd) => {
				const day = this.day_of(resource.name, ymd);
				const state = this.avail_state(day);
				const chips = ((day && day.bookings) || [])
					.map((booking) => this.booking_html(resource.name, ymd, booking))
					.join("");
				html.push(`
					<div class="pp-cell${ymd < today ? " pp-past" : ""}" data-date="${pp_esc(ymd)}" data-resource="${pp_esc(
					resource.name
				)}">
						<div class="pp-cap ${state.cls}" title="${pp_esc(this.day_tip(resource.name, ymd, day))}">
							<span class="pp-cap-text">${pp_esc(state.text)}</span>
							${this.bar_html(state)}
							${this.drive_html(resource.name, ymd, day)}
						</div>
						${chips}
					</div>`);
			});
		});
		html.push(this.p4_equipment_rows(days));
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
		if (booking.kind === "block") return this.p6d_block_html(resource, ymd, booking);
		const card = booking.ref && this.by_task[booking.ref];
		const hours = booking.slot ? booking.slot.join("–") : `${pp_hours(booking.hours)}h`;
		if (card) {
			const classes = ["pp-card"];
			if (card.movable && this.data.can_edit) classes.push("pp-movable");
			if (card.saving) classes.push("pp-saving");
			if (!this.task_visible(card)) classes.push("pp-dim");
			if (card.overdue) classes.push("pp-overdue");
			const pencil = !!(card.tentative || booking.tentative);
			if (pencil) classes.push("pp-tentative");
			const color = pp_color(card.color, PP.default_color);
			const warned = this.dependency_lines(card).length > 0 || this.gap_lines(card).length > 0;
			const tip = [
				card.subject,
				card.project_title || card.project,
				hours,
				pencil ? __("Pencil (tentative)") : "",
				...this.dependency_lines(card),
				...this.gap_lines(card),
			]
				.filter(Boolean)
				.join("\n");
			const sub = [hours, pencil ? __("Pencil") : "", warned ? "⚠" : ""].filter(Boolean).join(" · ");
			return `
				<div class="${classes.join(" ")}" data-task="${pp_esc(card.name)}" data-date="${pp_esc(ymd)}"
					data-resource="${pp_esc(resource)}" tabindex="0" title="${pp_esc(
					[tip, ...this.p4_tip_lines(card)].filter(Boolean).join("\n")
				)}" style="border-left-color:${pp_esc(color)}">
					<div class="pp-card-title">${pp_esc(card.subject || card.name)}</div>
					<div class="pp-card-sub">${pp_esc(sub)}</div>
					<div class="pp-card-chips">${this.p4_card_chips(card).join("")}</div>
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
		// A vehicle or asset used twice, in the shop or retired (the card's own `conflicts` list).
		return out.concat(this.p4_card_conflicts(card));
	}

	// Planning-helper warnings on a card, as sentences. They never block anything.
	dependency_lines(card) {
		return (card.blocked_by || []).map((item) => {
			const who = item.subject ? `${item.task} (${item.subject})` : item.task;
			return item.end
				? __("Starts before {0} ends on {1}", [who, pp_when(item.end)])
				: __("Starts before {0} ends", [who]);
		});
	}

	// One qualification gap as the credential type it names ("Confined Space Entry").
	gap_type(gap) {
		const raw = gap && typeof gap === "object" ? gap.credential_type || gap.type || gap.message || "" : gap;
		return String(raw == null ? "" : raw).replace(/^No one on the crew holds:\s*/i, "");
	}

	gap_lines(card) {
		return (card.qualification_gaps || [])
			.map((gap) => this.gap_type(gap))
			.filter(Boolean)
			.map((type) => __("No one on the crew holds: {0}", [type]));
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
		this.p4_card_chips(card).forEach((chip) => chips.push(chip));
		if (card.tentative) chips.unshift(`<span class="pp-chip pp-pencil">${pp_esc(__("Pencil"))}</span>`);
		const amber = (text) => `<span class="pp-chip pp-amber">${pp_esc(text)}</span>`;
		const blocked = card.blocked_by || [];
		if (blocked.length) {
			const first = blocked[0].subject || blocked[0].task;
			chips.push(
				amber(
					blocked.length > 1
						? __("Starts before {0} +{1}", [first, blocked.length - 1])
						: __("Starts before {0}", [first])
				)
			);
		}
		const gaps = (card.qualification_gaps || []).map((gap) => this.gap_type(gap)).filter(Boolean);
		if (gaps.length) {
			chips.push(
				amber(gaps.length > 1 ? __("Missing {0} +{1}", [gaps[0], gaps.length - 1]) : __("Missing {0}", [gaps[0]]))
			);
		}
		chips.push(...this.weather_chips(card, ymd));

		const classes = ["pp-card"];
		if (card.movable && this.data.can_edit) classes.push("pp-movable");
		if (card.overdue) classes.push("pp-overdue");
		if (card.tentative) classes.push("pp-tentative");
		if (card.saving) classes.push("pp-saving");
		const color = pp_color(card.color, PP.default_color);
		const sub = [card.project_title || card.project, this.hours_text(card)].filter(Boolean).join(" · ");
		const crew_names = (card.crew || []).map((member) => this.resource_label(member.resource, member.label));
		const tip = [
			card.subject,
			sub,
			card.tentative ? __("Pencil (tentative): not counted as booked") : "",
			crew_names.join(", "),
			...conflicts,
			...this.dependency_lines(card),
			...this.gap_lines(card),
			...this.p4_tip_lines(card),
			...this.weather_lines(card),
		]
			.filter(Boolean)
			.join("\n");
		// Cards in the Needs crew and Unscheduled trays offer to find a day (ymd is null there).
		const suggest =
			!ymd && card.movable && this.data.can_edit
				? `<div class="pp-suggest-row"><button type="button" class="btn btn-default btn-xs pp-suggest" data-suggest="${pp_esc(
						card.name
				  )}">${pp_esc(__("Suggest dates"))}</button></div>`
				: "";
		// A pencilled task can be made firm from its card (the server checks it like any change).
		const firm =
			card.tentative && card.movable && this.data.can_edit && this.view !== "month"
				? `<div class="pp-firm-row"><button type="button" class="btn btn-default btn-xs pp-firm" data-firm="${pp_esc(
						card.name
				  )}">${pp_esc(__("Firm up"))}</button></div>`
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
				${suggest}${firm}
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
			if (!e.target.closest || !e.target.closest(".pp-card, .pp-fcard, .pp-route, .pp-hm-cell, .pp-ecard")) return;
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
		const cell_el = target.closest(".pp-hm-cell[data-hm-week]");
		if (cell_el) {
			this.go("week", cell_el.getAttribute("data-hm-week"));
			return;
		}
		const equip_el = target.closest(".pp-ecard[data-etask]");
		if (equip_el) {
			const name = equip_el.getAttribute("data-etask");
			const task = this.by_task[name];
			if (task) this.open_card(task);
			else frappe.set_route("Form", "Task", name);
			return;
		}
		const firm_el = target.closest(".pp-firm[data-firm]");
		if (firm_el) {
			const task = this.by_task[firm_el.getAttribute("data-firm")];
			if (task) this.firm_up(task);
			return;
		}
		const od_el = target.closest(".pp-od-card[data-od-task]");
		if (od_el) {
			if (target.closest("input, button")) return;
			const name = od_el.getAttribute("data-od-task");
			const task = this.by_task[name];
			if (task) this.open_card(task);
			else frappe.set_route("Form", "Task", name);
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
		if (el.closest(".p6b-grip")) return null; // Phase 6B: an edge resizes, it never moves
		if (el.closest(".pk-drawer")) return this.p6a_drag_source(el);
		const person_el = el.closest(".pp-person[data-resource]");
		if (person_el) return { kind: "person", el: person_el, resource: person_el.getAttribute("data-resource") };
		// One overdue row at a time; its tick box and buttons stay clickable.
		const od_el = el.closest(".pp-od-card[data-od-task]");
		if (od_el) {
			if (el.closest("input, button")) return null;
			const row = this.overdue_row(od_el.getAttribute("data-od-task"));
			return row ? { kind: "overdue", el: od_el, row, from_date: null, from_resource: null } : null;
		}
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
		this.p6a_edge_scroll(x, y);
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
		this.p6b_lift(drag);
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
		if (this.p6b_drop(source, target)) return;
		if (source.kind === "overdue") {
			this.drop_overdue(source.row, target);
			return;
		}
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
			tentative: card.tentative ? 1 : 0,
			equipment: this.p4_equipment_of(card),
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
		})
			.then((result) => this.offer_shift(snapshot, method, result))
			.finally(() => this.load());
	}

	// Resolves either way: a refusal has already shown the server's own message.
	send(method, args, opts) {
		opts = opts || {};
		args = this.with_draft(method, args);
		return Promise.resolve(frappe.call({ method: `${(this.p6_send_modules || {})[method] || PP.api}.${method}`, args }))
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
				const toasted = opts.snapshot ? this.push_undo(opts.snapshot, opts.message) : false;
				if (result.name && result.modified) this.modified[result.name] = result.modified;
				if (opts.message && !toasted) frappe.show_alert({ message: opts.message, indicator: "green" }, 5);
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
						`<div class="pp-why"><b>${pp_esc(this.p4_conflict_heading(who))}</b><ul>${(list || [])
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

	push_undo(snapshot, message) {
		this.undo_stack.push(snapshot);
		while (this.undo_stack.length > PP.undo_max) this.undo_stack.shift();
		this.update_undo_button();
		return this.p6a_undo_toast(snapshot, message);
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
		this.p6a_close_toast();
		const snap = this.undo_stack.pop();
		this.update_undo_button();
		if (!snap) {
			frappe.show_alert({ message: __("Nothing to undo"), indicator: "blue" }, 4);
			return;
		}
		if (this.p6b_undo(snap)) return;
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
		if (snap.tentative != null) args.tentative = snap.tentative;
		if (snap.equipment) args.equipment = JSON.stringify(snap.equipment);
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
		if (card.tentative) {
			add(__("Pencil"), __("Tentative: pencilled in, shown as soft load and not counted as booked until firmed up."));
		}
		this.dependency_lines(card).forEach((text) => add(__("Dependency"), text));
		if ((card.depends_on || []).length) add(__("Depends on"), card.depends_on.join(", "));
		this.gap_lines(card).forEach((text) => add(__("Qualification"), text));
		rows.push(...this.p4_dialog_rows(card));
		this.weather_lines(card).forEach((text) => add(__("Weather"), text));
		if (!editable) add(__("Note"), __("This task is read-only here."));

		const links = [["task", __("Open task")]];
		if (card.project) links.push(["project", __("Open project")]);
		if (editable) links.push(["suggest", __("Suggest dates")]);
		if (editable && card.tentative) links.push(["firm", __("Firm up")]);
		links.push(...this.phase5_links(card));
		links.push(...this.p6a_links(card));
		links.push(...this.p6c_links(card));
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
				{
					fieldtype: "Check",
					fieldname: "tentative",
					label: __("Tentative (pencil)"),
					default: card.tentative ? 1 : 0,
					description: __("A pencilled task is soft load: it never needs a reason and is not counted as booked."),
				},
				...this.phase5_dialog_fields(card),
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
			fields.push(...this.p4_equipment_fields(card));
		}

		const dialog = this.p6a_dialog({ title: card.subject || card.name, fields, size: "large" }, card);
		if (editable) {
			dialog.set_primary_action(__("Save"), (values) => this.save_dialog(card, dialog, values));
		}
		dialog.$wrapper.find("[data-action]").on("click", (e) => {
			const action = e.currentTarget.getAttribute("data-action");
			dialog.hide();
			if (this.p6a_action(action, card)) return;
			if (this.p6c_action(action, card)) return;
			if (action === "task") frappe.set_route("Form", "Task", card.name);
			else if (action === "project") frappe.set_route("Form", "Project", card.project);
			else if (action === "suggest") this.suggest_dates(card);
			else if (action === "firm") this.firm_up(card);
			else this.phase5_action(action, card);
		});
		dialog.show();
		if (editable && (card.credentials || []).length) dialog.set_value("credentials", card.credentials.slice());
		this.p4_refine_actuals(card, dialog);
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
		const tentative = values.tentative ? 1 : 0;
		if (tentative !== (card.tentative ? 1 : 0)) args.tentative = tentative;

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
		this.p4_equipment_arg(card, values, args);

		dialog.hide();
		// Phase 5: Outdoor work / Customer-facing visit are saved first, on their own (see save_phase5_flags).
		const flags = this.save_phase5_flags(card, values);
		if (Object.keys(args).length === 1) {
			flags.then((saved) => saved && this.load());
			return;
		}
		flags.then(() => this.commit(card, "save_task", args, __("{0} updated", [card.subject || card.name])));
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
							<div>${drive}${long}${this.suggestion_weather_html(item)}</div>
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

	// ------------------------------------------------------------------ tentative and dependencies

	// Turn a pencilled task into a firm booking. The server checks it like any other change, so a
	// conflict asks for a reason through the same dialog.
	firm_up(card) {
		return this.commit(
			card,
			"save_task",
			{ task: card.name, tentative: 0 },
			__("{0} is now firm", [card.subject || card.name])
		);
	}

	// Mon-Fri days stepped from `from` (exclusive) to `to` (inclusive); 0 unless `to` is later.
	working_days_between(from, to) {
		if (!from || !to || to <= from) return 0;
		const day = moment(from, "YYYY-MM-DD");
		const last = moment(to, "YYYY-MM-DD");
		let count = 0;
		for (let i = 0; i < 400 && day.isBefore(last, "day"); i++) {
			day.add(1, "days");
			if (day.isoWeekday() <= 5) count++;
		}
		return count;
	}

	// After a task moved later: when other tasks depend on it, offer to move them by the same
	// working days. `successors` is the server's answer to the move (a count and the names).
	offer_shift(snapshot, method, result) {
		const found = result && result.successors;
		if (method !== "save_task" || !found) return result;
		const names = Array.isArray(found) ? found : found.names || found.tasks || [];
		const count = Number((Array.isArray(found) ? found.length : found.count) || names.length) || 0;
		if (!count) return result;
		const moved_to = (result.card && result.card.start) || "";
		const days = Number(found.working_days || found.days) || this.working_days_between(snapshot.start, moved_to);
		if (days <= 0) return result;
		const what = count === 1 ? __("Shift 1 task that follows") : __("Shift {0} tasks that follow", [count]);
		const by = days === 1 ? __("by 1 working day?") : __("by {0} working days?", [days]);
		const shown = names.slice(0, 8).map((name) => {
			const known = this.by_task[name];
			const label = known && known.subject ? `${name} · ${known.subject}` : name;
			return `<li>${pp_esc(label)}</li>`;
		});
		if (names.length > 8) shown.push(`<li>${pp_esc(__("and {0} more", [names.length - 8]))}</li>`);
		const message =
			`<p>${pp_esc(`${what} ${by}`)}</p>` +
			(shown.length ? `<ul>${shown.join("")}</ul>` : "") +
			`<p class="pp-stop-sub">${pp_esc(__("Each keeps its length and time of day. Weekends are skipped."))}</p>`;
		const task = result.name || snapshot.task;
		frappe.confirm(message, () => this.shift_successors(task, days, result.modified, count, names));
		return result;
	}

	// `names` are the successors save_task reported as still to move: passing them keeps a task
	// ERPNext already pushed during the save from being moved twice.
	shift_successors(task, days, modified, count, names) {
		const args = { task, days, modified: modified || this.modified[task] || "" };
		if ((names || []).length) args.tasks = JSON.stringify(names);
		return this.send("shift_successors", args)
			.then((result) => {
				if (!result) return;
				const moved = (result.moved || []).length || count;
				frappe.show_alert(
					{
						message:
							days === 1
								? __("{0} later task(s) shifted by 1 working day", [moved])
								: __("{0} later task(s) shifted by {1} working days", [moved, days]),
						indicator: "green",
					},
					6
				);
			})
			.finally(() => this.load());
	}

	// ------------------------------------------------------------------ overdue tray

	load_overdue() {
		const token = ++this.overdue_request;
		return Promise.resolve(
			frappe.call({ method: `${PP.api}.get_overdue`, args: { limit: PP.overdue_limit } })
		)
			.then((r) => {
				if (token !== this.overdue_request) return;
				this.overdue = this.normalize_overdue((r && r.message) || {});
				const known = new Set();
				this.overdue.groups.forEach((group) => group.rows.forEach((row) => known.add(row.name)));
				[...this.overdue_selected].forEach((name) => {
					if (!known.has(name)) this.overdue_selected.delete(name);
				});
				if (this.view !== PP.heatmap_view && this.data) this.render_trays();
			})
			.catch(() => null);
	}

	// get_overdue answers one group per project; read it forgivingly (a list or an object keyed
	// by project, rows under `tasks` or `rows`, crew as labels or as objects).
	normalize_overdue(raw) {
		const list = raw.projects || raw.groups || [];
		const entries = Array.isArray(list)
			? list
			: Object.entries(list).map(([project, group]) => Object.assign({ project }, group));
		const groups = entries.map((group) => {
			const rows = (group.tasks || group.rows || []).map((row) => ({
				name: row.name || row.task,
				subject: row.subject || row.name || row.task,
				end: row.end || row.exp_end_date || "",
				modified: row.modified || "",
				crew: (row.crew_labels || row.crew || []).map((member) =>
					typeof member === "string" ? member : member.label || member.resource || ""
				),
			}));
			return {
				project: group.project || "",
				title: group.project_title || group.title || group.project || "",
				count: Number(group.count) || rows.length,
				rows: rows.filter((row) => row.name),
			};
		});
		const total = Number(raw.total != null ? raw.total : raw.count);
		return { groups, total: Number.isFinite(total) ? total : groups.reduce((sum, group) => sum + group.count, 0) };
	}

	overdue_group_visible(group) {
		if (this.project && group.project !== this.project) return false;
		if (this.pm) {
			const project = group.project && this.by_project[group.project];
			if (!project || project.pm !== this.pm) return false;
		}
		return true;
	}

	overdue_row(name) {
		for (const group of (this.overdue && this.overdue.groups) || []) {
			const row = group.rows.find((item) => item.name === name);
			if (row) return row;
		}
		return null;
	}

	overdue_selection() {
		const rows = [];
		((this.overdue && this.overdue.groups) || []).forEach((group) =>
			group.rows.forEach((row) => {
				if (this.overdue_selected.has(row.name)) rows.push(row);
			})
		);
		return rows;
	}

	render_overdue_tray() {
		if (!this.overdue) return;
		const groups = this.overdue.groups.filter((group) => this.overdue_group_visible(group) && group.rows.length);
		const total = groups.reduce((sum, group) => sum + group.rows.length, 0);
		if (!total) return;
		const editable = !!(this.data && this.data.can_edit);
		const $tray = $('<div class="pp-od"></div>').appendTo(this.$trays);
		const $h = $("<h5></h5>").appendTo($tray);
		$("<span></span>").text(__("Overdue ({0})", [total])).appendTo($h);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(this.overdue_open ? __("Hide") : __("Show"))
			.on("click", () => {
				this.overdue_open = !this.overdue_open;
				this.render_trays();
			})
			.appendTo($h);
		if (!this.overdue_open) return;

		if (editable) {
			const $actions = $('<div class="pp-od-actions"></div>').appendTo($tray);
			[
				["reschedule", __("Reschedule to…"), "btn-default"],
				["complete", __("Mark done"), "btn-default"],
				["cancel", __("Cancel"), "btn-default"],
			].forEach(([action, label, cls]) => {
				$(`<button type="button" class="btn ${cls} btn-xs pp-od-act"></button>`)
					.attr("data-od-action", action)
					.text(label)
					.on("click", () => this.overdue_act(action))
					.appendTo($actions);
			});
			$('<span class="pp-od-count"></span>').appendTo($actions);
		}
		const html = [];
		groups.forEach((group) => {
			const all = group.rows.every((row) => this.overdue_selected.has(row.name));
			html.push(`<div class="pp-od-project">${
				editable
					? `<input type="checkbox" data-od-project="${pp_esc(group.project)}" ${all ? "checked" : ""} aria-label="${pp_esc(
							__("Select all overdue tasks of {0}", [group.title || group.project])
					  )}">`
					: ""
			}<span>${pp_esc(group.title || group.project || __("No project"))}</span><span class="pp-od-count">${pp_esc(
				group.rows.length
			)}</span></div>`);
			group.rows.forEach((row) => {
				const crew = row.crew.filter(Boolean).join(", ");
				html.push(`
					<div class="pp-od-card" data-od-task="${pp_esc(row.name)}" title="${pp_esc(
					[row.subject, crew, editable ? __("Drag onto a day to reschedule it") : ""].filter(Boolean).join("\n")
				)}">
						${
							editable
								? `<input type="checkbox" data-od-pick="${pp_esc(row.name)}" ${
										this.overdue_selected.has(row.name) ? "checked" : ""
								  } aria-label="${pp_esc(__("Select {0}", [row.subject]))}">`
								: ""
						}
						<div class="pp-od-main">
							<div class="pp-od-subject">${pp_esc(row.subject)}</div>
							<div class="pp-od-sub">${pp_esc(crew || __("No crew"))}</div>
						</div>
						<span class="pp-od-end">${pp_esc(row.end ? __("Ended {0}", [pp_when(row.end)]) : "")}</span>
					</div>`);
			});
		});
		const $list = $('<div class="pp-od-list"></div>').html(html.join("")).appendTo($tray);
		$tray.on("change", "input[data-od-pick], input[data-od-project]", (e) => {
			const input = e.target;
			if (input.hasAttribute("data-od-pick")) {
				const name = input.getAttribute("data-od-pick");
				if (input.checked) this.overdue_selected.add(name);
				else this.overdue_selected.delete(name);
			} else {
				const project = input.getAttribute("data-od-project");
				const group = groups.find((item) => item.project === project);
				((group && group.rows) || []).forEach((row) => {
					if (input.checked) this.overdue_selected.add(row.name);
					else this.overdue_selected.delete(row.name);
				});
			}
			this.sync_overdue($tray, groups);
		});
		this.sync_overdue($tray, groups);
		return $list;
	}

	// Keep the ticks, the count and the action buttons in step with the selection.
	sync_overdue($tray, groups) {
		groups.forEach((group) => {
			const picked = group.rows.filter((row) => this.overdue_selected.has(row.name)).length;
			const box = $tray.find("input[data-od-project]").filter((i, el) => el.getAttribute("data-od-project") === group.project)[0];
			if (box) {
				box.checked = picked === group.rows.length;
				box.indeterminate = picked > 0 && picked < group.rows.length;
			}
			group.rows.forEach((row) => {
				const pick = $tray.find("input[data-od-pick]").filter((i, el) => el.getAttribute("data-od-pick") === row.name)[0];
				if (pick) pick.checked = this.overdue_selected.has(row.name);
			});
		});
		const chosen = this.overdue_selection().length;
		$tray.find(".pp-od-act").prop("disabled", !chosen);
		$tray.find(".pp-od-actions .pp-od-count").text(chosen ? __("{0} selected", [chosen]) : __("Tick tasks to act on them"));
	}

	overdue_list_html(rows) {
		return `<ul>${rows
			.slice(0, 30)
			.map((row) => `<li>${pp_esc(row.subject)} <span class="pp-stop-sub">(${pp_esc(row.name)})</span></li>`)
			.join("")}${rows.length > 30 ? `<li>${pp_esc(__("and {0} more", [rows.length - 30]))}</li>` : ""}</ul>`;
	}

	overdue_act(action) {
		const rows = this.overdue_selection();
		if (!rows.length) return;
		if (action === "reschedule") {
			this.ask_reschedule(rows);
			return;
		}
		const done = action === "complete";
		const head = done
			? __("Mark these {0} task(s) as Completed?", [rows.length])
			: __("Cancel these {0} task(s)? They are set to Canceled and leave the planner.", [rows.length]);
		frappe.confirm(`<p>${pp_esc(head)}</p>${this.overdue_list_html(rows)}`, () =>
			this.run_bulk(
				rows.map((row) => row.name),
				action,
				null
			)
		);
	}

	ask_reschedule(rows) {
		const dialog = new frappe.ui.Dialog({
			title: __("Reschedule {0} overdue task(s)", [rows.length]),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "tasks",
					options: `<p>${pp_esc(
						__("Each task starts on the day you pick and keeps its length. Conflicts ask for a reason.")
					)}</p>${this.overdue_list_html(rows)}`,
				},
				{ fieldtype: "Date", fieldname: "date", label: __("Start on"), reqd: 1, default: this.today() },
			],
			primary_action_label: __("Reschedule"),
			primary_action: (values) => {
				const date = (values && values.date) || "";
				if (!date) return;
				if (date < this.today()) {
					frappe.msgprint(__("Pick today or a later day: the tasks would still be overdue."));
					return;
				}
				dialog.hide();
				this.run_bulk(
					rows.map((row) => row.name),
					"reschedule",
					date
				);
			},
		});
		dialog.show();
	}

	// One bulk_update. Conflicts (rescheduling) ask for a reason once for the whole batch; a task
	// that fails is reported and never stops the rest.
	run_bulk(names, action, date) {
		const args = { tasks: JSON.stringify(names), action };
		if (date) args.date = date;
		return this.send("bulk_update", args)
			.then((result) => {
				if (!result) return;
				const failed = result.failed || result.errors || [];
				const failed_names = new Set(
					failed.map((item) => (typeof item === "string" ? item : item.task || item.name))
				);
				names.forEach((name) => {
					if (!failed_names.has(name)) this.overdue_selected.delete(name);
				});
				const ok = Array.isArray(result.updated) ? result.updated.length : Math.max(names.length - failed.length, 0);
				const label = {
					reschedule: __("{0} task(s) rescheduled to {1}", [ok, date ? pp_when(date) : ""]),
					complete: __("{0} task(s) marked Completed", [ok]),
					cancel: __("{0} task(s) set to Canceled", [ok]),
				}[action];
				if (ok) frappe.show_alert({ message: label, indicator: "green" }, 6);
				if (failed.length) {
					const lines = failed.map((item) => {
						if (typeof item === "string") return `<li>${pp_esc(item)}</li>`;
						const why = item.error || item.message || item.reason || "";
						return `<li>${pp_esc(item.task || item.name)}${why ? `: ${pp_esc(why)}` : ""}</li>`;
					});
					frappe.msgprint({
						title: __("Some tasks were not changed"),
						message: `<p>${pp_esc(__("{0} of {1} task(s) failed:", [failed.length, names.length]))}</p><ul>${lines.join("")}</ul>`,
						indicator: "orange",
					});
				}
			})
			.finally(() => this.load());
	}

	// One overdue row dragged onto a day: reschedule it to start there.
	drop_overdue(row, target) {
		if (!target.date) return;
		const run = () => this.run_bulk([row.name], "reschedule", target.date);
		if (target.date < this.today()) {
			frappe.confirm(__("{0} is before today. Schedule the task on a past day?", [pp_when(target.date)]), run);
			return;
		}
		run();
	}

	// ------------------------------------------------------------------ capacity heatmap

	load_heatmap() {
		const { start } = this.range();
		const token = ++this.request;
		this.$title.text(this.title());
		Object.entries(this.$view_buttons).forEach(([view, $button]) => $button.toggleClass("pp-on", view === this.view));
		this.$body.addClass("pp-loading");
		return Promise.resolve(
			frappe.call({ method: `${PP.api}.get_heatmap`, args: { start: pp_ymd(start), weeks: PP.heatmap_weeks } })
		)
			.then((r) => {
				if (token !== this.request) return;
				this.$body.removeClass("pp-loading");
				this.heatmap = this.normalize_heatmap((r && r.message) || {}, start);
				this.render_heatmap();
			})
			.catch(() => {
				if (token !== this.request) return;
				this.$body.removeClass("pp-loading");
				this.heatmap = null;
				this.render_heatmap();
			});
	}

	// get_heatmap answers the weeks and, per person, a cell per week. Read it forgivingly: weeks as
	// dates or objects, a person's cells as a list in week order or keyed by the week's first day.
	normalize_heatmap(raw, start) {
		let weeks = (raw.weeks || []).map((week) => (typeof week === "string" ? { start: week } : week || {}));
		weeks = weeks.map((week) => ({ start: week.start || week.week_start || week.date || "" })).filter((week) => week.start);
		if (!weeks.length) {
			// Without a week list, count on from the server's week_start (else the week asked for).
			const first = moment(raw.week_start || pp_ymd(start), "YYYY-MM-DD");
			weeks = Array.from({ length: PP.heatmap_weeks }, (v, i) => ({ start: pp_ymd(first.clone().add(i * 7, "days")) }));
		}
		const people = raw.resources || raw.rows || raw.people || [];
		const rows = (Array.isArray(people) ? people : Object.entries(people).map(([name, row]) => Object.assign({ name }, row))).map(
			(person) => {
				const name = person.resource || person.name || "";
				const source = person.weeks || person.cells || (raw.cells && raw.cells[name]) || [];
				const by_start = {};
				const ordered = [];
				if (Array.isArray(source)) {
					source.forEach((cell, i) => {
						const key = (cell && (cell.start || cell.week_start || cell.week)) || (weeks[i] && weeks[i].start);
						by_start[key] = cell;
						ordered.push(cell);
					});
				} else {
					Object.entries(source).forEach(([key, cell]) => {
						by_start[key] = cell;
					});
				}
				return { name, label: person.label || name, group: person.group || "", by_start, ordered };
			}
		);
		return { weeks, rows };
	}

	// A week cell as a color class, a percentage and a short text. Firm hours only decide the
	// color; pencilled hours are drawn as a hatched stretch after them.
	heat_state(cell) {
		const capacity = Number(cell.capacity) || 0;
		const booked = Number(cell.booked) || 0;
		const soft = Math.max(0, Number(cell.soft_booked) || 0);
		const over_days = Number(cell.over_days) || 0;
		const off_days = Number(cell.off_days) || 0;
		if (capacity <= 0 && booked <= 0) {
			return { cls: "pp-idle pp-offday", pct: "", sub: soft > 0 ? __("+{0}h pencil", [pp_hours(soft)]) : __("Off"), ratio: 0, soft_ratio: 0, capacity, booked, soft, over_days, off_days };
		}
		const ratio = capacity > 0 ? booked / capacity : 2;
		// The server's band (get_heatmap `level`) decides the color; the same rule is the fallback.
		const level = ["green", "amber", "red"].includes(cell.level) ? cell.level : null;
		const cls = level ? `pp-${level}` : ratio > 1.0001 ? "pp-red" : ratio <= 0.75 ? "pp-green" : "pp-amber";
		const sub = [__("{0}h of {1}h", [pp_hours(booked), pp_hours(capacity)])];
		if (soft > 0.01) sub.push(__("+{0}h pencil", [pp_hours(soft)]));
		if (over_days > 0) sub.push(over_days === 1 ? __("1 day over") : __("{0} days over", [over_days]));
		return {
			cls,
			pct: capacity > 0 ? `${Math.round(ratio * 100)}%` : __("Off"),
			sub: sub.join(" · "),
			ratio,
			soft_ratio: capacity > 0 ? Math.min(Math.max(0, 1 - ratio), soft / capacity) : 0,
			capacity,
			booked,
			soft,
			over_days,
			off_days,
		};
	}

	heat_tip(label, week, cell, state) {
		const lines = [`${label} · ${__("week of {0}", [pp_when(week.start)])}`];
		lines.push(__("{0}h of {1}h booked", [pp_hours(state.booked), pp_hours(state.capacity)]));
		if (cell.free != null) lines.push(__("{0}h free", [pp_hours(cell.free)]));
		if (state.soft > 0.01) lines.push(__("{0}h pencilled (tentative, not counted as booked)", [pp_hours(state.soft)]));
		if (state.over_days > 0) lines.push(__("{0} day(s) over their hours", [state.over_days]));
		if (state.off_days > 0) lines.push(__("{0} day(s) off", [state.off_days]));
		lines.push(__("Click to open this week"));
		return lines.join("\n");
	}

	render_heatmap() {
		const data = this.heatmap;
		this.$hint.text(
			__(
				"Each square is one person's week: firm booked hours against their hours. Hatching is pencilled (tentative) work. Driving here uses saved and estimated times only. Click a week to open it."
			)
		);
		this.$grid_wrap.empty().removeClass("pp-month pp-week pp-crew-view");
		if (!data) {
			$('<div class="pp-section pp-empty-note"></div>')
				.text(__("The heatmap could not be loaded. Refresh the planner and try again."))
				.appendTo(this.$grid_wrap);
			return;
		}
		const order = (group) => {
			const index = PP.groups.indexOf(group);
			return index < 0 ? PP.groups.length : index;
		};
		const rows = data.rows
			.filter((row) => !this.group || !row.group || row.group === this.group)
			.slice()
			.sort((a, b) => order(a.group) - order(b.group) || String(a.label).localeCompare(String(b.label)));
		if (!rows.length) {
			$('<div class="pp-section pp-empty-note"></div>')
				.text(__("No planner resources to show. Add the people you schedule under Planner Resources."))
				.appendTo(this.$grid_wrap);
			return;
		}
		const today = this.today();
		const html = [`<div class="pp-hm-head"></div>`];
		data.weeks.forEach((week) => {
			const end = pp_ymd(moment(week.start, "YYYY-MM-DD").add(6, "days"));
			const current = today >= week.start && today <= end;
			html.push(
				`<div class="pp-hm-head${current ? " pp-today" : ""}">${pp_esc(moment(week.start, "YYYY-MM-DD").format("MMM D"))}</div>`
			);
		});
		rows.forEach((row) => {
			html.push(
				`<div class="pp-hm-name"><span class="pp-dot" style="background:${pp_esc(
					this.resource_color(row.name)
				)}"></span><span title="${pp_esc(row.label)}">${pp_esc(row.label)}</span></div>`
			);
			data.weeks.forEach((week, index) => {
				const cell = row.by_start[week.start] || row.ordered[index] || {};
				const state = this.heat_state(cell);
				const bar = state.capacity > 0 ? this.bar_html(state) : "";
				html.push(`
					<div>
						<div class="pp-hm-cell ${state.cls}${state.soft > 0.01 && state.capacity <= 0 ? " pp-soft-mini" : ""}" role="button" tabindex="0"
							data-hm-week="${pp_esc(week.start)}" title="${pp_esc(this.heat_tip(row.label, week, cell, state))}"
							aria-label="${pp_esc(`${row.label}, ${pp_when(week.start)}: ${state.pct || state.sub}`)}">
							<span class="pp-hm-pct">${pp_esc(state.pct)}</span>
							<span class="pp-hm-sub">${pp_esc(state.sub)}</span>
							${bar}
						</div>
					</div>`);
			});
		});
		const columns = `170px repeat(${data.weeks.length},minmax(78px,1fr))`;
		const $scroll = $('<div class="pp-section pp-hm-scroll"></div>').appendTo(this.$grid_wrap);
		$('<div class="pp-hm"></div>').css("grid-template-columns", columns).html(html.join("")).appendTo($scroll);
		$(`<div class="pp-hm-legend">
				<span class="pp-hm-key"><i class="pp-green"></i>${pp_esc(__("Up to 75% booked"))}</span>
				<span class="pp-hm-key"><i class="pp-amber"></i>${pp_esc(__("Up to 100%"))}</span>
				<span class="pp-hm-key"><i class="pp-red"></i>${pp_esc(__("Over (or a day over)"))}</span>
				<span class="pp-hm-key"><i class="pp-soft-key"></i>${pp_esc(__("Pencilled (tentative) hours"))}</span>
			</div>`).appendTo(this.$grid_wrap);
	}

	// ------------------------------------------------------------------ copy week

	// The first day of the week holding `ymd`, by the site's first weekday: the weeks that
	// copy_week and get_heatmap use.
	week_start_of(ymd) {
		const day = moment(ymd, "YYYY-MM-DD");
		return pp_ymd(day.clone().subtract((day.day() - this.first_weekday() + 7) % 7, "days"));
	}

	open_copy_week() {
		if (!this.data || !this.data.can_edit) return;
		// The source week is the one on screen (the site's first weekday to the day before it).
		const first = this.week_start_of(this.anchor);
		const last = pp_ymd(moment(first, "YYYY-MM-DD").add(6, "days"));
		const tasks = (this.data.tasks || [])
			.filter((card) => card.movable && card.start && card.start >= first && card.start <= last && this.task_visible(card))
			.sort((a, b) => String(a.start).localeCompare(String(b.start)) || this.compare(a, b));
		if (!tasks.length) {
			frappe.msgprint(
				__("No task starts in the week of {0}. (Tasks that began in an earlier week are not copied.)", [pp_when(first)])
			);
			return;
		}
		const rows = tasks.map((card) => {
			const when = card.end && card.end !== card.start ? `${pp_when(card.start)} – ${pp_when(card.end)}` : pp_when(card.start);
			const sub = [card.project_title || card.project, when, this.hours_text(card)].filter(Boolean).join(" · ");
			const pencil = card.tentative ? ` <span class="pp-chip pp-pencil">${pp_esc(__("Pencil"))}</span>` : "";
			return `
				<label class="pp-copy-row">
					<input type="checkbox" data-copy="${pp_esc(card.name)}" checked>
					<span><b>${pp_esc(card.subject || card.name)}</b>${pencil}
						<div class="pp-copy-sub">${pp_esc(sub)}</div></span>
				</label>`;
		});
		const dialog = new frappe.ui.Dialog({
			title: __("Copy week of {0}", [pp_when(first)]),
			size: "large",
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "tasks",
					options:
						`<p>${pp_esc(
							__("Copies become new tasks with the same crew, hours and qualifications, shifted by whole weeks. The originals stay where they are.")
						)}</p>` +
						`<div class="pp-links">
							<button type="button" class="btn btn-default btn-xs" data-copy-all="1">${pp_esc(__("Select all"))}</button>
							<button type="button" class="btn btn-default btn-xs" data-copy-all="0">${pp_esc(__("Select none"))}</button>
						</div>` +
						`<div class="pp-copy-list">${rows.join("")}</div>`,
				},
				{
					fieldtype: "Date",
					fieldname: "target",
					label: __("Copy into the week holding"),
					reqd: 1,
					default: pp_ymd(moment(first, "YYYY-MM-DD").add(7, "days")),
				},
			],
			primary_action_label: __("Preview"),
			primary_action: (values) => {
				const target_start = this.week_start_of((values && values.target) || "");
				const names = [];
				dialog.$wrapper.find("input[data-copy]").each((i, el) => {
					if (el.checked) names.push(el.getAttribute("data-copy"));
				});
				if (!values || !values.target) return;
				if (!names.length) {
					frappe.msgprint(__("Tick at least one task to copy."));
					return;
				}
				if (target_start === first) {
					frappe.msgprint(__("Pick a different week to copy into."));
					return;
				}
				dialog.hide();
				this.preview_copy(first, target_start, names);
			},
		});
		dialog.$wrapper.on("click", "[data-copy-all]", (e) => {
			const on = e.currentTarget.getAttribute("data-copy-all") === "1";
			dialog.$wrapper.find("input[data-copy]").prop("checked", on);
		});
		dialog.show();
	}

	// A dry run first: what would be created, and which conflicts it would cause. Nothing is
	// written until the planner confirms.
	preview_copy(source_start, target_start, names) {
		const base = { source_start, target_start, tasks: JSON.stringify(names) };
		return this.send("copy_week", Object.assign({ dry_run: 1 }, base)).then((result) => {
			if (!result) return;
			const copies = result.copies || [];
			if (!copies.length) {
				frappe.msgprint(__("Nothing to copy."));
				return;
			}
			const conflicts = result.conflicts || {};
			const clashes = Object.entries(conflicts).filter(([who, list]) => (list || []).length);
			const table = copies
				.map((item) => {
					const known = this.by_task[item.task];
					const was = known && known.start ? `${pp_when(known.start)} → ` : "";
					const to =
						item.to_end && item.to_end !== item.to_start
							? `${pp_when(item.to_start)} – ${pp_when(item.to_end)}`
							: pp_when(item.to_start);
					return `<tr><td><b>${pp_esc(item.subject || item.task)}</b></td><td>${pp_esc(was)}${pp_esc(to)}</td></tr>`;
				})
				.join("");
			const skipped = (result.skipped || []).map(
				(entry) => `<li>${pp_esc(entry.task)}${entry.reason ? `: ${pp_esc(entry.reason)}` : ""}</li>`
			);
			const skip_note = skipped.length
				? `<div class="pp-route-note"><b>${pp_esc(__("Not copied"))}</b><ul>${skipped.join("")}</ul></div>`
				: "";
			const warn = clashes.length
				? `<div class="pp-route-note"><b>${pp_esc(__("These copies would cause conflicts"))}</b>${clashes
						.map(
							([who, list]) =>
								`<div class="pp-why"><b>${pp_esc(this.p4_conflict_heading(who))}</b><ul>${list.map((text) => `<li>${pp_esc(text)}</li>`).join("")}</ul></div>`
						)
						.join("")}<div>${pp_esc(__("You can copy anyway; you will be asked for a reason."))}</div></div>`
				: "";
			const dialog = new frappe.ui.Dialog({
				title: __("Copy {0} task(s) into the week of {1}?", [copies.length, pp_when(target_start)]),
				size: "large",
				fields: [
					{
						fieldtype: "HTML",
						fieldname: "preview",
						options: `${warn}<table class="pp-copy-table">${table}</table>${skip_note}`,
					},
				],
				primary_action_label: clashes.length ? __("Copy anyway") : __("Copy"),
				primary_action: () => {
					dialog.hide();
					this.send("copy_week", Object.assign({ dry_run: 0 }, base)).then((done) => {
						if (!done) return;
						const made = (done.copies || done.created || []).length || copies.length;
						frappe.show_alert(
							{ message: __("{0} task(s) copied into the week of {1}", [made, pp_when(target_start)]), indicator: "green" },
							6
						);
						this.go(this.view, target_start);
					});
				},
			});
			dialog.show();
		});
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
						${leg}${waits}${missing}${this.stop_weather_html(stop)}${open ?`<div class="pp-stop-leg">${open}</div>` : ""}
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

// ====================================================================== Phase 3B: telling people
//
//   /desk/project-planner/my-week/2026-10-12  the signed-in person's own week, phone-first: each
//                                             day's stops in route order with time, site, address
//                                             (tap for Google Maps), crewmates, hours and notes,
//                                             and a link to that day's route view
//
// Draft mode is a toolbar switch, remembered per browser. While it is on, every drag, dialog save
// and Undo is sent with draft=1, so it lands on the planner's own drafts (Planner Draft Change)
// instead of the Task: nobody's assignments move and nobody is told. Drafted cards get a dashed
// outline and a "Draft" chip, and a bar along the bottom says "N unpublished changes · Publish ·
// Discard". Publish asks once for a reason when the drafts conflict together (all or nothing) and
// then tells each person whose days moved. "My week" and "Print crew sheet" are toolbar buttons;
// the crew sheet opens in a new window and prints there (server PDF is broken on production).
//
// Everything Phase 3B adds to the page is in this block, mixed into ProjectPlanner below, so the
// Phase 3A work on the same file merges cleanly. The hooks into the class above are one line each:
// init_phase3b (constructor), my_week_target (route_target), show_my_week (handle_route),
// draft_args (load), with_draft (send) and render_phase3b (render).

const PP3B = {
	my_week: "my-week",
	pref: "ee_project_planner_draft",
	// The writes that go to the drafts while draft mode is on.
	draft_methods: ["save_task", "add_crew", "swap_crew"],
};

const PP3B_STYLE = `
.pp-card.pp-drafted{outline:2px dashed var(--primary,#2490ef);outline-offset:-2px;}
.pp-chip.pp-draft-chip{background:rgba(36,144,239,.14);color:var(--primary,#2490ef);font-weight:600;}
.pp-draft-toggle.pp-draft-on{color:var(--primary,#2490ef);font-weight:600;}
.pp-draft-bar{position:sticky;bottom:8px;z-index:5;display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px 12px;margin:10px 0;border:1px dashed var(--primary,#2490ef);border-radius:10px;background:var(--card-bg);box-shadow:0 4px 14px rgba(0,0,0,.12);font-size:13px;}
.pp-draft-text{font-weight:600;}
.pp-my-week{max-width:720px;padding-bottom:24px;}
.pp-mw-title{font-weight:600;font-size:16px;margin:0 6px;}
.pp-mw-day{border:1px solid var(--border-color);border-radius:10px;background:var(--card-bg);margin-bottom:10px;overflow:hidden;}
.pp-mw-day-head{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:8px 10px;font-weight:600;font-size:13px;border-bottom:1px solid var(--border-color);}
.pp-mw-day-head.pp-mw-today{color:var(--primary,#2490ef);}
.pp-mw-day-head .pp-spacer{flex:1 1 auto;}
.pp-mw-item{padding:8px 10px;border-top:1px solid var(--border-color);font-size:14px;line-height:1.4;}
.pp-mw-item:first-of-type{border-top:none;}
.pp-mw-time{font-size:12px;color:var(--text-muted);font-weight:600;}
.pp-mw-label{font-weight:600;}
.pp-mw-sub{font-size:13px;color:var(--text-muted);}
.pp-mw-addr{display:inline-block;font-size:13px;padding:2px 0;}
.pp-mw-notes{font-size:12px;color:var(--text-muted);margin-top:2px;white-space:pre-wrap;}
.pp-mw-empty{padding:8px 10px;font-size:13px;color:var(--text-muted);}
@media (max-width:760px){
.pp-mw-title{width:100%;order:-1;margin:0;}
.pp-mw-item{font-size:15px;}
.pp-mw-addr{padding:6px 0;}
}
`;

const PP3B_METHODS = {
	init_phase3b() {
		if (!document.getElementById("pp-style-3b")) {
			$("<style id='pp-style-3b'>").text(PP3B_STYLE).appendTo(document.head);
		}
		this.draft_on = this.load_pref(PP3B.pref, "0") === "1";
		this.my_week_date = "";
		this.my_week_data = null;

		this.$draft_toggle = $('<label class="pp-draft-toggle"></label>')
			.attr("title", __("Keep your changes to yourself until you publish them"))
			.toggleClass("pp-draft-on", this.draft_on)
			.insertBefore(this.$undo);
		$('<input type="checkbox">')
			.prop("checked", this.draft_on)
			.on("change", (e) => this.set_draft_mode(e.target.checked))
			.appendTo(this.$draft_toggle);
		this.$draft_toggle.append(document.createTextNode(__("Draft mode")));
		$('<button type="button" class="btn btn-default btn-sm"></button>')
			.text(__("My week"))
			.on("click", () => this.go_my_week(frappe.datetime.get_today()))
			.insertBefore(this.$undo);
		$('<button type="button" class="btn btn-default btn-sm"></button>')
			.text(__("Print crew sheet"))
			.attr("title", __("A one-page sheet of who is where this week, to print"))
			.on("click", () => this.print_crew_sheet())
			.insertBefore(this.$undo);

		this.$draft_bar = $('<div class="pp-draft-bar"></div>').hide().insertAfter(this.$hint);
		this.$my_week = $('<div class="pp-my-week"></div>').hide().appendTo(this.$body);
	},

	// ------------------------------------------------------------------ draft mode

	draft_args() {
		return this.draft_on ? { draft: 1 } : {};
	},

	with_draft(method, args) {
		return this.draft_on && PP3B.draft_methods.includes(method) ? Object.assign({}, args, { draft: 1 }) : args;
	},

	set_draft_mode(on) {
		this.draft_on = !!on;
		this.save_pref(PP3B.pref, this.draft_on ? "1" : "0");
		this.$draft_toggle.toggleClass("pp-draft-on", this.draft_on);
		// An undo recorded in one mode must not be replayed in the other.
		this.undo_stack = [];
		this.update_undo_button();
		frappe.show_alert(
			{
				message: this.draft_on
					? __("Draft mode: your changes are kept for you until you publish them.")
					: __("Draft mode off: changes save straight to the tasks. Your unpublished drafts are kept."),
				indicator: "blue",
			},
			7
		);
		this.load();
	},

	render_phase3b() {
		this.decorate_drafts();
		this.render_draft_bar();
	},

	// A drafted card is drawn by the same card code as any other; the outline and chip go on after.
	decorate_drafts() {
		if (!this.draft_on || !this.data) return;
		this.$body.find(".pp-card[data-task]").each((_index, el) => {
			const card = this.by_task[el.getAttribute("data-task")];
			if (!card || !card.drafted) return;
			el.classList.add("pp-drafted");
			if (el.querySelector(".pp-draft-chip")) return;
			const holder = el.querySelector(".pp-card-chips") || el.querySelector(".pp-card-sub");
			if (holder) {
				holder.insertAdjacentHTML("afterbegin", `<span class="pp-chip pp-draft-chip">${pp_esc(__("Draft"))}</span>`);
			}
		});
	},

	render_draft_bar() {
		const $bar = this.$draft_bar;
		if (!$bar) return;
		if (!this.draft_on || !this.data || this.view === PP.route_view || this.view === PP3B.my_week) {
			$bar.hide().empty();
			return;
		}
		const drafts = this.data.drafts || {};
		const count = Number(drafts.count) || 0;
		const stale = drafts.stale || [];
		$bar.empty().show();
		$('<span class="pp-draft-text"></span>')
			.text(
				count
					? __("{0} unpublished change(s)", [count])
					: __("Draft mode: nothing drafted yet. Your changes stay yours until you publish them.")
			)
			.appendTo($bar);
		if (stale.length) {
			$('<span class="pp-chip pp-amber"></span>')
				.text(__("{0} out of date", [stale.length]))
				.attr("title", stale.map((entry) => `${entry.subject || entry.task}: ${entry.reason}`).join("\n"))
				.appendTo($bar);
		}
		$('<span class="pp-spacer"></span>').appendTo($bar);
		if (stale.length) {
			$('<button type="button" class="btn btn-default btn-xs"></button>')
				.text(__("Discard out-of-date"))
				.on("click", () => this.discard_drafts(stale.map((entry) => entry.task)))
				.appendTo($bar);
		}
		if (count > stale.length) {
			$('<button type="button" class="btn btn-primary btn-xs"></button>')
				.text(__("Publish"))
				.on("click", () => this.publish_drafts())
				.appendTo($bar);
		}
		if (count) {
			$('<button type="button" class="btn btn-default btn-xs"></button>')
				.text(__("Discard"))
				.on("click", () => this.discard_drafts())
				.appendTo($bar);
		}
	},

	// Publish every draft. Conflicts across the batch come back as needs_reason; the same reason
	// dialog as a drag asks once, and the batch is sent again with it (all or nothing).
	publish_drafts(reason) {
		return Promise.resolve(
			frappe.call({
				method: `${PP.api}.publish_drafts`,
				args: reason ? { reason } : {},
				freeze: true,
				freeze_message: __("Publishing your changes…"),
			})
		)
			.then((r) => {
				const result = (r && r.message) || {};
				if (result.needs_reason) {
					if (reason) {
						frappe.msgprint(__("Nothing was published. Refresh the planner and try again."));
						return null;
					}
					return this.ask_reason(result.conflicts || {}).then((given) =>
						given ? this.publish_drafts(given) : null
					);
				}
				this.show_publish_result(result);
				this.undo_stack = [];
				this.update_undo_button();
				return this.load();
			})
			.catch(() => null);
	},

	show_publish_result(result) {
		const published = result.published || [];
		const problems = [].concat(result.skipped || [], result.failed || []);
		const told = Number(result.notified) || 0;
		if (!problems.length) {
			frappe.show_alert(
				{ message: __("{0} change(s) published; {1} people told.", [published.length, told]), indicator: "green" },
				8
			);
			return;
		}
		const list = problems
			.map((entry) => `<li><b>${pp_esc(entry.subject || entry.task)}</b>: ${pp_esc(entry.reason)}</li>`)
			.join("");
		frappe.msgprint({
			title: __("Published {0} of {1}", [published.length, published.length + problems.length]),
			message:
				`<p>${pp_esc(__("{0} people were told about their changes.", [told]))}</p>` +
				`<p>${pp_esc(__("Not published (still in your drafts):"))}</p><ul>${list}</ul>`,
			indicator: "orange",
		});
	},

	// Discard every draft, or only `tasks` (the out-of-date ones). The tasks stay as they are.
	discard_drafts(tasks) {
		const drafts = (this.data && this.data.drafts) || {};
		const count = tasks ? tasks.length : Number(drafts.count) || 0;
		frappe.confirm(__("Discard {0} unpublished change(s)? The tasks stay as they are.", [count]), () =>
			Promise.resolve(
				frappe.call({
					method: `${PP.api}.discard_drafts`,
					args: tasks ? { tasks: JSON.stringify(tasks) } : {},
				})
			)
				.then((r) => {
					const done = ((r && r.message) || {}).discarded || 0;
					frappe.show_alert({ message: __("{0} draft(s) discarded", [done]), indicator: "blue" }, 5);
					this.undo_stack = [];
					this.update_undo_button();
					return this.load();
				})
				.catch(() => null)
		);
	},

	// ------------------------------------------------------------------ My week

	my_week_target(route) {
		if (route[1] !== PP3B.my_week) return null;
		const date = route[2] && moment(route[2], "YYYY-MM-DD", true).isValid() ? route[2] : frappe.datetime.get_today();
		return { view: PP3B.my_week, anchor: date };
	},

	go_my_week(ymd) {
		const route = frappe.get_route() || [];
		if (route[0] === PP.route && route[1] === PP3B.my_week && route[2] === ymd) {
			this.load_my_week();
			return;
		}
		this.p6a_route(() => frappe.set_route(PP.route, PP3B.my_week, ymd), false);
	},

	show_my_week(anchor) {
		this.my_week_date = anchor;
		this.set_mode(true);
		this.$route.hide();
		this.$my_week.show();
		this.load_my_week();
	},

	load_my_week() {
		const token = ++this.request;
		this.$body.addClass("pp-loading");
		return Promise.resolve(
			frappe.call({ method: `${PP.api}.get_my_week`, args: { date: this.my_week_date } })
		)
			.then((r) => {
				if (token !== this.request) return;
				this.$body.removeClass("pp-loading");
				this.my_week_data = (r && r.message) || null;
				this.render_my_week();
			})
			.catch(() => {
				if (token !== this.request) return;
				this.$body.removeClass("pp-loading");
				this.my_week_data = null;
				this.render_my_week();
			});
	},

	render_my_week() {
		const data = this.my_week_data;
		const anchor = this.my_week_date || frappe.datetime.get_today();
		const step = (sign) => this.go_my_week(pp_ymd(moment(anchor, "YYYY-MM-DD").add(sign * 7, "days")));
		const $root = this.$my_week.empty();
		const $head = $('<div class="pp-route-head"></div>').appendTo($root);
		$('<button type="button" class="btn btn-default btn-sm">‹</button>')
			.attr("title", __("Previous week"))
			.on("click", () => step(-1))
			.appendTo($head);
		$('<button type="button" class="btn btn-default btn-sm">›</button>')
			.attr("title", __("Next week"))
			.on("click", () => step(1))
			.appendTo($head);
		const $title = $('<span class="pp-mw-title"></span>').appendTo($head);
		$('<span class="pp-spacer"></span>').appendTo($head);
		$('<button type="button" class="btn btn-default btn-sm"></button>')
			.text(__("Back to planner"))
			.on("click", () => this.go("week", anchor))
			.appendTo($head);

		if (!data) {
			$title.text(__("My week"));
			$('<div class="pp-route-note"></div>').text(__("Your week could not be loaded. Refresh to try again.")).appendTo($root);
			return;
		}
		const start = moment(data.start, "YYYY-MM-DD");
		const end = moment(data.end, "YYYY-MM-DD");
		$title.text(`${data.label || __("My week")} · ${start.format("MMM D")} – ${end.format("MMM D, YYYY")}`);
		if (!data.resource) {
			$('<div class="pp-route-note"></div>').text(data.message || __("There is no week to show.")).appendTo($root);
			return;
		}
		(data.days || []).forEach((entry) => {
			const items = entry.items || [];
			const $day = $('<div class="pp-mw-day"></div>').appendTo($root);
			const $dh = $('<div class="pp-mw-day-head"></div>').appendTo($day);
			if (entry.date === data.today) $dh.addClass("pp-mw-today");
			$("<span></span>").text(moment(entry.date, "YYYY-MM-DD").format("dddd, MMM D")).appendTo($dh);
			if (entry.off) $('<span class="pp-chip"></span>').text(__(entry.off)).appendTo($dh);
			$('<span class="pp-spacer"></span>').appendTo($dh);
			if (items.length) {
				$('<button type="button" class="btn btn-default btn-xs"></button>')
					.text(__("Route"))
					.attr("title", __("This day's stops in order, with drive times and a map"))
					.on("click", () => this.go_route(data.resource, entry.date))
					.appendTo($dh);
			}
			if (entry.travel) $('<div class="pp-mw-empty"></div>').text(entry.travel).appendTo($day);
			if (!items.length && !entry.travel) {
				$('<div class="pp-mw-empty"></div>').text(entry.off ? __("Off") : __("Nothing booked")).appendTo($day);
			}
			items.forEach((stop) => $day.append(this.my_week_item_html(stop)));
			this.p6d_my_week_day($day, $dh, entry, data);
		});
	},

	// One stop. Everything is escaped; the address links to Google Maps only when the server sent a
	// Google Maps URL, and opens without an opener.
	my_week_item_html(stop) {
		const when = stop.slot && stop.slot.length === 2 ? stop.slot.join("–") : stop.time || "";
		const place = stop.project_title && stop.project_title !== stop.label ? stop.project_title : "";
		const maps = stop.maps_url && /^https:\/\/www\.google\.com\/maps\//.test(stop.maps_url) ? stop.maps_url : "";
		const facts = [place, Number(stop.hours) > 0 ? `${pp_hours(stop.hours)}h` : ""].filter(Boolean).join(" · ");
		let address = "";
		if (stop.address && maps) {
			address = `<a class="pp-mw-addr" href="${pp_esc(maps)}" target="_blank" rel="noopener noreferrer">${pp_esc(
				stop.address
			)}</a>`;
		} else if (stop.address) {
			address = `<div class="pp-mw-addr">${pp_esc(stop.address)}</div>`;
		}
		const crew = (stop.crew || []).length ? `<div class="pp-mw-sub">${pp_esc(__("With {0}", [stop.crew.join(", ")]))}</div>` : "";
		const notes = stop.notes ? `<div class="pp-mw-notes">${pp_esc(stop.notes)}</div>` : "";
		return `
			<div class="pp-mw-item">
				${when ? `<div class="pp-mw-time">${pp_esc(when)}</div>` : ""}
				<div class="pp-mw-label">${pp_esc(stop.label || stop.ref)}</div>
				${facts ? `<div class="pp-mw-sub">${pp_esc(facts)}</div>` : ""}
				${address}${crew}${notes}
			</div>`;
	},

	// ------------------------------------------------------------------ crew sheet

	// The server renders the sheet; the browser prints it. The window opens on the click itself
	// (a window opened after the request returns is a pop-up the browser blocks) and fills in when
	// the sheet arrives.
	print_crew_sheet() {
		const win = window.open("", "_blank");
		if (!win) {
			frappe.msgprint(__("Allow pop-ups for this site to print the crew sheet."));
			return;
		}
		win.document.write(
			`<!doctype html><title>${pp_esc(__("Crew sheet"))}</title><p style="font-family:sans-serif">${pp_esc(
				__("Preparing the crew sheet…")
			)}</p>`
		);
		const start = this.view === "month" ? this.anchor : pp_ymd(this.range().start);
		Promise.resolve(frappe.call({ method: `${PP.api}.crew_sheet_html`, args: { start, group: this.group || "" } }))
			.then((r) => {
				const html = r && r.message;
				if (win.closed) return;
				if (!html) {
					win.close();
					return;
				}
				win.document.open();
				win.document.write(html);
				win.document.close();
				win.focus();
				const fonts = win.document.fonts && win.document.fonts.ready ? win.document.fonts.ready : Promise.resolve();
				// Let the sheet lay out and its display face arrive before the print dialog measures it.
				fonts.then(() => win.setTimeout(() => win.print(), 250));
			})
			.catch(() => {
				if (!win.closed) win.close();
			});
	},
};

Object.assign(ProjectPlanner.prototype, PP3B_METHODS);

// ====================================================================== Phase 4: tracking
//
// Planned against worked hours, the labor forecast of the project on screen, and the vehicles and
// assets a task uses. Everything here is read from the server's numbers; nothing is computed on
// this side that decides anything:
//   - a card that has clock-in hours says "14h of 12h" (red when the server marks it over_plan);
//     the dialog lists each person's hours (card.crew[].actual, refined by get_actuals); the
//     toolbar's "Running over" chip shows only the over_plan tasks
//   - with a project picked, a strip shows its labor forecast (get_labor_forecast): hours always;
//     cost only when the answer says can_see_cost, and the rate it is at ("base rate" until the
//     burdened rates are in) - a wage is never worked out here from hours
//   - card.equipment shows as chips, the dialog has an Equipment table (Fleet Vehicle / Asset) sent
//     to save_task as `equipment`, an equipment conflict arrives in needs_reason under the
//     "Equipment" key and is asked about like any other, and the crew view gains an Equipment row
//     group (get_equipment): each vehicle or asset, the tasks using it each day
//   - a "Utilization" button opens the Crew Utilization report
//
// Everything Phase 4 adds is in this block, mixed into ProjectPlanner below. The hooks into the
// class above are one line each: init_phase4 (constructor), p4_set_mode (set_mode), load_phase4
// (load), render_phase4 (render), p4_equipment_rows (render_crew), p4_card_chips / p4_tip_lines /
// p4_card_conflicts (the cards), p4_conflict_heading (the reason dialogs), p4_equipment_of
// (snapshot) and p4_dialog_rows / p4_equipment_fields / p4_refine_actuals / p4_equipment_arg
// (the card dialog).

const PP4 = {
	report: "Crew Utilization",
	equipment_key: "Equipment",
	equipment_chips: 2,
	// A vehicle in either state cannot go out, whatever is booked on it.
	unavailable: ["In Shop", "Retired"],
};

const PP4_STYLE = `
.pp-chip.pp-equip{background:rgba(37,99,235,.12);color:var(--text-color);}
.pp-toggle{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--border-color);border-radius:14px;background:var(--card-bg);padding:3px 11px;font-size:13px;color:var(--text-color);}
.pp-toggle.pp-on{background:rgba(220,38,38,.12);border-color:#dc2626;color:#b91c1c;font-weight:600;}
.pp-forecast{display:flex;flex-wrap:wrap;align-items:center;gap:4px 16px;border:1px solid var(--border-color);border-radius:10px;background:var(--card-bg);padding:8px 12px;margin-bottom:10px;font-size:13px;}
.pp-forecast-note{flex:1 1 100%;font-size:11px;color:var(--text-muted);}
.pp-forecast .btn{margin-left:auto;}
.pp-fc-table{width:100%;font-size:13px;border-collapse:collapse;}
.pp-fc-table th{color:var(--text-muted);font-weight:normal;text-align:right;padding:3px 0 3px 12px;white-space:nowrap;}
.pp-fc-table td{padding:4px 0 4px 12px;text-align:right;border-top:1px solid var(--border-color);white-space:nowrap;}
.pp-fc-table th:first-child,.pp-fc-table td:first-child{text-align:left;padding-left:0;white-space:normal;}
.pp-actual-row{display:flex;justify-content:space-between;gap:12px;font-size:12px;color:var(--text-muted);}
.pp-crew-group{grid-column:1 / -1;padding:5px 8px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);background:var(--control-bg);}
.pp-equip-name{display:flex;flex-direction:column;gap:1px;padding:6px 8px;font-size:12px;min-width:0;position:sticky;left:0;background:var(--card-bg);z-index:1;}
.pp-equip-name b{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-equip-sub{font-size:11px;color:var(--text-muted);}
.pp-ecell{border-left:1px solid var(--border-color);padding:4px;display:flex;flex-direction:column;gap:3px;min-width:0;min-height:40px;}
.pp-ecell.pp-unavailable{background:repeating-linear-gradient(135deg,transparent 0 6px,var(--control-bg) 6px 8px);}
.pp-ecard{border:1px solid var(--border-color);border-left:4px solid #64748b;border-radius:6px;background:var(--card-bg);padding:2px 5px;font-size:11px;line-height:1.3;cursor:pointer;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-ecard:hover,.pp-ecard:focus{box-shadow:0 1px 4px rgba(0,0,0,.18);outline:none;}
.pp-ecard.pp-clash{border-left-color:#dc2626;box-shadow:inset 0 0 0 1px rgba(220,38,38,.55);}
@media (max-width:760px){
.pp-forecast{padding:6px 10px;}
.pp-forecast .btn{margin-left:0;}
.pp-equip-name{padding:4px 6px;}
}
`;

const PP4_METHODS = {
	init_phase4() {
		if (!document.getElementById("pp-style-4")) {
			$("<style id='pp-style-4'>").text(PP4_STYLE).appendTo(document.head);
		}
		this.over_only = false;
		this.forecast = null;
		this.forecast_for = "";
		this.forecast_request = 0;
		this.equip = null;
		this.equip_request = 0;

		// The filter is for this visit to the page: a chip that quietly hides most of the board
		// the next morning would be a trap, so it is not remembered.
		this.$over = $('<button type="button" class="pp-toggle" aria-pressed="false"></button>')
			.attr("title", __("Show only the tasks whose clocked hours are past their plan"))
			.on("click", () => {
				this.over_only = !this.over_only;
				this.render();
			})
			.insertAfter(this.$foreign);
		this.$util = $('<button type="button" class="btn btn-default btn-sm pp-util"></button>')
			.text(__("Utilization"))
			.attr("title", __("The Crew Utilization report: capacity, booked and worked hours per person"))
			.on("click", () => frappe.set_route("query-report", PP4.report))
			.insertBefore(this.$undo);
		this.$forecast = $('<div class="pp-forecast"></div>').hide().insertBefore(this.$panel);
	},

	// The route, heatmap and My week views have none of this; the calendar views draw it.
	p4_set_mode(mode) {
		const calendar = mode === "";
		if (this.$over) this.$over.toggle(calendar);
		if (this.$forecast && !calendar) this.$forecast.hide();
	},

	// A reload refreshes the forecast in place (the strip stays up until the new figures arrive).
	load_phase4() {
		this.forecast_for = this.project;
		this.p4_fetch_forecast();
		this.p4_load_equipment();
	},

	render_phase4() {
		this.p4_sync_forecast();
		this.p4_render_forecast();
		this.p4_update_over();
	},

	// ------------------------------------------------------------------ running over

	p4_update_over() {
		if (!this.data || !this.$over) return;
		const saved = this.over_only;
		this.over_only = false;
		const count = []
			.concat(this.data.tasks || [], this.data.unscheduled || [])
			.filter((card) => card.over_plan && this.task_visible(card)).length;
		this.over_only = saved;
		this.$over
			.text(`${__("Running over")} (${count})`)
			.toggleClass("pp-on", !!saved)
			.attr("aria-pressed", saved ? "true" : "false");
	},

	// What a task was planned for: its estimate, else what the engine booked for its crew.
	p4_planned(card) {
		const estimate = Number(card.expected_time) || 0;
		if (estimate > 0) return estimate;
		return (card.crew || []).reduce((sum, member) => sum + (Number(member.booked) || 0), 0);
	},

	// "14h of 12h": clocked hours against the plan. The server decides whether that is too far over.
	p4_actual_text(card) {
		const actual = Number(card.actual_hours) || 0;
		if (!(actual > 0)) return "";
		const planned = this.p4_planned(card);
		return planned > 0
			? __("{0}h of {1}h", [pp_hours(actual), pp_hours(planned)])
			: __("{0}h worked", [pp_hours(actual)]);
	},

	p4_equipment_list(card) {
		return (Array.isArray(card.equipment) ? card.equipment : []).filter((item) => item && (item.name || item.label));
	},

	p4_equipment_label(item) {
		return item.label || item.name || "";
	},

	p4_card_chips(card) {
		const out = [];
		const text = this.p4_actual_text(card);
		if (text) {
			const tip = card.over_plan
				? __("Worked {0}h against {1}h planned: running over", [
						pp_hours(card.actual_hours),
						pp_hours(this.p4_planned(card)),
				  ])
				: __("Hours clocked so far against the plan");
			const tone = card.over_plan ? " pp-red" : "";
			out.push(`<span class="pp-chip${tone}" title="${pp_esc(tip)}">${pp_esc(text)}</span>`);
		}
		const equipment = this.p4_equipment_list(card);
		equipment.slice(0, PP4.equipment_chips).forEach((item) => {
			const tip = `${__(item.type || "Vehicle")}: ${this.p4_equipment_label(item)}`;
			out.push(`<span class="pp-chip pp-equip" title="${pp_esc(tip)}">${pp_esc(this.p4_equipment_label(item))}</span>`);
		});
		if (equipment.length > PP4.equipment_chips) {
			const rest = equipment.slice(PP4.equipment_chips).map((item) => this.p4_equipment_label(item));
			out.push(`<span class="pp-chip pp-equip" title="${pp_esc(rest.join(", "))}">+${pp_esc(rest.length)}</span>`);
		}
		return out;
	},

	p4_tip_lines(card) {
		const lines = [];
		const text = this.p4_actual_text(card);
		if (text) lines.push(`${__("Hours")}: ${text}${card.over_plan ? ` (${__("running over")})` : ""}`);
		const equipment = this.p4_equipment_list(card);
		if (equipment.length) {
			lines.push(__("Equipment: {0}", [equipment.map((item) => this.p4_equipment_label(item)).join(", ")]));
		}
		return lines;
	},

	// An equipment problem the server put on the card itself (a vehicle used twice, in the shop).
	p4_card_conflicts(card) {
		if (!Array.isArray(card.conflicts)) return [];
		return card.conflicts
			.map((entry) => (entry && typeof entry === "object" ? entry.text || entry.message || "" : entry))
			.filter((text) => typeof text === "string" && text);
	},

	// The reason dialogs list conflicts under a heading per person; equipment has its own.
	p4_conflict_heading(who) {
		return who === PP4.equipment_key ? __("Equipment") : who;
	},

	// ------------------------------------------------------------------ card dialog

	// The rows save_task takes, from a card: [{equipment_type, vehicle | asset}], or null when the
	// server did not send equipment (an older server), so nothing is ever sent for it.
	p4_equipment_of(card) {
		if (!Array.isArray(card.equipment)) return null;
		return this.p4_equipment_list(card).map((item) => this.p4_equipment_row(item.type, item.name));
	},

	p4_equipment_row(type, name) {
		return type === "Asset" ? { equipment_type: "Asset", asset: name } : { equipment_type: "Vehicle", vehicle: name };
	},

	p4_equipment_key(rows) {
		return (rows || [])
			.map((row) => `${row.equipment_type}:${row.asset || row.vehicle}`)
			.sort()
			.join("|");
	},

	// What the dialog's Equipment table holds, as save_task rows: one per vehicle or asset, blank
	// rows dropped. A row without a type is read from whichever link it filled in.
	p4_rows_from_dialog(rows) {
		const seen = new Set();
		const out = [];
		(rows || []).forEach((row) => {
			if (!row) return;
			const type = row.equipment_type || (row.asset && !row.vehicle ? "Asset" : "Vehicle");
			const name = type === "Asset" ? row.asset : row.vehicle;
			if (!name || seen.has(`${type}:${name}`)) return;
			seen.add(`${type}:${name}`);
			out.push(this.p4_equipment_row(type, name));
		});
		return out;
	},

	p4_equipment_arg(card, values, args) {
		const before = this.p4_equipment_of(card);
		if (!before || !Array.isArray(values.equipment)) return;
		const next = this.p4_rows_from_dialog(values.equipment);
		if (this.p4_equipment_key(next) !== this.p4_equipment_key(before)) args.equipment = JSON.stringify(next);
	},

	p4_equipment_fields(card) {
		const before = this.p4_equipment_of(card);
		if (!before) return [];
		return [
			{ fieldtype: "Section Break", label: __("Equipment") },
			{
				fieldtype: "Table",
				fieldname: "equipment",
				label: __("Vehicles and assets"),
				cannot_add_rows: false,
				in_place_edit: true,
				data: before.map((row) => Object.assign({ vehicle: "", asset: "" }, row)),
				fields: [
					{
						fieldtype: "Select",
						fieldname: "equipment_type",
						label: __("Type"),
						options: "Vehicle\nAsset",
						default: "Vehicle",
						in_list_view: 1,
						columns: 2,
					},
					{
						fieldtype: "Link",
						fieldname: "vehicle",
						label: __("Vehicle"),
						options: "Fleet Vehicle",
						depends_on: "eval:doc.equipment_type=='Vehicle'",
						in_list_view: 1,
						columns: 4,
						get_query: () => ({ filters: { status: "Active" } }),
					},
					{
						fieldtype: "Link",
						fieldname: "asset",
						label: __("Asset"),
						options: "Asset",
						depends_on: "eval:doc.equipment_type=='Asset'",
						in_list_view: 1,
						columns: 4,
						get_query: () => ({ filters: { docstatus: 1, status: ["not in", ["Scrapped", "Sold"]] } }),
					},
				],
			},
			{
				fieldtype: "HTML",
				fieldname: "equipment_note",
				options: `<p class="pp-equip-sub">${pp_esc(
					__("A vehicle or asset can be on one task at a time. A double booking asks for a reason, like a crew one.")
				)}</p>`,
			},
		];
	},

	// Each person's worked hours against their planned share, as lines. `people` are get_actuals'
	// by_person rows; without them the card's own crew figures are used.
	p4_people_html(card, people) {
		const rows = Array.isArray(people)
			? people.map((row) => ({
					label: row.label || row.resource,
					planned: Number(row.planned) || 0,
					actual: Number(row.actual) || 0,
			  }))
			: (card.crew || []).map((member) => ({
					label: this.resource_label(member.resource, member.label),
					planned: Number(member.booked) || 0,
					actual: Number(member.actual) || 0,
			  }));
		return rows
			.filter((row) => row.actual > 0 || row.planned > 0)
			.map((row) => {
				const text =
					row.planned > 0
						? __("{0}h of {1}h", [pp_hours(row.actual), pp_hours(row.planned)])
						: __("{0}h worked", [pp_hours(row.actual)]);
				return `<div class="pp-actual-row"><span>${pp_esc(row.label)}</span><span>${pp_esc(text)}</span></div>`;
			})
			.join("");
	},

	// Rows for the dialog's summary table: hours worked (with each person's) and equipment.
	p4_dialog_rows(card) {
		const out = [];
		const text = this.p4_actual_text(card);
		if (text) {
			const flag = card.over_plan ? ` <span class="pp-chip pp-red">${pp_esc(__("Running over"))}</span>` : "";
			out.push(
				`<tr><th>${pp_esc(__("Hours worked"))}</th><td>${pp_esc(text)}${flag}` +
					`<div class="pp-actuals-people">${this.p4_people_html(card, null)}</div></td></tr>`
			);
		}
		const equipment = this.p4_equipment_list(card);
		if (equipment.length) {
			out.push(
				`<tr><th>${pp_esc(__("Equipment"))}</th><td>${pp_esc(
					equipment.map((item) => this.p4_equipment_label(item)).join(", ")
				)}</td></tr>`
			);
		}
		return out;
	},

	// get_actuals knows each person's plan as well as their clock-ins; swap its lines in when they
	// arrive. The card's own figures are already showing, so a failure costs nothing.
	p4_refine_actuals(card, dialog) {
		if (!(Number(card.actual_hours) > 0)) return;
		Promise.resolve(frappe.call({ method: `${PP.api}.get_actuals`, args: { tasks: JSON.stringify([card.name]) } }))
			.then((r) => {
				const entry = this.p4_actuals_entry((r && r.message) || {}, card.name);
				if (!entry || !Array.isArray(entry.by_person)) return;
				dialog.$wrapper.find(".pp-actuals-people").html(this.p4_people_html(card, entry.by_person));
			})
			.catch(() => null);
	},

	// get_actuals answers per task; read it forgivingly (keyed by task, or under `tasks`, or a list).
	p4_actuals_entry(raw, name) {
		const table = raw.tasks || raw;
		if (Array.isArray(table)) return table.find((item) => item && (item.task === name || item.name === name)) || null;
		return (table && table[name]) || null;
	},

	// ------------------------------------------------------------------ labor forecast

	p4_money(value) {
		const number = Number(value);
		if (!Number.isFinite(number)) return "";
		return typeof format_currency === "function" ? format_currency(number) : number.toFixed(2);
	},

	// A different project in the toolbar: drop the old figures and fetch the new ones.
	p4_sync_forecast() {
		if (this.forecast_for === this.project) return;
		this.forecast_for = this.project;
		this.forecast = null;
		this.p4_fetch_forecast();
	},

	p4_fetch_forecast() {
		if (!this.project) {
			this.forecast = null;
			return;
		}
		const project = this.project;
		const token = ++this.forecast_request;
		Promise.resolve(frappe.call({ method: `${PP.api}.get_labor_forecast`, args: { project } }))
			.then((r) => {
				if (token !== this.forecast_request || project !== this.project) return;
				this.forecast = (r && r.message) || null;
				this.p4_render_forecast();
			})
			.catch(() => {
				if (token !== this.forecast_request) return;
				this.forecast = null;
				this.p4_render_forecast();
			});
	},

	p4_render_forecast() {
		const forecast = this.forecast;
		if (!this.$forecast) return;
		if (!forecast || !this.project || !PP.views.includes(this.view) || this.forecast_for !== this.project) {
			this.$forecast.hide().empty();
			return;
		}
		const project = this.by_project[this.project];
		const title = (project && (project.title || project.name)) || this.project;
		const parts = [`<span><b>${pp_esc(__("Labor forecast"))}</b> ${pp_esc(title)}</span>`];
		const hours = [];
		if (forecast.booked_hours != null) hours.push(__("{0}h booked ahead", [pp_hours(forecast.booked_hours)]));
		if (forecast.actual_hours != null) hours.push(__("{0}h worked so far", [pp_hours(forecast.actual_hours)]));
		if (hours.length) parts.push(`<span>${pp_esc(hours.join(" · "))}</span>`);
		let note = "";
		// Money only when the server says this person may see it, and only the figures it sent.
		if (forecast.can_see_cost === true) {
			if (forecast.forecast_cost != null) {
				parts.push(`<span><b>${pp_esc(this.p4_money(forecast.forecast_cost))}</b> ${pp_esc(__("forecast"))}</span>`);
			}
			const split = [];
			if (forecast.actual_cost != null) split.push(__("{0} worked", [this.p4_money(forecast.actual_cost)]));
			if (forecast.booked_cost != null) split.push(__("{0} booked ahead", [this.p4_money(forecast.booked_cost)]));
			if (split.length) parts.push(`<span>${pp_esc(split.join(" + "))}</span>`);
			const unrated = (forecast.by_person || []).filter((row) => row.rate == null).length;
			note = forecast.burdened ? __("At burdened rates.") : __("At base pay rates: payroll burden is not included yet.");
			if (unrated) note += ` ${__("{0} without a rate are counted in hours only.", [unrated])}`;
		}
		const $strip = this.$forecast.empty().show();
		$strip.html(parts.join(""));
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(__("By person"))
			.on("click", () => this.p4_forecast_dialog())
			.appendTo($strip);
		if (note) $('<div class="pp-forecast-note"></div>').text(note).appendTo($strip);
	},

	p4_forecast_dialog() {
		const forecast = this.forecast;
		if (!forecast) return;
		const money = forecast.can_see_cost === true;
		const rows = (forecast.by_person || [])
			.map((row) => {
				const cost = money
					? `<td>${pp_esc(row.rate == null ? __("no rate") : `${this.p4_money(row.rate)}/h`)}</td><td>${pp_esc(
							row.cost == null ? "" : this.p4_money(row.cost)
					  )}</td>`
					: "";
				return `<tr><td>${pp_esc(row.label)}</td><td>${pp_esc(pp_hours(row.booked))}h</td><td>${pp_esc(
					pp_hours(row.actual)
				)}h</td>${cost}</tr>`;
			})
			.join("");
		const head = `<tr><th>${pp_esc(__("Person"))}</th><th>${pp_esc(__("Booked ahead"))}</th><th>${pp_esc(__("Worked"))}</th>${
			money ? `<th>${pp_esc(__("Rate"))}</th><th>${pp_esc(__("Cost"))}</th>` : ""
		}</tr>`;
		const dialog = new frappe.ui.Dialog({
			title: __("Labor forecast by person"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "people",
					options: rows
						? `<table class="pp-fc-table">${head}${rows}</table>`
						: `<p>${pp_esc(__("Nobody is booked on this project yet."))}</p>`,
				},
			],
		});
		dialog.show();
	},

	// ------------------------------------------------------------------ equipment in the crew view

	p4_load_equipment() {
		if (this.view !== "crew") return;
		const { start, end } = this.range();
		const first = pp_ymd(start);
		const token = ++this.equip_request;
		Promise.resolve(frappe.call({ method: `${PP.api}.get_equipment`, args: { start: first, end: pp_ymd(end) } }))
			.then((r) => {
				if (token !== this.equip_request) return;
				this.equip = { start: first, items: this.p4_normalize_equipment((r && r.message) || {}) };
				if (this.view === "crew" && this.data) this.render();
			})
			.catch(() => {
				if (token === this.equip_request) this.equip = null;
			});
	},

	// get_equipment answers each vehicle or asset with, per day, the tasks using it. Read it
	// forgivingly: a list or an object keyed by name, days keyed by date, tasks as names or objects.
	p4_normalize_equipment(raw) {
		const list = Array.isArray(raw) ? raw : raw.equipment || raw.items || raw.rows || [];
		const entries = Array.isArray(list) ? list : Object.entries(list).map(([name, item]) => Object.assign({ name }, item));
		return entries
			.map((item) => {
				const name = item.name || item.vehicle || item.asset || "";
				const days = {};
				const source = item.days || {};
				const pairs = Array.isArray(source)
					? source.map((day) => [day && (day.date || day.day), day])
					: Object.entries(source);
				pairs.forEach(([ymd, value]) => {
					if (!ymd) return;
					const used = Array.isArray(value) ? value : (value && (value.in_use_by || value.tasks || value.used_by)) || [];
					days[ymd] = used
						.map((use) =>
							typeof use === "string"
								? { task: use, subject: "" }
								: { task: use.task || use.name || use.ref || "", subject: use.subject || use.label || "" }
						)
						.filter((use) => use.task);
				});
				return {
					type: item.type || item.equipment_type || "Vehicle",
					name,
					label: item.label || name,
					status: item.status || "",
					days,
				};
			})
			.filter((item) => item.name);
	},

	// The grid cells of the Equipment group: a header across the row, then each vehicle or asset
	// with the tasks using it on each day. Two tasks on one day, or any on a vehicle that is in
	// the shop, are outlined red. Empty when the data is not here for this week.
	p4_equipment_rows(days) {
		const equip = this.equip;
		if (!equip || equip.start !== days[0] || !equip.items.length) return "";
		const html = [`<div class="pp-crew-group">${pp_esc(__("Equipment"))}</div>`];
		equip.items.forEach((item) => {
			const down = PP4.unavailable.includes(item.status);
			const status = down ? ` · <span class="pp-chip pp-red">${pp_esc(__(item.status))}</span>` : "";
			html.push(
				`<div class="pp-equip-name"><b title="${pp_esc(item.label)}">${pp_esc(item.label)}</b>` +
					`<span class="pp-equip-sub">${pp_esc(__(item.type))}${status}</span></div>`
			);
			days.forEach((ymd) => {
				const used = item.days[ymd] || [];
				const clash = used.length > 1 || (down && used.length > 0);
				const cards = used
					.map((use) => {
						const known = this.by_task[use.task];
						const subject = use.subject || (known && known.subject) || use.task;
						const tip = [subject, item.label, clash ? __("Conflict") : ""].filter(Boolean).join("\n");
						return `<div class="pp-ecard${clash ? " pp-clash" : ""}" data-etask="${pp_esc(use.task)}" tabindex="0" title="${pp_esc(
							tip
						)}">${pp_esc(subject)}</div>`;
					})
					.join("");
				html.push(`<div class="pp-ecell${down ? " pp-unavailable" : ""}">${cards}</div>`);
			});
		});
		return html.join("");
	},
};

Object.assign(ProjectPlanner.prototype, PP4_METHODS);

// ====================================================================== Phase 5: extras
//
// Weather (P5.2): a task ticked "Outdoor work" carries `weather` from get_planner, the forecast
// days of its span that have rain, freezing or high wind ([] when clear, null when there is no
// forecast for it). The card shows that day's flags as a chip (a tray card, its first flagged day),
// the dialog lists them, the route view puts them on the stop, and suggest_dates ranks flagged days
// lower and shows why.
//
// Customer-facing visits (P5.4): the dialog's "Customer-facing visit" box marks a task whose
// customer is emailed the date once it is firm, when Project Planner Settings has customer date
// confirmations switched on (they ship off). "Preview customer email" renders exactly what would go
// out, and to whom, and sends nothing; it opens in a sandboxed frame so the email's own styles and
// links stay inside it.
//
// The two boxes are saved by set_task_flags, straight to the task and never drafted: they are not
// bookings, and the server leaves the task's `modified` alone so the save_task that may follow in
// the same dialog is not refused as out of date.
//
// Everything Phase 5 adds to the page is in this block, mixed into ProjectPlanner below, so the
// Phase 4 work on the same file merges cleanly. The hooks into the class above are one line each:
// init_phase5 (constructor), weather_chips and weather_lines (card_html), weather_lines,
// phase5_links, phase5_dialog_fields and phase5_action (open_card), save_phase5_flags
// (save_dialog), suggestion_weather_html (show_suggestions) and stop_weather_html (render_route).

const PP5 = {
	// Who may preview a customer email (the endpoint checks the same roles).
	preview_roles: ["System Manager", "Projects Manager"],
};

const PP5_STYLE = `
.pp-chip.pp-weather{background:rgba(14,116,144,.14);color:#0e7490;font-weight:600;}
.pp-p5-meta{font-size:13px;margin-bottom:4px;}
.pp-p5-notes{margin:6px 0 10px;padding-left:18px;font-size:13px;color:var(--text-muted);}
.pp-p5-frame{display:block;width:100%;min-height:460px;border:1px solid var(--border-color);border-radius:8px;background:#ffffff;}
@media (max-width:760px){
.pp-p5-frame{min-height:340px;}
}
`;

const PP5_METHODS = {
	init_phase5() {
		if (!document.getElementById("pp-style-5")) {
			$("<style id='pp-style-5'>").text(PP5_STYLE).appendTo(document.head);
		}
	},

	// ------------------------------------------------------------------ weather

	// One forecast day of a card: the day it is drawn on, or (in a tray) its first flagged day.
	weather_day(card, ymd) {
		const days = Array.isArray(card.weather) ? card.weather : [];
		if (!days.length) return null;
		if (ymd) return days.find((entry) => entry.date === ymd) || null;
		return days[0];
	},

	weather_chips(card, ymd) {
		const entry = this.weather_day(card, ymd);
		const flags = (entry && entry.flags) || [];
		if (!flags.length) return [];
		const text = ymd ? flags.join(" · ") : `${flags[0]} · ${pp_when(entry.date)}`;
		return [`<span class="pp-chip pp-weather" title="${pp_esc(flags.join(", "))}">${pp_esc(text)}</span>`];
	},

	// "Thu, Oct 15: Rain 70%, Wind 45 km/h" for every flagged day of the task.
	weather_lines(card) {
		return (Array.isArray(card.weather) ? card.weather : [])
			.filter((entry) => (entry.flags || []).length)
			.map((entry) => `${pp_when(entry.date)}: ${entry.flags.join(", ")}`);
	},

	suggestion_weather_html(item) {
		const flags = (item && item.weather) || [];
		return flags.length ? `<span class="pp-chip pp-weather">${pp_esc(flags.join(" · "))}</span>` : "";
	},

	stop_weather_html(stop) {
		const flags = (stop && stop.weather) || [];
		return flags.length
			? `<div class="pp-stop-leg"><span class="pp-chip pp-weather">${pp_esc(flags.join(" · "))}</span></div>`
			: "";
	},

	// ------------------------------------------------------------------ the card dialog

	phase5_dialog_fields(card) {
		return [
			{
				fieldtype: "Check",
				fieldname: "outdoor",
				label: __("Outdoor work"),
				default: card.outdoor ? 1 : 0,
				description: __("Flag days whose forecast has rain, freezing or high wind, and avoid them in suggestions."),
			},
			{
				fieldtype: "Check",
				fieldname: "customer_visit",
				label: __("Customer-facing visit"),
				default: card.customer_visit ? 1 : 0,
				description: __(
					"The customer is emailed the date once it is firm, when customer date confirmations are switched on."
				),
			},
		];
	},

	can_preview_customer() {
		return PP5.preview_roles.some((role) => frappe.user.has_role(role));
	},

	phase5_links(card) {
		return card.customer_visit && this.can_preview_customer()
			? [["customer_preview", __("Preview customer email")]]
			: [];
	},

	phase5_action(action, card) {
		if (action === "customer_preview") this.preview_customer_email("Task", card.name);
	},

	// Outdoor / Customer-facing go to the task on their own, before any save_task from the same
	// dialog. Resolves true when something was saved.
	save_phase5_flags(card, values) {
		values = values || {};
		const args = { task: card.name };
		const outdoor = values.outdoor ? 1 : 0;
		const customer_visit = values.customer_visit ? 1 : 0;
		if (outdoor !== (card.outdoor ? 1 : 0)) args.outdoor = outdoor;
		if (customer_visit !== (card.customer_visit ? 1 : 0)) args.customer_visit = customer_visit;
		if (Object.keys(args).length === 1) return Promise.resolve(false);
		return Promise.resolve(frappe.call({ method: `${PP.api}.set_task_flags`, args }))
			.then((r) => {
				const result = (r && r.message) || {};
				if (result.queued) {
					frappe.show_alert({ message: __("The customer will be emailed the date."), indicator: "blue" }, 6);
				}
				return true;
			})
			.catch(() => false);
	},

	// ------------------------------------------------------------------ customer email preview

	preview_customer_email(doctype, name) {
		return Promise.resolve(
			frappe.call({
				method: `${PP.api}.preview_customer_confirmation`,
				args: { doctype, name },
				freeze: true,
				freeze_message: __("Rendering the customer email…"),
			})
		)
			.then((r) => this.show_customer_preview((r && r.message) || {}))
			.catch(() => null);
	},

	// What would go out, to whom, and why it would not go out now. Nothing here sends.
	show_customer_preview(answer) {
		const status = answer.would_send ? __("This email would be sent.") : __("This email would not be sent now.");
		const to = answer.recipient ? __("To: {0}", [answer.recipient]) : __("To: nobody (no email address found)");
		const notes = (answer.notes || []).map((text) => `<li>${pp_esc(text)}</li>`).join("");
		const parts = [
			`<div class="pp-p5-meta"><b>${pp_esc(status)}</b></div>`,
			`<div class="pp-p5-meta">${pp_esc(to)}</div>`,
		];
		if (answer.subject) parts.push(`<div class="pp-p5-meta">${pp_esc(__("Subject: {0}", [answer.subject]))}</div>`);
		if (notes) parts.push(`<ul class="pp-p5-notes">${notes}</ul>`);
		if (answer.error) parts.push(`<div class="pp-route-note">${pp_esc(answer.error)}</div>`);
		parts.push(`<iframe class="pp-p5-frame" sandbox="" title="${pp_esc(__("Email preview"))}"></iframe>`);
		const dialog = new frappe.ui.Dialog({
			title: __("Customer email preview"),
			size: "large",
			fields: [{ fieldtype: "HTML", fieldname: "preview", options: parts.join("") }],
		});
		dialog.show();
		const $frame = dialog.$wrapper.find("iframe.pp-p5-frame");
		if (answer.html) $frame.attr("srcdoc", answer.html);
		else $frame.hide();
	},
};

Object.assign(ProjectPlanner.prototype, PP5_METHODS);

// ====================================================================== Phase 6A: quick looks and polish
//
// Nik, 2026-10-09: "click the Technicians name or something and see what their specific schedule is
// in a pop up or something so as not to lose context overall." Everything here opens in the planner
// kit's side drawer (public/js/planner_kit, loaded with frappe.require below), which leaves the
// calendar visible and usable behind it (cards still move by drag); Esc and Back close it without
// leaving the planner.
//
//   - click a person's name (Resources available, a crew-view row, a crew badge on a card, the route
//     view's title)                          their week: every booking, free hours, days off,
//                                            conflicts, the chosen day's stops in driving order,
//                                            call/text/email (planner_views.get_person_schedule)
//   - click a date (week and month day numbers, Resources available's day heads, crew-view column
//     heads)                                 everyone's day side by side (get_day_overview)
//   - click a project's name on a card, "At a glance" beside the project filter, or "Project at a
//     glance" in a task's panel             the project: its open tasks on a timeline, planned vs
//                                            worked, the labor forecast (get_project_overview)
//   - click a card                          its editor opens in the side panel (planner_kit.panel
//                                            hosts the same FieldGroup the dialog had; save_dialog,
//                                            the reason prompt and every field are unchanged)
//   - drag a task out of a person, day or project drawer onto a day or a person's row: the same
//     drag_source / plan_drop / drop as a card on the board, so it asks for a reason and can be undone
//   - every change that can be undone shows a toast with Undo; hovering a name lights up that
//     person's bookings (pk-glow), a project's name its cards; the crew view's and the week view's
//     headers and the people column stay put while scrolling; "?" opens the legend
//
// Everything Phase 6A adds to the page is in this block, mixed into ProjectPlanner below, so the work
// of other phases on this file merges cleanly. The hooks into the class above are one line each:
// init_phase6a (constructor), render_phase6a (render), p6a_route (go, go_route and go_my_week),
// p6a_drag_source (drag_source), p6a_edge_scroll (edge_scroll), p6a_undo_toast (push_undo, whose
// result send() reads so the green alert is not shown twice), p6a_close_toast (undo), and p6a_dialog,
// p6a_links and p6a_action (open_card).

const PP6A = {
	kit: "planner_kit.bundle.js",
	views: "erpnext_enhancements.api.planner_views",
	owner: "pp",
	// Drawer widths on a wide screen; a phone gets the whole screen.
	widths: { person: 460, day: 760, project: 620, panel: 620 },
	hints: [
		["pp-person-peek", "Click a person's name to see their week without leaving the calendar."],
		["pp-day-peek", "Click a date to see everyone's day side by side."],
		["pp-glow", "Hover over a name to light up all of that person's bookings."],
	],
	route_icon:
		'<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" width="14" height="14">' +
		'<circle cx="3.5" cy="12.5" r="1.8"/><circle cx="12.5" cy="3.5" r="1.8"/>' +
		'<path d="M5.3 12.5H10a2.5 2.5 0 0 0 0-5H6a2.5 2.5 0 0 1 0-4h4.7"/></svg>',
};

const PP6A_STYLE = `
.pp-p6a-hints{display:flex;flex-direction:column;}
.pp-p6a-help{font-weight:700;min-width:30px;}
.pp-p6a-name{cursor:pointer;}
.pp-p6a-name:hover .pp-person-name,.pp-route-title.pp-p6a-name:hover{text-decoration:underline;}
.pp-p6a-proj{cursor:pointer;border-radius:4px;}
.pp-p6a-proj:hover{text-decoration:underline;color:var(--text-color);}
.pp-badge[data-pk-person]{cursor:pointer;}
.pp-day-num[data-pp6a-day],.pp-crew-head[data-pp6a-day],.pp-res-head[data-pp6a-day]{cursor:pointer;}
.pp-res-head[data-pp6a-day]:hover,.pp-crew-head[data-pp6a-day]:hover{color:var(--primary,#2490ef);}
.pp-route-title{cursor:pointer;}
.pp-p6a-sample{display:inline-block;font-size:11px;padding:1px 6px;border-radius:6px;border:1px solid var(--border-color);}
.pp-p6a-sample-card{display:inline-block;width:100px;font-size:11px;padding:1px 6px;border:1px solid var(--border-color);border-left:4px solid #2563eb;border-radius:6px;background:var(--card-bg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-p6a-sample-card.pp-overdue{border-left-color:#dc2626;}
.pp-p6a-sample-card.pp-drafted{outline:2px dashed var(--primary,#2490ef);outline-offset:-2px;}
.pp-p6a-sample-card.pp-tentative{border-top-style:dashed;border-right-style:dashed;border-bottom-style:dashed;background-image:repeating-linear-gradient(135deg,transparent 0 6px,rgba(100,116,139,.16) 6px 8px);}
.pp-p6a-bar{display:inline-block;width:90px;}
.pp-p6a-bar .pp-bar{display:block;}
.pp-p6a-row{cursor:pointer;}
.pp-p6a-row:hover{background:var(--control-bg);}
.pp-p6a-initials{display:inline-flex;gap:2px;margin-left:4px;vertical-align:middle;}
.pp-p6a-initials .pp-badge{width:16px;height:16px;font-size:8px;margin-left:0;}
.pp-p6a-forecast{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:13px;padding:6px 10px;border:1px solid var(--border-color);border-radius:8px;margin-bottom:6px;}
.pp-p6a-forecast-note{flex:1 1 100%;font-size:11px;color:var(--text-muted);}
@media (min-width:761px){
.pp-crew-view .pp-scroll{max-height:calc(100vh - 150px);overflow:auto;}
.pp-crew-view .pp-crew-head,.pp-crew-view .pp-crew-corner{position:sticky;top:0;z-index:3;background:var(--card-bg);}
.pp-crew-view .pp-crew-corner{left:0;z-index:4;}
.pp-week .pp-grid{max-height:calc(100vh - 150px);overflow:auto;}
.pp-week .pp-day-head{position:sticky;top:0;z-index:2;background:var(--card-bg);padding-top:2px;padding-bottom:2px;}
.pp-week .pp-day.pp-out .pp-day-head{background:var(--control-bg);}
.pp-res .pp-scroll{max-height:46vh;overflow:auto;}
.pp-res .pp-res-head{position:sticky;top:0;z-index:2;background:var(--card-bg);}
.pp-res .pp-res-grid > .pp-res-head:first-child{left:0;z-index:3;}
}
`;

const PP6A_METHODS = {
	init_phase6a() {
		if (!document.getElementById("pp-style-6a")) {
			$("<style id='pp-style-6a'>").text(PP6A_STYLE).appendTo(document.head);
		}
		this.p6a = { kit: null, peek: null, panel: null, panel_card: null, seen: null, glow: [] };
		this.$p6a_hints = $('<div class="pp-p6a-hints"></div>').insertAfter(this.$toolbar);
		this.$p6a_glance = $('<button type="button" class="btn btn-default btn-sm pp-p6a-glance"></button>')
			.text(__("At a glance"))
			.attr("title", __("The project picked here, at a glance: its tasks, hours and forecast"))
			.hide()
			.on("click", () => {
				if (this.project) this.p6a_open_project(this.project);
			})
			.insertAfter(this.$project);
		this.$p6a_help = $('<button type="button" class="btn btn-default btn-sm pp-p6a-help">?</button>')
			.attr("title", __("How to read the planner"))
			.attr("aria-label", __("How to read the planner"))
			.on("click", () => this.p6a_legend())
			.insertAfter(this.$undo);
		const root = this.$body[0];
		// Capture phase: a name, a date or a project inside a card is a quick look, not the card's
		// own click (which opens the task) and not the day number's old "show this week".
		root.addEventListener("click", (e) => this.p6a_click(e), true);
		root.addEventListener(
			"keydown",
			(e) => {
				if (e.key !== "Enter" && e.key !== " ") return;
				const el = e.target && e.target.closest ? e.target.closest(".pp-p6a-name, [data-pp6a-day]") : null;
				if (!el || e.target !== el) return;
				e.preventDefault();
				e.stopPropagation();
				el.click();
			},
			true
		);
		frappe.require(PP6A.kit, () => this.p6a_ready());
	},

	p6a_ready() {
		const kit = window.planner_kit;
		if (!kit || (this.p6a && this.p6a.kit)) return;
		this.p6a.kit = kit;
		// A booking dragged out of a drawer this page opened starts the page's own drag.
		kit.drawer.element().addEventListener("pointerdown", (e) => {
			const current = kit.drawer.current();
			if (current && current.owner === PP6A.owner) this.on_down(e);
		});
		this.p6a.glow = [kit.hover_glow(this.$body[0], "person"), kit.hover_glow(this.$body[0], "project")];
		this.p6a_show_hints();
		this.p6c_ready();
	},

	// ------------------------------------------------------------------ hooks

	// A route change of the planner's own: with `keep` (another week or view) an open drawer stays
	// open over it; otherwise it closes. Either way Back does not land on a drawer that has gone.
	p6a_route(fn, keep) {
		const kit = this.p6a && this.p6a.kit;
		return kit ? kit.drawer.route(fn, keep) : fn();
	},

	render_phase6a() {
		if (!this.p6a) return;
		this.p6a_decorate();
		if (this.$p6a_glance) this.$p6a_glance.toggle(!!this.project && PP.views.includes(this.view));
		this.p6a_refresh_open();
	},

	// The undo toast. True when it was shown, so send() leaves out the green alert it replaces.
	p6a_undo_toast(snapshot, message) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !message) return false;
		kit.toast(message, {
			action_label: __("Undo"),
			tone: "success",
			on_action: () => {
				// Only the change this toast announced: the Undo button may have been used since.
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

	// The card editor in the side panel, or the old dialog until the kit has loaded.
	p6a_dialog(opts, card) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit) return new frappe.ui.Dialog(opts);
		const panel = kit.panel(
			Object.assign({}, opts, {
				subtitle: [card.project_title || card.project, card.name].filter(Boolean).join(" · "),
				width: PP6A.widths.panel,
				key: `task:${card.name}`,
				owner: PP6A.owner,
				push: this.$body[0],
				reopen: () => {
					const fresh = this.by_task[card.name];
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
		return this.p6a && this.p6a.kit && card.project ? [["p6a_project", __("Project at a glance")]] : [];
	},

	p6a_action(action, card) {
		if (action !== "p6a_project") return false;
		if (card.project) this.p6a_open_project(card.project);
		return true;
	},

	// A drag that starts in a drawer: a task the page owns and may move, whether or not it is on the
	// board (a later week in a person's drawer; the server sent its full card for exactly this).
	p6a_drag_source(el) {
		const kit = this.p6a && this.p6a.kit;
		const current = kit && kit.drawer.current();
		if (!current || current.owner !== PP6A.owner) return null;
		const item = el.closest("[data-pk-drag]");
		if (!item || el.closest("button, a, input, select, textarea")) return null;
		if (item.getAttribute("data-pk-kind") !== "task") return null;
		const card = this.p6a_card(item.getAttribute("data-pk-ref"));
		if (!card || !card.movable || card.saving) return null;
		return {
			kind: "card",
			el: item,
			card,
			from_date: item.getAttribute("data-pk-date") || card.start || null,
			from_resource: item.getAttribute("data-pk-resource") || null,
		};
	},

	// The crew view, the week grid and the people panel scroll inside themselves on a wide screen
	// (so their headers can stay put): near their top or bottom edge, a drag scrolls them too.
	p6a_edge_scroll(x, y) {
		const el = document.elementFromPoint(x, y);
		const box = el && el.closest ? el.closest(".pp-crew-view .pp-scroll, .pp-week .pp-grid, .pp-res .pp-scroll") : null;
		if (!box || box.scrollHeight <= box.clientHeight) return;
		const rect = box.getBoundingClientRect();
		if (y < rect.top + 40) box.scrollTop -= 14;
		else if (y > rect.bottom - 40) box.scrollTop += 14;
	},

	// ------------------------------------------------------------------ markup the quick looks need

	// Drawn after every render, as Phase 3B's draft outline is: names and badges get the person's
	// key, cards the keys of their people and project (hover glow), and the date heads a day key.
	p6a_decorate() {
		if (!this.data) return;
		const root = this.$body[0];
		const key = (value) => encodeURIComponent(String(value == null ? "" : value));
		root.querySelectorAll(".pp-person[data-resource]").forEach((el) => {
			el.setAttribute("data-pk-person", key(el.getAttribute("data-resource")));
			el.classList.add("pp-p6a-name");
			el.setAttribute("role", "button");
			el.setAttribute("tabindex", "0");
			if (!el.getAttribute("data-pp6a-tip")) {
				el.setAttribute("data-pp6a-tip", "1");
				el.setAttribute("title", `${el.getAttribute("title") || ""}\n${__("Click to see their week.")}`.trim());
			}
		});
		root.querySelectorAll(".pp-card[data-task]").forEach((el) => {
			const card = this.by_task[el.getAttribute("data-task")];
			if (!card) return;
			const row = el.getAttribute("data-resource");
			const crew = (card.crew || []).map((member) => member.resource).filter(Boolean);
			el.setAttribute("data-pk-persons", (row ? [row] : crew).map(key).join(" "));
			if (card.project) el.setAttribute("data-pk-projects", key(card.project));
			el.querySelectorAll(".pp-badges .pp-badge:not(.pp-more)").forEach((badge, index) => {
				const member = (card.crew || [])[index];
				if (member && member.resource) badge.setAttribute("data-pk-person", key(member.resource));
			});
			// The project's name in the sub line becomes its own target (the crew view's chips have
			// no project in theirs).
			if (!row && card.project) {
				const sub = el.querySelector(".pp-card-sub");
				if (sub && !sub.querySelector("[data-pk-project]")) {
					const rest = this.hours_text(card);
					sub.innerHTML =
						`<span class="pp-p6a-proj" data-pk-project="${pp_esc(key(card.project))}" title="${pp_esc(
							__("See this project at a glance")
						)}">${pp_esc(card.project_title || card.project)}</span>` + (rest ? ` · ${pp_esc(rest)}` : "");
				}
			}
		});
		root.querySelectorAll(".pp-fcard").forEach((el) => {
			const item = this.by_foreign[el.getAttribute("data-fkey")];
			const cell = el.closest(".pp-cell[data-resource]");
			const who = (item && item.resource) || (cell && cell.getAttribute("data-resource"));
			if (who) el.setAttribute("data-pk-persons", key(who));
		});
		root.querySelectorAll("[data-week]").forEach((el) => {
			el.setAttribute("data-pp6a-day", el.getAttribute("data-week"));
			el.setAttribute("title", __("See everyone's day"));
			el.setAttribute("tabindex", "0");
		});
		const heads = root.querySelectorAll(".pp-res-grid > .pp-res-head");
		const days = this.range_days();
		heads.forEach((el, index) => {
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
		if (!target || !target.closest || target.closest("button, a, input, select, textarea, .pp-route")) return;
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
		const project = target.closest("[data-pk-project]");
		if (project) {
			take();
			this.p6a_open_project(decodeURIComponent(project.getAttribute("data-pk-project")));
			return;
		}
		const day = target.closest("[data-pp6a-day]");
		if (day) {
			take();
			this.p6a_open_day(day.getAttribute("data-pp6a-day"));
			return;
		}
		if (target.closest(".pp-route-title") && this.route_resource) {
			take();
			this.p6a_open_person(this.route_resource, this.route_date);
		}
	},

	// ------------------------------------------------------------------ the quick looks

	// The first day of the week the person drawer opens on: the week on screen (the site's first
	// weekday), the route view's day's week, or this week when the month on screen holds today.
	p6a_week_start() {
		const today = this.today();
		const { start, end } = this.range();
		let base = this.anchor;
		if (this.view === PP.route_view && this.route_date) base = this.route_date;
		else if (this.view === "month" && today >= pp_ymd(start) && today <= pp_ymd(end)) base = today;
		return this.week_start_of(base || today);
	},

	p6a_card(name) {
		if (!name) return null;
		if (this.by_task[name]) return this.by_task[name];
		const peek = this.p6a && this.p6a.peek;
		const data = peek && typeof peek.data === "function" ? peek.data() : null;
		if (!data) return null;
		if (data.cards && data.cards[name]) return data.cards[name];
		return [].concat(data.tasks || [], data.undated || []).find((card) => card && card.name === name) || null;
	},

	// The page owns project tasks: only those it may move can leave a drawer by drag.
	p6a_can_drag(booking, data) {
		if (!this.data || !this.data.can_edit || booking.kind !== "task") return false;
		const card = this.by_task[booking.ref] || (data && data.cards && data.cards[booking.ref]);
		return !!(card && card.movable);
	},

	p6a_open_person(resource, selected) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !resource) return;
		this.p6a.seen = this.data;
		this.p6a.peek = kit.peeks.person({
			resource,
			label: this.resource_label(resource),
			start: this.p6a_week_start(),
			selected: selected || null,
			days: 7,
			owner: PP6A.owner,
			push: this.$body[0],
			width: PP6A.widths.person,
			can_drag: (booking, day, person, data) => this.p6a_can_drag(booking, data),
			on_full_route: (who, ymd) => kit.drawer.navigate(() => this.go_route(who, ymd)),
			actions: this.p6d_person_actions(resource),
		});
	},

	p6a_open_day(ymd) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !ymd) return;
		this.p6a.seen = this.data;
		this.p6a.peek = kit.peeks.day({
			date: ymd,
			group: this.group || "",
			owner: PP6A.owner,
			push: this.$body[0],
			width: PP6A.widths.day,
			person_key: (person) => person.resource,
			can_drag: (booking, day, person, data) => this.p6a_can_drag(booking, data),
			on_person: (person) => this.p6a_open_person(person.resource, ymd),
			on_week: (date) => kit.drawer.navigate(() => this.go("week", date)),
			...this.p6d_day_peek_opts(),
		});
	},

	p6a_open_project(project) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !project) return;
		const known = this.by_project[project];
		const state = { data: null, token: 0 };
		this.p6a.seen = this.data;
		const actions = [
			{
				label: __("Open project"),
				on_click: () => kit.drawer.navigate(() => frappe.set_route("Form", "Project", project)),
			},
		];
		if (known && PP.views.includes(this.view)) {
			actions.unshift({ label: __("Show only this project"), on_click: () => this.p6a_filter_project(project) });
		}
		const handle = kit.drawer.open({
			title: (known && known.title) || project,
			subtitle: project,
			body: `<p class="pk-empty">${pp_esc(__("Loading…"))}</p>`,
			width: PP6A.widths.project,
			key: `project:${project}`,
			owner: PP6A.owner,
			push: this.$body[0],
			actions,
			reopen: () => this.p6a_open_project(project),
			on_click: (e) => {
				const row = e.target && e.target.closest ? e.target.closest("[data-pp6a-task]") : null;
				if (!row || Date.now() < this.click_blocked_until) return;
				const card = this.p6a_card(row.getAttribute("data-pp6a-task"));
				if (card) this.open_card(card);
			},
		});
		const load = () => {
			const token = ++state.token;
			if (state.data) handle.body.classList.add("pk-busy");
			return Promise.resolve(frappe.call({ method: `${PP6A.views}.get_project_overview`, args: { project } }))
				.then((r) => {
					if (token !== state.token || !handle.is_open()) return;
					handle.body.classList.remove("pk-busy");
					state.data = (r && r.message) || null;
					const data = state.data || {};
					handle.set_title(data.title || (known && known.title) || project);
					handle.set_subtitle([project, data.customer_name].filter(Boolean).join(" · "));
					handle.set_body(this.p6a_project_html(state.data));
				})
				.catch(() => {
					if (token !== state.token || !handle.is_open()) return;
					handle.body.classList.remove("pk-busy");
					handle.set_body(this.p6a_project_html(null));
				});
		};
		this.p6a.peek = { data: () => state.data, refresh: load, is_open: () => handle.is_open() };
		load();
		this.p6c_project_drawer(project, handle);
	},

	p6a_filter_project(project) {
		this.project = project;
		this.save_pref(PP.prefs.project, project);
		this.show_all_unscheduled = false;
		if (this.$project) this.$project.val(project);
		this.render();
	},

	// After every load, a drawer that shows what changed is refreshed: the task in the side panel is
	// reopened from its new card (so its `modified` lock and its fields are current), and a person,
	// day or project drawer asks for its numbers again.
	p6a_refresh_open() {
		const p6a = this.p6a;
		if (!p6a.kit || !this.data) return;
		if (p6a.panel && p6a.panel.is_open() && p6a.panel_card) {
			const fresh = this.by_task[p6a.panel_card.name];
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
			card.start,
			card.end,
			card.expected_time,
			card.tentative,
			card.drafted,
			(card.crew || []).map((member) => [member.resource, member.hours, member.is_lead]),
		]);
	},

	// ------------------------------------------------------------------ the project drawer

	p6a_initials(resource, label) {
		const parts = String(this.resource_label(resource, label) || "?").split(/\s+/).filter(Boolean);
		return ((parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
	},

	p6a_hours_line(card) {
		const actual = Number(card.actual_hours) || 0;
		const planned = Number(card.planned_hours) || 0;
		if (actual > 0) {
			return planned > 0
				? __("{0}h of {1}h", [pp_hours(actual), pp_hours(planned)])
				: __("{0}h worked", [pp_hours(actual)]);
		}
		return planned > 0 ? __("{0}h planned", [pp_hours(planned)]) : "";
	},

	p6a_task_row(card, data, track) {
		const grab = !!(card.movable && data.can_edit && this.data && this.data.can_edit);
		const crew = (card.crew || [])
			.slice(0, 3)
			.map(
				(member) =>
					`<span class="pp-badge" style="background:${pp_esc(this.resource_color(member.resource))}" title="${pp_esc(
						this.resource_label(member.resource, member.label)
					)}">${pp_esc(this.p6a_initials(member.resource, member.label))}</span>`
			)
			.join("");
		const when = card.start
			? card.end && card.end !== card.start
				? `${pp_when(card.start)} – ${pp_when(card.end)}`
				: pp_when(card.start)
			: __("No dates");
		const sub = [when, this.p6a_hours_line(card)].filter(Boolean).join(" · ");
		const chips = [];
		if (card.over_plan) chips.push(`<span class="pp-chip pp-red">${pp_esc(__("Running over"))}</span>`);
		if (card.tentative) chips.push(`<span class="pp-chip pp-pencil">${pp_esc(__("Pencil"))}</span>`);
		if (card.overdue) chips.push(`<span class="pp-chip pp-red">${pp_esc(__("Overdue"))}</span>`);
		if (card.rental_kind) chips.push(`<span class="pp-chip">${pp_esc(__(card.rental_kind))}</span>`);
		const drag = grab
			? ` data-pk-drag="1" data-pk-kind="task" data-pk-ref="${pp_esc(card.name)}" data-pk-key="${pp_esc(
					card.name
			  )}" data-pk-date="${pp_esc(card.start || "")}" data-pk-resource=""`
			: "";
		const tip = [card.subject, sub, grab ? __("Drag onto the calendar to move it; click to edit it.") : ""]
			.filter(Boolean)
			.join("\n");
		return `
			<div class="pk-tl-row pp-p6a-row${grab ? " pk-grab" : ""}" data-pp6a-task="${pp_esc(card.name)}"${drag}
				title="${pp_esc(tip)}">
				<div class="pk-tl-label"><b>${pp_esc(card.subject || card.name)}<span class="pp-p6a-initials">${crew}</span></b>
					<span>${pp_esc(sub)}</span><div>${chips.join("")}</div></div>
				${track}
			</div>`;
	},

	p6a_track(card, data) {
		const bar = card.bar || {};
		if (bar.outside) {
			return `<div class="pk-tl-off">${pp_esc(bar.outside === "before" ? __("← earlier") : __("later →"))}</div>`;
		}
		const classes = ["pk-tl-bar"];
		if (card.tentative) classes.push("pk-tl-pencil");
		else if (card.over_plan) classes.push("pk-tl-over");
		if (card.overdue) classes.push("pk-tl-late");
		if (bar.clipped_start) classes.push("pk-tl-clip-start");
		if (bar.clipped_end) classes.push("pk-tl-clip-end");
		const left = Math.max(0, Math.min(100, Number(bar.left_pct) || 0));
		const width = Math.max(0, Math.min(100 - left, Number(bar.width_pct) || 0));
		const color = card.tentative || card.over_plan ? "" : `background:${pp_esc(pp_color(card.color, PP.default_color))};`;
		return `<div class="pk-tl-track">${this.p6a_today_mark(data)}<span class="${classes.join(" ")}" style="left:${left}%;width:${width}%;${color}"></span></div>`;
	},

	p6a_today_mark(data) {
		const window_ = data.window;
		if (!window_ || !data.today || data.today < window_.start || data.today > window_.end) return "";
		const total = moment(window_.end, "YYYY-MM-DD").diff(moment(window_.start, "YYYY-MM-DD"), "days") + 1;
		const at = (moment(data.today, "YYYY-MM-DD").diff(moment(window_.start, "YYYY-MM-DD"), "days") + 0.5) / total;
		return `<span class="pk-tl-today" style="left:${Math.round(at * 10000) / 100}%" title="${pp_esc(__("Today"))}"></span>`;
	},

	p6a_forecast_html(forecast) {
		if (!forecast) return "";
		const parts = [];
		if (forecast.booked_hours != null) parts.push(__("{0}h booked ahead", [pp_hours(forecast.booked_hours)]));
		if (forecast.actual_hours != null) parts.push(__("{0}h worked so far", [pp_hours(forecast.actual_hours)]));
		let note = "";
		// Money only when the server says this person may see it, and only the figures it sent.
		if (forecast.can_see_cost === true) {
			if (forecast.forecast_cost != null) parts.push(__("{0} forecast", [this.p4_money(forecast.forecast_cost)]));
			note = forecast.burdened ? __("At burdened rates.") : __("At base pay rates: payroll burden is not included yet.");
		}
		if (!parts.length) return "";
		return (
			`<div class="pk-section-title">${pp_esc(__("Labor forecast"))}</div>` +
			`<div class="pp-p6a-forecast">${parts.map((text) => `<span>${pp_esc(text)}</span>`).join("")}${
				note ? `<span class="pp-p6a-forecast-note">${pp_esc(note)}</span>` : ""
			}</div>`
		);
	},

	p6a_project_html(data) {
		if (!data) return `<p class="pk-empty">${pp_esc(__("The project could not be loaded. Try again."))}</p>`;
		if (!data.on_planner) {
			return `<div class="pk-note pk-note-warn">${pp_esc(data.message || __("This project is not on the planner."))}</div>`;
		}
		const facts = [];
		const fact = (label, value) => {
			if (value) facts.push(`<tr><th>${pp_esc(label)}</th><td>${pp_esc(value)}</td></tr>`);
		};
		fact(__("Project"), data.project);
		fact(__("Account"), data.customer_name);
		fact(__("Project manager"), data.pm);
		fact(__("Status"), data.status ? __(data.status) : "");
		fact(__("Type"), data.project_type ? __(data.project_type) : "");
		if (data.expected_start || data.expected_end) {
			fact(__("Expected"), [data.expected_start, data.expected_end].filter(Boolean).map((ymd) => pp_when(ymd)).join(" – "));
		}
		const tasks = data.tasks || [];
		const undated = data.undated || [];
		const parts = [`<table class="pk-facts">${facts.join("")}</table>`, this.p6a_forecast_html(data.forecast)];
		parts.push(`<div class="pk-section-title">${pp_esc(__("Scheduled tasks ({0})", [tasks.length]))}</div>`);
		if (tasks.length && data.window) {
			parts.push(
				`<div class="pk-tl"><div class="pk-tl-scale"><span>${pp_esc(pp_when(data.window.start))}</span><span>${pp_esc(
					pp_when(data.window.end)
				)}</span></div>${tasks.map((card) => this.p6a_task_row(card, data, this.p6a_track(card, data))).join("")}</div>`
			);
		} else {
			parts.push(`<p class="pk-empty">${pp_esc(__("No open task has dates yet."))}</p>`);
		}
		if (undated.length) {
			parts.push(`<div class="pk-section-title">${pp_esc(__("Not scheduled yet ({0})", [undated.length]))}</div>`);
			parts.push(`<div class="pk-tl">${undated.map((card) => this.p6a_task_row(card, data, "")).join("")}</div>`);
		}
		if (data.truncated) {
			parts.push(`<p class="pk-note">${pp_esc(__("Showing the first {0} open tasks.", [data.limit]))}</p>`);
		}
		if (data.can_edit && (tasks.length || undated.length)) {
			parts.push(`<p class="pk-note">${pp_esc(__("Drag a task onto a day or a person's row to move it. Click it to edit it."))}</p>`);
		}
		return `<div class="pp-p6a-project">${parts.join("")}</div>`;
	},

	// ------------------------------------------------------------------ legend and hints

	p6a_show_hints() {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !this.$p6a_hints) return;
		let shown = 0;
		PP6A.hints.forEach(([key, text]) => {
			if (shown >= 2 || kit.hint.seen(key)) return;
			if (kit.hint(key, __(text), { container: this.$p6a_hints[0] })) shown += 1;
		});
	},

	p6a_legend() {
		const kit = this.p6a && this.p6a.kit;
		if (!kit) return;
		const chip = (cls, text) => `<span class="pp-chip${cls ? ` ${cls}` : ""}">${pp_esc(__(text))}</span>`;
		const card = (cls, text, style) =>
			`<span class="pp-p6a-sample-card${cls ? ` ${cls}` : ""}"${style ? ` style="${style}"` : ""}>${pp_esc(__(text))}</span>`;
		const bar = (cls, width, soft) =>
			`<span class="pp-p6a-bar ${cls}"><span class="pp-bar"><i style="width:${width}%"></i>${
				soft ? `<b style="left:${width}%;width:${soft}%"></b>` : ""
			}</span></span>`;
		const item = (sample_html, text) => ({ sample_html, text: __(text) });
		kit.legend(
			[
				...this.p6c_legend_sections(),
				{
					title: __("Quick looks"),
					items: [
						item("", "Click a person's name (in the panel, a crew row or a crew badge) to see their week: bookings, free hours, days off and the day's stops in driving order."),
						item("", "Click a date to see everyone's day side by side."),
						item("", "Click a project's name on a card to see the whole project on a timeline, with hours and the labor forecast."),
						item("", "Drag a task out of any of these onto a day or a person's row to move it. Esc or Back closes the side panel."),
						item("", "Hover over a name to light up all of that person's bookings, or over a project's name to light up its cards."),
					],
				},
				{
					title: __("Task cards"),
					items: [
						item(card("", "Dig"), "A project task. The left edge is the task's color. Drag it to another day to move it; it keeps its length."),
						item(card("pp-overdue", "Dig"), "Overdue: the end date has passed and the task is not done."),
						item(card("pp-tentative", "Dig"), "Pencil (tentative): soft load, not counted as booked until you firm it up."),
						item(card("pp-drafted", "Dig"), "Draft: a change kept to yourself until you publish it (Draft mode)."),
					],
				},
				{
					title: __("Chips on a card"),
					items: [
						item(chip("", "Day 2 of 3"), "Which day of a multi-day task this is."),
						item(chip("pp-amber", "Needs 1 more"), "Fewer people than the task's Crew needed."),
						item(chip("pp-amber", "No crew"), "Nobody is on it yet."),
						item(chip("pp-red", "Conflict"), "Someone on it is over their hours, double-booked or booked on a day off."),
						item(chip("pp-red", "Overdue"), "Its end date has passed."),
						item(chip("pp-pencil", "Pencil"), "Pencilled in, not firm yet."),
						item(chip("pp-amber", "Starts before Dig"), "It starts before a task it depends on ends."),
						item(chip("pp-amber", "Missing Forklift"), "Nobody on the crew holds a qualification it needs."),
						item(chip("pp-weather", "Rain 70%"), "Outdoor work on a day with rain, freezing or high wind in the forecast."),
						item(chip("", "14h of 12h"), "Hours clocked against the plan. Red when it is running over."),
						item(chip("pp-equip", "Truck 3"), "A vehicle or asset the task uses."),
						item(chip("", "Delivery"), "A rental crew task. Its dates follow its Rental Booking."),
						item(chip("", "No dates"), "Not scheduled yet: drag it onto a day."),
						item(chip("pp-draft-chip", "Draft"), "Changed in Draft mode and not published yet."),
					],
				},
				{
					title: __("People"),
					items: [
						item(`<span class="pp-badge" style="background:#2563eb">AH</span>`, "Crew initials. Click one to see that person's week."),
						item(`<span class="pp-badge pp-lead" style="background:#16a34a">JD</span>`, "The crew lead (ringed)."),
						item(`<span class="pp-badge pp-more">+2</span>`, "More people than fit on the card."),
					],
				},
				{
					title: __("A person's day"),
					note: __("In Resources available and the crew view. Hours count driving when Settings say so."),
					items: [
						item(bar("pp-green", 50), "Up to 75% of their hours booked."),
						item(bar("pp-amber", 90), "More than 75% booked."),
						item(bar("pp-red", 100), "Over their hours."),
						item(bar("pp-green", 40, 30), "Hatched: pencilled hours, drawn after the firm ones."),
						item(`<span class="pp-p6a-sample pp-offday">${pp_esc(__("Off"))}</span>`, "Not working: time off, a holiday or not a work day. The type and reason of time off are never shown."),
						item(`<span class="pp-p6a-sample pp-conflict">${pp_esc(__("2h free"))}</span>`, "Red outline: a conflict on that day."),
						item(`<span class="pp-p6a-sample pp-softover">${pp_esc(__("Full"))}</span>`, "Amber outline: pencilled work would put them over."),
						item(chip("pp-red", "Long drive"), "More driving than the Settings limit."),
						item(`<span class="pp-p6a-sample">${PP6A.route_icon}</span>`, "Open the day's route: stops in driving order, drive times and a map."),
					],
				},
				{
					title: __("Other bookings (read-only)"),
					items: [
						item(`<span class="pp-fcard pp-k-visit pp-p6a-sample">${pp_esc(__("Visit"))}</span>`, "A maintenance visit. It moves on the Maintenance Planner."),
						item(`<span class="pp-fcard pp-k-rental pp-p6a-sample">${pp_esc(__("Rental"))}</span>`, "A rental crew task. It moves with its Rental Booking."),
						item(`<span class="pp-fcard pp-k-travel pp-p6a-sample">${pp_esc(__("Travel"))}</span>`, "A travel day. The whole day is taken."),
						item(`<span class="pp-ecard pp-clash pp-p6a-sample">${pp_esc(__("Truck 3"))}</span>`, "Crew view, equipment rows: red when a vehicle or asset is on two tasks at once, striped when it is in the shop."),
					],
				},
				{
					title: __("Dragging"),
					items: [
						item(`<span class="pp-p6a-sample pp-over">${pp_esc(__("Drop here"))}</span>`, "Where the card will land."),
						item(`<span class="pp-p6a-sample pp-over-past">${pp_esc(__("Past day"))}</span>`, "A day before today: the planner asks before it schedules work there."),
						item("", "On a touch screen, hold a card for a moment before you drag it."),
						item("", "A change that causes a conflict asks for a reason, never blocks. Undo puts the last change back."),
					],
				},
				...this.p6d_legend_sections(),
			].concat(this.p6b_legend_sections()),
			{ title: __("How to read the Project Planner"), owner: PP6A.owner }
		);
	},
};

Object.assign(ProjectPlanner.prototype, PP6A_METHODS);

// ====================================================================== Phase 6B: faster scheduling
//
// Nik picked four things on 2026-10-09 (TASK-2026-02468), and they all live here:
//
//   - right-click a card, an empty spot or a name (a long press of about half a second on a touch
//     screen, when the finger does not move)  a menu (planner_kit.menu): Edit, Assign to..., Move to
//                                            next free day, Pencil / Firm up, Duplicate, Duplicate
//                                            to..., Split, Project at a glance; Add task here... on an
//                                            empty spot; See their week on a name. Other phases add
//                                            items through this.p6_menu_providers
//   - drag the left or right edge of a card (week and crew views)
//                                            its start or end day moves (save_task start + end)
//   - double-click an empty spot             quick add for that person and day (quick_add_task)
//   - Shift-, Ctrl- or Cmd-click cards        a selection (this.p6_selection); drag one and they all
//                                            move by the same number of calendar days (move_many,
//                                            one reason, one Undo); a bar offers Move, Pencil, Clear
//   - the search box (or /)                  tasks, projects and people on screen first, then the
//                                            server (search_planner); a task jumps to its week
//   - T, the arrows, 1 2 3, Ctrl/Cmd+Z, ?, Esc keyboard shortcuts, listed in the legend
//
// Nothing here picks a person: Assign to... lists who is free on the day (who_is_free) and the
// planner clicks one ("suggest a crew" was declined). Every write goes through send(), so the reason
// prompt, draft mode and Undo apply; the planner_actions endpoints are routed there through
// this.p6_send_modules. A new task cannot be a draft, so duplicate, split and quick add say so in
// draft mode instead of asking the server.
//
// Hooks into the class above, one line each: init_phase6b (constructor), render_phase6b (render),
// p6_send_modules (send), the grip check (drag_source), p6b_lift (lift), p6b_drop (drop), p6b_undo
// (undo), and p6b_legend_sections (p6a_legend).

const PP6B = {
	actions: "erpnext_enhancements.api.planner_actions",
	conflicts: "erpnext_enhancements.api.planner_conflicts",
	// The planner_actions writes. send() looks their module up in this.p6_send_modules.
	action_methods: ["duplicate_task", "split_task", "quick_add_task", "move_many", "remove_created_task"],
	long_press_ms: 500,
	search_min: 2,
	search_wait_ms: 250,
	search_limit: 20,
	flash_ms: 2600,
	widths: { assign: 440, panel: 460 },
};

// Pure helpers: no frappe, no moment, no page. tests/test_planner_phase6b.py runs them under node
// (and the Maintenance Planner keeps an identical copy, MP6B_PURE, run through the same cases).
const PP6B_PURE = {
	// "2026-10-12" + 3 -> "2026-10-15", by the calendar (in UTC, so no daylight-saving step).
	ymd_add(ymd, days) {
		const date = new Date(`${ymd}T00:00:00Z`);
		date.setUTCDate(date.getUTCDate() + (Number(days) || 0));
		return date.toISOString().slice(0, 10);
	},
	// Days from `a` to `b`, negative when `b` is earlier.
	ymd_diff(a, b) {
		return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86400000);
	},
	// Monday to Friday from `first` to `last` inclusive (0 when `last` is earlier).
	working_days(first, last) {
		let count = 0;
		for (let day = first, i = 0; day <= last && i < 400; day = PP6B_PURE.ymd_add(day, 1), i++) {
			const weekday = new Date(`${day}T00:00:00Z`).getUTCDay();
			if (weekday !== 0 && weekday !== 6) count++;
		}
		return count;
	},
	// How a split shares hours: api/planner_actions.split_weights, kept in step (both are tested).
	split_weights(start, end, split) {
		const first_last = PP6B_PURE.ymd_add(split, -1);
		const first = PP6B_PURE.working_days(start, first_last);
		const second = PP6B_PURE.working_days(split, end);
		if (first && second) return [first, second];
		return [PP6B_PURE.ymd_diff(start, first_last) + 1, PP6B_PURE.ymd_diff(split, end) + 1];
	},
	split_hours(total, first_weight, second_weight) {
		const weight = (Number(first_weight) || 0) + (Number(second_weight) || 0);
		const amount = Math.round((Number(total) || 0) * 100) / 100;
		if (amount <= 0 || weight <= 0) return [0, 0];
		const first = Math.round(((amount * first_weight) / weight) * 100) / 100;
		return [first, Math.round((amount - first) * 100) / 100];
	},
	// A multi-select move: every card the same calendar days later (or earlier), keeping its length.
	offset_moves(cards, days, modified_of) {
		return (cards || [])
			.filter((card) => card && card.start)
			.map((card) => ({
				task: card.name,
				modified: modified_of ? modified_of(card.name) : card.modified,
				start: PP6B_PURE.ymd_add(card.start, days),
				end: PP6B_PURE.ymd_add(card.end && card.end >= card.start ? card.end : card.start, days),
			}));
	},
	// The hours one person needs free on a day of this task: who_is_free's `hours`. A slot is its
	// length; no estimate is a full day; otherwise the estimate shared by the crew (one more when
	// someone is added) over the task's working days.
	need_hours(card, mode, day_hours) {
		const full = Number(day_hours) > 0 ? Number(day_hours) : 8;
		if (card.slot && card.slot.length === 2) {
			const minutes = card.slot.map((text) => {
				const parts = String(text).split(":");
				return Number(parts[0]) * 60 + Number(parts[1] || 0);
			});
			if (minutes[1] > minutes[0]) return Math.max(0.25, Math.round(((minutes[1] - minutes[0]) / 60) * 100) / 100);
		}
		const expected = Number(card.expected_time) || 0;
		if (expected <= 0) return Math.min(24, full);
		const people = Math.max(1, (card.crew || []).length + (mode === "add" ? 1 : 0));
		const last = card.end && card.end >= card.start ? card.end : card.start;
		const days = (card.start && PP6B_PURE.working_days(card.start, last)) || 1;
		return Math.min(24, Math.max(0.25, Math.round((expected / people / days) * 100) / 100));
	},
	// Toggle one key in a selection Set; true when it is selected afterwards.
	toggle(set, key) {
		if (set.has(key)) {
			set.delete(key);
			return false;
		}
		set.add(key);
		return true;
	},
	// A keydown as a planner shortcut, or null. `info`: key, ctrl, meta, shift, alt, editable (focus in
	// an input, textarea, select or contenteditable) and dialog (a frappe dialog is open).
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
	// The menu: the page's own items, then each provider's group, a divider before each group.
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
	// Search what is loaded: every word somewhere in an entry's label or keys. A label that starts
	// with the first word comes first, then people, projects (sites) and tasks (visits), then by label.
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

const PP6B_STYLE = `
.pp-card.p6b-selected{outline:2px solid #7c3aed;outline-offset:1px;}
.p6b-bar{position:fixed;left:50%;bottom:72px;transform:translateX(-50%);z-index:1020;display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;max-width:calc(100vw - 24px);padding:6px 10px;border:1px solid #7c3aed;border-radius:10px;background:var(--card-bg);color:var(--text-color);box-shadow:0 8px 24px rgba(0,0,0,.18);font-size:13px;}
.p6b-bar-count{font-weight:600;}
.p6b-bar-note{flex:1 1 100%;font-size:11px;color:var(--text-muted);}
.p6b-count{position:absolute;top:-9px;right:-9px;min-width:20px;height:20px;padding:0 5px;border-radius:10px;background:#7c3aed;color:#fff;font-size:11px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;}
.p6b-grip{position:absolute;top:0;bottom:0;width:9px;cursor:ew-resize;touch-action:none;z-index:2;}
.p6b-grip-l{left:-4px;}
.p6b-grip-r{right:-4px;}
.p6b-grip::after{content:"";position:absolute;top:25%;bottom:25%;left:3px;width:3px;border-radius:2px;background:var(--text-muted);opacity:0;}
.pp-card:hover .p6b-grip::after,.p6b-grip:focus::after{opacity:.55;}
.p6b-span{background:rgba(124,58,237,.10);box-shadow:inset 0 0 0 2px rgba(124,58,237,.55);}
.p6b-span-label{position:fixed;z-index:1100;pointer-events:none;padding:3px 8px;border-radius:6px;background:var(--text-color);color:var(--card-bg);font-size:12px;white-space:nowrap;}
body.p6b-resizing,body.p6b-resizing *{cursor:ew-resize !important;-webkit-user-select:none;user-select:none;}
.pp-card.p6b-flash{animation:p6b-flash .8s ease-in-out 3;}
@keyframes p6b-flash{0%,100%{box-shadow:none;}50%{box-shadow:0 0 0 4px rgba(245,158,11,.85);}}
.p6b-search-wrap{position:relative;display:inline-block;}
.pp-toolbar input.p6b-search{width:210px;}
.p6b-results{position:absolute;top:100%;left:0;z-index:1030;margin-top:4px;min-width:300px;max-width:min(420px,calc(100vw - 24px));max-height:60vh;overflow-y:auto;padding:4px 0;border:1px solid var(--border-color);border-radius:8px;background:var(--card-bg);box-shadow:0 10px 28px rgba(0,0,0,.2);}
.p6b-result{display:flex;flex-direction:column;padding:5px 10px;cursor:pointer;font-size:13px;}
.p6b-result.p6b-active,.p6b-result:hover{background:var(--control-bg);}
.p6b-result-kind{font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);}
.p6b-result-label{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.p6b-result-sub{font-size:11px;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.p6b-results-note{padding:4px 10px;font-size:11px;color:var(--text-muted);}
.p6b-modes{margin:4px 0 8px;}
.p6b-modes label{display:flex;align-items:center;gap:6px;margin:2px 0;font-weight:normal;}
.p6b-pick{display:flex;justify-content:space-between;align-items:center;gap:8px;width:100%;margin-bottom:4px;padding:6px 8px;border:1px solid var(--border-color);border-radius:8px;background:var(--card-bg);color:var(--text-color);text-align:left;cursor:pointer;}
.p6b-pick:hover,.p6b-pick:focus{background:var(--control-bg);outline:none;}
.p6b-pick-busy{border-style:dashed;}
.p6b-pick-why{font-size:11px;color:var(--text-muted);}
.p6b-kbd{display:inline-block;min-width:18px;margin-right:3px;padding:0 5px;border:1px solid var(--border-color);border-bottom-width:2px;border-radius:4px;background:var(--control-bg);font-size:11px;font-family:inherit;text-align:center;}
.p6b-legend-grip{display:inline-block;position:relative;width:46px;height:16px;border:1px solid var(--border-color);border-left:4px solid #2563eb;border-radius:4px;}
.p6b-legend-grip::after{content:"";position:absolute;top:3px;bottom:3px;right:2px;width:3px;border-radius:2px;background:var(--text-muted);}
@media (max-width:760px){
.p6b-search-wrap{flex:1 1 100%;}
.pp-toolbar input.p6b-search{width:100%;}
.pp-week .p6b-grip{display:none;}
.p6b-bar{bottom:64px;}
}
`;

const PP6B_METHODS = {
	init_phase6b() {
		if (!document.getElementById("pp-style-6b")) {
			$("<style id='pp-style-6b'>").text(PP6B_STYLE).appendTo(document.head);
		}
		// The registries other phases add to (phase6-wave2): menu items, legend sections, endpoint modules.
		this.p6_menu_providers = this.p6_menu_providers || [];
		this.p6_legend_providers = this.p6_legend_providers || [];
		this.p6_send_modules = this.p6_send_modules || {};
		PP6B.action_methods.forEach((method) => (this.p6_send_modules[method] = PP6B.actions));
		// The multi-selection: task names. 6C's print reads it.
		this.p6_selection = this.p6_selection || new Set();
		this.p6b = {
			press: null,
			resize: null,
			flash: null,
			seen: null,
			next_free: {},
			keys_on: false,
			search: { q: "", token: 0, timer: null, local: [], remote: [], loading: false, active: 0, shown: [], list_id: "" },
		};
		this.p6b_build_search();
		this.$p6b_bar = $('<div class="p6b-bar" role="toolbar"></div>')
			.attr("aria-label", __("Selected tasks"))
			.hide()
			.appendTo(this.$body);
		const root = this.$body[0];
		// Capture on the page's own container: a modified click on a card selects it, ahead of the
		// card's own click (which opens it) and of 6A's quick-look clicks.
		(root.parentElement || root).addEventListener("click", (e) => this.p6b_modifier_click(e), true);
		root.addEventListener("click", (e) => this.p6b_plain_click(e));
		root.addEventListener("contextmenu", (e) => this.p6b_contextmenu(e));
		root.addEventListener("dblclick", (e) => this.p6b_dblclick(e));
		root.addEventListener("pointerdown", (e) => this.p6b_pointer_down(e), true);
		document.addEventListener("pointermove", (e) => this.p6b_pointer_move(e));
		document.addEventListener("pointerup", (e) => this.p6b_pointer_up(e));
		document.addEventListener("pointercancel", (e) => this.p6b_pointer_cancel(e));
		document.addEventListener(
			"touchmove",
			(e) => {
				if (this.p6b.resize) e.preventDefault();
			},
			{ passive: false }
		);
		this.p6b_bind_keys();
		// The route view, My week and the heatmap draw no cards: the selection bar and an open search
		// list must not stay on top of them.
		if (frappe.router && typeof frappe.router.on === "function") {
			frappe.router.on("change", () => this.p6b_route_changed());
		}
	},

	p6b_route_changed() {
		const route = frappe.get_route() || [];
		const calendar = route[0] === PP.route && (!route[1] || PP.views.includes(route[1]));
		if (!calendar && this.$p6b_bar) this.$p6b_bar.hide();
		if (this.p6b.resize) this.p6b_resize_end(false);
		this.p6b_close_results();
	},

	render_phase6b() {
		if (!this.p6b) return;
		if (this.p6b.seen !== this.data) {
			// A new answer from the server: the next-free-day answers may be out of date.
			this.p6b.seen = this.data;
			this.p6b.next_free = {};
		}
		this.p6b_prune_selection();
		this.p6b_decorate_selection();
		this.p6b_add_grips();
		this.p6b_render_bar();
		this.p6b_flash_pending();
	},

	// Writes to api/planner_actions go through send() like every other write.
	p6b_send(method, args, opts) {
		return this.send(method, args, opts);
	},

	p6b_kit() {
		return (this.p6a && this.p6a.kit) || null;
	},

	p6b_user(resource) {
		const person = resource && this.by_resource[resource];
		return (person && person.user) || null;
	},

	p6b_span_text(start, end) {
		if (!start) return __("No dates");
		return end && end !== start ? `${pp_when(start)} – ${pp_when(end)}` : pp_when(start);
	},

	// A route change from a menu item or search result: the kit closes its drawer first.
	p6b_navigate(fn) {
		const kit = this.p6b_kit();
		return kit ? kit.drawer.navigate(fn) : fn();
	},

	// The kit's side panel (or a Dialog until the kit has loaded) for the small forms here.
	p6b_panel(opts) {
		const kit = this.p6b_kit();
		if (!kit) return new frappe.ui.Dialog({ title: opts.title, fields: opts.fields });
		return kit.panel({
			title: opts.title,
			subtitle: opts.subtitle || "",
			fields: opts.fields,
			width: PP6B.widths.panel,
			key: opts.key || null,
			owner: PP6A.owner,
			push: this.$body[0],
		});
	},

	p6b_panel_open(panel) {
		return panel && (typeof panel.is_open === "function" ? panel.is_open() : !!panel.display);
	},

	p6b_refuse_in_draft() {
		if (!this.draft_on) return false;
		frappe.msgprint(
			__(
				"Draft mode holds back changes to tasks that already exist; a new task cannot be a draft. Turn Draft mode off to add it."
			)
		);
		return true;
	},

	// ------------------------------------------------------------------ the right-click menu

	// What a right-click or long press landed on, in the shape the menu providers take.
	p6b_target(el) {
		if (!el || !el.closest || !this.$body[0].contains(el)) return null;
		if (el.closest("button, a, input, select, textarea, .pp-toolbar, .pp-fcard, .pp-route, .p6b-bar, .pp-draft-bar")) {
			return null;
		}
		const card_el = el.closest(".pp-card[data-task]");
		if (card_el) {
			const card = this.by_task[card_el.getAttribute("data-task")];
			if (!card) return null;
			return {
				kind: "card",
				card,
				ymd: card_el.getAttribute("data-date") || card.start || null,
				resource: card_el.getAttribute("data-resource") || null,
				el: card_el,
			};
		}
		const person_el = el.closest(".pp-person[data-resource]");
		if (person_el) {
			const resource = person_el.getAttribute("data-resource");
			return { kind: "person", resource, user: this.p6b_user(resource), el: person_el };
		}
		const cell = el.closest(".pp-cell[data-date][data-resource]");
		if (cell) {
			const resource = cell.getAttribute("data-resource");
			return { kind: "cell", resource, user: this.p6b_user(resource), ymd: cell.getAttribute("data-date"), el: cell };
		}
		const day = el.closest(".pp-day[data-date]");
		if (day) return { kind: "cell", resource: null, user: null, ymd: day.getAttribute("data-date"), el: day };
		return null;
	},

	p6b_contextmenu(e) {
		if (this.drag || this.p6b.resize) return;
		if (!this.p6b_open_for(e.target, { x: e.clientX, y: e.clientY }, !!this.p6b.press)) return;
		e.preventDefault();
		if (this.p6b.press) this.p6b.press.opened = true;
	},

	// Opens the menu for whatever is under `el`. False (and the browser's own menu) when there is
	// nothing of ours there or the kit has not loaded.
	p6b_open_for(el, anchor, from_touch) {
		if (!this.p6b_kit() || !this.data || !PP.views.includes(this.view)) return false;
		const target = this.p6b_target(el);
		if (!target) return false;
		// The page's own items, then every provider's (6C, 6D-UI): a target with none of ours can still
		// carry theirs, and a provider that throws is skipped.
		const items = this.p6b_menu_items(target);
		const all = PP6B_PURE.with_providers(items, this.p6_menu_providers, target);
		if (!all.length) return false;
		if (from_touch) {
			// The finger lifting is a click on whatever the menu now covers.
			this.click_blocked_until = Date.now() + 600;
			this.p6b_swallow_click();
		}
		this.p6b_open_menu(target, all, anchor);
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
		if (target.kind === "card") return target.card.subject || target.card.name;
		if (target.kind === "person") return this.resource_label(target.resource);
		return [target.resource ? this.resource_label(target.resource) : "", target.ymd ? pp_when(target.ymd) : ""]
			.filter(Boolean)
			.join(" · ");
	},

	p6b_menu_items(target) {
		if (target.kind === "card") return this.p6b_card_items(target);
		if (target.kind === "person") {
			return [{ label: __("See their week"), on_click: () => this.p6a_open_person(target.resource) }];
		}
		if (target.kind === "cell") return this.p6b_cell_items(target);
		return [];
	},

	// `all`: the page's items with the providers' already appended (p6b_open_for).
	p6b_open_menu(target, all, anchor) {
		const kit = this.p6b_kit();
		const handle = kit.menu({ anchor, items: all, title: this.p6b_menu_title(target), owner: PP6A.owner });
		// The next free day is asked for as the menu opens; its hint fills in when the answer arrives.
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

	p6b_card_items(target) {
		const card = target.card;
		const can_edit = !!(this.data && this.data.can_edit);
		const editable = !!(card.movable && can_edit);
		const why = !can_edit
			? __("You cannot change tasks")
			: card.rental_kind
			? __("Follows its Rental Booking")
			: __("Read-only here");
		const creating = editable && !this.draft_on;
		const create_why = !editable ? why : this.draft_on ? __("Not in Draft mode") : "";
		const end = card.end && card.start && card.end > card.start ? card.end : card.start;
		const multi = !!(card.start && end > card.start);
		const items = [
			{ label: __("Edit"), on_click: () => this.open_card(card) },
			{
				label: __("Assign to…"),
				hint: editable ? "" : why,
				disabled: !editable,
				on_click: () => this.p6b_assign(card, target.ymd),
			},
			this.p6b_next_free_item(target, editable, why),
			card.tentative
				? { label: __("Firm up"), hint: editable ? "" : why, disabled: !editable, on_click: () => this.firm_up(card) }
				: {
						label: __("Pencil"),
						hint: editable ? __("Make it tentative") : why,
						disabled: !editable,
						on_click: () => this.p6b_pencil(card),
				  },
			{
				label: __("Duplicate"),
				hint: create_why || __("Same days"),
				disabled: !creating,
				on_click: () => this.p6b_duplicate(card, null),
			},
			{ label: __("Duplicate to…"), hint: create_why, disabled: !creating, on_click: () => this.p6b_duplicate_to(card) },
			{
				label: multi ? __("Split across days…") : __("Split in two…"),
				hint: create_why || (card.start ? "" : __("No dates")),
				disabled: !creating || !card.start,
				on_click: () => this.p6b_split(card, target),
			},
		];
		if (card.project) {
			items.push(
				{ divider: true },
				{ label: __("Project at a glance"), on_click: () => this.p6a_open_project(card.project) },
				{
					label: __("Open project"),
					on_click: () => this.p6b_navigate(() => frappe.set_route("Form", "Project", card.project)),
				}
			);
		}
		return items;
	},

	p6b_cell_items(target) {
		const can = !!(this.data && this.data.can_edit);
		const items = [
			{
				label: __("Add task here…"),
				hint: !can ? __("You cannot add tasks") : this.draft_on ? __("Not in Draft mode") : __("Double-click"),
				disabled: !can || this.draft_on,
				on_click: () => this.p6b_quick_add(target),
			},
		];
		if (target.ymd) items.push({ label: __("Everyone's day"), on_click: () => this.p6a_open_day(target.ymd) });
		return items;
	},

	// The person a "next free day" or "split in two" is about: the crew-view row the card sits in, the
	// lead, or the only person on it. None when several are on it and none leads.
	p6b_lead(card, row) {
		const crew = card.crew || [];
		if (row && crew.some((member) => member.resource === row)) return row;
		const lead = crew.find((member) => member.is_lead);
		if (lead) return lead.resource;
		return crew.length === 1 ? crew[0].resource : null;
	},

	p6b_next_free(card, resource, hours) {
		const key = [card.name, resource, hours || "", this.modified[card.name] || card.modified].join("|");
		if (!this.p6b.next_free[key]) {
			const args = { resource, task: card.name };
			if (card.start) args.after = card.start;
			if (hours) args.hours = hours;
			this.p6b.next_free[key] = Promise.resolve(
				frappe.call({ method: `${PP6B.conflicts}.get_next_free_day`, args })
			)
				.then((r) => (r && r.message) || null)
				.catch(() => null);
		}
		return this.p6b.next_free[key];
	},

	p6b_free_state(answer) {
		if (!answer) return { hint: __("Could not check"), disabled: false };
		if (answer.date) return { hint: pp_when(answer.date), disabled: false };
		return { hint: __("Nothing free in {0} days", [answer.horizon || 30]), disabled: true };
	},

	p6b_next_free_item(target, editable, why) {
		const card = target.card;
		const item = { label: __("Move to next free day"), hint: __("Looking…"), disabled: false };
		if (!editable) return Object.assign(item, { hint: why, disabled: true });
		const resource = this.p6b_lead(card, target.resource);
		if (!resource) {
			return Object.assign(item, { hint: (card.crew || []).length ? __("Mark a lead first") : __("No crew"), disabled: true });
		}
		const who = this.resource_label(resource);
		const lookup = this.p6b_next_free(card, resource);
		item.p6b_lookup = lookup;
		item.on_click = () =>
			lookup.then((answer) => {
				if (answer && answer.date) {
					this.p6b_move_to(card, answer.date);
					return;
				}
				frappe.show_alert(
					{ message: (answer && answer.note) || __("No free day found for {0}.", [who]), indicator: "orange" },
					7
				);
			});
		return item;
	},

	p6b_move_to(card, date) {
		const subject = card.subject || card.name;
		return this.commit(card, "save_task", { task: card.name, start: date }, __("{0} moved to {1}", [subject, pp_when(date)]));
	},

	p6b_pencil(card) {
		return this.commit(
			card,
			"save_task",
			{ task: card.name, tentative: 1 },
			__("{0} is now pencilled in", [card.subject || card.name])
		);
	},

	// ------------------------------------------------------------------ assign to...

	// Who has the hours free that day, from who_is_free. The planner picks; nothing is picked for them.
	p6b_assign(card, ymd) {
		const kit = this.p6b_kit();
		const day = ymd || card.start;
		if (!kit) {
			this.open_card(card);
			return;
		}
		if (!day) {
			frappe.msgprint(__("Give the task a date first: who is free depends on the day."));
			return;
		}
		const state = { mode: "add", data: null, token: 0 };
		const handle = kit.drawer.open({
			title: __("Assign to…"),
			subtitle: [card.subject || card.name, pp_when(day)].join(" · "),
			body: `<p class="pk-empty">${pp_esc(__("Finding who is free…"))}</p>`,
			width: PP6B.widths.assign,
			key: `assign:${card.name}`,
			owner: PP6A.owner,
			push: this.$body[0],
			reopen: () => this.p6b_assign(this.by_task[card.name] || card, ymd),
			on_click: (e, h) => this.p6b_assign_click(e, h, card, day, state),
		});
		this.p6b_assign_load(handle, card, day, state);
	},

	p6b_assign_need(card, state) {
		const day_hours = this.data && this.data.settings && this.data.settings.default_day_hours;
		return PP6B_PURE.need_hours(card, state.mode === "add" ? "add" : "replace", day_hours);
	},

	p6b_assign_load(handle, card, day, state) {
		const token = ++state.token;
		const hours = this.p6b_assign_need(card, state);
		return Promise.resolve(frappe.call({ method: `${PP.api}.who_is_free`, args: { start: day, hours } }))
			.then((r) => {
				if (token !== state.token || !handle.is_open()) return;
				state.data = (r && r.message) || null;
				handle.set_body(this.p6b_assign_html(card, day, state));
			})
			.catch(() => {
				if (token !== state.token || !handle.is_open()) return;
				state.data = null;
				handle.set_body(this.p6b_assign_html(card, day, state));
			});
	},

	p6b_assign_html(card, day, state) {
		const crew = card.crew || [];
		const on = new Set(crew.map((member) => member.resource));
		const parts = [
			`<p class="pk-note">${pp_esc(
				__("Pick who should do it: nobody is chosen for you. Someone who is not free can still be booked; you will be asked for a reason.")
			)}</p>`,
		];
		if (crew.length) {
			const option = (value, text) =>
				`<label><input type="radio" name="p6b-mode" value="${pp_esc(value)}"${state.mode === value ? " checked" : ""}> ${pp_esc(text)}</label>`;
			parts.push(
				`<div class="p6b-modes" role="radiogroup" aria-label="${pp_esc(__("How to assign"))}">${option(
					"add",
					__("Add to the crew")
				)}${crew
					.map((member) => option(member.resource, __("Replace {0}", [this.resource_label(member.resource, member.label)])))
					.join("")}</div>`
			);
		}
		const data = state.data;
		if (!data) {
			parts.push(`<p class="pk-empty">${pp_esc(__("Who is free could not be loaded. Close this and try again."))}</p>`);
			return parts.join("");
		}
		const entry = (data.days || [])[0] || { free: [], not_free: [] };
		const row = (person, busy) =>
			`<button type="button" class="p6b-pick${busy ? " p6b-pick-busy" : ""}" data-p6b-pick="${pp_esc(person.resource)}">` +
			`<span><b>${pp_esc(person.label || person.resource)}</b>${
				person.group ? ` <span class="p6b-pick-why">${pp_esc(__(person.group))}</span>` : ""
			}</span>` +
			`<span class="p6b-pick-why">${pp_esc(busy ? person.reason || "" : __("{0}h free", [pp_hours(person.free_hours)]))}</span></button>`;
		const free = (entry.free || []).filter((person) => !on.has(person.resource));
		const busy = (entry.not_free || []).filter((person) => !on.has(person.resource));
		parts.push(
			`<div class="pk-section-title">${pp_esc(
				__("Free on {0} ({1}h or more)", [pp_when(entry.date || day), pp_hours(data.hours)])
			)}</div>`
		);
		parts.push(
			free.length
				? free.map((person) => row(person, false)).join("")
				: `<p class="pk-empty">${pp_esc(__("Nobody has those hours free that day."))}</p>`
		);
		if (busy.length) {
			parts.push(`<div class="pk-section-title">${pp_esc(__("Not free"))}</div>`);
			parts.push(busy.map((person) => row(person, true)).join(""));
		}
		if (crew.length) {
			parts.push(
				`<div class="pk-section-title">${pp_esc(__("Already on it"))}</div><p class="pk-note">${pp_esc(
					crew.map((member) => this.resource_label(member.resource, member.label)).join(", ")
				)}</p>`
			);
		}
		if (data.note) parts.push(`<p class="pk-note">${pp_esc(data.note)}</p>`);
		return parts.join("");
	},

	p6b_assign_click(e, handle, card, day, state) {
		const radio = e.target && e.target.closest ? e.target.closest("input[name='p6b-mode']") : null;
		if (radio) {
			const before = this.p6b_assign_need(card, state);
			state.mode = radio.value;
			if (this.p6b_assign_need(card, state) !== before) this.p6b_assign_load(handle, card, day, state);
			return;
		}
		const pick = e.target && e.target.closest ? e.target.closest("[data-p6b-pick]") : null;
		if (!pick) return;
		const resource = pick.getAttribute("data-p6b-pick");
		const mode = state.mode;
		handle.close();
		this.p6b_assign_to(card, resource, mode);
	},

	p6b_assign_to(card, resource, mode) {
		const fresh = this.by_task[card.name] || card;
		const who = this.resource_label(resource);
		const subject = fresh.subject || fresh.name;
		if (this.crew_has(fresh, resource)) {
			frappe.show_alert({ message: __("{0} is already on {1}.", [who, subject]), indicator: "orange" }, 6);
			return null;
		}
		if (mode && mode !== "add" && this.crew_has(fresh, mode)) {
			return this.commit(
				fresh,
				"swap_crew",
				{ task: fresh.name, from_resource: mode, to_resource: resource },
				__("{0}: {1} in place of {2}", [subject, who, this.resource_label(mode)])
			);
		}
		return this.commit(fresh, "add_crew", { task: fresh.name, resource }, __("{0} added to {1}", [who, subject]));
	},

	// ------------------------------------------------------------------ duplicate, split, quick add

	// A new task from api/planner_actions. Undo deletes it again (remove_created_task), and only when
	// nothing has been attached to it since; `extra` shapes the Undo entry (a split's puts the first
	// half back too).
	p6b_create(method, args, subject, message, extra) {
		return this.p6b_send(method, args, {})
			.then((result) => {
				if (!result || !result.name) return result;
				const created = extra ? extra(result) : { task: result.name, modified: result.modified };
				if (created.task && created.modified) this.modified[created.task] = created.modified;
				const snapshot = Object.assign({ p6b: "created", subject }, created);
				if (!this.push_undo(snapshot, message)) frappe.show_alert({ message, indicator: "green" }, 5);
				const card = result.second ? result.second.card : result.card;
				this.p6b.flash = { name: created.task, date: (card && card.start) || null, wait: false };
				return result;
			})
			.finally(() => this.load());
	},

	p6b_duplicate(card, date) {
		if (this.p6b_refuse_in_draft()) return null;
		const subject = card.subject || card.name;
		const args = { task: card.name };
		if (date) args.date = date;
		const message = date ? __("{0} duplicated to {1}", [subject, pp_when(date)]) : __("{0} duplicated", [subject]);
		return this.p6b_create("duplicate_task", args, subject, message);
	},

	p6b_duplicate_to(card) {
		if (this.p6b_refuse_in_draft()) return;
		const panel = this.p6b_panel({
			title: __("Duplicate to…"),
			subtitle: card.subject || card.name,
			key: `p6b-duplicate:${card.name}`,
			fields: [
				{
					fieldtype: "Date",
					fieldname: "date",
					label: __("The copy starts on"),
					reqd: 1,
					default: card.start || this.today(),
					description: __("The copy keeps the task's length, crew, hours, qualifications, equipment and pencil flag."),
				},
			],
		});
		panel.set_primary_action(__("Duplicate"), (values) => {
			const date = values && values.date;
			if (!date) return;
			panel.hide();
			this.p6b_duplicate(card, date);
		});
		panel.show();
	},

	p6b_split(card, target) {
		if (this.p6b_refuse_in_draft() || !card.start) return;
		const end = card.end && card.end >= card.start ? card.end : card.start;
		const multi = end > card.start;
		const subject = card.subject || card.name;
		const middle = PP6B_PURE.ymd_add(card.start, Math.ceil((PP6B_PURE.ymd_diff(card.start, end) + 1) / 2));
		const panel = this.p6b_panel({
			title: multi ? __("Split across days") : __("Split in two"),
			subtitle: subject,
			key: `p6b-split:${card.name}`,
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "p6b_split_note",
					options: `<p class="pk-note">${pp_esc(
						multi
							? __("The task keeps the days before the day you pick. A new task does the rest, depends on it and has the same crew, qualifications and equipment. Hours are shared by working days.")
							: __("The task keeps its day with half the hours. A new task with the other half goes on the day you pick, with the same crew, qualifications and equipment.")
					)}</p>`,
				},
				{
					fieldtype: "Date",
					fieldname: "split_date",
					label: multi ? __("The second part starts on") : __("The second half goes on"),
					reqd: 1,
					default: multi ? middle : "",
				},
				{ fieldtype: "HTML", fieldname: "p6b_split_preview", options: '<p class="pk-note p6b-split-preview"></p>' },
			],
		});
		const preview = () => this.p6b_split_preview(panel, card, end, multi);
		panel.set_primary_action(__("Split"), (values) => {
			const date = values && values.split_date;
			if (!date) return;
			if (multi && !(date > card.start && date <= end)) {
				frappe.msgprint(__("Pick a day from {0} to {1}.", [pp_when(PP6B_PURE.ymd_add(card.start, 1)), pp_when(end)]));
				return;
			}
			if (!multi && date <= card.start) {
				frappe.msgprint(__("Pick a day after {0}.", [pp_when(card.start)]));
				return;
			}
			panel.hide();
			this.p6b_split_save(card, date);
		});
		panel.show();
		const field = panel.fields_dict && panel.fields_dict.split_date;
		if (field && field.$input) field.$input.on("change", preview);
		preview();
		if (multi) return;
		// Split in two: the second half goes on the person's next free day, unless the planner picks one.
		const resource = this.p6b_lead(card, target && target.resource);
		if (!resource) return;
		const who = this.resource_label(resource);
		const day_hours = this.data && this.data.settings && this.data.settings.default_day_hours;
		const half = Math.max(0.25, Math.round((PP6B_PURE.need_hours(card, "replace", day_hours) / 2) * 100) / 100);
		this.p6b_next_free(card, resource, half).then((answer) => {
			if (!this.p6b_panel_open(panel)) return;
			if (answer && answer.date && !panel.get_value("split_date")) panel.set_value("split_date", answer.date);
			const note =
				answer && answer.date
					? __("{0} is {1}'s next free day.", [pp_when(answer.date), who])
					: __("{0} has no free day in the next 30 days: pick one.", [who]);
			panel.$wrapper.find(".p6b-split-preview").attr("data-p6b-free", note);
			preview();
		});
	},

	p6b_split_preview(panel, card, end, multi) {
		const $box = panel.$wrapper.find(".p6b-split-preview");
		if (!$box.length) return;
		const date = panel.get_value("split_date");
		const free = $box.attr("data-p6b-free") || "";
		let text = "";
		if (!date) {
			text = free;
		} else if (multi) {
			if (!(date > card.start && date <= end)) {
				text = __("Pick a day from {0} to {1}.", [pp_when(PP6B_PURE.ymd_add(card.start, 1)), pp_when(end)]);
			} else {
				const [a, b] = PP6B_PURE.split_weights(card.start, end, date);
				const [first_hours, second_hours] = PP6B_PURE.split_hours(card.expected_time, a, b);
				const first = this.p6b_span_text(card.start, PP6B_PURE.ymd_add(date, -1));
				const second = this.p6b_span_text(date, end);
				text =
					Number(card.expected_time) > 0
						? __("First part: {0}, {1}h. Second part: {2}, {3}h.", [first, pp_hours(first_hours), second, pp_hours(second_hours)])
						: __("First part: {0}. Second part: {1}. With no estimate, each still books a full day per day.", [first, second]);
			}
		} else {
			text = [__("First half: {0}. Second half: {1}. The hours are shared equally.", [pp_when(card.start), pp_when(date)]), free]
				.filter(Boolean)
				.join(" ");
		}
		$box.text(text);
	},

	p6b_split_save(card, date) {
		const subject = card.subject || card.name;
		const restore = {
			task: card.name,
			start: card.start,
			end: card.end || card.start,
			expected_time: Number(card.expected_time) || 0,
			crew: this.crew_rows(card),
		};
		return this.p6b_create(
			"split_task",
			{ task: card.name, split_date: date, modified: this.modified[card.name] || card.modified },
			subject,
			__("{0} split in two", [subject]),
			(result) => ({
				p6b: "split",
				task: result.second.name,
				modified: result.second.modified,
				restore: Object.assign({}, restore, { modified: result.modified }),
			})
		);
	},

	p6b_dblclick(e) {
		if (!this.data || !this.data.can_edit || !PP.views.includes(this.view)) return;
		const el = e.target;
		if (!el || !el.closest) return;
		if (el.closest(".pp-card, .pp-fcard, .pp-day-head, button, a, input, select, textarea, .pp-route, [data-pp6a-day], .p6b-grip")) {
			return;
		}
		const target = this.p6b_target(el);
		if (!target || target.kind !== "cell") return;
		e.preventDefault();
		if (window.getSelection) window.getSelection().removeAllRanges();
		this.p6b_quick_add(target);
	},

	p6b_quick_add(target) {
		if (!this.data || !this.data.can_edit || this.p6b_refuse_in_draft()) return;
		// The project list is the planner's own: Active customer jobs (engine.PLANNER_PROJECT_TYPES).
		const names = (this.data.projects || []).map((project) => project.name);
		const panel = this.p6b_panel({
			title: __("Add a task"),
			subtitle: [target.resource ? this.resource_label(target.resource) : "", target.ymd ? pp_when(target.ymd) : ""]
				.filter(Boolean)
				.join(" · "),
			key: `p6b-add:${target.resource || ""}:${target.ymd || ""}`,
			fields: [
				{
					fieldtype: "Link",
					fieldname: "project",
					label: __("Project"),
					options: "Project",
					reqd: 1,
					default: this.project || "",
					description: __("Customer jobs only."),
					get_query: () => ({ filters: { name: ["in", names.length ? names : [""]] } }),
				},
				{ fieldtype: "Data", fieldname: "subject", label: __("Subject"), reqd: 1 },
				{
					fieldtype: "Float",
					fieldname: "hours",
					label: __("Hours"),
					description: __("Blank: a full day for the person. More hours than a day holds run over the next weekdays."),
				},
				{
					fieldtype: "Link",
					fieldname: "resource",
					label: __("Person"),
					options: "Planner Resource",
					default: target.resource || "",
					get_query: () => ({ filters: { is_active: 1 } }),
				},
				{ fieldtype: "Date", fieldname: "date", label: __("Date"), reqd: 1, default: target.ymd || this.today() },
				{ fieldtype: "Check", fieldname: "tentative", label: __("Pencil (tentative)") },
			],
		});
		panel.set_primary_action(__("Add"), (values) => this.p6b_quick_add_save(panel, values || {}));
		panel.show();
	},

	p6b_quick_add_save(panel, values) {
		const subject = String(values.subject || "").trim();
		if (!values.project || !subject || !values.date) return null;
		const args = { project: values.project, subject, date: values.date, tentative: values.tentative ? 1 : 0 };
		if (Number(values.hours) > 0) args.hours = Number(values.hours);
		if (values.resource) args.resource = values.resource;
		panel.hide();
		return this.p6b_create("quick_add_task", args, subject, __("{0} added on {1}", [subject, pp_when(values.date)]));
	},

	// ------------------------------------------------------------------ resize

	// The left and right edges of a card become grips: on the first and last day of a multi-day task
	// in the week view, and on every card in the crew view. Never on a rental crew task (its dates
	// follow its booking), a time-slot task (the slot decides its day) or in the month view.
	p6b_add_grips() {
		if (!this.data || !this.data.can_edit || (this.view !== "week" && this.view !== "crew")) return;
		const tip_start = pp_esc(__("Drag to change the first day"));
		const tip_end = pp_esc(__("Drag to change the last day"));
		this.$grid_wrap[0].querySelectorAll(".pp-card[data-task]").forEach((el) => {
			if (el.querySelector(".p6b-grip")) return;
			const card = this.by_task[el.getAttribute("data-task")];
			const ymd = el.getAttribute("data-date");
			if (!card || !card.movable || !card.start || !ymd || (card.slot && card.slot.length === 2)) return;
			const end = card.end && card.end >= card.start ? card.end : card.start;
			if (end === card.start && this.view !== "crew") return;
			if (ymd === card.start) {
				el.insertAdjacentHTML("beforeend", `<span class="p6b-grip p6b-grip-l" data-p6b-edge="start" title="${tip_start}"></span>`);
			}
			if (ymd === end) {
				el.insertAdjacentHTML("beforeend", `<span class="p6b-grip p6b-grip-r" data-p6b-edge="end" title="${tip_end}"></span>`);
			}
		});
	},

	p6b_pointer_down(e) {
		const grip = e.target && e.target.closest ? e.target.closest(".p6b-grip[data-p6b-edge]") : null;
		if (grip && !(e.button > 0)) {
			this.p6b_resize_start(e, grip);
			return;
		}
		// A touch press is remembered for the long-press menu.
		this.p6b.press =
			e.pointerType === "touch" && !(e.button > 0)
				? { id: e.pointerId, x: e.clientX, y: e.clientY, at: Date.now(), target: e.target, moved: false, opened: false }
				: null;
	},

	p6b_pointer_move(e) {
		const resize = this.p6b.resize;
		if (resize && e.pointerId === resize.id) {
			this.p6b_resize_move(e);
			return;
		}
		const press = this.p6b.press;
		if (press && e.pointerId === press.id && Math.hypot(e.clientX - press.x, e.clientY - press.y) > PP.drag_px) {
			press.moved = true;
		}
	},

	p6b_pointer_up(e) {
		const resize = this.p6b.resize;
		if (resize && e.pointerId === resize.id) {
			this.p6b_resize_end(true);
			return;
		}
		const press = this.p6b.press;
		this.p6b.press = null;
		if (!press || e.pointerId !== press.id || press.moved || press.opened) return;
		// A long press that did not become a drag: the card was lifted but never moved.
		if (Date.now() - press.at < PP6B.long_press_ms) return;
		this.p6b_open_for(press.target, { x: e.clientX, y: e.clientY }, true);
	},

	p6b_pointer_cancel(e) {
		if (this.p6b.resize && e.pointerId === this.p6b.resize.id) this.p6b_resize_end(false);
		if (this.p6b.press && e.pointerId === this.p6b.press.id) this.p6b.press = null;
	},

	p6b_resize_start(e, grip) {
		if (this.drag || this.p6b.resize || !this.data || !this.data.can_edit) return;
		const card_el = grip.closest(".pp-card[data-task]");
		const card = card_el && this.by_task[card_el.getAttribute("data-task")];
		if (!card || !card.movable || card.saving || !card.start) return;
		// Never a move: the page's own pointerdown (bubble) never sees this one.
		e.preventDefault();
		e.stopPropagation();
		const end = card.end && card.end >= card.start ? card.end : card.start;
		const label = document.createElement("div");
		label.className = "p6b-span-label";
		document.body.appendChild(label);
		this.p6b.resize = {
			id: e.pointerId,
			edge: grip.getAttribute("data-p6b-edge") === "start" ? "start" : "end",
			card,
			row: card_el.getAttribute("data-resource") || null,
			start: card.start,
			end,
			new_start: card.start,
			new_end: end,
			label,
		};
		document.body.classList.add("p6b-resizing");
		this.p6b_resize_paint(e.clientX, e.clientY);
	},

	p6b_resize_move(e) {
		const resize = this.p6b.resize;
		e.preventDefault();
		const el = document.elementFromPoint(e.clientX, e.clientY);
		const spot = el && el.closest ? el.closest(".pp-cell[data-date], .pp-day[data-date]") : null;
		if (spot && this.$grid_wrap[0].contains(spot)) {
			const ymd = spot.getAttribute("data-date");
			// At least one day: an edge never passes the other one.
			if (resize.edge === "end") resize.new_end = ymd < resize.start ? resize.start : ymd;
			else resize.new_start = ymd > resize.end ? resize.end : ymd;
		}
		this.edge_scroll(e.clientX, e.clientY);
		this.p6b_resize_paint(e.clientX, e.clientY);
	},

	// The live ghost: the days of the new span lit up (in the card's own row in the crew view) and a
	// label by the pointer.
	p6b_resize_paint(x, y) {
		const resize = this.p6b.resize;
		if (!resize) return;
		const grid = this.$grid_wrap[0];
		grid.querySelectorAll(".p6b-span").forEach((el) => el.classList.remove("p6b-span"));
		const spots = grid.querySelectorAll(this.view === "crew" ? ".pp-cell[data-date][data-resource]" : ".pp-day[data-date]");
		spots.forEach((el) => {
			if (this.view === "crew" && resize.row && el.getAttribute("data-resource") !== resize.row) return;
			const ymd = el.getAttribute("data-date");
			if (ymd >= resize.new_start && ymd <= resize.new_end) el.classList.add("p6b-span");
		});
		const days = PP6B_PURE.ymd_diff(resize.new_start, resize.new_end) + 1;
		resize.label.textContent =
			days === 1
				? __("{0} · 1 day", [pp_when(resize.new_start)])
				: __("{0} – {1} · {2} days", [pp_when(resize.new_start), pp_when(resize.new_end), days]);
		resize.label.style.left = `${Math.round(x + 14)}px`;
		resize.label.style.top = `${Math.round(y + 14)}px`;
	},

	p6b_resize_end(save) {
		const resize = this.p6b.resize;
		this.p6b.resize = null;
		if (!resize) return;
		document.body.classList.remove("p6b-resizing");
		if (resize.label) resize.label.remove();
		this.$grid_wrap[0].querySelectorAll(".p6b-span").forEach((el) => el.classList.remove("p6b-span"));
		if (!save || (resize.new_start === resize.start && resize.new_end === resize.end)) return;
		this.click_blocked_until = Date.now() + 400;
		const card = resize.card;
		const subject = card.subject || card.name;
		const message =
			resize.new_start === resize.new_end
				? __("{0} is now on {1}", [subject, pp_when(resize.new_start)])
				: __("{0} now runs {1} – {2}", [subject, pp_when(resize.new_start), pp_when(resize.new_end)]);
		// The same save as the dialog's: both dates, through send(), so a conflict asks for a reason
		// and Undo puts the old span back.
		const run = () => this.commit(card, "save_task", { task: card.name, start: resize.new_start, end: resize.new_end }, message);
		if (resize.new_start !== resize.start && resize.new_start < this.today()) {
			frappe.confirm(__("{0} is before today. Schedule the task on a past day?", [pp_when(resize.new_start)]), run);
			return;
		}
		run();
	},

	// ------------------------------------------------------------------ selection

	p6b_selectable(card) {
		return !!(card && card.movable && card.start && this.data && this.data.can_edit);
	},

	p6b_selected_cards() {
		return [...this.p6_selection].map((name) => this.by_task[name]).filter((card) => this.p6b_selectable(card));
	},

	p6b_modifier_click(e) {
		if (!(e.shiftKey || e.ctrlKey || e.metaKey) || e.button > 0 || Date.now() < this.click_blocked_until) return;
		const el = e.target && e.target.closest ? e.target.closest(".pp-card[data-task]") : null;
		if (!el || !this.$body[0].contains(el) || e.target.closest("button, a, input, select, textarea, .p6b-grip")) return;
		e.preventDefault();
		e.stopPropagation();
		this.p6b_toggle(el.getAttribute("data-task"));
	},

	p6b_toggle(name) {
		const card = this.by_task[name];
		if (!card) return;
		if (!this.p6_selection.has(name) && !this.p6b_selectable(card)) {
			frappe.show_alert(
				{ message: __("{0} cannot be moved from the planner, so it cannot be selected.", [card.subject || name]), indicator: "orange" },
				5
			);
			return;
		}
		PP6B_PURE.toggle(this.p6_selection, name);
		this.p6b_selection_changed();
	},

	// A plain click on empty space clears the selection.
	p6b_plain_click(e) {
		if (!this.p6_selection.size || e.shiftKey || e.ctrlKey || e.metaKey) return;
		const el = e.target;
		if (!el || !el.closest) return;
		if (el.closest(".pp-card, .pp-fcard, .p6b-bar, .pp-toolbar, .pp-draft-bar, button, a, input, select, textarea, label")) return;
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
		[...this.p6_selection].forEach((name) => {
			if (!this.p6b_selectable(this.by_task[name])) {
				this.p6_selection.delete(name);
				changed = true;
			}
		});
		if (changed) $(document).trigger("p6-selection-changed", [this]);
	},

	p6b_decorate_selection() {
		this.$body[0].querySelectorAll(".pp-card[data-task]").forEach((el) => {
			el.classList.toggle("p6b-selected", this.p6_selection.has(el.getAttribute("data-task")));
		});
	},

	p6b_render_bar() {
		const $bar = this.$p6b_bar;
		if (!$bar) return;
		const cards = this.p6b_selected_cards();
		if (!cards.length || !PP.views.includes(this.view)) {
			$bar.hide().empty();
			return;
		}
		$bar.empty().show();
		$('<span class="p6b-bar-count"></span>').text(__("{0} selected", [cards.length])).appendTo($bar);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(__("Move…"))
			.on("click", () => this.p6b_move_panel())
			.appendTo($bar);
		const all_pencil = cards.every((card) => card.tentative);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(all_pencil ? __("Firm up") : __("Pencil"))
			.attr("title", all_pencil ? __("Make them all firm bookings") : __("Make them all tentative"))
			.on("click", () => this.p6b_pencil_many(all_pencil ? 0 : 1))
			.appendTo($bar);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(__("Clear"))
			.on("click", () => this.p6b_clear_selection())
			.appendTo($bar);
		$('<span class="p6b-bar-note"></span>')
			.text(
				__("Drag any of them and they all move by the same number of calendar days, each keeping its length. Esc clears the selection.")
			)
			.appendTo($bar);
	},

	p6b_lift(drag) {
		if (!drag || !drag.ghost || !drag.source || drag.source.kind !== "card") return;
		if (!this.p6_selection.has(drag.source.card.name)) return;
		const count = this.p6b_selected_cards().length;
		if (count < 2) return;
		const badge = document.createElement("span");
		badge.className = "p6b-count";
		badge.textContent = String(count);
		drag.ghost.appendChild(badge);
	},

	// A drop of one selected card when several are selected: they all move by the same calendar days.
	// True when it was handled here (drop() then does nothing more).
	p6b_drop(source, target) {
		if (!source || source.kind !== "card" || !source.card || this.p6_selection.size < 2) return false;
		if (!this.p6_selection.has(source.card.name) || this.p6b_selected_cards().length < 2) return false;
		if (target.resource && source.from_resource && target.resource !== source.from_resource) {
			frappe.show_alert(
				{
					message: __(
						"Several tasks are selected: drop them on the same person's row to change their days. To hand one task to someone else, clear the selection first."
					),
					indicator: "orange",
				},
				8
			);
			return true;
		}
		const from = source.from_date || source.card.start;
		if (!from || !target.date) return true;
		const days = PP6B_PURE.ymd_diff(from, target.date);
		if (days) this.p6b_move_selection(days);
		return true;
	},

	p6b_move_panel() {
		const cards = this.p6b_selected_cards();
		if (!cards.length) return;
		const panel = this.p6b_panel({
			title: __("Move {0} tasks", [cards.length]),
			key: "p6b-move",
			fields: [
				{
					fieldtype: "Int",
					fieldname: "days",
					label: __("Move by (days)"),
					reqd: 1,
					default: 7,
					description: __("Calendar days; a negative number moves them earlier. Each task keeps its length and time of day."),
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

	p6b_move_selection(days) {
		const cards = this.p6b_selected_cards();
		if (!cards.length || !days) return null;
		const moves = PP6B_PURE.offset_moves(cards, days, (name) => this.modified[name] || (this.by_task[name] || {}).modified);
		const snapshot = {
			p6b: "many",
			what: "dates",
			subject: __("{0} tasks", [cards.length]),
			items: cards.map((card) => ({ task: card.name, start: card.start, end: card.end || card.start })),
		};
		const message =
			days > 0
				? __("{0} tasks moved {1} day(s) later", [cards.length, days])
				: __("{0} tasks moved {1} day(s) earlier", [cards.length, -days]);
		const run = () => this.p6b_commit_many(cards, moves, snapshot, message);
		if (moves.some((move) => move.start < this.today())) {
			frappe.confirm(__("Some of these tasks would start before today. Move them anyway?"), run);
			return null;
		}
		return run();
	},

	p6b_pencil_many(on) {
		const cards = this.p6b_selected_cards().filter((card) => (card.tentative ? 1 : 0) !== on);
		if (!cards.length) return null;
		const moves = cards.map((card) => ({ task: card.name, modified: this.modified[card.name] || card.modified, tentative: on }));
		const snapshot = {
			p6b: "many",
			what: "pencil",
			subject: __("{0} tasks", [cards.length]),
			items: cards.map((card) => ({ task: card.name, tentative: card.tentative ? 1 : 0 })),
		};
		const message = on ? __("{0} tasks pencilled in", [cards.length]) : __("{0} tasks firmed up", [cards.length]);
		return this.p6b_commit_many(cards, moves, snapshot, message);
	},

	// One move_many through send(): one reason prompt for the lot, all or nothing, one Undo entry.
	p6b_commit_many(cards, moves, snapshot, message) {
		cards.forEach((card) => (card.saving = true));
		this.render();
		return this.p6b_send("move_many", { moves: JSON.stringify(moves), draft: this.draft_on ? 1 : 0 }, { snapshot, message })
			.then((result) => {
				((result && result.moved) || []).forEach((move) => {
					if (move.modified) this.modified[move.task] = move.modified;
				});
				return result;
			})
			.finally(() => this.load());
	},

	// ------------------------------------------------------------------ undo

	// Undo of a Phase 6B change. False for any other entry, which undo() handles as before.
	p6b_undo(snap) {
		if (!snap || !snap.p6b) return false;
		const message = __("Undone: {0}", [snap.subject]);
		if (snap.p6b === "many") {
			const moves = (snap.items || []).map((item) => {
				const move = { task: item.task, modified: this.modified[item.task] || (this.by_task[item.task] || {}).modified || "" };
				if (snap.what === "pencil") move.tentative = item.tentative;
				else Object.assign(move, { start: item.start, end: item.end });
				return move;
			});
			this.p6b_send(
				"move_many",
				{ moves: JSON.stringify(moves), draft: this.draft_on ? 1 : 0 },
				{ message, auto_reason: __(PP.undo_reason) }
			).finally(() => this.load());
			return true;
		}
		// A task created here (a duplicate, a quick add, a split's second half): delete it again, and
		// put a split's first half back. The server refuses when anything was attached since.
		const args = { task: snap.task, modified: this.modified[snap.task] || snap.modified };
		if (snap.restore) {
			const first = snap.restore.task;
			args.restore = JSON.stringify(Object.assign({}, snap.restore, { modified: this.modified[first] || snap.restore.modified }));
		}
		this.p6b_send("remove_created_task", args, { message }).finally(() => this.load());
		return true;
	},

	// ------------------------------------------------------------------ search

	p6b_build_search() {
		const $wrap = $('<span class="p6b-search-wrap"></span>');
		const $seg = this.$toolbar.children(".pp-seg").first();
		if ($seg.length) $wrap.insertAfter($seg);
		else $wrap.appendTo(this.$toolbar);
		const list_id = `p6b-results-${Math.random().toString(36).slice(2, 9)}`;
		this.p6b.search.list_id = list_id;
		this.$p6b_search = $('<input type="search" class="form-control input-sm p6b-search" autocomplete="off" spellcheck="false">')
			.attr({
				placeholder: __("Search  ( / )"),
				title: __("Find a task, project or person. Press / to come here."),
				"aria-label": __("Search tasks, projects and people"),
				role: "combobox",
				"aria-autocomplete": "list",
				"aria-expanded": "false",
				"aria-controls": list_id,
			})
			.appendTo($wrap);
		this.$p6b_results = $('<div class="p6b-results" role="listbox"></div>').attr("id", list_id).hide().appendTo($wrap);
		this.$p6b_search.on("input", () => this.p6b_search_input());
		this.$p6b_search.on("keydown", (e) => this.p6b_search_key(e));
		this.$p6b_search.on("focus", () => {
			if (this.p6b.search.q.length >= PP6B.search_min) this.p6b_render_results();
		});
		this.$p6b_search.on("blur", () => setTimeout(() => this.p6b_close_results(), 150));
		// Keep the focus in the box while a result is clicked.
		this.$p6b_results.on("mousedown", (e) => e.preventDefault());
		this.$p6b_results.on("click", "[data-p6b-result]", (e) => {
			const item = this.p6b.search.shown[Number(e.currentTarget.getAttribute("data-p6b-result"))];
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
		if (search.q.length < PP6B.search_min) {
			search.local = [];
			search.loading = false;
			this.p6b_close_results();
			return;
		}
		search.local = PP6B_PURE.search_local(search.q, this.p6b_local_entries(), PP6B.search_limit);
		search.loading = true;
		this.p6b_render_results();
		const q = search.q;
		search.timer = setTimeout(() => this.p6b_remote_search(q, token), PP6B.search_wait_ms);
	},

	// What is on screen: every task card, the projects of the filter and the people.
	p6b_local_entries() {
		const out = [];
		Object.values(this.by_task || {}).forEach((card) => {
			out.push({
				kind: "task",
				name: card.name,
				label: card.subject || card.name,
				keys: [card.name, card.project_title || "", card.project || ""],
				sub: [card.project_title || card.project, this.p6b_span_text(card.start, card.end)].filter(Boolean).join(" · "),
				start: card.start || null,
				end: card.end || null,
			});
		});
		((this.data && this.data.projects) || []).forEach((project) => {
			out.push({
				kind: "project",
				name: project.name,
				label: project.title || project.name,
				keys: [project.name],
				sub: project.title && project.title !== project.name ? project.name : "",
			});
		});
		((this.data && this.data.resources) || []).forEach((person) => {
			out.push({
				kind: "person",
				resource: person.name,
				user: person.user || null,
				label: person.label || person.name,
				keys: [],
				sub: person.group ? __(person.group) : "",
			});
		});
		return out;
	},

	p6b_result_key(item) {
		return `${item.kind}|${item.name || item.resource || ""}`;
	},

	p6b_from_server(item) {
		if (!item || !item.kind) return null;
		if (item.kind === "task") {
			return {
				kind: "task",
				name: item.name,
				label: item.label || item.name,
				sub: [item.project_title || item.project, this.p6b_span_text(item.start, item.end)].filter(Boolean).join(" · "),
				start: item.start || null,
				end: item.end || null,
			};
		}
		if (item.kind === "project") {
			return { kind: "project", name: item.name, label: item.label || item.name, sub: [item.name, item.status ? __(item.status) : ""].filter(Boolean).join(" · ") };
		}
		if (item.kind === "person") {
			return { kind: "person", resource: item.resource, user: item.user || null, label: item.label || item.resource, sub: item.group ? __(item.group) : "" };
		}
		return null;
	},

	// Tasks outside the range on screen (and projects or people the page has not loaded) come from
	// the server; what the page already listed is not repeated.
	p6b_remote_search(q, token) {
		return Promise.resolve(frappe.call({ method: `${PP6B.actions}.search_planner`, args: { q, start: this.anchor } }))
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
		if (search.q.length < PP6B.search_min) {
			this.p6b_close_results();
			return;
		}
		const shown = search.local.concat(search.remote).slice(0, PP6B.search_limit);
		search.shown = shown;
		if (search.active >= shown.length) search.active = 0;
		const kinds = { task: __("Task"), project: __("Project"), person: __("Person") };
		const rows = shown.map(
			(item, index) =>
				`<div class="p6b-result${index === search.active ? " p6b-active" : ""}" role="option" id="${pp_esc(
					`${search.list_id}-${index}`
				)}" aria-selected="${index === search.active ? "true" : "false"}" data-p6b-result="${index}">` +
				`<span class="p6b-result-kind">${pp_esc(kinds[item.kind] || item.kind)}</span>` +
				`<span class="p6b-result-label">${pp_esc(item.label)}</span>` +
				(item.sub ? `<span class="p6b-result-sub">${pp_esc(item.sub)}</span>` : "") +
				`</div>`
		);
		const note = search.loading ? __("Searching the rest…") : shown.length ? "" : __("Nothing found.");
		this.$p6b_results.html(rows.join("") + (note ? `<div class="p6b-results-note">${pp_esc(note)}</div>` : "")).show();
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
			// The box's own Esc: it never reaches the drawer or frappe behind it.
			e.preventDefault();
			e.stopPropagation();
			if (this.$p6b_results.is(":visible")) this.p6b_close_results();
			else this.$p6b_search.val("").trigger("blur");
		}
	},

	p6b_pick(item) {
		this.p6b_close_results();
		this.$p6b_search.trigger("blur");
		if (item.kind === "person") this.p6a_open_person(item.resource);
		else if (item.kind === "project") this.p6a_open_project(item.name);
		else if (item.kind === "task") this.p6b_jump_to_task(item);
	},

	// A task: the calendar goes to its week (a real route, so Back comes back) and the card flashes.
	p6b_jump_to_task(item) {
		const card = this.by_task[item.name];
		const start = item.start || (card && card.start) || null;
		if (!start) {
			if (card && PP.views.includes(this.view)) {
				this.p6b.flash = { name: card.name, date: null, wait: false };
				this.p6b_flash_pending();
				return;
			}
			this.p6b_navigate(() => frappe.set_route("Form", "Task", item.name));
			return;
		}
		this.p6b.flash = { name: item.name, date: start, wait: true };
		if (PP.views.includes(this.view) && this.range_days().includes(start)) {
			this.p6b_flash_pending();
			return;
		}
		this.go(this.view === "week" || this.view === "crew" ? this.view : "week", start);
	},

	p6b_flash_pending() {
		const flash = this.p6b.flash;
		if (!flash || !this.data || !PP.views.includes(this.view)) return;
		if (flash.date && !(this.data.start <= flash.date && flash.date <= this.data.end)) {
			// A jump waits for its week to load; a new task drawn elsewhere is simply not flashed.
			if (!flash.wait) this.p6b.flash = null;
			return;
		}
		this.p6b.flash = null;
		const el = Array.from(this.$body[0].querySelectorAll(".pp-card[data-task]")).find(
			(node) => node.getAttribute("data-task") === flash.name
		);
		if (!el) {
			if (flash.wait) {
				frappe.show_alert({ message: __("{0} is hidden by the filters on screen.", [flash.name]), indicator: "orange" }, 6);
			}
			return;
		}
		el.classList.add("p6b-flash");
		try {
			el.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
		} catch (e) {
			el.scrollIntoView();
		}
		setTimeout(() => el.classList.remove("p6b-flash"), PP6B.flash_ms);
	},

	// ------------------------------------------------------------------ keyboard

	// One keydown listener on the document (capture, so frappe's window handler does not also act on
	// a key used here), added while the page shows and removed when it hides. frappe.ui.keys does not
	// fit: its "shift+/" handler is global and would open frappe's own shortcut list next to ours.
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
		if (route[0] !== PP.route) return false;
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
		const action = PP6B_PURE.shortcut({
			key: e.key,
			ctrl: e.ctrlKey,
			meta: e.metaKey,
			shift: e.shiftKey,
			alt: e.altKey,
			editable,
			dialog: !!(window.cur_dialog && window.cur_dialog.display),
		});
		if (!action) return;
		// A menu's own keys (arrows, Enter, Esc) are the menu's.
		if (target && target.closest && target.closest(".pk-menu")) return;
		if (action === "escape") {
			// Never swallowed: the kit closes its overlay and frappe blurs as usual.
			this.p6b_escape();
			return;
		}
		if (!this.p6b_run_key(action)) return;
		e.preventDefault();
		e.stopPropagation();
	},

	p6b_run_key(action) {
		// The route view and My week keep their own buttons.
		if (!PP.views.includes(this.view) && this.view !== PP.heatmap_view) return false;
		if (action === "today") {
			this.go(this.view, frappe.datetime.get_today());
			return true;
		}
		if (action === "prev" || action === "next") {
			this.shift(action === "prev" ? -1 : 1);
			return true;
		}
		if (action.startsWith("view")) {
			const view = PP.views[Number(action.slice(4)) - 1];
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
		if (this.p6b.resize) {
			this.p6b_resize_end(false);
			return;
		}
		const kit = this.p6b_kit();
		// An open menu or drawer closes first; the selection stays.
		if (kit && kit.guard && typeof kit.guard.top === "function" && kit.guard.top()) return;
		this.p6b_clear_selection();
	},

	// The legend's Keyboard and Faster scheduling sections, then any other phase's (p6_legend_providers).
	p6b_legend_sections() {
		const key = (text) => `<kbd class="p6b-kbd">${pp_esc(text)}</kbd>`;
		const item = (sample_html, text) => ({ sample_html, text: __(text) });
		const own = [
			{
				title: __("Keyboard"),
				note: __("Not while typing in a box or with a dialog open."),
				items: [
					item(key("T"), "Go to today."),
					item(key("←") + key("→"), "The previous or next week (month, or four weeks of the heatmap)."),
					item(key("1") + key("2") + key("3"), "Week, month or crew view."),
					item(key("Ctrl") + key("Z"), "Undo the last change (⌘Z on a Mac)."),
					item(key("/"), "Search tasks, projects and people."),
					item(key("?"), "This help."),
					item(key("Esc"), "Close a menu or side panel, or clear the selection."),
				],
			},
			{
				title: __("Faster scheduling"),
				items: [
					item("", "Right-click a card, an empty spot or a name (on a touch screen, hold it still for a moment) for a menu: assign someone, move to the next free day, pencil, duplicate, split."),
					item(
						`<span class="pp-p6a-sample-card p6b-selected">${pp_esc(__("Dig"))}</span>`,
						"Shift-click (or Ctrl- or ⌘-click) cards to select several. Drag one and they all move by the same number of calendar days."
					),
					item('<span class="p6b-legend-grip"></span>', "Drag a card's left or right edge to change its first or last day (week and crew views)."),
					item("", "Double-click an empty spot to add a task for that person and day."),
					item("", "Assign to… lists who is free that day. Nobody is picked for you."),
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

Object.assign(ProjectPlanner.prototype, PP6B_METHODS);

// ====================================================================== Phase 6C: big picture, tablet mode and print
//
// TASK-2026-02469. Nik picked all six on 2026-10-09; each is on both planners (the Maintenance Planner
// has the same block, MP6C_METHODS):
//
//   - Color by (toolbar)                     the cards' left edge by Task color (today's look, the
//                                            default), Person (the lead, or the row in the crew view),
//                                            Job type, Project, Project manager or Status; a key under
//                                            the toolbar and in the "?" legend. Job type and status
//                                            use fixed palettes; project and PM a stable hash
//                                            (planner_kit.big_picture), so a project is always the
//                                            same color on every device; a person their own color,
//                                            as on the dots and crew badges
//   - click a project's name on a card, "Highlight" in the project drawer, "Highlight this project"
//     in a task's panel or the right-click menu (6B's p6_menu_providers)
//                                            every card of that project lights up and the rest fade,
//                                            in every view and week; a toolbar pill and Esc clear it.
//                                            Not remembered: a reload starts clear
//   - Views (toolbar)                        named views ("Field crew, Build only") saved on the
//                                            server per person (api/planner_views_prefs), switch and
//                                            delete; the view last used is saved a moment after any
//                                            change and restored when the planner opens, on any
//                                            device. The route always wins: only a bare
//                                            /project-planner takes the saved view, and it replaces
//                                            that history entry (route_flags.replace_route), so
//                                            Back never lands on a second copy
//   - the team strip                         over the week and crew views: per day the free hours of
//                                            everyone the group filter shows, amber at 90% booked,
//                                            red past capacity, pencil hatched; click a day for
//                                            everyone's day (6A's day peek)
//   - Tap to move (toolbar; on by default on a touch screen)
//                                            tap a card, then the day (or a person's day in the crew
//                                            view): the move goes through drop -> plan_drop ->
//                                            commit -> send, exactly like a drag, so the reason
//                                            prompt, the past-day question and Undo all apply. A tap
//                                            on a card with it off still opens the card
//   - Print (toolbar)                        exactly what is on screen (view, range, filters, colors,
//                                            highlight), or only 6B's selection, built from the
//                                            loaded data (never the live DOM) on the print design
//                                            system's chrome, printed from a browser window
//
// Everything Phase 6C adds is in this block, mixed into ProjectPlanner below. The hooks into the
// class and the blocks above are one line each: init_phase6c (constructor), p6c_before_route
// (handle_route), p6c_set_mode (set_mode), render_phase6c (render), p6c_links and p6c_action
// (open_card), p6c_ready (p6a_ready), p6c_project_drawer (p6a_open_project) and p6c_legend_sections
// (p6a_legend).

const PP6C = {
	prefs: "erpnext_enhancements.api.planner_views_prefs",
	planner: "project",
	default_view: "week",
	tap_pref: "ee_project_planner_tap_move",
	save_delay: 1500,
	restore_wait: 4000,
	key_limit: 12,
	legend_limit: 40,
	// What a saved view may hold: the same rules as api/planner_views_prefs.SCHEMA["project"].
	schema: {
		view: ["enum", ["week", "month", "crew", "heatmap"]],
		project: ["text"],
		pm: ["text"],
		group: ["enum", ["", "Field", "PM", "Design", "Subcontractor"]],
		foreign: ["bool"],
		over_only: ["bool"],
		panel: ["bool"],
		color_by: ["enum", ["default", "person", "job_type", "project", "pm", "status"]],
	},
	// Phase 4's Running over chip is never restored on its own the next morning (a named view may hold it).
	not_remembered: ["over_only"],
	color_modes: [
		["default", "Task color"],
		["person", "Person"],
		["job_type", "Job type"],
		["project", "Project"],
		["pm", "Project manager"],
		["status", "Status"],
	],
	view_labels: { week: "Week", month: "Month", crew: "Crew", heatmap: "Heatmap" },
	none_color: "#94a3b8",
};

const PP6C_STYLE = `
.pp-p6c-color{max-width:190px;}
.pp-p6c-pill{display:inline-flex;align-items:center;gap:4px;max-width:340px;border:1px solid var(--primary,#2490ef);border-radius:14px;padding:1px 2px 1px 10px;font-size:12px;background:rgba(36,144,239,.08);color:var(--text-color);}
.pp-p6c-pill-text{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-p6c-pill-x{border:none;background:none;color:inherit;font-size:15px;line-height:1;padding:2px 7px;cursor:pointer;border-radius:10px;}
.pp-p6c-pill-x:hover,.pp-p6c-pill-x:focus{background:var(--control-bg);outline:none;}
.pp-p6c-tap.pp-p6c-on{background:rgba(36,144,239,.12);border-color:var(--primary,#2490ef);color:var(--primary,#2490ef);font-weight:600;}
.pp-p6c-key{display:flex;flex-wrap:wrap;align-items:center;gap:3px 12px;font-size:12px;color:var(--text-muted);margin:-2px 0 8px;}
.pp-p6c-key-title{font-weight:600;}
.pp-p6c-key-item{display:inline-flex;align-items:center;gap:5px;max-width:240px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-p6c-key-item[data-p6c-hi]{cursor:pointer;border-radius:6px;}
.pp-p6c-key-item[data-p6c-hi]:hover{color:var(--text-color);text-decoration:underline;}
.pp-p6c-swatch{flex:0 0 auto;display:inline-block;width:16px;height:11px;border:1px solid var(--border-color);border-left-width:4px;border-radius:3px;background:var(--card-bg);}
.pp-card.pp-p6c-colored{border-left-color:var(--pp-p6c-c) !important;}
.pp-p6c-on .pp-card:not(.pp-p6c-hi),.pp-p6c-on .pp-fcard,.pp-p6c-on .pp-ecard{opacity:.22;}
.pp-p6c-on .pp-card.pp-p6c-hi{box-shadow:0 0 0 2px var(--primary,#2490ef),0 0 10px rgba(36,144,239,.45);}
.pp-p6c-strip{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));border:1px solid var(--border-color);border-radius:10px;background:var(--card-bg);margin-bottom:6px;overflow:hidden;}
.pp-p6c-sday{display:flex;flex-direction:column;gap:2px;min-width:0;padding:3px 6px;font-size:11px;cursor:pointer;border-left:1px solid var(--border-color);}
.pp-p6c-strip .pp-p6c-sday:first-child{border-left:none;}
.pp-p6c-sday:hover,.pp-p6c-sday:focus{background-color:var(--control-bg);outline:none;}
.pp-p6c-slabel{font-size:10px;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-p6c-stext{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-p6c-sday.pp-red{background-color:rgba(220,38,38,.10);}
.pp-p6c-sday.pp-red .pp-p6c-stext{color:#b91c1c;}
.pp-p6c-sday.pp-amber .pp-p6c-stext{color:#b45309;}
.pp-p6c-scorner{display:flex;flex-direction:column;justify-content:center;gap:1px;padding:3px 8px;font-size:11px;min-width:0;position:sticky;left:0;background:var(--card-bg);z-index:1;}
.pp-p6c-scorner span{font-size:10px;color:var(--text-muted);}
.pp-crew > .pp-p6c-sday{border-left:1px solid var(--border-color);}
.pp-p6c-movebar{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:1024;display:flex;flex-wrap:wrap;align-items:center;gap:8px;max-width:min(640px,calc(100vw - 24px));padding:8px 10px 8px 14px;border:1px solid var(--primary,#2490ef);border-radius:10px;background:var(--card-bg);color:var(--text-color);box-shadow:0 10px 28px rgba(0,0,0,.22);font-size:13px;}
.pp-p6c-movebar-text{flex:1 1 220px;min-width:0;}
.pp-p6c-movebar-hint{display:block;font-size:12px;color:var(--text-muted);}
.pp-card.pp-p6c-moving,.pp-od-card.pp-p6c-moving{outline:2px dashed var(--primary,#2490ef);outline-offset:1px;}
.pp-p6c-armed .pp-day,.pp-p6c-armed .pp-cell{cursor:copy;}
.pp-p6c-views-list{list-style:none;margin:0;padding:0;}
.pp-p6c-views-row{display:flex;align-items:center;gap:8px;padding:7px 0;border-top:1px solid var(--border-color);}
.pp-p6c-views-row:first-child{border-top:none;}
.pp-p6c-views-main{flex:1 1 auto;min-width:0;}
.pp-p6c-views-name{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pp-p6c-views-sub{font-size:12px;color:var(--text-muted);}
@media (pointer:coarse){
.pp-toolbar .btn,.pp-toolbar select,.pp-toolbar .pp-toggle,.pp-toolbar label,.pp-seg button{min-height:40px;}
.pp-week .pp-card,.pp-crew .pp-card,.pp-tray .pp-card{min-height:40px;}
.pp-day-num{display:inline-flex;align-items:center;min-height:32px;padding:0 10px;}
.pp-p6c-sday{min-height:40px;}
.pp-p6c-pill-x{min-width:40px;min-height:36px;}
.pp-p6c-movebar .btn{min-height:40px;min-width:64px;}
.pp-p6c-views-row .btn{min-height:40px;}
}
@media (max-width:1024px){
.pp-wrap{max-width:100%;overflow-x:clip;}
}
@media (max-width:760px){
.pp-p6c-pill{max-width:100%;}
.pp-p6c-movebar{left:12px;right:12px;bottom:12px;transform:none;max-width:none;}
.pp-p6c-sday{padding:2px 3px;}
.pp-p6c-key-item{max-width:160px;}
}
`;

const PP6C_METHODS = {
	init_phase6c() {
		if (!document.getElementById("pp-style-6c")) {
			$("<style id='pp-style-6c'>").text(PP6C_STYLE).appendTo(document.head);
		}
		this.p6c = {
			color_by: "default",
			highlight: null,
			moving: null,
			tap: this.p6c_tap_default(),
			views: [],
			last: null,
			loaded: false,
			loading: null,
			waiting: false,
			restored: false,
			saved_text: null,
			save_timer: null,
			drawer_tool: null,
		};
		// 6B's right-click menu asks every provider for items; on this branch alone nobody does, and
		// each action here has its own way in as well (a project name, the panel's link, the toolbar).
		this.p6_menu_providers = this.p6_menu_providers || [];
		this.p6_menu_providers.push((target) => this.p6c_menu_items(target));

		this.$p6c_pill = $('<span class="pp-p6c-pill" role="status"></span>').hide().insertAfter(this.$title);
		$('<span class="pp-p6c-pill-text"></span>').appendTo(this.$p6c_pill);
		$('<button type="button" class="pp-p6c-pill-x">×</button>')
			.attr("title", __("Stop highlighting (Esc)"))
			.attr("aria-label", __("Stop highlighting"))
			.on("click", () => this.p6c_clear_highlight())
			.appendTo(this.$p6c_pill);
		this.$p6c_color = this.make_select(this.$toolbar, __("Color by"), (value) => this.p6c_set_color(value))
			.addClass("pp-p6c-color")
			.insertAfter(this.$group);
		PP6C.color_modes.forEach(([value, label]) => {
			$("<option></option>").val(value).text(__("Color: {0}", [__(label)])).appendTo(this.$p6c_color);
		});
		this.$p6c_tap = $('<button type="button" class="pp-toggle pp-p6c-tap" aria-pressed="false"></button>')
			.text(__("Tap to move"))
			.attr("title", __("Tap a card, then tap the day where it goes, instead of dragging"))
			.on("click", () => this.p6c_set_tap(!this.p6c.tap))
			.insertBefore(this.$undo);
		this.$p6c_views = $('<button type="button" class="btn btn-default btn-sm pp-p6c-views"></button>')
			.text(__("Views"))
			.attr("title", __("Your saved views: filters, view and colors"))
			.on("click", (e) => this.p6c_views_menu(e.currentTarget))
			.insertBefore(this.$undo);
		this.$p6c_print = $('<button type="button" class="btn btn-default btn-sm pp-p6c-print"></button>')
			.text(__("Print"))
			.attr("title", __("Print exactly what is on screen, with your filters and colors"))
			.on("click", (e) => this.p6c_print_click(e.currentTarget))
			.insertBefore(this.$undo);
		this.$p6c_key = $('<div class="pp-p6c-key"></div>').hide().insertAfter(this.$toolbar);
		this.$p6c_movebar = $('<div class="pp-p6c-movebar" role="status"></div>').hide().appendTo(this.$body);

		// Capture on the document: a tap that moves a card is taken before 6A's quick looks and the
		// card's own click see it.
		document.addEventListener("click", (e) => this.p6c_tap_click(e), true);
		window.addEventListener("keydown", (e) => this.p6c_keydown(e), true);
		// After 6A's capture listener on the same element: a project's name on a card highlights it
		// (6A opens its drawer from the same click).
		this.$body[0].addEventListener("click", (e) => this.p6c_body_click(e), true);
		this.$body.on("change click", () => this.p6c_state_soon());
		this.p6c_sync_controls();
		this.p6c_fetch_views();
	},

	// The kit has loaded (6A's p6a_ready): paint what needed it.
	p6c_ready() {
		if (this.p6c && this.data && PP.views.includes(this.view)) this.render_phase6c();
	},

	render_phase6c() {
		if (!this.p6c || !this.data || !PP.views.includes(this.view)) return;
		this.p6c_sync_controls();
		this.p6c_paint();
		this.p6c_apply_highlight();
		this.p6c_render_strip();
		this.p6c_render_key();
		this.p6c_mark_moving();
		this.p6c_state_soon();
	},

	// The route, heatmap and My week views have no cards to color, highlight or move.
	p6c_set_mode(mode) {
		if (!this.p6c) return;
		const calendar = mode === "";
		[this.$p6c_color, this.$p6c_tap].forEach(($el) => $el && $el.toggle(calendar));
		[this.$p6c_views, this.$p6c_print].forEach(($el) => $el && $el.toggle(calendar || mode === PP.heatmap_view));
		if (!calendar) {
			this.p6c_disarm();
			if (this.$p6c_key) this.$p6c_key.hide();
			if (this.$p6c_pill) this.$p6c_pill.hide();
		}
		this.p6c_state_soon();
	},

	p6c_kit() {
		const kit = this.p6a && this.p6a.kit;
		return kit && kit.big_picture ? kit : null;
	},

	p6c_pick(state) {
		const kit = this.p6c_kit();
		if (kit) return kit.big_picture.pick_view(state, PP6C.schema);
		// Before the kit: the keys, unchecked (the server has already cleaned what it sends).
		const out = {};
		Object.keys(PP6C.schema).forEach((key) => {
			if (state && Object.prototype.hasOwnProperty.call(state, key)) out[key] = state[key];
		});
		return out;
	},

	p6c_here() {
		return (frappe.get_route() || [])[0] === PP.route && this.$body.is(":visible");
	},

	// ------------------------------------------------------------------ controls

	p6c_sync_controls() {
		const p6c = this.p6c;
		if (!p6c) return;
		// A value whose option is not there yet (before the first load) is picked up by fill_filters.
		if (this.$project) this.$project.val(this.project || "");
		if (this.$pm) this.$pm.val(this.pm || "");
		if (this.$group) this.$group.val(PP.groups.includes(this.group) ? this.group : "");
		if (this.$foreign) this.$foreign.find("input").prop("checked", !!this.show_foreign);
		if (this.$p6c_color) this.$p6c_color.val(p6c.color_by);
		if (this.$p6c_tap) {
			this.$p6c_tap.toggleClass("pp-p6c-on", !!p6c.tap).attr("aria-pressed", p6c.tap ? "true" : "false");
		}
		if (this.$p6c_pill) {
			const on = !!p6c.highlight && PP.views.includes(this.view);
			this.$p6c_pill.toggle(on);
			if (on) {
				const text = __("Highlighting {0}", [p6c.highlight.label || p6c.highlight.key]);
				this.$p6c_pill.find(".pp-p6c-pill-text").text(text).attr("title", text);
			}
		}
	},

	// ------------------------------------------------------------------ color by

	p6c_set_color(mode) {
		const known = PP6C.color_modes.map(([value]) => value);
		this.p6c.color_by = known.includes(mode) ? mode : "default";
		this.render();
	},

	p6c_mode_label(mode) {
		const found = PP6C.color_modes.find(([value]) => value === mode);
		return __(found ? found[1] : "Task color");
	},

	p6c_lead(card) {
		const crew = card.crew || [];
		return crew.find((member) => member.is_lead) || crew[0] || null;
	},

	// The color of a card in the current mode; null in the default mode (the card keeps its own).
	// `row` is the crew-view row the card sits in.
	p6c_card_color(card, row) {
		const kit = this.p6c_kit();
		const mode = this.p6c.color_by;
		if (!kit || mode === "default" || !card) return null;
		const bp = kit.big_picture;
		if (mode === "person") {
			const lead = row || (this.p6c_lead(card) || {}).resource;
			return lead ? this.resource_color(lead) : PP6C.none_color;
		}
		if (mode === "job_type") return bp.palette_color(bp.JOB_TYPES, bp.job_type(card.project_type, card.rental_kind));
		if (mode === "project") return card.project ? bp.hash_color(card.project) : PP6C.none_color;
		if (mode === "pm") {
			const project = card.project && this.by_project[card.project];
			return project && project.pm ? bp.hash_color(project.pm) : PP6C.none_color;
		}
		if (mode === "status") return bp.palette_color(bp.TASK_STATUSES, bp.task_status(card.status, card.overdue));
		return null;
	},

	p6c_paint() {
		const kit = this.p6c_kit();
		if (!kit || this.p6c.color_by === "default") return;
		this.$body[0].querySelectorAll(".pp-card[data-task]").forEach((el) => {
			const card = this.by_task[el.getAttribute("data-task")];
			const color = kit.big_picture.safe_paint(this.p6c_card_color(card, el.getAttribute("data-resource")), "");
			if (!color) return;
			el.style.setProperty("--pp-p6c-c", color);
			el.classList.add("pp-p6c-colored");
		});
	},

	// The cards a key or a legend describes: the ones the filters let through.
	p6c_visible_cards() {
		if (!this.data) return [];
		return [].concat(this.data.tasks || [], this.data.unscheduled || []).filter((card) => this.task_visible(card));
	},

	// What the current colors mean: [{key, label, color, hi}] (hi: a project a click can highlight).
	p6c_legend_entries() {
		const kit = this.p6c_kit();
		const mode = this.p6c.color_by;
		if (!kit || mode === "default") return [];
		const bp = kit.big_picture;
		if (mode === "job_type") return bp.palette_legend(bp.JOB_TYPES);
		if (mode === "status") return bp.palette_legend(bp.TASK_STATUSES);
		if (mode === "person") {
			return this.resources().map((resource) => ({
				key: resource.name,
				label: resource.label || resource.name,
				color: this.resource_color(resource.name),
			}));
		}
		const seen = {};
		let none = false;
		this.p6c_visible_cards().forEach((card) => {
			if (mode === "project") {
				if (!card.project) none = true;
				else if (!seen[card.project]) {
					seen[card.project] = {
						key: card.project,
						label: card.project_title && card.project_title !== card.project ? card.project_title : card.project,
						color: bp.hash_color(card.project),
						hi: card.project,
					};
				}
				return;
			}
			const project = card.project && this.by_project[card.project];
			if (!project || !project.pm) none = true;
			else if (!seen[project.pm]) seen[project.pm] = { key: project.pm, label: project.pm, color: bp.hash_color(project.pm) };
		});
		const out = Object.values(seen).sort((a, b) => String(a.label).localeCompare(String(b.label)));
		if (none) {
			out.push({
				key: "",
				label: mode === "project" ? __("No project") : __("No project manager"),
				color: PP6C.none_color,
			});
		}
		return out;
	},

	p6c_swatch_html(color) {
		const kit = this.p6c_kit();
		const paint = kit ? kit.big_picture.safe_paint(color, PP6C.none_color) : PP6C.none_color;
		return `<i class="pp-p6c-swatch" style="border-left-color:${pp_esc(paint)}"></i>`;
	},

	p6c_render_key() {
		const $key = this.$p6c_key;
		if (!$key) return;
		const entries = this.p6c_legend_entries();
		if (!entries.length || !PP.views.includes(this.view)) {
			$key.hide().empty();
			return;
		}
		const shown = entries.slice(0, PP6C.key_limit);
		const items = shown.map((entry) => {
			const hi = entry.hi ? ` data-p6c-hi="${pp_esc(entry.hi)}" role="button" tabindex="0"` : "";
			const tip = entry.hi ? __("Highlight {0}", [entry.label]) : entry.label;
			return `<span class="pp-p6c-key-item"${hi} title="${pp_esc(tip)}">${this.p6c_swatch_html(entry.color)}${pp_esc(
				entry.label
			)}</span>`;
		});
		if (entries.length > shown.length) {
			items.push(`<span class="pp-p6c-key-item">${pp_esc(__("+{0} more (see ?)", [entries.length - shown.length]))}</span>`);
		}
		$key[0].innerHTML = `<span class="pp-p6c-key-title">${pp_esc(__("Colors: {0}", [this.p6c_mode_label(this.p6c.color_by)]))}</span>${items.join("")}`;
		$key.show();
	},

	// The "?" legend's sections (6A's p6a_legend lists them first).
	p6c_legend_sections() {
		if (!this.p6c) return [];
		const mode = this.p6c.color_by;
		const entries = this.p6c_legend_entries().slice(0, PP6C.legend_limit);
		const colors = {
			title: __("Card colors: {0}", [this.p6c_mode_label(mode)]),
			note:
				mode === "default"
					? __("Each card's left edge is the task's own color. Color by in the toolbar colors the cards by person, job type, project, project manager or status instead.")
					: "",
			items:
				mode === "default"
					? [{ sample_html: this.p6c_swatch_html(PP.default_color), text: __("A task with no color of its own.") }]
					: entries.map((entry) => ({ sample_html: this.p6c_swatch_html(entry.color), text: entry.label })),
		};
		const strip = `<span class="pp-p6c-sday pp-amber pp-p6a-sample"><span class="pp-p6c-stext">${pp_esc(__("46h free"))}</span></span>`;
		const pill = `<span class="pp-p6c-pill"><span class="pp-p6c-pill-text">${pp_esc(__("Highlighting"))}</span></span>`;
		return [
			colors,
			{
				title: __("The big picture"),
				items: [
					{
						sample_html: pill,
						text: __("Click a project's name on a card, or Highlight this project, to light up all its tasks in every view and fade the rest. Esc or × clears it."),
					},
					{
						sample_html: strip,
						text: __("The team row over the week and crew views: the free hours of everyone the group filter shows. Amber at 90% booked, red when the team is booked past its hours, pencil hatched. Click a day to see everyone's day."),
					},
					{
						sample_html: "",
						text: __("Tap to move: tap a card, then tap the day (or a person's day in the crew view) where it goes. On by default on a touch screen; it asks for a reason and can be undone like a drag."),
					},
					{
						sample_html: "",
						text: __("Views: save your filters, view and colors under a name and switch between them. The planner opens the way you last left it, on any device."),
					},
					{
						sample_html: "",
						text: __("Print: exactly what is on screen, with your filters and colors, or only the cards you selected."),
					},
				],
			},
		];
	},

	// ------------------------------------------------------------------ highlight a project

	p6c_project_label(project) {
		const known = this.by_project[project];
		return known && known.title && known.title !== project ? `${known.title} (${project})` : project;
	},

	p6c_highlight(project) {
		if (!project) return;
		this.p6c.highlight = { key: project, label: this.p6c_project_label(project) };
		this.p6c_apply_highlight();
		this.p6c_sync_controls();
		this.p6c_refresh_drawer_tool();
	},

	p6c_clear_highlight() {
		if (!this.p6c.highlight) return;
		this.p6c.highlight = null;
		this.p6c_apply_highlight();
		this.p6c_sync_controls();
		this.p6c_refresh_drawer_tool();
	},

	p6c_toggle_highlight(project) {
		if (this.p6c.highlight && this.p6c.highlight.key === project) this.p6c_clear_highlight();
		else this.p6c_highlight(project);
	},

	p6c_apply_highlight() {
		const root = this.$body[0];
		const hi = this.p6c.highlight;
		root.classList.toggle("pp-p6c-on", !!hi && PP.views.includes(this.view));
		root.querySelectorAll(".pp-card[data-task]").forEach((el) => {
			const card = this.by_task[el.getAttribute("data-task")];
			el.classList.toggle("pp-p6c-hi", !!(hi && card && card.project === hi.key));
		});
	},

	// A project's name on a card (6A made it a target), or an entry of the color key.
	p6c_body_click(e) {
		const target = e.target;
		if (!this.p6c || !target || !target.closest || Date.now() < this.click_blocked_until) return;
		const key_item = target.closest(".pp-p6c-key [data-p6c-hi]");
		if (key_item) {
			this.p6c_toggle_highlight(key_item.getAttribute("data-p6c-hi"));
			return;
		}
		const chip = target.closest(".pp-card [data-pk-project]");
		if (chip) this.p6c_highlight(decodeURIComponent(chip.getAttribute("data-pk-project")));
	},

	p6c_links(card) {
		if (!this.p6c || !card || !card.project) return [];
		const on = this.p6c.highlight && this.p6c.highlight.key === card.project;
		return [["p6c_highlight", on ? __("Stop highlighting this project") : __("Highlight this project")]];
	},

	p6c_action(action, card) {
		if (action !== "p6c_highlight") return false;
		if (card && card.project) this.p6c_toggle_highlight(card.project);
		return true;
	},

	p6c_menu_items(target) {
		if (!this.p6c || !target || target.kind !== "card" || !target.card) return [];
		const card = target.card;
		const items = [];
		if (card.project) {
			const on = this.p6c.highlight && this.p6c.highlight.key === card.project;
			items.push({
				label: on ? __("Stop highlighting this project") : __("Highlight this project"),
				on_click: () => this.p6c_toggle_highlight(card.project),
			});
		}
		const source = target.el ? this.drag_source(target.el) : null;
		if (source && source.kind === "card") {
			items.push({ label: __("Move with taps…"), hint: __("then tap a day"), on_click: () => this.p6c_arm(source) });
		}
		return items;
	},

	// The project drawer (6A) gets a Highlight switch in its header.
	p6c_project_drawer(project, handle) {
		if (!this.p6c || !handle || typeof handle.set_tools !== "function") return;
		const button = document.createElement("button");
		button.type = "button";
		button.className = "btn btn-default btn-xs pp-p6c-drawer-hi";
		button.title = __("Light up this project's tasks on the calendar and fade the rest");
		button.addEventListener("click", () => this.p6c_toggle_highlight(project));
		handle.set_tools(button);
		this.p6c.drawer_tool = { project, handle, button };
		this.p6c_refresh_drawer_tool();
	},

	p6c_refresh_drawer_tool() {
		const tool = this.p6c.drawer_tool;
		if (!tool) return;
		if (!tool.handle.is_open()) {
			this.p6c.drawer_tool = null;
			return;
		}
		const on = !!(this.p6c.highlight && this.p6c.highlight.key === tool.project);
		tool.button.textContent = on ? __("Highlighted ✓") : __("Highlight");
		tool.button.setAttribute("aria-pressed", on ? "true" : "false");
	},

	// Esc: a move being placed first, then the highlight. A drawer or menu on top takes Esc itself.
	p6c_keydown(e) {
		if (e.key !== "Escape" || e.defaultPrevented || !this.p6c || this.drag) return;
		if (!this.p6c.moving && !this.p6c.highlight) return;
		if (!this.p6c_here()) return;
		const kit = this.p6a && this.p6a.kit;
		if (kit && kit.guard && kit.guard.top()) return;
		if (window.cur_dialog && window.cur_dialog.display) return;
		const tag = (e.target && e.target.tagName) || "";
		if (/^(INPUT|SELECT|TEXTAREA)$/.test(tag)) return;
		if (this.p6c.moving) this.p6c_disarm();
		else this.p6c_clear_highlight();
	},

	// ------------------------------------------------------------------ the team strip

	// One day of the people the group filter shows, from the payload (no request).
	p6c_team_sum(ymd) {
		const kit = this.p6c_kit();
		if (!kit) return null;
		return kit.big_picture.team_day(this.resources().map((resource) => this.day_of(resource.name, ymd)));
	},

	p6c_team_label() {
		return this.group ? __("{0} crew", [__(this.group)]) : __("Team");
	},

	p6c_strip_cells() {
		const kit = this.p6c_kit();
		if (!kit) return [];
		const bp = kit.big_picture;
		const label = this.p6c_team_label();
		return this.range_days().map((ymd) => {
			const sum = this.p6c_team_sum(ymd);
			const weekday = (kit.format.day_label(ymd) || "").split(",")[0];
			const tone = { off: "pp-offday", red: "pp-red", amber: "pp-amber", green: "pp-green" }[sum.level] || "";
			return {
				ymd,
				weekday,
				sum,
				tone,
				text: bp.team_text(sum),
				tip: `${bp.team_tip(label, weekday, sum)}\n${__("Click to see everyone's day")}`,
			};
		});
	},

	p6c_strip_cell_html(cell) {
		const bar = cell.sum.capacity > 0 ? this.bar_html({ ratio: Math.min(1, cell.sum.ratio), soft_ratio: cell.sum.soft_ratio }) : "";
		return `
			<div class="pp-p6c-sday ${cell.tone}" role="button" tabindex="0" data-pp6a-day="${pp_esc(cell.ymd)}"
				title="${pp_esc(cell.tip)}" aria-label="${pp_esc(cell.tip.split("\n")[0])}">
				<span class="pp-p6c-slabel">${pp_esc(cell.weekday)}</span>
				<span class="pp-p6c-stext">${pp_esc(cell.text)}</span>${bar}
			</div>`;
	},

	p6c_render_strip() {
		this.$grid_wrap.find(".pp-p6c-strip, .pp-crew > .pp-p6c-sday, .pp-crew > .pp-p6c-scorner").remove();
		if (this.view !== "week" && this.view !== "crew") return;
		const cells = this.p6c_strip_cells();
		if (!cells.length || !this.resources().length) return;
		const html = cells.map((cell) => this.p6c_strip_cell_html(cell)).join("");
		if (this.view === "week") {
			const $strip = $('<div class="pp-p6c-strip" role="group"></div>').attr("aria-label", __("Team capacity"));
			$strip[0].innerHTML = html;
			this.$grid_wrap.prepend($strip);
			return;
		}
		const heads = this.$grid_wrap[0].querySelectorAll(".pp-crew > .pp-crew-head");
		if (!heads.length) return;
		const corner = `<div class="pp-p6c-scorner"><b>${pp_esc(this.p6c_team_label())}</b><span>${pp_esc(
			__("free hours")
		)}</span></div>`;
		heads[heads.length - 1].insertAdjacentHTML("afterend", corner + html);
	},

	// ------------------------------------------------------------------ tap to move

	p6c_coarse() {
		try {
			return !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
		} catch (e) {
			return false;
		}
	},

	// This device's choice (a tablet stays a tablet), else on for a touch screen.
	p6c_tap_default() {
		const saved = this.load_pref(PP6C.tap_pref, "");
		if (saved === "1") return true;
		if (saved === "0") return false;
		return this.p6c_coarse();
	},

	p6c_set_tap(on) {
		this.p6c.tap = !!on;
		this.save_pref(PP6C.tap_pref, on ? "1" : "0");
		if (!on) this.p6c_disarm();
		this.p6c_sync_controls();
		frappe.show_alert(
			{
				message: on
					? __("Tap to move: tap a card, then tap the day where it goes.")
					: __("Tap to move is off: tap a card to open it, drag it to move it."),
				indicator: "blue",
			},
			5
		);
	},

	// Where a tap puts the card: a person's day in the crew view, or a day of the calendar.
	p6c_tap_target(el) {
		const cell = el.closest(".pp-cell[data-date][data-resource]");
		if (cell) return { el: cell, date: cell.getAttribute("data-date"), resource: cell.getAttribute("data-resource") };
		const day = el.closest(".pp-day[data-date]");
		if (day) return { el: day, date: day.getAttribute("data-date") };
		return null;
	},

	p6c_tap_click(e) {
		const p6c = this.p6c;
		if (!p6c || !this.data || (!p6c.tap && !p6c.moving)) return;
		const target = e.target;
		if (!target || !target.closest || !this.$body[0].contains(target)) return;
		if (e.button > 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
		if (!PP.views.includes(this.view) || Date.now() < this.click_blocked_until) return;
		if (target.closest(".pp-p6c-movebar, button, a, input, select, textarea, label, .pp-route, [data-pk-person], [data-pp6a-day]")) {
			return;
		}
		const take = () => {
			e.preventDefault();
			e.stopPropagation();
		};
		const moving = p6c.moving;
		if (!moving) {
			const source = this.drag_source(target);
			if (!source || (source.kind !== "card" && source.kind !== "overdue")) return;
			take();
			this.p6c_arm(source);
			return;
		}
		const card_el = target.closest(".pp-card[data-task], .pp-od-card[data-od-task]");
		if (card_el && this.p6c_is_moving_el(card_el)) {
			take();
			this.p6c_disarm();
			return;
		}
		const place = this.p6c_tap_target(target);
		if (!place) {
			// Another card outside the calendar (a tray, the Overdue list): pick that one instead.
			const other = p6c.tap ? this.drag_source(target) : null;
			if (other && (other.kind === "card" || other.kind === "overdue")) {
				take();
				this.p6c_arm(other);
			}
			return;
		}
		take();
		this.p6c_tap_drop(place);
	},

	// The move goes through the drag's own path: drop -> plan_drop -> commit -> send (reason, Undo).
	p6c_tap_drop(place) {
		const moving = this.p6c.moving;
		if (!moving) return;
		this.p6c_disarm();
		this.drop(moving.source, place);
	},

	p6c_arm(source) {
		if (!source) return;
		const label =
			source.kind === "overdue"
				? (source.row && (source.row.subject || source.row.name)) || ""
				: (source.card && (source.card.subject || source.card.name)) || "";
		this.p6c.moving = {
			source,
			label,
			task: source.kind === "overdue" ? source.row.name : source.card.name,
			date: source.from_date || "",
			resource: source.from_resource || "",
		};
		this.p6c_render_movebar();
		this.p6c_mark_moving();
	},

	p6c_disarm() {
		if (!this.p6c || !this.p6c.moving) return;
		this.p6c.moving = null;
		this.p6c_render_movebar();
		this.p6c_mark_moving();
	},

	p6c_is_moving_el(el) {
		const moving = this.p6c.moving;
		if (!moving || !el) return false;
		if (moving.source.kind === "overdue") return el.getAttribute("data-od-task") === moving.task;
		return (
			el.getAttribute("data-task") === moving.task &&
			(el.getAttribute("data-date") || "") === moving.date &&
			(el.getAttribute("data-resource") || "") === moving.resource
		);
	},

	p6c_mark_moving() {
		const root = this.$body[0];
		root.classList.toggle("pp-p6c-armed", !!(this.p6c && this.p6c.moving));
		root.querySelectorAll(".pp-p6c-moving").forEach((el) => el.classList.remove("pp-p6c-moving"));
		if (!this.p6c || !this.p6c.moving) return;
		root.querySelectorAll(".pp-card[data-task], .pp-od-card[data-od-task]").forEach((el) => {
			if (this.p6c_is_moving_el(el)) el.classList.add("pp-p6c-moving");
		});
	},

	p6c_render_movebar() {
		const $movebar = this.$p6c_movebar;
		if (!$movebar) return;
		const moving = this.p6c.moving;
		if (!moving) {
			$movebar.hide().empty();
			return;
		}
		$movebar.empty().show();
		const $text = $('<span class="pp-p6c-movebar-text"></span>').appendTo($movebar);
		$("<b></b>").text(__("Moving: {0}", [moving.label])).appendTo($text);
		$('<span class="pp-p6c-movebar-hint"></span>')
			.text(
				this.view === "crew"
					? __("Tap a day on a person's row to put it there.")
					: __("Tap a day to put it there; it keeps its length.")
			)
			.appendTo($text);
		const card = moving.source.kind === "card" ? moving.source.card : null;
		if (card) {
			$('<button type="button" class="btn btn-default btn-sm"></button>')
				.text(__("Open"))
				.on("click", () => {
					this.p6c_disarm();
					const fresh = this.by_task[card.name] || card;
					this.open_card(fresh);
				})
				.appendTo($movebar);
		}
		$('<button type="button" class="btn btn-default btn-sm"></button>')
			.text(__("Cancel"))
			.on("click", () => this.p6c_disarm())
			.appendTo($movebar);
	},

	// ------------------------------------------------------------------ saved views

	p6c_fetch_views() {
		const p6c = this.p6c;
		if (p6c.loading) return p6c.loading;
		const call = Promise.resolve(
			frappe.call({ method: `${PP6C.prefs}.get_views`, args: { planner: PP6C.planner } })
		)
			.then((r) => {
				const answer = (r && r.message) || {};
				p6c.views = Array.isArray(answer.views) ? answer.views : [];
				p6c.last = answer.last && typeof answer.last === "object" ? answer.last : null;
			})
			.catch(() => null);
		// A slow answer must not keep the planner blank: after a few seconds it opens without it.
		const late = new Promise((resolve) => setTimeout(resolve, PP6C.restore_wait));
		p6c.loading = Promise.race([call, late]).then(() => {
			p6c.loaded = true;
		});
		return p6c.loading;
	},

	// Called first thing in handle_route. True: it has taken this route over (it is waiting for the
	// saved views, or it has replaced a bare /project-planner with the saved view's route) and
	// handle_route runs again when the route lands. The URL always wins: a route that names a view is
	// used as it is, and only the filters are restored on the first open.
	p6c_before_route() {
		const p6c = this.p6c;
		if (!p6c) return false;
		const route = frappe.get_route() || [];
		if (route[0] !== PP.route) return false;
		if (!p6c.loaded) {
			if (!p6c.waiting) {
				p6c.waiting = true;
				this.p6c_fetch_views().then(() => {
					p6c.waiting = false;
					this.handle_route();
				});
			}
			return true;
		}
		const last = p6c.last ? this.p6c_pick(p6c.last) : null;
		// The server never remembers these; nor does the page, whatever an older row says.
		if (last) PP6C.not_remembered.forEach((key) => delete last[key]);
		if (!p6c.restored) {
			p6c.restored = true;
			if (last) {
				this.p6c_apply_state(last, { render: false });
				p6c.saved_text = JSON.stringify(last);
			}
		}
		const view = last && last.view;
		if (!route[1] && view && view !== PP6C.default_view && PP6C.schema.view[1].includes(view)) {
			// Replace, never push: Back from here leaves the planner, as it would have from the bare URL.
			frappe.route_flags = frappe.route_flags || {};
			frappe.route_flags.replace_route = true;
			frappe.set_route(PP.route, view, frappe.datetime.get_today());
			return true;
		}
		return false;
	},

	// The page's state as a view. `remembered`: as "last used" (without the Running over chip).
	p6c_state(remembered) {
		const views = PP6C.schema.view[1];
		const state = {
			project: this.project || "",
			pm: this.pm || "",
			group: this.group || "",
			foreign: !!this.show_foreign,
			panel: !!this.panel_open,
			color_by: this.p6c.color_by,
		};
		if (views.includes(this.view)) state.view = this.view;
		if (!remembered) state.over_only = !!this.over_only;
		return this.p6c_pick(state);
	},

	// Apply a view: filters, colors, and (with `route`) its calendar view as a normal route change.
	p6c_apply_state(state, opts) {
		opts = opts || {};
		const view = this.p6c_pick(state);
		if ("project" in view) this.project = view.project;
		if ("pm" in view) this.pm = view.pm;
		if ("group" in view) this.group = view.group;
		if ("foreign" in view) this.show_foreign = !!view.foreign;
		if ("panel" in view) this.panel_open = !!view.panel;
		if ("over_only" in view) this.over_only = !!view.over_only;
		if ("color_by" in view) this.p6c.color_by = view.color_by;
		if (this.data) {
			// A project or PM no longer on the planner would filter out everything.
			if (this.project && !this.by_project[this.project]) this.project = "";
			if (this.pm && !(this.data.projects || []).some((project) => project.pm === this.pm)) this.pm = "";
		}
		this.show_all_unscheduled = false;
		this.p6c_sync_controls();
		if (opts.route && view.view && view.view !== this.view) {
			this.go(view.view, this.anchor);
			return;
		}
		if (opts.render !== false && this.data) this.render();
	},

	p6c_state_soon() {
		const p6c = this.p6c;
		if (!p6c || !p6c.restored) return;
		clearTimeout(p6c.save_timer);
		p6c.save_timer = setTimeout(() => this.p6c_save_last(), PP6C.save_delay);
	},

	p6c_save_last() {
		const p6c = this.p6c;
		if (!PP6C.schema.view[1].includes(this.view)) return;
		const state = this.p6c_state(true);
		const text = JSON.stringify(state);
		if (text === p6c.saved_text) return;
		p6c.saved_text = text;
		Promise.resolve(
			frappe.call({ method: `${PP6C.prefs}.save_last_view`, args: { planner: PP6C.planner, data: text } })
		)
			.then((r) => {
				const answer = (r && r.message) || {};
				p6c.last = answer.last && typeof answer.last === "object" ? answer.last : state;
			})
			.catch(() => {
				p6c.saved_text = null;
			});
	},

	// The saved view the page shows right now, if any.
	p6c_matching_view() {
		const now = JSON.stringify(this.p6c_state(false));
		return (this.p6c.views || []).find((entry) => JSON.stringify(this.p6c_pick(entry.data || {})) === now) || null;
	},

	p6c_views_menu(anchor) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit) {
			frappe.show_alert({ message: __("One moment: the planner is still loading."), indicator: "blue" }, 4);
			return;
		}
		const current = this.p6c_matching_view();
		const items = (this.p6c.views || []).map((entry) => ({
			label: entry.name,
			hint: current && current.name === entry.name ? "✓" : this.p6c_view_summary(entry.data || {}),
			on_click: () => this.p6c_use_view(entry),
		}));
		if (!this.p6c.loaded) items.push({ label: __("Loading your views…"), disabled: true });
		else if (!items.length) items.push({ label: __("No saved views yet"), disabled: true });
		items.push({ divider: true });
		items.push({ label: __("Save view as…"), on_click: () => this.p6c_save_as() });
		if ((this.p6c.views || []).length) items.push({ label: __("Manage views…"), on_click: () => this.p6c_manage_views() });
		kit.menu({ anchor, items, title: __("Saved views"), owner: PP6A.owner });
	},

	p6c_use_view(entry) {
		if (!entry) return;
		this.p6c_apply_state(entry.data || {}, { route: true });
		frappe.show_alert({ message: __("View: {0}", [pp_esc(entry.name)]), indicator: "green" }, 4);
	},

	// "Crew · Field · Job type colors": what a saved view holds, in a few words.
	p6c_view_summary(view) {
		const parts = [];
		view = this.p6c_pick(view || {});
		if (view.view) parts.push(__(PP6C.view_labels[view.view] || view.view));
		if (view.group) parts.push(__(view.group));
		if (view.project) parts.push(this.p6c_project_label(view.project));
		if (view.pm) parts.push(view.pm);
		if (view.over_only) parts.push(__("Running over"));
		if (view.foreign === false) parts.push(__("No maintenance, rentals, travel"));
		if (view.color_by && view.color_by !== "default") parts.push(__("{0} colors", [this.p6c_mode_label(view.color_by)]));
		return parts.join(" · ");
	},

	p6c_save_as() {
		const current = this.p6c_matching_view();
		frappe.prompt(
			[
				{
					fieldtype: "Data",
					fieldname: "name",
					label: __("Name"),
					reqd: 1,
					default: current ? current.name : "",
					description: __("For example: Field crew, Build only. Only you see your views."),
				},
			],
			(values) => {
				const name = String((values && values.name) || "")
					.replace(/\s+/g, " ")
					.trim()
					.slice(0, 60);
				if (!name) return;
				const exists = (this.p6c.views || []).some((entry) => entry.name.toLowerCase() === name.toLowerCase());
				const run = () => this.p6c_store_view(name);
				if (exists) frappe.confirm(__("Replace your saved view {0}?", [pp_esc(name)]), run);
				else run();
			},
			__("Save view as"),
			__("Save")
		);
	},

	p6c_store_view(name) {
		return Promise.resolve(
			frappe.call({
				method: `${PP6C.prefs}.save_view`,
				args: { planner: PP6C.planner, name, data: JSON.stringify(this.p6c_state(false)) },
			})
		)
			.then((r) => {
				const answer = (r && r.message) || {};
				if (Array.isArray(answer.views)) this.p6c.views = answer.views;
				frappe.show_alert({ message: __("Saved view {0}", [pp_esc(answer.saved || name)]), indicator: "green" }, 5);
			})
			.catch(() => null);
	},

	p6c_views_html() {
		const views = this.p6c.views || [];
		if (!views.length) return `<p class="pk-empty">${pp_esc(__("No saved views yet. Save one from the Views button."))}</p>`;
		const rows = views.map(
			(entry) => `
			<li class="pp-p6c-views-row">
				<div class="pp-p6c-views-main">
					<div class="pp-p6c-views-name">${pp_esc(entry.name)}</div>
					<div class="pp-p6c-views-sub">${pp_esc(this.p6c_view_summary(entry.data || {}))}</div>
				</div>
				<button type="button" class="btn btn-default btn-xs" data-p6c-use="${pp_esc(entry.name)}">${pp_esc(__("Use"))}</button>
				<button type="button" class="btn btn-default btn-xs" data-p6c-delete="${pp_esc(entry.name)}">${pp_esc(__("Delete"))}</button>
			</li>`
		);
		return `<ul class="pp-p6c-views-list">${rows.join("")}</ul><p class="pk-note">${pp_esc(
			__("Your views are saved for you on the server, so they follow you to any device. Nobody else sees them.")
		)}</p>`;
	},

	p6c_manage_views() {
		const kit = this.p6a && this.p6a.kit;
		if (!kit) return;
		kit.drawer.open({
			title: __("Saved views"),
			body: this.p6c_views_html(),
			width: 420,
			key: "p6c-views",
			owner: PP6A.owner,
			push: this.$body[0],
			reopen: () => this.p6c_manage_views(),
			on_click: (e, handle) => {
				const el = e.target && e.target.closest ? e.target.closest("[data-p6c-use], [data-p6c-delete]") : null;
				if (!el) return;
				const use = el.getAttribute("data-p6c-use");
				const name = use || el.getAttribute("data-p6c-delete");
				const entry = (this.p6c.views || []).find((item) => item.name === name);
				if (!entry) return;
				if (use) {
					kit.drawer.navigate(() => this.p6c_use_view(entry));
					return;
				}
				frappe.confirm(__("Delete your saved view {0}? This cannot be undone.", [pp_esc(entry.name)]), () =>
					Promise.resolve(
						frappe.call({ method: `${PP6C.prefs}.delete_view`, args: { planner: PP6C.planner, name: entry.name } })
					)
						.then((r) => {
							const answer = (r && r.message) || {};
							if (Array.isArray(answer.views)) this.p6c.views = answer.views;
							handle.set_body(this.p6c_views_html());
						})
						.catch(() => null)
				);
			},
		});
	},

	// ------------------------------------------------------------------ print

	// 6B's multi-selection (task names), read defensively: empty when 6B is not there.
	p6c_selection() {
		const selection = this.p6_selection;
		return selection && typeof selection.has === "function" && selection.size ? selection : new Set();
	},

	p6c_print_click(anchor) {
		const kit = this.p6a && this.p6a.kit;
		const selected = this.p6c_selection();
		if (selected.size && kit) {
			kit.menu({
				anchor,
				owner: PP6A.owner,
				items: [
					{ label: __("Print this view"), on_click: () => this.p6c_print(false) },
					{ label: __("Print selection only ({0})", [selected.size]), on_click: () => this.p6c_print(true) },
				],
			});
			return;
		}
		this.p6c_print(false);
	},

	// The window opens on the click itself (one opened after a request is a blocked pop-up) and fills
	// in when the print design system's chrome arrives; the body is built from the data now.
	p6c_print(selection_only) {
		const kit = this.p6c_kit();
		if (!kit || (!this.data && this.view !== PP.heatmap_view)) {
			frappe.show_alert({ message: __("One moment: the planner is still loading."), indicator: "blue" }, 4);
			return;
		}
		if (!PP6C.schema.view[1].includes(this.view)) {
			frappe.msgprint(__("Open the week, month, crew or heatmap view to print it."));
			return;
		}
		const win = window.open("", "_blank");
		if (!win) {
			frappe.msgprint(__("Allow pop-ups for this site to print the planner."));
			return;
		}
		win.document.write(
			`<!doctype html><title>${pp_esc(__("Print"))}</title><p style="font-family:sans-serif">${pp_esc(
				__("Preparing the page…")
			)}</p>`
		);
		const model = this.p6c_print_model(!!selection_only);
		Promise.resolve(
			frappe.call({
				method: `${PP6C.prefs}.get_print_chrome`,
				args: { planner: PP6C.planner, title: model.heading, lines: JSON.stringify(model.lines) },
			})
		)
			.then((r) => (r && r.message) || null)
			.catch(() => null)
			.then((chrome) => {
				if (win.closed) return;
				const html = kit.big_picture.print_document(
					Object.assign({}, model, { chrome: chrome && typeof chrome.open === "string" ? chrome : null })
				);
				win.document.open();
				win.document.write(html);
				win.document.close();
				win.focus();
				const fonts = win.document.fonts && win.document.fonts.ready ? win.document.fonts.ready : Promise.resolve();
				fonts.then(() => win.setTimeout(() => win.print(), 250));
			});
	},

	p6c_day_label(ymd, short) {
		const kit = this.p6c_kit();
		const label = kit ? kit.format.day_label(ymd) : ymd;
		return short ? label.split(", ").slice(1).join(", ") || label : label;
	},

	// Whole days from `a` to `b` ("YYYY-MM-DD"), without the Desk's moment (the print model runs in tests).
	p6c_days_between(a, b) {
		const day = (ymd) => Date.UTC(Number(ymd.slice(0, 4)), Number(ymd.slice(5, 7)) - 1, Number(ymd.slice(8, 10)));
		return Math.round((day(b) - day(a)) / 86400000);
	},

	// The color a printed card carries: the current mode's, else the card's own edge as on screen.
	p6c_print_color(card, row) {
		const color = this.p6c_card_color(card, row);
		if (color) return color;
		if (card.overdue) return "#dc2626";
		return pp_color(card.color, PP.default_color);
	},

	p6c_print_item(card, ymd, row) {
		const hi = this.p6c.highlight;
		const marks = [];
		if (card.tentative) marks.push(__("Pencil"));
		if (card.overdue) marks.push({ text: __("Overdue"), tone: "red" });
		if (this.task_conflicts(card).length) marks.push({ text: __("Conflict"), tone: "red" });
		if (card.drafted) marks.push(__("Draft"));
		if (ymd && card.start && card.end && card.end > card.start) {
			marks.push(
				__("Day {0} of {1}", [this.p6c_days_between(card.start, ymd) + 1, this.p6c_days_between(card.start, card.end) + 1])
			);
		}
		const crew = (card.crew || []).map((member) => this.resource_label(member.resource, member.label)).join(", ");
		return {
			title: card.subject || card.name,
			sub: [row ? "" : card.project_title || card.project, this.hours_text(card), row ? "" : crew].filter(Boolean).join(" · "),
			color: this.p6c_print_color(card, row),
			dashed: !!card.tentative,
			marks,
			faded: !!(hi && card.project !== hi.key) || !!(row && !this.task_visible(card)),
			highlight: !!(hi && card.project === hi.key),
		};
	},

	p6c_foreign_item(label, sub) {
		return { title: label, sub, color: PP6C.none_color, dashed: true, faded: !!this.p6c.highlight };
	},

	p6c_print_lines(selection_only, count) {
		const lines = [];
		if (this.view === PP.heatmap_view) {
			// The heatmap answers to the group filter only.
			if (this.group) lines.push(__("Group: {0}", [__(this.group)]));
			return lines;
		}
		if (this.project) lines.push(__("Project: {0}", [this.p6c_project_label(this.project)]));
		if (this.pm) lines.push(__("Project manager: {0}", [this.pm]));
		if (this.group) lines.push(__("Group: {0}", [__(this.group)]));
		if (this.over_only) lines.push(__("Only tasks running over"));
		if (!this.show_foreign && this.view !== PP.heatmap_view) lines.push(__("Maintenance, rentals and travel hidden"));
		if (this.p6c.color_by !== "default") lines.push(__("Colored by {0}", [this.p6c_mode_label(this.p6c.color_by)]));
		if (this.p6c.highlight) lines.push(__("Highlighting {0}", [this.p6c.highlight.label]));
		if (selection_only) lines.push(__("Selected tasks only ({0})", [count]));
		if (this.draft_on) lines.push(__("With your unpublished drafts"));
		return lines;
	},

	// Exactly what is on screen, as a model for planner_kit.big_picture.print_document.
	p6c_print_model(selection_only) {
		const view = this.view;
		const heading = `${__(PP6C.view_labels[view] || view)} · ${this.title()}`;
		const legend = this.p6c_legend_entries().map((entry) => ({ color: entry.color, label: entry.label }));
		const model = {
			doc_title: `${__("Project Planner")} · ${heading}`,
			heading,
			legend_title: __("Colors: {0}", [this.p6c_mode_label(this.p6c.color_by)]),
			legend,
			notes: [],
		};
		const selected = this.p6c_selection();
		if (selection_only && selected.size && this.data) {
			const cards = [].concat(this.data.tasks || [], this.data.unscheduled || []).filter((card) => selected.has(card.name));
			cards.sort((a, b) => String(a.start || "9999").localeCompare(String(b.start || "9999")) || this.compare(a, b));
			model.layout = "list";
			model.columns = [__("Dates"), __("Task"), __("Project"), __("Crew"), __("Hours"), __("Notes")];
			model.rows = cards.map((card) => {
				const item = this.p6c_print_item(card, null, null);
				const when = card.start
					? card.end && card.end !== card.start
						? `${this.p6c_day_label(card.start)} – ${this.p6c_day_label(card.end)}`
						: this.p6c_day_label(card.start)
					: __("Not scheduled");
				return {
					color: item.color,
					cols: [
						when,
						card.subject || card.name,
						card.project_title || card.project || "",
						(card.crew || []).map((member) => this.resource_label(member.resource, member.label)).join(", "),
						this.hours_text(card),
						item.marks.map((mark) => (typeof mark === "string" ? mark : mark.text)).join(", "),
					],
				};
			});
			model.lines = [__("Selected tasks")].concat(this.p6c_print_lines(true, cards.length));
			model.empty = __("None of the selected tasks is on screen.");
			return model;
		}
		model.lines = this.p6c_print_lines(false, 0);
		if (view === PP.heatmap_view) return Object.assign(model, this.p6c_print_heatmap());
		if (view === "crew") return Object.assign(model, this.p6c_print_crew());
		return Object.assign(model, this.p6c_print_calendar());
	},

	p6c_print_calendar() {
		const days = this.range_days();
		const people = this.resources();
		const visible_people = new Set(people.map((resource) => resource.name));
		const month = this.view === "month" ? String(this.anchor).slice(0, 7) : "";
		const rows = [];
		for (let index = 0; index < days.length; index += 7) {
			const cells = days.slice(index, index + 7).map((ymd) => {
				const cards = (this.data.tasks || [])
					.filter((card) => this.task_visible(card) && this.covered(card, [ymd]).length)
					.sort((a, b) => this.compare(a, b));
				const items = cards.map((card) => this.p6c_print_item(card, ymd, null));
				if (this.show_foreign) {
					(this.data.foreign || []).forEach((item) => {
						if (item.date !== ymd || (item.ref && this.by_task[item.ref])) return;
						if (item.resource && !visible_people.has(item.resource)) return;
						const who = item.resource ? this.resource_label(item.resource) : "";
						const hours = item.hours ? `${pp_hours(item.hours)}h` : "";
						const kind = { visit: __("Maintenance"), rental: __("Rental"), travel: __("Travel") }[item.kind] || "";
						items.push(this.p6c_foreign_item(item.label || item.ref, [kind, who, hours].filter(Boolean).join(" · ")));
					});
				}
				const sum = this.p6c_team_sum(ymd);
				const kit = this.p6c_kit();
				return {
					head: this.view === "week" ? this.p6c_day_label(ymd) : this.p6c_day_label(ymd, true),
					sub: people.length && sum && kit ? kit.big_picture.team_text(sum) : "",
					tone: month && ymd.slice(0, 7) !== month ? "out" : "",
					items,
				};
			});
			rows.push({ cells });
		}
		return {
			layout: "grid",
			label_column: false,
			columns: days.slice(0, 7).map((ymd) => this.p6c_day_label(ymd).split(",")[0]),
			rows,
		};
	},

	p6c_print_crew() {
		const days = this.range_days();
		const rows = this.resources().map((resource) => ({
			label: resource.label || resource.name,
			sub: resource.group ? __(resource.group) : "",
			cells: days.map((ymd) => {
				const day = this.day_of(resource.name, ymd);
				const state = this.avail_state(day);
				const items = [];
				((day && day.bookings) || []).forEach((booking) => {
					if (booking.kind === "drive") return;
					const card = booking.ref && this.by_task[booking.ref];
					if (card) {
						items.push(this.p6c_print_item(card, null, resource.name));
						return;
					}
					if (!this.show_foreign) return;
					const length = booking.slot ? booking.slot.join("–") : `${pp_hours(booking.hours)}h`;
					items.push(this.p6c_foreign_item(booking.label || booking.ref, length));
				});
				return { sub: state.text, tone: String(state.cls).includes("pp-offday") ? "off" : "", items };
			}),
		}));
		const kit = this.p6c_kit();
		if (kit && rows.length) {
			rows.unshift({
				label: this.p6c_team_label(),
				sub: __("free hours"),
				cells: days.map((ymd) => ({ head: kit.big_picture.team_text(this.p6c_team_sum(ymd)) })),
			});
		}
		return {
			layout: "grid",
			label_column: true,
			columns: [__("Person")].concat(days.map((ymd) => this.p6c_day_label(ymd))),
			rows,
			empty: __("No planner resources to show."),
		};
	},

	p6c_print_heatmap() {
		const heat = this.heatmap;
		if (!heat) return { layout: "grid", columns: [], rows: [], empty: __("The heatmap could not be loaded.") };
		const rows = heat.rows
			.filter((row) => !this.group || !row.group || row.group === this.group)
			.map((row) => ({
				label: row.label,
				sub: row.group ? __(row.group) : "",
				cells: heat.weeks.map((week, index) => {
					const state = this.heat_state(row.by_start[week.start] || row.ordered[index] || {});
					return { head: state.pct, sub: state.sub, tone: String(state.cls).includes("pp-offday") ? "off" : "" };
				}),
			}));
		return {
			layout: "grid",
			label_column: true,
			columns: [__("Person")].concat(heat.weeks.map((week) => __("Week of {0}", [this.p6c_day_label(week.start, true)]))),
			rows,
			legend: [],
			empty: __("No planner resources to show."),
		};
	},
};

Object.assign(ProjectPlanner.prototype, PP6C_METHODS);

// ====================================================================== Phase 6D: Conflict center, personal blocks, day notes
//
// Nik, 2026-10-09, for both planners: one list of everything wrong right now with one-click fixes
// (the Conflict center); personal blocks ("unavailable 2–4 pm", "shop day") that planners put on
// anyone and each person on their own time, which others see as "Unavailable" and planners with the
// note; and day notes for the whole crew ("Shop meeting 7 am"). The backend is api/planner_blocks.py
// and api/planner_conflicts.py (Phase 6D); the drawer, the two forms and the markup are the planner
// kit's (planner_kit/conflicts.js and blocks.js), shared with the Maintenance Planner.
//
//   - "Conflicts (N)" on the toolbar        the Conflict center for the dates on screen. N is what
//                                            nobody has kept, red above 0, read after each load (one
//                                            call, debounced), never on a plain re-render. A card in
//                                            a conflict has its Conflict chip turned into the way in
//                                            (a small marker when it had no chip): it opens the list
//                                            at that conflict.
//   - a fix in the list                     exactly the method and arguments the server worked out,
//                                            sent through commit()/send() like a drag, so a conflict
//                                            asks for a reason, Draft mode drafts it and Undo puts it
//                                            back. "Pick someone who's free" lists who_is_free and
//                                            picks nobody; "Keep it with a reason" needs a reason.
//   - "Block time" (planners), "Block       the block form. A block shows in the crew view as a
//     time…" in a person's drawer and the    hatched "Unavailable 2–4 pm" bar, in week and month as a
//     right-click menu                       chip; its note only when block_notes has it. Blocks are
//                                            never dragged.
//   - a day's notes                         a thin row under each day head (week), a row under the
//                                            crew view's column heads, a small marker (month); "+ note"
//                                            for planners; the day drawer shows them in full on top
//   - My week                               your own blocks with their notes, the notes for your
//                                            group, and "Block my time" on each day (only your own)
//
// Hooks into the class above, one line each: init_phase6d (constructor), render_phase6d (render),
// p6d_set_mode (set_mode), p6d_state (avail_state), p6d_block_html (booking_html), p6d_day_html
// (render_calendar), p6d_crew_notes_row (render_crew), p6d_my_week_day (render_my_week), and in
// Phase 6A's block p6d_person_actions (p6a_open_person), p6d_day_peek_opts (p6a_open_day) and
// p6d_legend_sections (p6a_legend).

const PP6D = {
	planner: "project",
	width: 540,
	max_days: 60,
	unavailable: "Unavailable",
	// The Conflict center runs only these, each the page's own write sent through commit()/send().
	fix_methods: {
		[`${PP.api}.save_task`]: "save_task",
		[`${PP.api}.swap_crew`]: "swap_crew",
		[`${PP.api}.add_crew`]: "add_crew",
	},
	// A maintenance visit opens on the Maintenance Planner, which Projects Users cannot open.
	maintenance_roles: ["System Manager", "Projects Manager", "Maintenance Supervisor", "Maintenance User"],
	// What this block's capture listener answers: a block, a note, a conflict marker.
	targets: "[data-pk-block], [data-pk-note-open], [data-p6d-conflict]",
};

const PP6D_STYLE = `
.pp-p6d-conflicts.pp-p6d-hot{color:#b91c1c;border-color:rgba(220,38,38,.55);font-weight:600;}
.pp-chip.pp-p6d-link{cursor:pointer;text-decoration:underline dotted;}
.pp-day > .pk-notes,.pp-day > .pk-notes-mark{margin:0 2px;}
.pp-p6d-crew-notes{border-left:1px solid var(--border-color);padding:3px 4px;min-width:0;}
.pp-p6d-crew-corner{position:sticky;left:0;z-index:1;border-left:none;background:var(--card-bg);font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);padding:4px 8px;}
.pp-p6d-mw{display:flex;flex-direction:column;gap:4px;padding:6px 10px;border-bottom:1px solid var(--border-color);}
.pp-p6d-mw .pk-block{font-size:13px;padding:6px 8px;}
@media (max-width:760px){
.pp-p6d-mw-btn{padding:4px 10px;font-size:13px;}
}
`;

const PP6D_METHODS = {
	init_phase6d() {
		if (!document.getElementById("pp-style-6d")) {
			$("<style id='pp-style-6d'>").text(PP6D_STYLE).appendTo(document.head);
		}
		this.p6d = { center: null, counted: null, notes: {}, asked: {}, notes_for: null };
		// The right-click menu registry (phase6-wave2): 6B opens the menu, this adds its items.
		this.p6_menu_providers = this.p6_menu_providers || [];
		this.p6_menu_providers.push((target) => this.p6d_menu_items(target));
		this.$p6d_conflicts = $('<button type="button" class="btn btn-default btn-sm pp-p6d-conflicts"></button>')
			.text(__("Conflicts"))
			.attr("title", __("Everything wrong in the dates on screen, with one-click fixes"))
			.on("click", () => this.p6d_open_conflicts(null))
			.insertBefore(this.$undo);
		this.$p6d_block = $('<button type="button" class="btn btn-default btn-sm pp-p6d-block-btn"></button>')
			.text(__("Block time"))
			.attr("title", __("Mark someone unavailable for part of a day or all of it"))
			.hide()
			.on("click", () => this.p6d_block_form({ date: this.p6d_default_date() }))
			.insertBefore(this.$undo);
		const root = this.$body[0];
		// Capture phase: a block, a note or a conflict marker inside a card or a day is its own click,
		// not the card's (which opens the task) and not the day's.
		root.addEventListener("click", (e) => this.p6d_click(e), true);
		root.addEventListener(
			"keydown",
			(e) => {
				if (e.key !== "Enter" && e.key !== " ") return;
				const el = e.target && e.target.closest ? e.target.closest(PP6D.targets) : null;
				if (!el || e.target !== el) return;
				e.preventDefault();
				e.stopPropagation();
				el.click();
			},
			true
		);
		frappe.require(PP6A.kit, () => this.p6d_ready());
	},

	p6d_ready() {
		const kit = window.planner_kit;
		if (!kit || !kit.conflicts || !kit.blocks || (this.p6d && this.p6d.center)) return;
		this.p6d.center = kit.conflicts.center({
			planner: PP6D.planner,
			owner: PP6A.owner,
			push: this.$body[0],
			width: PP6D.width,
			range: () => this.p6d_range(),
			can_schedule: () => !!(this.data && this.data.can_schedule),
			draft: () => !!this.draft_on,
			block_note: (name) => this.p6d_block_note(name),
			run_fix: (job) => this.p6d_run_fix(job),
			open_item: (conflict, item) => this.p6d_open_item(conflict, item),
			exclude: (conflict, fix) => this.p6d_exclude(conflict, fix),
			pick_context: (conflict, fix) => this.p6d_pick_context(conflict, fix),
			on_list: () => this.p6d_after_list(),
		});
		// The calendar may have been drawn before the kit arrived: draw its blocks and notes now.
		if (this.data && PP.views.includes(this.view)) this.render();
	},

	p6d_kit() {
		return this.p6d && this.p6d.center ? window.planner_kit : null;
	},

	// ------------------------------------------------------------------ hooks

	render_phase6d() {
		if (!this.p6d || !this.data) return;
		if (this.$p6d_block) this.$p6d_block.toggle(!!this.data.can_schedule && PP.views.includes(this.view));
		if (this.p6d.notes_for !== this.data) {
			// Notes read for days off the screen are as old as the last load.
			this.p6d.notes = {};
			this.p6d.asked = {};
			this.p6d.notes_for = this.data;
		}
		this.p6d_decorate();
		// One Conflict center read per load (debounced), never one per render.
		const center = this.p6d.center;
		if (center && this.p6d.counted !== this.data) {
			this.p6d.counted = this.data;
			center.schedule();
		}
	},

	// The route, heatmap and My week views have no Conflicts or Block time button.
	p6d_set_mode(mode) {
		const calendar = mode === "";
		if (this.$p6d_conflicts) this.$p6d_conflicts.toggle(calendar);
		if (this.$p6d_block && !calendar) this.$p6d_block.hide();
	},

	// An all-day personal block makes the day read "Unavailable" rather than "Off".
	p6d_state(day, state) {
		if (!day || day.off !== PP6D.unavailable || Number(day.capacity) > 0) return;
		const booked = Number(day.booked) || 0;
		state.text = booked > 0.01 ? __("Unavailable, {0}h booked", [pp_hours(booked)]) : __("Unavailable");
	},

	// A block in the crew view: a hatched "Unavailable 2–4 pm" bar, never a card that drags.
	p6d_block_html(resource, ymd, booking) {
		const kit = this.p6d_kit();
		if (!kit) return "";
		return kit.blocks.chip_html(booking, {
			note: this.p6d_block_note(booking.ref),
			date: ymd,
			resource,
			person: resource,
			editable: this.p6d_can_edit_block(resource),
		});
	},

	// Under a day's head in the week and month views: its notes, then everyone shown who is blocked.
	p6d_day_html(ymd, resources) {
		const kit = this.p6d_kit();
		if (!kit || !this.data) return "";
		const month = this.view === "month";
		const can = !!this.data.can_schedule;
		const parts = [
			kit.blocks.notes_html(this.p6d_notes_of(ymd), { date: ymd, compact: month, can_add: can && !month, project_link: true }),
		];
		(resources || []).forEach((person) => {
			this.p6d_blocks_of(person.name, ymd).forEach((booking) => {
				parts.push(
					kit.blocks.chip_html(booking, {
						who: month ? this.p6a_initials(person.name, person.label) : person.label,
						note: month ? "" : this.p6d_block_note(booking.ref),
						date: ymd,
						resource: person.name,
						person: person.name,
						compact: month,
						editable: this.p6d_can_edit_block(person.name),
					})
				);
			});
		});
		return parts.join("");
	},

	// The crew view's notes row under its column heads; left out when there is nothing to show or add.
	p6d_crew_notes_row(days) {
		const kit = this.p6d_kit();
		if (!kit || !this.data) return "";
		const can = !!this.data.can_schedule;
		if (!can && !days.some((ymd) => this.p6d_notes_of(ymd).length)) return "";
		const cells = days.map(
			(ymd) =>
				`<div class="pp-p6d-crew-notes">${kit.blocks.notes_html(this.p6d_notes_of(ymd), {
					date: ymd,
					can_add: can,
					project_link: true,
				})}</div>`
		);
		return `<div class="pp-p6d-crew-notes pp-p6d-crew-corner">${pp_esc(__("Notes"))}</div>${cells.join("")}`;
	},

	// ------------------------------------------------------------------ what the page knows

	p6d_notes_of(ymd) {
		return ((this.data && this.data.day_notes) || {})[ymd] || [];
	},

	p6d_blocks_of(resource, ymd) {
		const day = resource ? this.day_of(resource, ymd) : null;
		return ((day && day.bookings) || []).filter((booking) => booking && booking.kind === "block" && booking.ref);
	},

	// A block's note: ONLY from the payload's block_notes, which the server fills for the viewer (the
	// planners read every note, a person their own). Every block this page draws reads it here.
	p6d_block_note(name) {
		const notes = (this.data && this.data.block_notes) || {};
		return name && notes[name] ? String(notes[name]) : "";
	},

	p6d_block_of(booking, resource, ymd) {
		return { name: booking.ref, date: ymd, resource, all_day: !!booking.all_day, slot: booking.slot || null };
	},

	p6d_own_resource() {
		const me = frappe.session && frappe.session.user;
		const own = ((this.data && this.data.resources) || []).find((person) => person.user && person.user === me);
		return own ? own.name : "";
	},

	// A planner edits anyone's block; everyone else only their own.
	p6d_can_edit_block(resource) {
		if (this.data && this.data.can_schedule) return true;
		return !!resource && resource === this.p6d_own_resource();
	},

	// Who may be offered "Block time…" for `resource` (none: the person themselves, if on the planner).
	p6d_can_block(resource) {
		if (this.data && this.data.can_schedule) return true;
		const own = this.p6d_own_resource();
		return !!own && (!resource || resource === own);
	},

	p6d_default_date() {
		const today = this.today();
		const { start, end } = this.range();
		return today >= pp_ymd(start) && today <= pp_ymd(end) ? today : pp_ymd(start);
	},

	p6d_reload() {
		if (this.view === PP3B.my_week) return this.load_my_week();
		if (PP.views.includes(this.view)) return this.load();
		return null;
	},

	// ------------------------------------------------------------------ clicks

	p6d_click(e) {
		if (!this.p6d || Date.now() < this.click_blocked_until) return;
		const target = e.target;
		if (!target || !target.closest) return;
		const take = () => {
			e.stopPropagation();
			e.preventDefault();
		};
		const marker = target.closest("[data-p6d-conflict]");
		if (marker) {
			take();
			this.p6d_open_conflicts({ key: marker.getAttribute("data-p6d-conflict") });
			return;
		}
		const add = target.closest("[data-pk-note-add]");
		if (add) {
			take();
			this.p6d_note_form({ date: add.getAttribute("data-pk-note-add") });
			return;
		}
		const note = target.closest("[data-pk-note-open]");
		if (note) {
			take();
			this.p6a_open_day(note.getAttribute("data-pk-note-open"));
			return;
		}
		const block = target.closest("[data-pk-block]");
		if (block) {
			take();
			this.p6d_block_click(block);
		}
	},

	p6d_block_click(el) {
		const kit = this.p6d_kit();
		const name = el.getAttribute("data-pk-block");
		const ymd = el.getAttribute("data-pk-block-date");
		if (!kit || !name) return;
		if (this.view === PP3B.my_week) {
			const mine = ((this.my_week_data && this.my_week_data.blocks) || []).find((block) => block && block.name === name);
			if (mine) this.p6d_my_week_block(mine.date, mine);
			return;
		}
		const resource = el.getAttribute("data-pk-block-resource");
		const booking = this.p6d_blocks_of(resource, ymd).find((entry) => entry.ref === name);
		if (!booking) return;
		if (this.p6d_can_edit_block(resource)) {
			this.p6d_block_form({ block: this.p6d_block_of(booking, resource, ymd), resource, date: ymd });
			return;
		}
		kit.toast(`${this.resource_label(resource)}: ${kit.blocks.block_text(booking)}`, { tone: "info" });
	},

	// ------------------------------------------------------------------ blocks and notes

	// The block form: a planner picks anyone; everyone else blocks only their own time (the form then
	// sends no person at all, and the server makes it the caller's own).
	p6d_block_form(opts) {
		const kit = this.p6d_kit();
		if (!kit || !this.data) return;
		opts = opts || {};
		const can = !!this.data.can_schedule;
		const own = this.p6d_own_resource();
		const resource = opts.resource || (can ? "" : own);
		if (!can && resource && resource !== own) {
			frappe.show_alert({ message: __("You can only block your own time."), indicator: "orange" }, 6);
			return;
		}
		const people = (this.data.resources || [])
			.slice()
			.sort((a, b) => String(a.label).localeCompare(String(b.label)))
			.map((person) => ({ value: person.name, label: person.label }));
		kit.blocks.form({
			block: opts.block || null,
			note: opts.block ? this.p6d_block_note(opts.block.name) : "",
			date: opts.date || this.p6d_default_date(),
			resource,
			resource_label: resource ? this.resource_label(resource) : "",
			people: can ? people : null,
			self_only: !can,
			on_saved: () => this.p6d_reload(),
			on_deleted: () => this.p6d_reload(),
		});
	},

	p6d_note_form(opts) {
		const kit = this.p6d_kit();
		if (!kit || !this.data || !this.data.can_schedule) return;
		opts = opts || {};
		kit.blocks.note_form({
			note: opts.note || null,
			date: opts.date || this.p6d_default_date(),
			on_saved: () => this.load(),
			on_deleted: () => this.load(),
		});
	},

	// "Block time…" in a person's drawer (6A), for a planner or for the person themselves.
	p6d_person_actions(resource) {
		if (!this.data || !this.p6d_can_block(resource)) return [];
		return [
			{
				label: __("Block time…"),
				on_click: (info) => this.p6d_block_form({ resource: (info && info.resource) || resource, date: info && info.date }),
			},
		];
	},

	// The day drawer (6A): the day's notes in full at the top, Edit on each for a planner, "Add a note".
	p6d_day_peek_opts() {
		const can = !!(this.data && this.data.can_schedule);
		return {
			head_html: (ymd) => this.p6d_peek_head(ymd),
			on_head_click: (e, ymd) => this.p6d_peek_click(e, ymd),
			actions: can ? [{ label: __("Add a note"), on_click: (ymd) => this.p6d_note_form({ date: ymd }) }] : [],
		};
	},

	p6d_peek_head(ymd) {
		const kit = this.p6d_kit();
		if (!kit || !this.data) return "";
		const notes = this.p6d_peek_notes(ymd);
		return kit.blocks.note_list_html(notes || [], { loading: notes === null, project_link: true });
	},

	// The notes of a day on screen come with the planner; a day the drawer stepped past them is read
	// once (get_day_notes) and drawn when it arrives.
	p6d_peek_notes(ymd) {
		const { start, end } = this.range();
		if (ymd >= pp_ymd(start) && ymd <= pp_ymd(end)) return this.p6d_notes_of(ymd);
		const p6d = this.p6d;
		if (p6d.notes[ymd]) return p6d.notes[ymd];
		if (!p6d.asked[ymd]) {
			p6d.asked[ymd] = true;
			const kit = this.p6d_kit();
			if (!kit) return [];
			kit.blocks
				.get_notes(ymd)
				.then((notes) => {
					p6d.notes[ymd] = notes || [];
					const peek = this.p6a && this.p6a.peek;
					if (peek && peek.is_open() && typeof peek.render === "function" && peek.date && peek.date() === ymd) peek.render();
				})
				.catch(() => {
					p6d.notes[ymd] = [];
				});
		}
		return null;
	},

	p6d_peek_click(e, ymd) {
		const target = e.target;
		if (!target || !target.closest) return false;
		const edit = target.closest("[data-pk-note-edit]");
		if (edit) {
			const name = edit.getAttribute("data-pk-note-edit");
			const note = (this.p6d_peek_notes(ymd) || []).find((entry) => entry.name === name);
			if (note) this.p6d_note_form({ note, date: ymd });
			return true;
		}
		const project = target.closest("[data-pk-note-project]");
		if (project) {
			this.p6a_open_project(project.getAttribute("data-pk-note-project"));
			return true;
		}
		return false;
	},

	// ------------------------------------------------------------------ My week

	// The phone view: the person's own blocks with their notes, the notes for their group, and "Block
	// my time" on each day, which only ever blocks their own time.
	p6d_my_week_day($day, $dh, entry, data) {
		const kit = this.p6d_kit();
		if (!kit || !data || !data.resource || !entry) return;
		$('<button type="button" class="btn btn-default btn-xs pp-p6d-mw-btn"></button>')
			.text(__("Block my time"))
			.attr("title", __("Mark yourself unavailable for part of this day or all of it"))
			.on("click", () => this.p6d_my_week_block(entry.date, null))
			.appendTo($dh);
		const notes = (data.day_notes || {})[entry.date] || [];
		const mine = (data.blocks || []).filter((block) => block && block.date === entry.date);
		if (!notes.length && !mine.length) return;
		const $box = $('<div class="pp-p6d-mw"></div>');
		if (notes.length) $box.append(kit.blocks.notes_html(notes, { date: entry.date, full: true }));
		// get_my_week returns only the person's own blocks, with the note it read through block_notes.
		mine.forEach((block) =>
			$box.append(kit.blocks.chip_html(block, { note: block.note, date: entry.date, editable: !!block.can_edit }))
		);
		$box.insertAfter($dh);
		// An all-day block says so itself; the plain "Off" line under it would only repeat it.
		if (mine.some((block) => block.all_day) && !(entry.items || []).length && !entry.travel) {
			$day.children(".pp-mw-empty").remove();
		}
	},

	p6d_my_week_block(ymd, block) {
		const kit = this.p6d_kit();
		const data = this.my_week_data;
		if (!kit || !data || !data.resource) return;
		kit.blocks.form({
			block: block || null,
			note: block ? block.note : "",
			date: ymd,
			resource_label: data.label,
			self_only: true,
			can_delete: block ? !!block.can_edit : false,
			on_saved: () => this.load_my_week(),
			on_deleted: () => this.load_my_week(),
		});
	},

	// ------------------------------------------------------------------ the Conflict center

	p6d_open_conflicts(focus) {
		const center = this.p6d && this.p6d.center;
		if (center) center.open(focus || null);
	},

	p6d_range() {
		if (!this.data || !PP.views.includes(this.view)) return null;
		const { start, end } = this.range();
		const last = moment.min(end.clone(), start.clone().add(PP6D.max_days - 1, "days"));
		return { start: pp_ymd(start), end: pp_ymd(last), label: `${pp_when(pp_ymd(start))} – ${pp_when(pp_ymd(last))}` };
	},

	p6d_after_list() {
		const center = this.p6d && this.p6d.center;
		const count = center ? center.count() : null;
		if (this.$p6d_conflicts) {
			this.$p6d_conflicts
				.text(count == null ? __("Conflicts") : __("Conflicts ({0})", [count]))
				.toggleClass("pp-p6d-hot", Number(count) > 0)
				.attr(
					"title",
					count
						? __("{0} conflict(s) nobody has kept in the dates on screen", [count])
						: __("Everything wrong in the dates on screen, with one-click fixes")
				);
		}
		this.p6d_decorate();
	},

	// A fix from the Conflict center: exactly the method and arguments the server worked out, sent
	// through commit() (or send() for a task that is not on the board) like any drag, so the reason
	// prompt, Draft mode and Undo all apply. Nothing but this page's own writes may run from here.
	p6d_run_fix(job) {
		const method = PP6D.fix_methods[job.fix.method];
		if (!method || !job.args || !job.args.task) {
			frappe.show_alert({ message: __("That fix cannot run from this planner."), indicator: "orange" }, 6);
			return Promise.resolve(null);
		}
		const card = this.by_task[job.args.task] || null;
		const message = this.p6d_fix_message(job, card);
		if (card) return this.commit(card, method, job.args, message);
		return this.send(method, job.args, { message }).finally(() => this.load());
	},

	p6d_fix_message(job, card) {
		const fix = job.fix;
		const args = job.args;
		const item = (job.conflict.items || []).find((entry) => entry.name === args.task) || {};
		const subject = (card && (card.subject || card.name)) || item.title || args.task;
		const picked = (job.picked && job.picked.label) || "";
		if (fix.type === "next_free_day") return __("{0} moved to {1}", [subject, pp_when(args.start)]);
		if (fix.type === "pencil") return __("{0} is now pencil", [subject]);
		if (fix.type === "pick_free" && args.to_resource) {
			return __("{0}: {1} in place of {2}", [subject, this.resource_label(args.to_resource, picked), this.resource_label(args.from_resource)]);
		}
		if (fix.type === "pick_free") return __("{0} added to {1}", [this.resource_label(args.resource, picked), subject]);
		return __("{0} updated", [subject]);
	},

	// A record in a conflict: this planner's task opens in its side panel; anything else is a link.
	p6d_open_item(conflict, item) {
		const kit = this.p6d_kit();
		const go = (fn) => (kit ? kit.drawer.navigate(fn) : fn());
		if (item.own && item.doctype === "Task") {
			const card = this.by_task[item.name];
			if (card) this.open_card(card);
			else go(() => frappe.set_route("Form", "Task", item.name));
			return;
		}
		if (item.planner === "maintenance") {
			if (frappe.user && frappe.user.has_role && frappe.user.has_role(PP6D.maintenance_roles)) {
				go(() => frappe.set_route("maintenance-planner", "week", conflict.date));
			} else {
				frappe.show_alert({ message: __("A maintenance visit: it moves on the Maintenance Planner."), indicator: "blue" });
			}
		} else if (item.planner === "travel") {
			go(() => frappe.set_route("Form", "Travel Trip", item.name));
		} else if (item.doctype === "Task") {
			go(() => frappe.set_route("Form", "Task", item.name));
		}
	},

	// Who cannot be picked to take a task: whoever it is booked on now and everyone already on it.
	p6d_exclude(conflict, fix) {
		const out = {};
		const card = this.by_task[fix.name];
		((card && card.crew) || []).forEach((member) => {
			if (member && member.resource) out[member.resource] = __("Already on it");
		});
		if (fix.resource) out[fix.resource] = __("Booked on it now");
		return out;
	},

	p6d_pick_context(conflict, fix) {
		const card = this.by_task[fix.name];
		const item = (conflict.items || []).find((entry) => entry.name === fix.name) || {};
		const subject = (card && (card.subject || card.name)) || item.title || fix.name;
		if (fix.args && fix.args.from_resource) {
			return __("In place of {0} on {1}", [this.resource_label(fix.args.from_resource), subject]);
		}
		if ((fix.credentials || []).length) return __("Add someone to {0}. It needs: {1}", [subject, fix.credentials.join(", ")]);
		return __("Add someone to {0}", [subject]);
	},

	// A card in a conflict nobody kept: its red Conflict chip (or, for a missing qualification, the
	// amber chip that says so) becomes the way into the Conflict center; a card with neither gets one
	// small marker. Never two marks for one conflict.
	p6d_decorate() {
		const center = this.p6d && this.p6d.center;
		if (!center || !this.data) return;
		const root = this.$body[0];
		root.querySelectorAll("[data-p6d-conflict]").forEach((node) => this.p6d_undecorate(node));
		const index = center.index();
		const missing = __("Missing {0}", ["|"]).split("|")[0];
		root.querySelectorAll(".pp-card[data-task]").forEach((el) => {
			const found = index[`Task|${el.getAttribute("data-task")}`];
			if (!found) return;
			const row = el.getAttribute("data-resource");
			const ymd = el.getAttribute("data-date");
			const hit = row ? found.find((entry) => entry.resource === row && entry.date === ymd) : found[0];
			const holder = el.querySelector(".pp-card-chips");
			if (!hit || !holder) return;
			const chips = Array.from(holder.querySelectorAll(".pp-chip"));
			let chip = chips.find((node) => node.classList.contains("pp-red") && node.textContent === __("Conflict"));
			if (!chip && hit.kind === "qualification") {
				chip = chips.find((node) => node.classList.contains("pp-amber") && missing && node.textContent.indexOf(missing) === 0);
			}
			if (!chip) {
				chip = document.createElement("span");
				chip.className = "pp-chip pp-red pp-p6d-mark";
				chip.textContent = __("Conflict");
				holder.appendChild(chip);
			}
			chip.classList.add("pp-p6d-link");
			chip.setAttribute("data-p6d-conflict", hit.key);
			chip.setAttribute("role", "button");
			chip.setAttribute("tabindex", "0");
			chip.setAttribute("title", __("Open the Conflict center at this conflict"));
		});
	},

	p6d_undecorate(node) {
		if (node.classList.contains("pp-p6d-mark")) {
			node.remove();
			return;
		}
		node.classList.remove("pp-p6d-link");
		["data-p6d-conflict", "role", "tabindex", "title"].forEach((name) => node.removeAttribute(name));
	},

	// ------------------------------------------------------------------ the right-click menu

	// 6B's menu calls this with what was right-clicked (phase6-wave2's registry).
	p6d_menu_items(target) {
		if (!this.p6d || !this.data || !target) return [];
		const out = [];
		const center = this.p6d.center;
		if (target.kind === "card" && target.card && center) {
			const hits = center.find("Task", target.card.name);
			if (hits && hits.length) {
				out.push({ label: __("Show conflicts"), on_click: () => this.p6d_open_conflicts({ key: hits[0].key }) });
			}
		}
		if (target.kind === "cell" && target.ymd) {
			if (center) out.push({ label: __("Conflicts this day"), on_click: () => this.p6d_open_conflicts({ date: target.ymd }) });
			if (this.data.can_schedule) {
				out.push({ label: __("Add a day note…"), on_click: () => this.p6d_note_form({ date: target.ymd }) });
			}
		}
		if ((target.kind === "cell" || target.kind === "person") && this.p6d_can_block(target.resource)) {
			out.push({
				label: __("Block time…"),
				on_click: () => this.p6d_block_form({ resource: target.resource || "", date: target.ymd || this.p6d_default_date() }),
			});
		}
		return out;
	},

	// ------------------------------------------------------------------ legend

	p6d_legend_sections() {
		const kit = this.p6d_kit();
		if (!kit) return [];
		const item = (sample_html, text) => ({ sample_html, text: __(text) });
		return [
			{
				title: __("Unavailable time, day notes and conflicts"),
				items: [
					item(
						kit.blocks.chip_html({ name: "", slot: ["14:00", "16:00"] }, {}),
						"A personal block: the person is unavailable then. The planners and the person see its note; everyone else sees “Unavailable”. Click it to change it, if it is yours to change."
					),
					item(kit.blocks.chip_html({ name: "", all_day: 1 }, {}), "Unavailable all day: the day counts like time off."),
					item(
						kit.blocks.notes_html([{ note: __("Shop meeting 7 am") }], {}),
						"A note for the crew on that day. Hover over it or tap it to read it all; planners add one with + note."
					),
					item(
						`<span class="pp-chip pp-red pp-p6d-link">${pp_esc(__("Conflict"))}</span>`,
						"Click a Conflict chip to open the Conflict center at that conflict."
					),
					item(
						"",
						"Conflicts (N) on the toolbar lists everything wrong in the dates on screen, with one-click fixes. A fix that causes a conflict still asks for a reason, like a drag, and Undo puts it back."
					),
				],
			},
		];
	},
};

Object.assign(ProjectPlanner.prototype, PP6D_METHODS);
