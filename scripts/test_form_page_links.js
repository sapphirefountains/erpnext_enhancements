#!/usr/bin/env node
/**
 * Links from forms to the pages that work on the same record (custom pages S5b, v1.667.0),
 * run against a model of Frappe v16.50's router.
 *
 * Loads the REAL form scripts and the REAL page scripts into vm contexts that share one fake
 * Desk, presses each "Open in ..." button, and checks that the target page lands in the right
 * state:
 *
 *   Project                      View > Open in Project Planner   the planner's Project filter
 *   Project Quality Inspection   Open in Inspection Wizard        that inspection, section 0
 *   Rental Booking               View > Open in Rental Planner    the board from the booking's
 *                                                                 first day (?start=, S5a)
 *   Maintenance Day Board        a submitted visit's card links to Visit Review, and the toolbar
 *                                opens the Maintenance Planner's week for the day shown
 *
 * The router model follows v16.50 where the links depend on it. set_route moves a plain object
 * among its parts into frappe.route_options (make_url) and writes those options as the query
 * with the path (frappe PR #40040, new in v16.50; before it the path was pushed alone). It pushes
 * an entry, or replaces the one on screen when frappe.route_flags.replace_route is set, and does
 * nothing when the path and query are already on screen. Every route change copies the address's
 * query into frappe.route_options, creating the object when it is null (set_route_options_from_url),
 * then shows the page, which runs on_page_show. Back and Forward land on an entry the same way.
 *
 * The pages are not built: each page's class is taken from its own script and its real routing
 * methods run on an instance whose drawing methods are recorders. What is under test is the
 * hand-off: the route and options a button sends, and what the page makes of them.
 *
 * Run: node scripts/test_form_page_links.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.join(__dirname, "..", "erpnext_enhancements");
const FILES = {
	projectLink: path.join(APP, "public", "js", "project_enhancements", "project_planner_link.js"),
	inspectionForm: path.join(APP, "quality", "doctype", "project_quality_inspection", "project_quality_inspection.js"),
	bookingForm: path.join(APP, "asset_management", "doctype", "rental_booking", "rental_booking.js"),
	projectPlanner: path.join(APP, "project_enhancements", "page", "project_planner", "project_planner.js"),
	inspectionWizard: path.join(APP, "quality", "page", "inspection_wizard", "inspection_wizard.js"),
	rentalPlanner: path.join(APP, "asset_management", "page", "rental_planner", "rental_planner.js"),
	dayBoard: path.join(APP, "sapphire_maintenance", "page", "maintenance_day_board", "maintenance_day_board.js"),
};

let checks = 0;
let failures = 0;
const tests = [];
const test = (name, fn) => tests.push({ name, fn });

function check(label, actual, expected) {
	checks += 1;
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a !== e) {
		failures += 1;
		console.log(`  FAIL ${label}\n    expected ${e}\n    actual   ${a}`);
	}
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle() {
	for (let i = 0; i < 8; i++) await tick();
}

const ALL_PAGES = ["project-planner", "inspection-wizard", "rental-planner", "visit-review", "maintenance-planner", "maintenance-day-board"];

// ------------------------------------------------------------------ the Desk and its router

function makeDesk(options) {
	const o = options || {};
	const desk = {
		entries: [o.start || "/desk/project/PRJ-00001"],
		index: 0,
		today: o.today || "2026-10-14",
		route: [],
		shows: [],
		handlers: {},
		forms: {},
		prefs: {},
		calls: [],
	};
	const current = () => desk.entries[desk.index];
	const location = {
		get pathname() {
			const url = current();
			const q = url.indexOf("?");
			return q < 0 ? url : url.slice(0, q);
		},
		get search() {
			const url = current();
			const q = url.indexOf("?");
			return q < 0 ? "" : url.slice(q);
		},
	};
	const history = {
		state: null,
		get length() {
			return desk.entries.length;
		},
		pushState(_state, _title, url) {
			desk.entries = desk.entries.slice(0, desk.index + 1);
			desk.entries.push(url);
			desk.index += 1;
		},
		replaceState(_state, _title, url) {
			desk.entries[desk.index] = url;
		},
	};
	const isPlainObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
	const pages = new Proxy(
		{},
		{
			get(target, key) {
				if (!(key in target)) target[key] = {};
				return target[key];
			},
		}
	);

	const frappe = {
		route_options: null,
		route_flags: {},
		boot: { page_info: {} },
		pages,
		datetime: {
			get_today: () => desk.today,
			now_time: () => "09:00:00",
			str_to_user: (value) => String(value),
		},
		utils: {
			escape_html: (value) =>
				String(value == null ? "" : value)
					.replace(/&/g, "&amp;")
					.replace(/</g, "&lt;")
					.replace(/>/g, "&gt;")
					.replace(/"/g, "&quot;")
					.replace(/'/g, "&#39;"),
			get_url_arg: (name) => new URLSearchParams(location.search).get(name) || "",
			get_form_link: (doctype, name) =>
				"/desk/" + doctype.toLowerCase().replace(/ /g, "-") + "/" + encodeURIComponent(name),
		},
		ui: {
			form: {
				on(doctype, handlers) {
					(desk.forms[doctype] = desk.forms[doctype] || []).push(handlers);
				},
			},
		},
		db: { get_value: () => Promise.resolve({ message: null }) },
		// Server calls are recorded, and answer nothing.
		call(opts) {
			desk.calls.push({ method: opts.method, args: opts.args || {} });
			return Promise.resolve({});
		},
		user: { has_role: () => false },
		get_route: () => desk.route,
		set_route(...args) {
			const parts = args.length === 1 && Array.isArray(args[0]) ? args[0] : args;
			// make_url: a plain object among the parts becomes frappe.route_options.
			const segments = [];
			parts.forEach((part) => {
				if (isPlainObject(part)) frappe.route_options = part;
				else segments.push(encodeURIComponent(String(part)));
			});
			const route_options = frappe.route_options || {};
			const query = Object.entries(route_options)
				.map(
					([key, value]) =>
						`${key}=` + encodeURIComponent(value !== null && typeof value === "object" ? JSON.stringify(value) : String(value))
				)
				.join("&");
			push_state("/desk/" + segments.join("/"), query ? `?${query}` : "");
			frappe.route_flags = {};
			return Promise.resolve();
		},
	};

	function push_state(pathname, query) {
		if (location.pathname !== pathname || location.search !== query) {
			history[frappe.route_flags.replace_route ? "replaceState" : "pushState"](null, null, pathname + query);
			route();
		}
	}

	function route() {
		desk.route = location.pathname
			.replace(/^\/desk\/?/, "")
			.split("/")
			.filter(Boolean)
			.map(decodeURIComponent);
		// set_route_options_from_url
		if (!frappe.route_options) frappe.route_options = {};
		new URLSearchParams(location.search).forEach((value, key) => {
			frappe.route_options[key] = value;
		});
		desk.shows.push({ route: desk.route.slice(), options: Object.assign({}, frappe.route_options) });
		const show = desk.handlers[desk.route[0]];
		if (show) show();
	}

	const storage = {
		getItem: (key) => (key in desk.prefs ? desk.prefs[key] : null),
		setItem: (key, value) => {
			desk.prefs[key] = String(value);
		},
		removeItem: (key) => {
			delete desk.prefs[key];
		},
	};
	const window = { location, history, localStorage: storage };

	Object.assign(desk, {
		frappe,
		window,
		here: () => current(),
		open(url) {
			history.pushState(null, null, url);
			route();
		},
		back() {
			desk.index -= 1;
			route();
		},
		forward() {
			desk.index += 1;
			route();
		},
		allow(names) {
			frappe.boot.page_info = {};
			(names || ALL_PAGES).forEach((name) => (frappe.boot.page_info[name] = { title: name, modified: "" }));
		},
	});
	desk.allow(o.pages);
	route();
	return desk;
}

function translate(text, args) {
	return String(text).replace(/\{(\d+)\}/g, (_m, i) => (args && args[i] !== undefined ? String(args[i]) : ""));
}

// A strict YYYY-MM-DD check is all the routing code asks of moment.
function fakeMoment(value, format, strict) {
	const text = String(value == null ? "" : value);
	const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
	const valid =
		!!m &&
		(() => {
			const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
			return d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3];
		})();
	return { isValid: () => (strict ? valid : valid), format: () => text };
}

// A recording stand-in: every element remembers its markup, text and html, and any other method
// returns the element.
function makeJQuery(made) {
	function element(html) {
		const state = { made: typeof html === "string" ? html : "", html: "", text: "", value: "" };
		const self = new Proxy(function () {}, {
			get(_target, prop) {
				if (prop === "__state") return state;
				if (prop === "then") return undefined;
				if (prop === "html") {
					return (value) => {
						if (value === undefined) return state.html;
						state.html = String(value);
						return self;
					};
				}
				if (prop === "text") {
					return (value) => {
						if (value === undefined) return state.text;
						state.text = String(value);
						return self;
					};
				}
				if (prop === "val") {
					return (value) => {
						if (value === undefined) return state.value;
						state.value = value;
						return self;
					};
				}
				if (prop === "find") return () => element();
				return () => self;
			},
		});
		made.push(self);
		return self;
	}
	return (html) => element(html);
}

function load(desk, file, extra) {
	const sandbox = Object.assign(
		{
			frappe: desk.frappe,
			window: desk.window,
			__: translate,
			moment: fakeMoment,
			console,
			setTimeout: () => 0,
			clearTimeout: () => {},
			setInterval: () => 0,
			clearInterval: () => {},
			document: { hidden: false, getElementById: () => null },
			Promise,
			URLSearchParams,
		},
		extra || {}
	);
	const context = vm.createContext(sandbox);
	vm.runInContext(fs.readFileSync(file, "utf-8"), context, { filename: file });
	return context;
}

// The form: refresh runs every handler a script registered for the doctype, and the buttons it
// added are recorded with their group.
function openForm(desk, doctype, doc, opts) {
	const buttons = [];
	const frm = {
		doctype,
		doc: Object.assign({ doctype, docstatus: 0 }, doc),
		is_new: () => !!(opts && opts.is_new),
		is_dirty: () => false,
		set_df_property() {},
		set_query() {},
		trigger() {},
		refresh_field() {},
		reload_doc() {},
		call: () => Promise.resolve({}),
		dashboard: { set_headline_alert() {} },
		add_custom_button(label, fn, group) {
			buttons.push({ label, fn, group: group || null });
			return { addClass() {
				return this;
			} };
		},
	};
	(desk.forms[doctype] || []).forEach((handlers) => handlers.refresh && handlers.refresh(frm));
	return {
		frm,
		buttons,
		press(label) {
			const button = buttons.find((b) => b.label === label);
			if (!button) throw new Error(`no "${label}" button; have ${JSON.stringify(buttons.map((b) => b.label))}`);
			button.fn();
		},
	};
}

// ------------------------------------------------------------------ the target pages

function projectPlanner(desk, init, extra) {
	const context = load(desk, FILES.projectPlanner, extra);
	const ProjectPlanner = vm.runInContext("ProjectPlanner", context);
	const pp = Object.create(ProjectPlanner.prototype);
	Object.assign(pp, { project: "", pm: "", group: "", show_all_unscheduled: true, p6c: null, loads: [] }, init || {});
	pp.set_mode = () => {};
	pp.load = () => pp.loads.push({ view: pp.view, anchor: pp.anchor, project: pp.project, pm: pp.pm });
	pp.p6c_kit = () => null;
	pp.p6c_sync_controls = () => {};
	desk.handlers["project-planner"] = () => pp.handle_route();
	return pp;
}

function inspectionWizard(desk) {
	const context = load(desk, FILES.inspectionWizard);
	const InspectionWizard = vm.runInContext("InspectionWizard", context);
	const iw = Object.create(InspectionWizard.prototype);
	iw.opened = [];
	iw.lists = 0;
	iw.commit_active_input = () => {};
	iw.clear_screen = () => 0;
	iw.show_inspection = (name, index) => iw.opened.push([name, index]);
	iw.render_list = () => {
		iw.lists += 1;
	};
	desk.handlers["inspection-wizard"] = () => iw.handle_route();
	return iw;
}

function rentalPlanner(desk) {
	const context = load(desk, FILES.rentalPlanner);
	const RentalPlanner = vm.runInContext("RentalPlanner", context);
	const rp = Object.create(RentalPlanner.prototype);
	Object.assign(rp, { pushed: [], created: null, board_start: desk.today, boards: [] });
	rp.current_screen = () => "board";
	rp.show_board = () => rp.boards.push(rp.board_start);
	rp.show_step = () => {};
	rp.show_created = () => {};
	rp.first_unfinished_step = () => "dates";
	desk.handlers["rental-planner"] = () => rp.handle_route();
	return { rp, reads_start: typeof RentalPlanner.prototype.take_route_start === "function" };
}

function dayBoard(desk, data) {
	const made = [];
	const page = {
		buttons: [],
		body: null,
		main: null,
		add_inner_button(label, fn) {
			page.buttons.push({ label, fn });
		},
		set_title() {},
	};
	const $ = makeJQuery(made);
	page.body = $();
	page.main = page.body;
	const kit = {
		esc: desk.frappe.utils.escape_html,
		call: () => Promise.resolve(data),
		state: { render() {} },
		toast() {},
	};
	const window = Object.assign({}, desk.window, { sf_kit_ready: () => Promise.resolve(kit) });
	desk.frappe.ui.make_app_page = () => page;
	load(desk, FILES.dayBoard, { $, window });
	const wrapper = {};
	desk.frappe.pages["maintenance-day-board"].on_page_load(wrapper);
	desk.handlers["maintenance-day-board"] = () => wrapper.mdb_day_board.show();
	const board = made.find((el) => el.__state.made.includes('class="mdb-board"'));
	return { page, wrapper, html: () => board.__state.html };
}

// ------------------------------------------------------------------ Project -> Project Planner

function projectDesk(pages) {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", pages });
	load(desk, FILES.projectLink);
	return desk;
}

// A customer job the planner plans, open.
const JOB = { project_type: "Build", status: "Active" };
const project = (fields) => Object.assign({}, JOB, fields);

// The person's own filters, remembered before they ever followed a link.
const MINE = { ee_project_planner_project: "PRJ-MINE", ee_project_planner_pm: "me@example.com" };

test("Project: View > Open in Project Planner opens the planner on that project", () => {
	const desk = projectDesk();
	Object.assign(desk.prefs, MINE);
	const pp = projectPlanner(desk, { project: "PRJ-MINE", pm: "me@example.com" });
	const form = openForm(desk, "Project", project({ name: "PRJ-00001" }));
	check("the button is under View", form.buttons.map((b) => [b.label, b.group]), [["Open in Project Planner", "View"]]);
	// An options object replaces whatever an earlier page left in route_options.
	desk.frappe.route_options = { stale: "1" };
	form.press("Open in Project Planner");
	check("the page saw the project in route_options", desk.shows.slice(-1)[0].options, { project: "PRJ-00001" });
	check("the planner filters to it", pp.project, "PRJ-00001");
	check("the PM filter is cleared, so it cannot hide the project's cards", pp.pm, "");
	check("the unscheduled tray is back to its short list", pp.show_all_unscheduled, false);
	check("route_options is consumed", desk.frappe.route_options, null);
	check("one load, this week", pp.loads, [{ view: "week", anchor: "2026-10-14", project: "PRJ-00001", pm: "" }]);
	check("a history entry of its own, its ?project= taken out in place", desk.entries, [
		"/desk/project/PRJ-00001",
		"/desk/project-planner",
	]);
	desk.back();
	check("Back returns to the form", desk.route, ["project", "PRJ-00001"]);
	desk.forward();
	check("Forward lands on the planner, which keeps the filter it had", pp.project, "PRJ-00001");
});

test("Project: the link is a one-off and leaves the person's own filters alone", () => {
	const desk = projectDesk();
	Object.assign(desk.prefs, MINE);
	const pp = projectPlanner(desk, {
		project: "PRJ-MINE",
		pm: "me@example.com",
		view: "week",
		p6c: { loaded: true, restored: true, last: { view: "week", project: "PRJ-MINE", pm: "me@example.com" } },
	});
	openForm(desk, "Project", project({ name: "PRJ-00001" })).press("Open in Project Planner");
	check("the planner shows the form's project", pp.project, "PRJ-00001");
	check("the remembered filters are untouched", desk.prefs, MINE);
	pp.p6c_save_last();
	check("no 'last used' save while the link's project is the filter", desk.calls, []);
	// The person picks another filter (the select's own handler sets it and saves it).
	pp.project = "";
	pp.p6c_save_last();
	check(
		"a filter they pick is saved as last used again",
		desk.calls.map((c) => [c.method, JSON.parse(c.args.data).project]),
		[["erpnext_enhancements.api.planner_views_prefs.save_last_view", ""]]
	);
});

test("Project: Back after switching to All does not snap the filter back", () => {
	const desk = projectDesk();
	const pp = projectPlanner(desk);
	openForm(desk, "Project", project({ name: "PRJ-00001" })).press("Open in Project Planner");
	check("arrived filtered", pp.project, "PRJ-00001");
	// "All projects" in the select, then a task opened from the calendar.
	pp.project = "";
	desk.frappe.set_route("Form", "Task", "TASK-0001");
	check("on the task", desk.route, ["Form", "Task", "TASK-0001"]);
	desk.back();
	check("Back is the planner's entry, with no project in it", desk.here(), "/desk/project-planner");
	check("the filter stays All", pp.project, "");
	check("and nothing is left in route_options to apply later", desk.frappe.route_options, {});
});

test("Project: a project that starts later opens on the week it starts", () => {
	const desk = projectDesk();
	const pp = projectPlanner(desk);
	openForm(desk, "Project", project({ name: "PRJ-00002", expected_start_date: "2026-11-02" })).press(
		"Open in Project Planner"
	);
	check("the week holding its start, as pushed", desk.shows.slice(-1)[0].route, ["project-planner", "week", "2026-11-02"]);
	check("its address once the project is taken", desk.here(), "/desk/project-planner/week/2026-11-02");
	check("loaded there with the filter", pp.loads.slice(-1)[0], {
		view: "week",
		anchor: "2026-11-02",
		project: "PRJ-00002",
		pm: "",
	});
	// One already under way opens on this week.
	openForm(desk, "Project", project({ name: "PRJ-00003", expected_start_date: "2026-09-01" })).press(
		"Open in Project Planner"
	);
	check("a started project opens on this week", desk.here(), "/desk/project-planner");
	check("filtered to it", pp.project, "PRJ-00003");
});

test("Project: the planner's last-used view still applies, and the project wins over its filter", () => {
	const desk = projectDesk();
	const pp = projectPlanner(desk, {
		p6c: { loaded: true, restored: false, last: { view: "month", project: "PRJ-OLD", pm: "pm@example.com" } },
	});
	openForm(desk, "Project", project({ name: "PRJ-00001" })).press("Open in Project Planner");
	check("the last-used Month view replaces the entry", desk.here(), "/desk/project-planner/month/2026-10-14");
	check("still one entry past the form", desk.entries.length, 2);
	check("the form's project, not the remembered one", pp.project, "PRJ-00001");
	check("and no remembered PM hiding it", pp.pm, "");
	check("loaded once, on the month", pp.loads, [{ view: "month", anchor: "2026-10-14", project: "PRJ-00001", pm: "" }]);
});

test("Project: a name that needs escaping arrives whole, and other options are left alone", () => {
	const desk = projectDesk();
	const pp = projectPlanner(desk);
	openForm(desk, "Project", project({ name: "PRJ 7/8 & co" })).press("Open in Project Planner");
	check("pushed encoded", desk.entries.length, 2);
	check("decoded for the filter", pp.project, "PRJ 7/8 & co");
	// Another page's key in the same options is not the planner's to take.
	desk.frappe.set_route("project-planner", { project: "PRJ-00009", other: "x" });
	check("only `project` is taken", desk.frappe.route_options, { other: "x" });
	check("and only it leaves the address", desk.here(), "/desk/project-planner?other=x");
	check("the new project applies", pp.project, "PRJ-00009");
});

test("Project: a linked project the planner does not list stays the filter, named in the calendar", () => {
	const desk = projectDesk();
	const made = [];
	const $ = makeJQuery(made);
	const pp = projectPlanner(desk, {}, { $ });
	openForm(desk, "Project", project({ name: "PRJ-00004" })).press("Open in Project Planner");
	Object.assign(pp, { $project: $(), $pm: $(), data: { projects: [{ name: "PRJ-00001", title: "Other" }] } });
	pp.by_project = { "PRJ-00001": pp.data.projects[0] };
	pp.fill_filters();
	check("kept, not reset to all", pp.project, "PRJ-00004");
	check("offered in the select", made.some((el) => el.__state.value === "PRJ-00004"), true);
	// A filter the person picked that the planner no longer lists still resets, as before.
	pp.project = "PRJ-GONE";
	pp.fill_filters();
	check("an unlisted pick resets", pp.project, "");
});

test("Project: no button on a new project, an unplanned or closed one, or for someone the page refuses", () => {
	const fresh = projectDesk();
	check("a new project", openForm(fresh, "Project", project({ name: "new-project-1" }), { is_new: true }).buttons, []);
	check("an internal project", openForm(fresh, "Project", project({ name: "P", project_type: "Internal" })).buttons, []);
	check("no type", openForm(fresh, "Project", project({ name: "P", project_type: "" })).buttons, []);
	check("completed", openForm(fresh, "Project", project({ name: "P", status: "Completed" })).buttons, []);
	check("canceled", openForm(fresh, "Project", project({ name: "P", status: "Canceled" })).buttons, []);
	check(
		"each planner type gets it",
		["Design", "Build", "Service", "Events", "Rent", "Delivery"].map(
			(type) => openForm(fresh, "Project", project({ name: "P", project_type: type })).buttons.length
		),
		[1, 1, 1, 1, 1, 1]
	);
	const refused = projectDesk(["inspection-wizard", "rental-planner"]);
	check("without the page", openForm(refused, "Project", project({ name: "PRJ-00001" })).buttons, []);
});

// ------------------------------------------------------------------ Inspection -> Inspection Wizard

function inspectionDesk(pages) {
	const desk = makeDesk({ start: "/desk/project-quality-inspection/QIR-2026-00001", pages });
	load(desk, FILES.inspectionForm);
	return desk;
}

test("Project Quality Inspection: Open in Inspection Wizard opens that inspection", () => {
	const desk = inspectionDesk();
	const iw = inspectionWizard(desk);
	const form = openForm(desk, "Project Quality Inspection", { name: "QIR-2026-00001" });
	check("one button, on its own", form.buttons.map((b) => [b.label, b.group]), [["Open in Inspection Wizard", null]]);
	// Left by an earlier page: the router copies every query here and nothing clears it.
	desk.frappe.route_options = { stale: "1" };
	form.press("Open in Inspection Wizard");
	check("the wizard's own path form, section 0", desk.here(), "/desk/inspection-wizard/QIR-2026-00001/0");
	check("a history entry of its own", desk.entries.length, 2);
	check("the wizard opens that inspection at its first section", iw.opened, [["QIR-2026-00001", 0]]);
	check("not the list", iw.lists, 0);
	desk.back();
	check("Back returns to the form", desk.route, ["project-quality-inspection", "QIR-2026-00001"]);
});

test("Project Quality Inspection: no button once submitted, when new, or without the page", () => {
	const desk = inspectionDesk();
	check("submitted", openForm(desk, "Project Quality Inspection", { name: "QIR-1", docstatus: 1 }).buttons, []);
	check("canceled", openForm(desk, "Project Quality Inspection", { name: "QIR-1", docstatus: 2 }).buttons, []);
	check("new", openForm(desk, "Project Quality Inspection", { name: "new-1" }, { is_new: true }).buttons, []);
	const refused = inspectionDesk(["project-planner"]);
	check("without the page", openForm(refused, "Project Quality Inspection", { name: "QIR-1" }).buttons, []);
});

// ------------------------------------------------------------------ Rental Booking -> Rental Planner

function bookingDesk(pages) {
	const desk = makeDesk({ start: "/desk/rental-booking/RB-0001", pages });
	load(desk, FILES.bookingForm, { format_currency: (v) => String(v) });
	return desk;
}

const BOOKING = {
	name: "RB-0001",
	status: "Confirmed",
	__onload: { next_statuses: [] },
	delivery_datetime: "2026-11-03 08:00:00",
	setup_datetime: "2026-11-03 10:00:00",
	event_start_datetime: "2026-11-04 18:00:00",
	takedown_datetime: "2026-11-05 09:00:00",
};

test("Rental Booking: View > Open in Rental Planner opens the board on the booking's dates", () => {
	const desk = bookingDesk();
	const { rp, reads_start } = rentalPlanner(desk);
	const form = openForm(desk, "Rental Booking", BOOKING);
	const views = form.buttons.filter((b) => b.group === "View").map((b) => b.label);
	check("under View, in place of the old bare link", views.includes("Open in Rental Planner") && !views.includes("Rental Planner"), true);
	form.press("Open in Rental Planner");
	check("the address carries the first day as ?start=", desk.here(), "/desk/rental-planner?start=2026-11-03");
	check("a history entry of its own", desk.entries.length, 2);
	check("the page saw start in route_options", desk.shows.slice(-1)[0].options, { start: "2026-11-03" });
	check("the board was shown", rp.boards.length, 1);
	// S5a (v1.662.0): the planner reads `start` and consumes it.
	check("the planner reads ?start=", reads_start, true);
	check("the board starts on the delivery day", rp.boards[0], "2026-11-03");
	check("route_options is consumed", desk.frappe.route_options, null);
	check("the address is left as the button wrote it", desk.here(), "/desk/rental-planner?start=2026-11-03");
	desk.back();
	check("Back returns to the form", desk.route, ["rental-booking", "RB-0001"]);
});

test("Rental Booking: the first day falls back to setup, then the event; none means today", () => {
	const desk = bookingDesk();
	rentalPlanner(desk);
	openForm(desk, "Rental Booking", Object.assign({}, BOOKING, { delivery_datetime: null })).press("Open in Rental Planner");
	check("setup day", desk.here(), "/desk/rental-planner?start=2026-11-03");
	desk.back();
	openForm(desk, "Rental Booking", Object.assign({}, BOOKING, { delivery_datetime: null, setup_datetime: null })).press(
		"Open in Rental Planner"
	);
	check("event day", desk.here(), "/desk/rental-planner?start=2026-11-04");
	desk.back();
	// Whatever an earlier page left in route_options stays out of the bare address.
	desk.frappe.route_options = { stale: "1" };
	openForm(desk, "Rental Booking", {
		name: "RB-0002",
		status: "Tentative",
		__onload: { next_statuses: [] },
	}).press("Open in Rental Planner");
	check("no dates yet: the bare board", desk.here(), "/desk/rental-planner");
});

test("Rental Booking: no planner button for someone the page refuses", () => {
	const desk = bookingDesk(["project-planner"]);
	const labels = openForm(desk, "Rental Booking", BOOKING).buttons.map((b) => b.label);
	check("absent", labels.includes("Open in Rental Planner"), false);
});

// ------------------------------------------------------------------ Day Board -> Visit Review, Planner

const BOARD = {
	scheduled: [{ name: "SMR-D1", project: "PRJ-1", project_title: "Site A", completion_percent: 20 }],
	in_progress: [],
	submitted_today: [{ name: "SMR-S1", project: "PRJ-1", project_title: "Site A", completion_percent: 100 }],
	flagged: [{ name: "SMR/F 1", project: "PRJ-1", project_title: "Site A", has_out_of_range_readings: 1 }],
};

test("Day Board: a submitted visit's card links to Visit Review", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-1" });
	const board = dayBoard(desk, BOARD);
	desk.frappe.set_route("maintenance-day-board");
	await settle();
	const html = board.html();
	check("the submitted visit", html.includes('href="/desk/visit-review/SMR-S1"'), true);
	check("the flagged visit, its name encoded", html.includes('href="/desk/visit-review/SMR%2FF%201"'), true);
	check("not the draft", html.includes("visit-review/SMR-D1"), false);
	check("two review links", (html.match(/Open in Visit Review/g) || []).length, 2);
	check("no /app/ link", html.includes('"/app/'), false);
});

test("Day Board: no review link for someone Visit Review refuses", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-1", pages: ["maintenance-day-board", "maintenance-planner"] });
	const board = dayBoard(desk, BOARD);
	desk.frappe.set_route("maintenance-day-board");
	await settle();
	check("none drawn", board.html().includes("visit-review"), false);
	check("the board still drew its cards", board.html().includes("SMR-S1"), true);
});

test("Day Board: Open in Maintenance Planner opens the week of the day shown", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-1" });
	const board = dayBoard(desk, BOARD);
	desk.frappe.set_route("maintenance-day-board");
	await settle();
	const button = board.page.buttons.find((b) => b.label === "Open in Maintenance Planner");
	check("the toolbar button is there", Boolean(button), true);
	desk.frappe.route_options = { stale: "1" };
	button.fn();
	check("the planner's week route for today", desk.here(), "/desk/maintenance-planner/week/2026-10-14");
	check("a history entry of its own", desk.entries.slice(-2), ["/desk/maintenance-day-board", "/desk/maintenance-planner/week/2026-10-14"]);
	desk.back();
	check("Back returns to the board", desk.route, ["maintenance-day-board"]);
	const refused = makeDesk({ start: "/desk/project/PRJ-1", pages: ["maintenance-day-board", "visit-review"] });
	check("no button without the planner page", dayBoard(refused, BOARD).page.buttons.length, 0);
});

test("Day Board: on another day, the planner opens on that day's week", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-1" });
	const board = dayBoard(desk, BOARD);
	// The board keeps its day in the address (S5a, v1.662.0).
	desk.frappe.set_route("maintenance-day-board", { date: "2026-10-01" });
	await settle();
	check("the board is on that day", desk.here(), "/desk/maintenance-day-board?date=2026-10-01");
	board.page.buttons.find((b) => b.label === "Open in Maintenance Planner").fn();
	check("the week holding it", desk.here(), "/desk/maintenance-planner/week/2026-10-01");
	desk.back();
	await settle();
	check("Back returns to the board on that day", desk.here(), "/desk/maintenance-day-board?date=2026-10-01");
	check("which reads it again", board.wrapper.mdb_day_board.day(), "2026-10-01");
	check("the visits done that day still link to Visit Review", board.html().includes('href="/desk/visit-review/SMR-S1"'), true);
});

// ------------------------------------------------------------------ run

(async () => {
	for (const { name, fn } of tests) {
		const before = failures;
		try {
			await fn();
		} catch (error) {
			failures += 1;
			console.log(`  ERROR ${error && error.stack}`);
		}
		console.log(`${failures === before ? "ok  " : "FAIL"} ${name}`);
	}
	if (failures) {
		console.log(`\n${failures} of ${checks} checks failed`);
		process.exit(1);
	}
	console.log(`\nall ${checks} checks passed`);
})();
