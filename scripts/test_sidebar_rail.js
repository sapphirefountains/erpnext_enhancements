#!/usr/bin/env node
/**
 * The sidebar rail (sidebar_rail.js, v1.793.0), executed against a stub of frappe 16.53's Sidebar
 * prototype, storage and jQuery rather than grepped.
 *
 * Nik, 2026-11-03: the module sidebar beside the dock should be an icon rail with hover labels and
 * a pin, the rows a person uses most first, count badges, a Recent list, the rail by default, and a
 * bottom bar on phones. Every rule below fails silently in production (the sidebar simply hides, or
 * a row is drawn in the wrong place, or a count is wrong), so each is executed here:
 *
 *   1. Sidebar.hides_when_collapsed answers false on a desktop (a collapse is a rail) and frappe's
 *      own answer on a phone (the drawer); installed once; nothing happens when the method is missing.
 *   2. Rail by default runs once per person per browser, folding the panel whatever frappe's key
 *      says, and never again; a person's later "open" stands; no user, nothing.
 *   3. route_key spells what a row opens the way Route History does, for every link type, and
 *      answers null for a form.
 *   4. count_for counts the route and what is under it, never a sibling that shares a prefix.
 *   5. arrange puts the rows with enough visits first, most first, ties in frappe's order, at most
 *      four, and never moves a group, a child or a hidden row; no band, no change.
 *   6. create_sidebar's wrapper reorders with the switch on (phones too, so the drawer and the bar
 *      agree) and hands frappe its own order with the switch off.
 *   7. The phone bar takes the first four plain links of the arranged list.
 *   8. badge_for reads the Task and Pending Confirmations rows and nothing else; zero is no badge.
 *   9. push_recent keeps one entry per route, newest first, at most six.
 *  10. dock_entry_route answers the Course list for the Training tile and frappe's own otherwise.
 *  11. The badge poll is throttled to one request a minute whether or not the day's seed succeeded,
 *      and once seeded it asks for the badges only (usage=0).
 *  12. A redraw whose setup() throws puts the module back; a good one draws it.
 *  13. A Recent href is a Desk path or nothing.
 *  14. The phone bar stays off a page that hides the sidebar, off one frappe says has no sidebar,
 *      off one with its own bar on screen, and not off a page whose own bar is merely in the DOM
 *      (a wizard once opened, the board's close bar, the planner's move bar).
 *  15. A missing-anchor canary warns once per key.
 *
 * If a marker no longer resolves this exits 2 rather than passing vacuously.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const FILE = path.join(ROOT, "erpnext_enhancements", "public", "js", "global_enhancements", "sidebar_rail.js");

if (!fs.existsSync(FILE)) {
	console.error("MARKERS NOT FOUND: " + path.relative(process.cwd(), FILE) + " does not exist.");
	process.exit(2);
}
const SOURCE = fs.readFileSync(FILE, "utf8");

let failures = 0;
function check(condition, message) {
	if (condition) console.log("  ok    " + message);
	else {
		failures += 1;
		console.error("  FAIL  " + message);
	}
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function makeStorage(initial) {
	const data = new Map(Object.entries(initial || {}));
	return {
		data,
		getItem: (key) => (data.has(key) ? data.get(key) : null),
		setItem: (key, value) => data.set(key, String(value)),
		removeItem: (key) => data.delete(key),
	};
}

// Enough of jQuery for evaluation: $(document).on / $(window).on are recorded, everything else is a
// chainable empty set. The DOM steps are not exercised here (they need a drawn sidebar).
function makeJQuery(world) {
	const empty = () => {
		const set = {
			length: 0,
			on(event, fn) {
				world.handlers.push([event, fn]);
				return set;
			},
		};
		["find", "each", "attr", "removeAttr", "text", "remove", "addClass", "removeClass", "toggleClass", "append", "prepend", "closest", "css", "first", "empty", "appendTo", "off", "is", "hasClass", "toggle", "filter"].forEach((m) => {
			set[m] = () => set;
		});
		// The one DOM question the harness answers: how many own bottom bars the page holds, and
		// how many of them are on screen (world.bars_in_dom / world.bars_visible).
		set.find = (selector) => (typeof selector === "string" && selector.indexOf(".vz-nav") !== -1 ? bars() : set);
		return set;
	};
	const bars = () => {
		const found = empty();
		found.length = world.bars_in_dom || 0;
		found.filter = (what) => {
			const shown = empty();
			shown.length = what === ":visible" ? world.bars_visible || 0 : found.length;
			return shown;
		};
		return found;
	};
	const $ = () => empty();
	return $;
}

function makeSidebarClass(options) {
	options = options || {};
	class Sidebar {}
	Sidebar.prototype.hides_when_collapsed = function () {
		return "frappe-says";
	};
	Sidebar.prototype.apply_expanded_state = function () {
		return "applied";
	};
	Sidebar.prototype.create_sidebar = function (items) {
		this.drawn = items;
		return "created";
	};
	Sidebar.prototype.make_sidebar = function () {
		return "made";
	};
	Sidebar.prototype.dock_entry_route = function (entry) {
		if (!entry) return null;
		return "/desk/" + String(entry.link_to || entry.url || "").toLowerCase().replace(/ /g, "-");
	};
	if (options.without) options.without.forEach((name) => delete Sidebar.prototype[name]);
	return Sidebar;
}

function makeWorld(options) {
	options = options || {};
	const world = { handlers: [], storage: makeStorage(options.storage), mobile: !!options.mobile, fetches: [], warnings: [], fetch_impl: null };
	const Sidebar = options.no_sidebar ? null : makeSidebarClass(options);
	const frappe = {
		session: { user: options.user === undefined ? "clegg@sapphirefountains.com" : options.user },
		boot: {},
		is_mobile: () => world.mobile,
		ui: Sidebar ? { Sidebar } : {},
		router: {
			routes: { task: { doctype: "Task" }, opportunity: { doctype: "Opportunity" }, "training-course": { doctype: "Training Course" } },
			on: (event, fn) => world.handlers.push(["router:" + event, fn]),
		},
		workspaces: { projects: { name: "Projects", title: "Projects" }, training: { name: "Training", title: "Training" } },
		utils: { escape_html: (s) => String(s) },
	};
	const sandbox = {
		console: { log: console.log, error: console.error, warn: (...args) => world.warnings.push(args.join(" ")) },
		frappe,
		document: { cookie: "" },
		localStorage: world.storage,
		setTimeout,
		Date,
		JSON,
		Math,
		Number,
		String,
		Object,
		Array,
		Set,
		Promise,
		fetch: (url) => {
			world.fetches.push(url);
			return world.fetch_impl ? world.fetch_impl(url) : Promise.reject(new Error("no network in the harness"));
		},
	};
	sandbox.window = sandbox;
	sandbox.$ = makeJQuery(world);
	vm.createContext(sandbox);
	vm.runInContext(SOURCE, sandbox, { filename: "sidebar_rail.js" });
	world.sandbox = sandbox;
	world.frappe = frappe;
	world.Sidebar = Sidebar;
	world.api = sandbox.erpnext_enhancements && sandbox.erpnext_enhancements.sidebar_rail;
	return world;
}

const USER = "clegg@sapphirefountains.com";

// ---------------------------------------------------------------- 1. the wrapper
{
	const world = makeWorld();
	const api = world.api;
	check(!!api, "the module exports erpnext_enhancements.sidebar_rail");
	const proto = world.Sidebar.prototype;
	check(proto._ee_rail_patched === true, "the Sidebar prototype is patched at evaluation");
	const sb = new world.Sidebar();
	check(sb.hides_when_collapsed() === false, "desktop: a collapse is a rail (hides_when_collapsed answers false)");
	world.mobile = true;
	check(sb.hides_when_collapsed() === "frappe-says", "phone: frappe's own answer stands");
	world.mobile = false;
	check(api.patch_sidebar() === true, "patching again is a no-op that reports success");
	check(proto.hides_when_collapsed !== makeSidebarClass().prototype.hides_when_collapsed, "the wrapper replaced the method once");
}
{
	const world = makeWorld({ without: ["hides_when_collapsed"] });
	check(world.Sidebar.prototype._ee_rail_patched !== true, "a frappe without hides_when_collapsed is left alone");
	check(world.handlers.some(([e]) => e === "app_ready"), "...and the patch waits for app_ready instead");
}
{
	const world = makeWorld({ no_sidebar: true });
	check(!!world.api && world.api.patch_sidebar() === false, "no Sidebar class: nothing is patched, nothing throws");
}

// ---------------------------------------------------------------- 2. rail by default
{
	const world = makeWorld({ storage: { "desk-sidebar-collapsed": "0", ["ee-nav-collapsed@" + USER]: "0" } });
	const s = world.storage;
	check(s.getItem("desk-sidebar-collapsed") === "1", "first visit after shipping: the panel starts folded even over frappe's incidental 0");
	check(s.getItem("ee-nav-collapsed@" + USER) === "1", "...and this person's own copy says so too");
	check(s.getItem("ee-rail-intro-v1@" + USER) === "1", "...and the intro is marked done");
	// The person pins it open; a later evaluation leaves that alone.
	s.setItem("desk-sidebar-collapsed", "0");
	s.setItem("ee-nav-collapsed@" + USER, "0");
	check(world.api.apply_intro(s, USER) === false, "the intro never runs twice");
	check(s.getItem("desk-sidebar-collapsed") === "0", "...so a pinned-open panel stays open");
	check(world.api.apply_intro(makeStorage(), "") === false, "no user (Guest, unknown): nothing is written");
	const phone = makeWorld({ mobile: true });
	check(phone.storage.getItem("ee-rail-intro-v1@" + USER) === null, "a phone drawer is never folded by the intro");
}

// ---------------------------------------------------------------- 3. route_key
{
	const api = makeWorld().api;
	const lookup = (seg) => {
		if (seg === "task") return { kind: "doctype", name: "Task" };
		if (seg === "opportunity") return { kind: "doctype", name: "Opportunity" };
		if (seg === "projects") return { kind: "workspace", name: "Projects" };
		return { kind: "page" };
	};
	const key = (item) => api.route_key(item, lookup);
	check(key({ type: "Link", link_type: "Workspace", link_to: "Projects" }) === "Workspaces/Projects", "Workspace row: Workspaces/<name>");
	check(key({ type: "Link", link_type: "DocType", link_to: "Task" }) === "List/Task", "DocType row: List/<doctype> (every view under it)");
	check(key({ type: "Link", link_type: "Page", link_to: "project-planner" }) === "project-planner", "Page row: the page name");
	check(key({ type: "Link", link_type: "Dashboard", link_to: "Project" }) === "dashboard-view/Project", "Dashboard row: dashboard-view/<name>");
	check(
		key({ type: "Link", link_type: "Report", link_to: "Project Summary", report: { report_type: "Script Report", ref_doctype: "Project" } }) === "query-report/Project Summary",
		"Script report: query-report/<name>"
	);
	check(
		key({ type: "Link", link_type: "Report", link_to: "Delayed Tasks Summary", report: { report_type: "Report Builder", ref_doctype: "Task" } }) === "List/Task/Report/Delayed Tasks Summary",
		"Report Builder report: List/<ref>/Report/<name>"
	);
	check(key({ type: "Link", link_type: "URL", url: "/desk/opportunity/view/kanban/Opportunity" }) === "List/Opportunity/Kanban/Opportunity", "URL row to a kanban: List/<dt>/Kanban/<board>");
	check(key({ type: "Link", link_type: "URL", url: "/desk/task" }) === "List/Task", "URL row to a list: List/<dt>");
	check(key({ type: "Link", link_type: "URL", url: "/desk/task/view/tree" }) === "Tree/Task", "URL row to a tree: Tree/<dt>");
	check(key({ type: "Link", link_type: "URL", url: "/desk/projects?x=1" }) === "Workspaces/Projects", "URL row to a workspace, query string ignored");
	check(key({ type: "Link", link_type: "URL", url: "/desk/task/TASK-0001" }) === null, "a URL row to a form counts nothing (Route History skips forms)");
	check(key({ type: "Link", link_type: "URL", url: "/kiosk" }) === null, "a URL outside the desk counts nothing");
	check(key({ type: "Link", link_type: "URL", url: "/desk/my-work" }) === "my-work", "URL row to a page: the page name");
	check(key({ type: "Section Break", label: "Reports" }) === null, "a group heading has no route");
}

// ---------------------------------------------------------------- 4. count_for
{
	const api = makeWorld().api;
	const usage = { "List/Task/List": 16, "List/Task/Kanban/Board": 3, "List/Task Type/List": 9, "Workspaces/Projects": 291, "project-planner/lookahead/2026-11-03": 7, "project-planner": 30 };
	check(api.count_for("List/Task", usage) === 19, "List/Task counts its views and not Task Type");
	check(api.count_for("project-planner", usage) === 37, "a page counts its sub-routes");
	check(api.count_for("Workspaces/Projects", usage) === 291, "an exact route counts");
	check(api.count_for("List/Project", usage) === 0 && api.count_for(null, usage) === 0 && api.count_for("x", null) === 0, "nothing, null key or no usage: 0");
}

// ---------------------------------------------------------------- 5. arrange
{
	const api = makeWorld().api;
	const home = { type: "Link", link_type: "Workspace", link_to: "Projects", label: "Home" };
	const task = { type: "Link", link_type: "DocType", link_to: "Task", label: "Task" };
	const planner = { type: "Link", link_type: "Page", link_to: "project-planner", label: "Project Planner" };
	const timesheet = { type: "Link", link_type: "DocType", link_to: "Timesheet", label: "Timesheet" };
	const hiddenRow = { type: "Link", link_type: "DocType", link_to: "Project", label: "Project", hidden: 1 };
	const reports = { type: "Section Break", label: "Reports", nested_items: [{ type: "Link", link_type: "Report", link_to: "Project Summary", child: 1 }] };
	const child = { type: "Link", link_type: "Report", link_to: "Crew Utilization", child: 1, label: "Crew Utilization" };
	const setup = { type: "Section Break", label: "Setup" };
	const items = [home, hiddenRow, task, planner, timesheet, reports, child, setup];
	// Clegg in Projects, 90 days to 2026-11-03: Home 291, Planner 37, Task 16, Timesheet 6.
	const counts = new Map([[home, 291], [task, 16], [planner, 37], [timesheet, 6], [hiddenRow, 500], [child, 999]]);
	const result = api.arrange(items, (item) => counts.get(item) || 0);
	check(same(result.items.map((i) => i.label), ["Home", "Project Planner", "Task", "Timesheet", "Project", "Reports", "Crew Utilization", "Setup"]), "used-most first, then the rest in frappe's order");
	check(same(result.band.map((i) => i.label), ["Home", "Project Planner", "Task", "Timesheet"]), "the band is the four rows with enough visits");
	const capped = api.arrange(items, (item) => counts.get(item) || 0, { max: 2 });
	check(same(capped.band.map((i) => i.label), ["Home", "Project Planner"]), "at most `max` rows move");
	check(same(capped.items.map((i) => i.label), ["Home", "Project Planner", "Project", "Task", "Timesheet", "Reports", "Crew Utilization", "Setup"]), "...and the others keep their places");
	const floor = api.arrange(items, (item) => counts.get(item) || 0, { floor: 20 });
	check(same(floor.band.map((i) => i.label), ["Home", "Project Planner"]), "a row under the floor stays where it was");
	const ties = api.arrange([task, planner, timesheet], () => 5);
	check(same(ties.items.map((i) => i.label), ["Task", "Project Planner", "Timesheet"]), "ties keep frappe's order");
	const none = api.arrange(items, () => 0);
	check(same(none.items, items) && none.band.length === 0, "no row above the floor: the order is untouched");
	check(api.arrange(null, () => 1).items.length === 0, "no items: nothing");
	check(!result.items.some((i, idx) => i === hiddenRow && idx < 4) && !result.band.includes(child), "a hidden row and a child never move, whatever their count");
}

// ---------------------------------------------------------------- 6. create_sidebar's wrapper
{
	const usage = { "Workspaces/Projects": 291, "project-planner/week/2026-11-01": 37, "List/Task/List": 16 };
	const world = makeWorld({ storage: { ["ee-rail-use@" + USER]: JSON.stringify(usage) } });
	const items = [
		{ type: "Link", link_type: "DocType", link_to: "Task", label: "Task" },
		{ type: "Link", link_type: "Page", link_to: "project-planner", label: "Project Planner" },
		{ type: "Link", link_type: "Workspace", link_to: "Projects", label: "Home" },
		{ type: "Section Break", label: "Reports" },
	];
	const sb = new world.Sidebar();
	check(sb.create_sidebar(items) === "created", "the wrapper returns frappe's own result");
	check(same(sb.drawn.map((i) => i.label), ["Home", "Project Planner", "Task", "Reports"]), "frappe draws the rows in used-most order");
	world.storage.setItem("ee-rail-adaptive@" + USER, "0");
	sb.create_sidebar(items);
	check(same(sb.drawn.map((i) => i.label), ["Task", "Project Planner", "Home", "Reports"]), "the switch off: frappe's order");
	world.storage.removeItem("ee-rail-adaptive@" + USER);
	world.mobile = true;
	sb.create_sidebar(items);
	check(same(sb.drawn.map((i) => i.label), ["Home", "Project Planner", "Task", "Reports"]), "a phone drawer is reordered too, so it and the bottom bar agree");
	world.mobile = false;
	const broken = new world.Sidebar();
	check(broken.create_sidebar("not a list") === "created", "an unexpected argument is handed to frappe unchanged");
}

// ---------------------------------------------------------------- 7. the phone bar's rows
{
	const api = makeWorld().api;
	const links = ["Home", "Project Planner", "Task", "My Work", "Timesheet"].map((label) => ({ type: "Link", link_type: "Page", link_to: label, label }));
	const items = [links[0], { type: "Section Break", label: "Reports" }, links[1], { type: "Link", child: 1, label: "nested" }, links[2], links[3], links[4]];
	check(same(api.pick_bar_rows(items).map((i) => i.label), ["Home", "Project Planner", "Task", "My Work"]), "the first four plain links, groups and children skipped");
	check(api.pick_bar_rows([]).length === 0 && api.pick_bar_rows(null).length === 0, "nothing to draw: no rows");
	check(api.BAR_ROWS === 4 && api.TOP_BAND_MAX === 4 && api.USAGE_FLOOR === 3 && api.RECENT_MAX === 6, "the constants the mocks were drawn with");
}

// ---------------------------------------------------------------- 8. badges
{
	const api = makeWorld().api;
	const badges = { tasks: { count: 3, overdue: 1 }, pending_actions: 12, my_work: { count: 5 } };
	check(same(api.badge_for({ link_type: "DocType", link_to: "Task" }, badges), { count: 3, overdue: 1 }), "the Task row shows assigned tasks and overdue");
	check(same(api.badge_for({ link_type: "DocType", link_to: "AI Pending Action" }, badges), { count: 12, overdue: 0 }), "Pending Confirmations shows the pending count");
	check(api.badge_for({ link_type: "DocType", link_to: "Timesheet" }, badges) === null, "other rows carry no badge");
	check(api.badge_for({ link_type: "DocType", link_to: "Task" }, { tasks: { count: 0, overdue: 0 } }) === null, "zero is no badge");
	check(api.badge_for({ link_type: "DocType", link_to: "Task" }, null) === null, "no data (a failed read): no badge");
	check(api.badge_text(7) === "7" && api.badge_text(140) === "99+", "a count over 99 reads 99+");
}

// ---------------------------------------------------------------- 9. recents
{
	const api = makeWorld().api;
	let list = [];
	for (let i = 1; i <= 8; i++) list = api.push_recent(list, { route: "/desk/task/T-" + i, label: "T-" + i, at: i });
	check(list.length === 6 && list[0].route === "/desk/task/T-8" && list[5].route === "/desk/task/T-3", "newest first, at most six");
	list = api.push_recent(list, { route: "/desk/task/T-5", label: "T-5 again", at: 99 });
	check(list[0].label === "T-5 again" && list.filter((e) => e.route === "/desk/task/T-5").length === 1, "a route opened again moves to the top, once");
	check(same(api.push_recent(list, null), list) && api.push_recent(null, { route: "/x" }).length === 1, "a missing entry or list is tolerated");
}

// ---------------------------------------------------------------- 10. the Training tile
{
	const world = makeWorld();
	const sb = new world.Sidebar();
	check(sb.dock_entry_route({ link_type: "Workspace", link_to: "Training" }) === "/desk/training-course", "the Training tile opens the Course list");
	check(sb.dock_entry_route({ link_type: "Workspace", link_to: "Projects" }) === "/desk/projects", "every other tile is frappe's own route");
	check(sb.dock_entry_route(null) === null, "a missing entry is handed to frappe");
	check(same(Object.keys(world.api.LANDINGS), ["Workspace|Training|"]), "one landing override, keyed like dock_entry_key");
	check(world.api.item_key({ link_type: "URL", url: "/desk/x" }) === "URL||/desk/x", "item_key is link_type|link_to|url");
}

// ---------------------------------------------------------------- 11. the badge poll
async function asyncChecks() {
	{
		const world = makeWorld();
		const api = world.api;
		await api.refresh_data(true);
		check(world.fetches.length === 1 && world.fetches[0].endsWith("get_rail_data"), "the first read asks for usage and badges in one request");
		await api.refresh_data(false);
		check(world.fetches.length === 1, "a failed seed does not make the next route change ask again within the minute");
		await api.refresh_data(true);
		check(world.fetches.length === 2 && world.fetches[1].endsWith("get_rail_data"), "unseeded, a forced read still asks for usage");
		world.fetch_impl = () =>
			Promise.resolve({ ok: true, json: () => Promise.resolve({ message: { usage: { "List/Task": 4 }, badges: { tasks: { count: 2, overdue: 0 } } } }) });
		await api.refresh_data(true);
		const today = new Date().toISOString().slice(0, 10);
		check(world.storage.getItem("ee-rail-seed@" + USER) === today, "a good answer marks the seed as today's");
		check(JSON.parse(world.storage.getItem("ee-rail-use@" + USER) || "{}")["List/Task"] === 4, "...and keeps the server's counts");
		await api.refresh_data(true);
		check(world.fetches[world.fetches.length - 1].endsWith("get_rail_data?usage=0"), "seeded, the poll asks for the badges only");
	}
	// ------------------------------------------------------------ 12. redraw never strands the sidebar
	{
		const world = makeWorld();
		const sb = new world.Sidebar();
		sb.current_module = "Projects";
		sb.setup = () => {
			throw new Error("boom");
		};
		world.frappe.app = { sidebar: sb };
		world.api.redraw_sidebar();
		check(sb.current_module === "Projects", "a redraw whose setup() throws puts the module back");
		check(world.warnings.some((w) => w.startsWith("sidebar_rail:")), "...and says so in the console");
		sb.setup = (module) => {
			sb.set_up = module;
			sb.current_module = module;
		};
		world.api.redraw_sidebar();
		check(sb.set_up === "Projects" && sb.current_module === "Projects", "a good redraw draws the module again");
	}
	// ------------------------------------------------------------ 13. a Recent href is a Desk path
	{
		const api = makeWorld().api;
		check(api.safe_route("/desk/task/TASK-0001") === "/desk/task/TASK-0001" && api.safe_route("/app/task") === "/app/task", "a Desk path is kept");
		check(api.safe_route("javascript:alert(1)") === null && api.safe_route("https://example.com/desk/x") === null && api.safe_route("//evil") === null, "anything else is dropped");
		check(api.safe_route("/kiosk") === null && api.safe_route(null) === null && api.safe_route("/deskx") === null, "a path outside the Desk is dropped too");
	}
	// ------------------------------------------------------------ 14. the bar and the page
	{
		const api = makeWorld().api;
		const sb = (page, allows) => ({ current_page: () => page, page_allows_sidebar: () => allows });
		check(api.bar_suppressed(sb({ hide_sidebar: 1 }, true)) === true, "a page that hides the sidebar gets no bar");
		check(api.bar_suppressed(sb({}, false)) === true, "a page frappe says has no sidebar gets no bar");
		check(api.bar_suppressed(sb(null, true)) === true, "no page yet: no bar");
		check(api.bar_suppressed(sb({ title: "Projects" }, true)) === false, "an ordinary page keeps it");
		// A bar of the page's own: only one on screen, inside the page on screen, counts.
		const world = makeWorld();
		world.frappe.container = { page: { page: { title: "Visit Wizard" } } };
		const page = sb({ title: "Visit Wizard" }, true);
		world.bars_in_dom = 1;
		world.bars_visible = 0;
		check(world.api.bar_suppressed(page) === false, "a hidden leftover bar (a wizard once opened, the board's close bar, the planner's move bar) does not suppress it");
		world.bars_visible = 1;
		check(world.api.bar_suppressed(page) === true, "a bar on screen in this page does");
		world.bars_visible = 0;
		check(world.api.bar_suppressed(page) === false, "...and once it is hidden again the bar comes back");
		delete world.frappe.container;
		world.bars_visible = 1;
		check(world.api.bar_suppressed(page) === false, "no page container to look in: nothing is counted");
	}
	// ------------------------------------------------------------ 15. the canary
	{
		const world = makeWorld();
		world.api.warn_once("band", "x");
		world.api.warn_once("band", "x again");
		world.api.warn_once("icon:DocType|Task|", "y");
		const ours = world.warnings.filter((w) => w.startsWith("sidebar_rail: "));
		check(ours.length === 2 && ours[0] === "sidebar_rail: x", "one warning per key, prefixed sidebar_rail:");
	}
}

asyncChecks()
	.catch((e) => {
		failures += 1;
		console.error("  FAIL  async checks threw: " + (e && e.stack ? e.stack : e));
	})
	.then(() => {
		if (failures) {
			console.error("\n" + failures + " check(s) failed");
			process.exit(1);
		}
		console.log("\nall checks passed");
	});
