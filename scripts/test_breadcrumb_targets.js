#!/usr/bin/env node
/**
 * Form breadcrumbs to this app's pages (breadcrumb_targets.js, v1.779.0), executed against a
 * stub of frappe 16.53's `frappe.ui.form.get_breadcrumbs` rather than grepped.
 *
 * The stub returns what form/form.js returns at v16.53.0: the list crumb `{label, href}` and then
 * the document's title with no href. The checks:
 *
 *   1. Project: "Projects" to /desk/projects (the Projects workspace), then the title, unlinked.
 *   2. Opportunity: the Kanban's URL, the one the CRM sidebar opens on.
 *   3. Task with a project: Projects, the project (id first, its title once get_value answers,
 *      repainted through frm.page.set_breadcrumbs only while that document is still on the form),
 *      then the task. A Project already in locals is titled at once. One fetch per project.
 *   4. Task without a project: Projects, then the task.
 *   5. Any other doctype, and the print view's plain frm object, are the stock trail.
 *   6. A trail without a list crumb (a Single) keeps its one item.
 *   7. Every href is a /desk/ path (routed in place; a history entry; Back works).
 *   8. The wrapper installs once, and not at all when the function is missing.
 *   9. A target that throws falls back to the stock trail.
 *
 * If a marker no longer resolves this exits 2 rather than passing vacuously.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const FILE = path.join(ROOT, "erpnext_enhancements", "public", "js", "global_enhancements", "breadcrumb_targets.js");

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

function makeDesk(options) {
	options = options || {};
	const locals = options.locals || {};
	const answers = options.answers || {};
	const fetched = [];
	const frappe = {
		ui: { form: {} },
		router: { slug: (dt) => String(dt).toLowerCase().replace(/ /g, "-") },
		get_doc: (dt, name) => locals[dt + "/" + name] || null,
		db: {
			get_value(dt, name, field) {
				fetched.push([dt, name, field]);
				return new Promise((resolve) => {
					setImmediate(() => resolve({ message: answers[name] ? { project_name: answers[name] } : null }));
				});
			},
		},
		provide(ns) {
			const parts = ns.split(".");
			let node = sandbox;
			parts.forEach((p) => {
				node[p] = node[p] || {};
				node = node[p];
			});
			return node;
		},
	};
	// frappe 16.53's own builder: the list crumb, then the title without a link.
	const stock = function (frm) {
		const items = [];
		if (!(frm.meta && frm.meta.issingle)) {
			items.push({ label: frm.doctype, href: "/desk/" + frappe.router.slug(frm.doctype) });
		}
		items.push({ label: frm.doc.title || frm.doc.name });
		return items;
	};
	if (!options.missing) frappe.ui.form.get_breadcrumbs = stock;
	const sandbox = { frappe, console, __: (t) => t, setImmediate, window: {} };
	sandbox.window = sandbox;
	vm.createContext(sandbox);
	vm.runInContext(SOURCE, sandbox, { filename: "breadcrumb_targets.js" });
	return { frappe, fetched, stock, sandbox };
}

function form(doctype, doc, extra) {
	const painted = [];
	return Object.assign(
		{
			doctype,
			docname: doc.name,
			doc: Object.assign({ doctype }, doc),
			meta: {},
			page: { set_breadcrumbs: (items) => painted.push(items) },
			painted,
		},
		extra || {}
	);
}

const tick = () => new Promise((resolve) => setImmediate(() => setImmediate(resolve)));

(async () => {
	// 1. Project
	{
		const { frappe } = makeDesk();
		const items = frappe.ui.form.get_breadcrumbs(form("Project", { name: "PRJ-00779", title: "Burnell Burgon - Winterization" }));
		check(same(items, [{ label: "Projects", href: "/desk/projects" }, { label: "Burnell Burgon - Winterization" }]), "Project: Projects > the title");
	}

	// 2. Opportunity
	{
		const { frappe } = makeDesk();
		const items = frappe.ui.form.get_breadcrumbs(form("Opportunity", { name: "CRM-OPP-2026-00182", title: "Acme deal" }));
		check(
			same(items, [{ label: "Opportunity", href: "/desk/opportunity/view/kanban/Opportunity" }, { label: "Acme deal" }]),
			"Opportunity: the Kanban, then the title"
		);
	}

	// 3. Task with a project: id first, title after the fetch, repainted on the same form only
	{
		const { frappe, fetched } = makeDesk({ answers: { "PRJ-00219": "Salt Development - Sanctuary" } });
		const frm = form("Task", { name: "TASK-2026-03000", title: "Pour footings", project: "PRJ-00219" });
		const first = frappe.ui.form.get_breadcrumbs(frm);
		check(
			same(first, [
				{ label: "Projects", href: "/desk/projects" },
				{ label: "PRJ-00219", href: "/desk/project/PRJ-00219" },
				{ label: "Pour footings" },
			]),
			"Task: Projects > the project's id > the task, before the title arrives"
		);
		check(same(fetched, [["Project", "PRJ-00219", "project_name"]]), "the title is fetched once");
		frappe.ui.form.get_breadcrumbs(frm);
		check(fetched.length === 1, "a second build while the fetch is pending does not fetch again");
		await tick();
		check(frm.painted.length === 1 && frm.painted[0][1].label === "Salt Development - Sanctuary", "the trail is repainted with the title through frm.page.set_breadcrumbs");
		const again = frappe.ui.form.get_breadcrumbs(frm);
		check(again[1].label === "Salt Development - Sanctuary" && fetched.length === 1, "the title is cached for the page load");

		// the form moved on to another task before the answer came: no repaint of the wrong trail
		const { frappe: f2 } = makeDesk({ answers: { "PRJ-00001": "Late answer" } });
		const moving = form("Task", { name: "TASK-A", title: "A", project: "PRJ-00001" });
		f2.ui.form.get_breadcrumbs(moving);
		moving.docname = "TASK-B";
		moving.doc = { doctype: "Task", name: "TASK-B", title: "B", project: "PRJ-00002" };
		await tick();
		check(moving.painted.length === 0, "an answer for a document the form no longer shows paints nothing");
	}

	// 3b. a Project in locals is titled at once, no fetch
	{
		const { frappe, fetched } = makeDesk({ locals: { "Project/PRJ-00701": { project_name: "Myers Mortuary - Roy Fountain" } } });
		const items = frappe.ui.form.get_breadcrumbs(form("Task", { name: "TASK-1", title: "Set pump", project: "PRJ-00701" }));
		check(items[1].label === "Myers Mortuary - Roy Fountain" && fetched.length === 0, "a loaded Project titles the crumb without a fetch");
	}

	// 4. Task without a project
	{
		const { frappe } = makeDesk();
		const items = frappe.ui.form.get_breadcrumbs(form("Task", { name: "TASK-2", title: "Loose task" }));
		check(same(items, [{ label: "Projects", href: "/desk/projects" }, { label: "Loose task" }]), "Task without a project: Projects > the task");
	}

	// 5. other doctypes and the print view's plain object are untouched
	{
		const { frappe, stock } = makeDesk();
		const frm = form("Customer", { name: "CUST-1", title: "Acme" });
		check(same(frappe.ui.form.get_breadcrumbs(frm), stock(frm)), "another doctype keeps the stock trail");
		const print = { doctype: "Sales Invoice", doc: { name: "SINV-1", title: "SINV-1" }, meta: {} };
		check(same(frappe.ui.form.get_breadcrumbs(print), stock(print)), "the print view's plain object keeps the stock trail");
		const print_project = { doctype: "Project", doc: { name: "PRJ-1", title: "P" }, meta: {} };
		check(frappe.ui.form.get_breadcrumbs(print_project)[0].href === "/desk/projects", "the print view of a Project gets the dashboard crumb too");
	}

	// 6. a trail with no list crumb keeps its one item
	{
		const { frappe } = makeDesk();
		const single = form("Project", { name: "Only", title: "Only" }, { meta: { issingle: 1 } });
		const items = frappe.ui.form.get_breadcrumbs(single);
		check(items.length === 2 && items[1].label === "Only", "the document crumb is always the last item kept");
	}

	// 7. every href is a /desk/ path
	{
		const { frappe } = makeDesk();
		const all = [
			...frappe.ui.form.get_breadcrumbs(form("Project", { name: "P", title: "P" })),
			...frappe.ui.form.get_breadcrumbs(form("Opportunity", { name: "O", title: "O" })),
			...frappe.ui.form.get_breadcrumbs(form("Task", { name: "T", title: "T", project: "PRJ 7/8 & co" })),
		];
		check(all.every((i) => !i.href || i.href.startsWith("/desk/")), "every link is a /desk/ path");
		check(all.some((i) => i.href === "/desk/project/PRJ%207%2F8%20%26%20co"), "a project name is encoded in its link");
	}

	// 8. installs once; nothing when the function is missing
	{
		const { frappe, sandbox } = makeDesk();
		const installed = frappe.ui.form.get_breadcrumbs;
		vm.runInContext(SOURCE, sandbox, { filename: "breadcrumb_targets.js" });
		check(frappe.ui.form.get_breadcrumbs === installed, "evaluated twice, the first wrapper stays");
		check(typeof installed.original === "function" && installed.ee_breadcrumb_targets === true, "the wrapper keeps the original");
		const { frappe: none } = makeDesk({ missing: true });
		check(none.ui.form.get_breadcrumbs === undefined, "an older frappe without get_breadcrumbs is left alone");
	}

	// 9. a throwing target falls back to the stock trail
	{
		const { frappe, stock, sandbox } = makeDesk();
		sandbox.erpnext_enhancements.breadcrumb_targets.TARGETS.Project = () => {
			throw new Error("boom");
		};
		const frm = form("Project", { name: "PRJ-X", title: "X" });
		check(same(frappe.ui.form.get_breadcrumbs(frm), stock(frm)), "a target that throws leaves the stock trail");
	}

	if (failures) {
		console.error(failures + " check(s) failed");
		process.exit(1);
	}
	console.log("breadcrumb targets: all checks passed");
})().catch((error) => {
	console.error(error);
	process.exit(1);
});
