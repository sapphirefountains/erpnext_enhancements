#!/usr/bin/env node
/**
 * The Procurement Tracker's pure decisions (v1.806.0) under node:
 * public/js/procurement_tracker/logic.js, the module the Project form script draws from.
 *
 *   the address    the view and its filters round-trip through the address's query, other
 *                  people's keys are left alone, a default view writes nothing, and the widget's
 *                  keys come back out of frappe.route_options
 *   the filters    place, status, supplier and "needs action" narrow lines, documents and the
 *                  by-item rows; the search keeps its old rule (a document whose own name matches
 *                  keeps all its lines); a line is never copied
 *   sorting        blanks last in BOTH directions, zero is not blank, statuses in the order
 *                  somebody chases them, item codes as numbers, and never in place
 *   labels         a half-done status never gets a finished status's colour
 *   markup         a name is never read as HTML, in the table or on the printed list
 *
 * Nik's rule: a view a person can return to is in the address, and Back and Forward still work.
 * The widget writes the address with history.replaceState and never pushes; the last test reads
 * the form script to hold it to that.
 *
 * Run: node scripts/test_procurement_tracker.mjs
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "erpnext_enhancements");
const pt = await import(pathToFileURL(path.join(APP, "public/js/procurement_tracker/logic.js")).href);
const SCRIPT = fs.readFileSync(path.join(APP, "public/js/project_enhancements.js"), "utf8");
const OVERVIEW = fs.readFileSync(path.join(APP, "procurement_overview.py"), "utf8");

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
		console.error(`  FAIL ${label}\n       got      ${a}\n       expected ${e}`);
	}
}

const esc = (s) =>
	String(s)
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;");

// A small job: a request line split across two orders (the same line object under each), a
// direct order line, and a quote.
function sample() {
	const elbow_a = {
		row_id: "mri-1#0", item_code: "417-080", item_name: "FITTING, ELBOW, PVC, 2 IN", supplier: "Acme Supply",
		mr: "MR-1", po: "PO-1", pr: "PR-1", warehouse: "Project Shelf - SF", where_now_text: "4 on the Project Shelf",
		where_now: [{ kind: "project_shelf", text: "4 on the Project Shelf", qty: 4 }], where_now_lines: 1,
		requested_qty: 10, ordered_qty: 10, received_qty: 4, line_state: "partly_received", line_state_label: "Partly received",
		line_states: ["partly_received"], needs_action: true, location_kinds: ["project_shelf", "not_received"],
	};
	const elbow_b = Object.assign({}, elbow_a, {
		row_id: "mri-1#1", supplier: "Bolt Co", po: "PO-2", pr: null, warehouse: null,
		line_state: "late", line_state_label: "Late", line_states: ["late", "partly_received"],
	});
	const pump = {
		row_id: "poi-3#2", item_code: "2622-010", item_name: "PUMP", supplier: "Acme Supply", po: "PO-3", pr: "PR-3",
		where_now_text: "Location not tracked (not a stock item)", where_now: [], location_tracked: false,
		requested_qty: null, ordered_qty: 2, received_qty: 2, line_state: "received", line_state_label: "Received",
		line_states: ["received"], needs_action: false, location_kinds: ["not_tracked"],
	};
	const quote = {
		row_id: "Supplier Quotation#x", item_code: "417-080", item_name: "FITTING, ELBOW, PVC, 2 IN", supplier: "Cheap Co",
		sq: "SQ-9", line_state: "quote_only", line_state_label: "Quote only", line_states: ["quote_only"],
		needs_action: false, location_kinds: ["project_shelf"], requested_qty: 10, ordered_qty: 0, received_qty: 0,
	};
	const groups = [
		{ doctype: "Material Request", documents: [{ name: "MR-1", status: "Partially Received", items: [elbow_a, elbow_b] }] },
		{ doctype: "Supplier Quotation", documents: [{ name: "SQ-9", supplier: "Cheap Co", status: "Submitted", items: [quote] }] },
		{
			doctype: "Purchase Order",
			documents: [
				{ name: "PO-1", supplier: "Acme Supply", status: "Completed", items: [elbow_a] },
				{ name: "PO-2", supplier: "Bolt Co", status: "To Receive and Bill", items: [elbow_b] },
				{ name: "PO-3", supplier: "Acme Supply", status: "Completed", items: [pump] },
			],
		},
	];
	const items = [
		{
			row_id: "item#417-080", item_code: "417-080", item_name: "FITTING, ELBOW, PVC, 2 IN", requested_qty: 10, ordered_qty: 10,
			received_qty: 4, state: "late", state_label: "Late", states: ["late", "partly_received"], needs_action: true,
			suppliers: ["Acme Supply", "Bolt Co", "Cheap Co"], location_kinds: ["project_shelf", "not_received"],
			where_now_text: "4 on the Project Shelf", where_now: [{ kind: "project_shelf", text: "4 on the Project Shelf" }],
			expected_date: "2026-11-01", documents: { "Material Request": ["MR-1"], "Purchase Order": ["PO-1", "PO-2"] },
			received_at: ["Project Shelf - SF"],
		},
		{
			row_id: "item#2622-010", item_code: "2622-010", item_name: "PUMP", requested_qty: null, ordered_qty: 2, received_qty: 2,
			state: "received", state_label: "Received", states: ["received"], needs_action: false, suppliers: ["Acme Supply"],
			location_kinds: ["not_tracked"], where_now_text: "Location not tracked (not a stock item)", where_now: [],
			expected_date: null, documents: { "Purchase Order": ["PO-3"] },
		},
	];
	return { groups, items, elbow_a, elbow_b, pump, quote };
}

const names = (groups) => groups.map((g) => `${g.doctype}: ${g.documents.map((d) => `${d.name}(${d.items.length})`).join(" ")}`);

// ------------------------------------------------------------------ the address

test("the view state round-trips through the address", () => {
	const state = { view: "items", location: "project_shelf", status: "late", supplier: "Acme Supply", action: true, q: "elbow 2 in" };
	const query = pt.write_query("", state);
	check("query", query, "?pt_view=items&pt_where=project_shelf&pt_status=late&pt_supplier=Acme+Supply&pt_action=1&pt_q=elbow+2+in");
	check("read back", pt.read_query(query), state);
});

test("a default view writes nothing, and other people's keys are left alone", () => {
	check("default", pt.write_query("", pt.default_state()), "");
	check("default, from a filtered address", pt.write_query("?pt_view=items&pt_status=late", pt.default_state()), "");
	check("kept", pt.write_query("?scroll_to=notes&pt_status=late", { view: "items" }), "?scroll_to=notes&pt_view=items");
	check("stable", pt.write_query(pt.write_query("?x=1", { status: "late" }), { status: "late" }), "?x=1&pt_status=late");
	check("no query at all", pt.read_query(undefined), pt.default_state());
});

test("a view the address does not know is the default view; filter values are kept as given", () => {
	check("unknown view", pt.read_query("?pt_view=kanban").view, "documents");
	check("a supplier this job no longer has", pt.read_query("?pt_supplier=Gone+Co").supplier, "Gone Co");
	check("action needs a 1", pt.read_query("?pt_action=yes").action, false);
});

test("the widget's keys come back out of frappe.route_options", () => {
	check("mixed", pt.without_own_keys({ pt_view: "items", pt_q: "x", scroll_to: "notes" }), { scroll_to: "notes" });
	check("only ours", pt.without_own_keys({ pt_view: "items" }), null);
	check("none", pt.without_own_keys(null), null);
	check("every key is prefixed", Object.values(pt.QUERY_KEYS).every((k) => k.startsWith("pt_")), true);
});

test("clearing keeps the grouping and drops every filter", () => {
	check("cleared", pt.cleared({ view: "items", status: "late", q: "x", action: true }), Object.assign(pt.default_state(), { view: "items" }));
	check("is_filtered", [pt.is_filtered({ q: "x" }), pt.is_filtered({ action: true }), pt.is_narrowed({ q: " x " }), pt.is_narrowed({ q: "  " })], [false, true, true, false]);
});

// ------------------------------------------------------------------ the filters

test("no filter and no search returns the tree untouched", () => {
	const { groups, items } = sample();
	check("same array", pt.filter_groups(groups, pt.default_state()) === groups, true);
	check("same items", pt.filter_items(items, pt.default_state()) === items, true);
});

test("a filter narrows to the lines that match, and a document with none is not shown", () => {
	const { groups, elbow_b } = sample();
	const late = pt.filter_groups(groups, { status: "late" });
	check("late", names(late), ["Material Request: MR-1(1)", "Purchase Order: PO-2(1)"]);
	check("the line is the same object, not a copy", late[0].documents[0].items[0] === elbow_b, true);
	check("the document says how many it is showing", pt.item_count_text(late[0].documents[0]), "1 of 2 items");
	check("an unfiltered document", pt.item_count_text(groups[0].documents[0]), "2 item(s)");
	check("the input is not changed", groups[0].documents[0].items.length, 2);
});

test("place, supplier and needs-action each narrow, and they combine", () => {
	const { groups } = sample();
	check("on the shelf", names(pt.filter_groups(groups, { location: "project_shelf" })), [
		"Material Request: MR-1(2)", "Supplier Quotation: SQ-9(1)", "Purchase Order: PO-1(1) PO-2(1)",
	]);
	check("not tracked", names(pt.filter_groups(groups, { location: "not_tracked" })), ["Purchase Order: PO-3(1)"]);
	check("a supplier", names(pt.filter_groups(groups, { supplier: "Acme Supply" })), ["Material Request: MR-1(1)", "Purchase Order: PO-1(1) PO-3(1)"]);
	check("needs action", names(pt.filter_groups(groups, { action: true })), ["Material Request: MR-1(2)", "Purchase Order: PO-1(1) PO-2(1)"]);
	check("combined", names(pt.filter_groups(groups, { action: true, supplier: "Bolt Co", location: "not_received" })), [
		"Material Request: MR-1(1)", "Purchase Order: PO-2(1)",
	]);
	check("nothing matches", pt.filter_groups(groups, { status: "to_order" }), []);
});

test("the search keeps its rule: a document whose own name matches keeps all its lines", () => {
	const { groups } = sample();
	check("by document name", names(pt.filter_groups(groups, { q: "mr-1" })), ["Material Request: MR-1(2)", "Purchase Order: PO-1(1) PO-2(1)"]);
	check("by item words, every word", names(pt.filter_groups(groups, { q: "pump acme" })), ["Purchase Order: PO-3(1)"]);
	check("by where it is", names(pt.filter_groups(groups, { q: "not tracked" })), ["Purchase Order: PO-3(1)"]);
	check("by state", names(pt.filter_groups(groups, { q: "late" })), ["Material Request: MR-1(1)", "Purchase Order: PO-2(1)"]);
	// A filter first, then the search inside what is left.
	check("search inside a filter", names(pt.filter_groups(groups, { supplier: "Acme Supply", q: "po-3" })), ["Purchase Order: PO-3(1)"]);
	check("a header match cannot bring back filtered-out lines", names(pt.filter_groups(groups, { status: "late", q: "mr-1" })), [
		"Material Request: MR-1(1)", "Purchase Order: PO-2(1)",
	]);
});

test("the by-item rows filter on the same words, in the plural", () => {
	const { items } = sample();
	const codes = (state) => pt.filter_items(items, state).map((r) => r.item_code);
	check("late", codes({ status: "late" }), ["417-080"]);
	check("a state it is also in", codes({ status: "partly_received" }), ["417-080"]);
	check("supplier", codes({ supplier: "Cheap Co" }), ["417-080"]);
	check("place", codes({ location: "not_tracked" }), ["2622-010"]);
	check("needs action", codes({ action: true }), ["417-080"]);
	check("search a document", codes({ q: "po-3" }), ["2622-010"]);
	check("search a supplier", codes({ q: "bolt" }), ["417-080"]);
});

test("lines are counted once, however many documents they sit under", () => {
	const { groups } = sample();
	check("all", pt.count_lines(groups), 4);
	check("late", pt.count_lines(pt.filter_groups(groups, { status: "late" })), 1);
});

// ------------------------------------------------------------------ sorting

test("blanks sort last in both directions, and zero is not blank", () => {
	const rows = [{ requested_qty: 5 }, { requested_qty: null }, { requested_qty: 0 }, { requested_qty: 12 }];
	const order = (dir) => pt.sort_rows(rows, pt.LINE_COLUMNS, { key: "requested", dir }).map((r) => r.requested_qty);
	check("ascending", order("asc"), [0, 5, 12, null]);
	check("descending", order("desc"), [12, 5, 0, null]);
});

test("statuses sort in the order somebody chases them, and codes as numbers", () => {
	const states = ["received", "to_order", "closed", "late", "ordered", "partly_received", "mystery"].map((s) => ({ line_state: s }));
	check("status", pt.sort_rows(states, pt.LINE_COLUMNS, { key: "status", dir: "asc" }).map((r) => r.line_state), [
		"late", "to_order", "ordered", "partly_received", "received", "closed", "mystery",
	]);
	const codes = ["417-100", "2622-010", "417-080"].map((c) => ({ item_code: c }));
	check("item", pt.sort_rows(codes, pt.LINE_COLUMNS, { key: "item", dir: "asc" }).map((r) => r.item_code), ["417-080", "417-100", "2622-010"]);
	const { items } = sample();
	check("by-item due date, blank last", pt.sort_rows(items, pt.ITEM_COLUMNS, { key: "due", dir: "desc" }).map((r) => r.item_code), ["417-080", "2622-010"]);
});

test("a sort never changes the list it was given, and no sort returns it as it came", () => {
	const rows = [{ ordered_qty: 3 }, { ordered_qty: 1 }];
	const sorted = pt.sort_rows(rows, pt.LINE_COLUMNS, { key: "ordered", dir: "asc" });
	check("input order", rows.map((r) => r.ordered_qty), [3, 1]);
	check("a new array", sorted !== rows, true);
	check("no sort", pt.sort_rows(rows, pt.LINE_COLUMNS, null) === rows, true);
	check("unknown column", pt.sort_rows(rows, pt.LINE_COLUMNS, { key: "doc_chain", dir: "asc" }) === rows, true);
});

test("a header click goes ascending, descending, then back to the document's own order", () => {
	const one = pt.next_sort(null, "ordered", pt.LINE_COLUMNS);
	const two = pt.next_sort(one, "ordered", pt.LINE_COLUMNS);
	check("steps", [one, two, pt.next_sort(two, "ordered", pt.LINE_COLUMNS)], [{ key: "ordered", dir: "asc" }, { key: "ordered", dir: "desc" }, null]);
	check("another column starts over", pt.next_sort(two, "item", pt.LINE_COLUMNS), { key: "item", dir: "asc" });
	check("unknown column changes nothing", pt.next_sort(one, "doc_chain", pt.LINE_COLUMNS), one);
});

// ------------------------------------------------------------------ labels

test("a half-done status never gets a finished status's colour", () => {
	check("ordered pair", pt.status_theme("Partially Ordered") !== pt.status_theme("Ordered"), true);
	check("received pair", pt.status_theme("Partially Received") !== pt.status_theme("Received"), true);
	check("ERPNext's own", ["Draft", "To Receive and Bill", "Completed", "Cancelled", "Closed", ""].map(pt.status_theme), [
		"gray", "orange", "green", "red", "gray", "gray",
	]);
	check("states", ["late", "to_order", "ordered", "partly_received", "received", "closed", "nope"].map(pt.state_theme), [
		"red", "orange", "blue", "amber", "green", "gray", "gray",
	]);
});

test("the client's state words and order are the server's", () => {
	const rank = OVERVIEW.match(/STATE_RANK = \(([^)]*)\)/)[1].split(",").map((s) => s.trim()).filter(Boolean);
	const value = (name) => OVERVIEW.match(new RegExp(`^${name} = "(\\w+)"`, "m"))[1];
	check("rank", pt.STATE_RANK, rank.map(value));
	for (const name of rank) {
		const label = OVERVIEW.match(new RegExp(`\\t${name}: "([^"]+)"`))[1];
		check(`label of ${name}`, pt.STATE_LABELS[value(name)], label);
	}
});

test("quantities read as quantities", () => {
	check("fmt", [4, 4.0, 2.5, 1.23456, 0, null, undefined, "", "x"].map(pt.fmt_qty), ["4", "4", "2.5", "1.235", "0", "-", "-", "-", "-"]);
});

test("a where-now shared by several lines of one item says so", () => {
	check("one line", pt.shared_where_note({ where_now_lines: 1, where_now: [{}] }), "");
	check("three lines", pt.shared_where_note({ where_now_lines: 3, where_now: [{}] }), "For all 3 lines of this item on the job.");
	check("nowhere yet", pt.shared_where_note({ where_now_lines: 3, where_now: [] }), "");
});

test("the filters in words", () => {
	const options = { states: [{ value: "late", label: "Late" }], locations: [{ value: "project_shelf", label: "Project Shelf" }] };
	check("all", pt.describe_filters({ status: "late", location: "project_shelf", supplier: "Acme Supply", action: true, q: " elbow " }, options),
		'Late · Project Shelf · Acme Supply · needs action · "elbow"');
	check("none", pt.describe_filters(pt.default_state(), options), "");
});

// ------------------------------------------------------------------ markup

test("a name is never read as markup, and a search cannot cut an escaped character in half", () => {
	check("escaped", pt.highlight_html('<img src=x onerror=alert(1)>', "", esc), "&lt;img src=x onerror=alert(1)&gt;");
	check("marked and escaped", pt.highlight_html("A&B <b>", "b", esc), "A&amp;<mark>B</mark> &lt;<mark>b</mark>&gt;");
	check("'amp' does not match inside &amp;", pt.highlight_html("A&B", "amp", esc), "A&amp;B");
	check("regex characters are literal", pt.highlight_html("PIPE (2 IN.)", "(2 in.)", esc), "PIPE <mark>(2</mark> <mark>IN.)</mark>");
	check("blank", [pt.highlight_html(null, "x", esc), pt.highlight_html(undefined, "", esc)], ["", ""]);
});

test("the printed list escapes every value and lists each place", () => {
	const { items } = sample();
	items[0].item_name = "<script>alert(1)</script>";
	items[0].where_now = [{ text: "4 on the Project Shelf" }, { text: "2 at the job site" }];
	const html = pt.print_html(items, { title: "PRJ-90001 materials", subtitle: "Water <Wall>", filters: "Late", printed: "11-12-2026" }, esc);
	check("no script", html.includes("<script"), false);
	check("escaped name", html.includes("&lt;script&gt;alert(1)&lt;/script&gt;"), true);
	check("escaped subtitle", html.includes("Water &lt;Wall&gt;"), true);
	check("each place", html.includes("4 on the Project Shelf<br>2 at the job site"), true);
	check("not tracked, in words", html.includes("Location not tracked (not a stock item)"), true);
	check("what is showing", html.includes("Showing: Late"), true);
	check("nobody asked is a dash", html.includes('<td class="n">-</td>'), true);
});

// ------------------------------------------------------------------ the form script

test("the form script draws every column from the registries and never pushes history", () => {
	const code = SCRIPT.replace(/\/\/[^\n]*/g, "").replace(/\/\*[\s\S]*?\*\//g, "");
	check("line columns", code.includes("columns(pt.LINE_COLUMNS)"), true);
	check("item columns", code.includes("columns(pt.ITEM_COLUMNS)"), true);
	check("reads the address", code.includes("pt.read_query(window.location.search)"), true);
	check("writes it in place", code.includes("window.history.replaceState("), true);
	check("never pushes", /pushState\(/.test(code), false);
	check("never routes to change a filter", /set_route\([^)]*pt_/.test(code), false);
	check("gives the keys back", code.includes("frappe.route_options = pt.without_own_keys(frappe.route_options)"), true);
	check("unmounts the last app", code.includes("frm.__pt_app.unmount()"), true);
	// Receive and Print are still there, and still stop the header's own click.
	check("receive", code.includes('@click.stop="receiveAgainst(doc.name)"'), true);
	check("print one", code.includes('@click.stop="printDoc(group.doctype, doc.name)"'), true);
	check("print a group", code.includes('@click.stop="printGroup(group.doctype)"'), true);
	// Every method and computed the template names exists on the app.
	const template = SCRIPT.slice(SCRIPT.indexOf("template: `"), SCRIPT.indexOf("app.mount(el)"));
	const called = new Set([...template.matchAll(/\b([a-zA-Z_]\w*)\(/g)].map((m) => m[1]));
	const defined = new Set([...SCRIPT.matchAll(/^\t\t\t([a-zA-Z_]\w*)\([^)]*\) \{$/gm)].map((m) => m[1]));
	const missing = [...called].filter((name) => !defined.has(name) && !["String", "rect"].includes(name));
	check("template calls only what exists", missing, []);
});

// ------------------------------------------------------------------ run

for (const { name, fn } of tests) {
	const before = failures;
	try {
		await fn();
	} catch (e) {
		failures += 1;
		console.error(`  THREW ${e && e.stack ? e.stack : e}`);
	}
	console.log(`${failures === before ? "ok  " : "FAIL"} ${name}`);
}
console.log(`\n${tests.length} tests, ${checks} checks, ${failures} failed`);
process.exit(failures ? 1 : 0);
