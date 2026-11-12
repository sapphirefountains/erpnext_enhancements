#!/usr/bin/env node
/**
 * The Procurement Tracker's pure decisions (v1.811.0) under node:
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
 *   prices         (v1.814.0) the Rate, Amount, Unit price and Spent columns, "No price" and "No charge",
 *                  a range when lines differ, the total following the filters, sorting with blanks last
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
	const state = { view: "items", location: "project_shelf", status: "late", supplier: "Acme Supply", action: true, noprice: false, q: "elbow 2 in" };
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

test("a Supplier Return under the places says whether the goods have left", () => {
	check("sent back", pt.return_words({ status: "Sent Back", gone: true }), "sent back (Sent Back)");
	check("money back", pt.return_words({ status: "Credit Received", gone: true }), "sent back (Credit Received)");
	check("still here", pt.return_words({ status: "Requested", gone: false }), "to go back (Requested)");
	check("no status", [pt.return_words({ gone: true }), pt.return_words(null)], ["sent back", "to go back"]);
	// The template lists them from the row, and a click opens the return without toggling anything.
	check("listed in the where cell", SCRIPT.includes(`v-for="r in (row.supplier_returns || [])"`), true);
	check("opens the Supplier Return", SCRIPT.includes(`@click.stop="openDoc('Supplier Return', r.supplier_return)"`), true);
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
	check("line columns", code.includes("visible_columns(pt.LINE_COLUMNS"), true);
	check("item columns", code.includes("visible_columns(pt.ITEM_COLUMNS"), true);
	check("reads the address", code.includes("pt.read_query(window.location.search)"), true);
	check("writes it in place", code.includes("window.history.replaceState("), true);
	check("never pushes", /pushState\(/.test(code), false);
	check("never routes to change a filter", /set_route\([^)]*pt_/.test(code), false);
	check("gives the keys back", code.includes("frappe.route_options = pt.without_own_keys(frappe.route_options)"), true);
	check("unmounts the last app", code.includes("frm.__pt_app.unmount()"), true);
	// A write to the address still waiting when the form refreshes dies with its app: it would put
	// the old view back a quarter of a second after the new app read the address.
	check("the waiting address write is cancelled with its app", /beforeUnmount\(\) \{\s*clearTimeout\(address_timer\);/.test(code), true);
	check("and only the app on screen ever writes", code.includes("if (frm.__pt_app && frm.__pt_app !== app) return;"), true);
	// A group that first appears on a later refresh starts collapsed like the rest.
	check("a new group starts collapsed", code.includes("if (!(g.doctype in keep.collapsedGroups)) keep.collapsedGroups[g.doctype] = true;"), true);
	// Receive and Print are still there, and still stop the header's own click.
	check("receive", code.includes('@click.stop="receiveAgainst(doc.name)"'), true);
	check("print one", code.includes('@click.stop="printDoc(group.doctype, doc.name)"'), true);
	check("print a group", code.includes('@click.stop="printGroup(group.doctype)"'), true);
	// Supplier Returns (v1.807.0) put a Return on a receipt's header and on a line's receipt step; both
	// were carried into this widget when it was rewritten, with the same rules and the same launcher.
	check("return on a receipt's header, without toggling it", code.includes('<button v-if="canReturn(group.doctype, doc)" type="button" class="btn-receive"') && code.includes('@click.stop="returnToSupplier(doc.name)"'), true);
	check("return on one line, by mouse and by keyboard", code.includes('v-if="canReturnLine(row)"') && code.includes('@click="returnToSupplier(row.pr, row.item_code)"') && code.includes('@keydown.enter.prevent="returnToSupplier(row.pr, row.item_code)"'), true);
	check("only through the launcher, which opens an unsaved return", (code.match(/launcher\.can_start\(\)/g) || []).length === 2 && code.includes("launcher.start_from_receipt(receiptName, itemCode ? { item_code: itemCode } : {})"), true);
	check("never on a return, a draft or a canceled receipt", code.includes('["Return", "Return Issued", "Closed"].includes(doc.status)') && code.includes('["Draft", "Cancelled", "Return", "Return Issued"].includes(row.pr_status)'), true);
	// Every method and computed the template names exists on the app.
	const template = SCRIPT.slice(SCRIPT.indexOf("template: `"), SCRIPT.indexOf("app.mount(el)"));
	const called = new Set([...template.matchAll(/\b([a-zA-Z_]\w*)\(/g)].map((m) => m[1]));
	const defined = new Set([...SCRIPT.matchAll(/^\t\t\t([a-zA-Z_]\w*)\([^)]*\) \{$/gm)].map((m) => m[1]));
	const missing = [...called].filter((name) => !defined.has(name) && !["String", "rect"].includes(name));
	check("template calls only what exists", missing, []);
});

// ------------------------------------------------------------------ prices (v1.814.0)
//
// Nik, 2026-11-12: "on the procurement tracker, there should be another column for the price of the
// item we spent." Every figure below is made up. ERPNext holds no bills, so nothing here is billed or
// paid; Spent is what was ordered.

// Two items on a job. 417-080: three order lines, two prices, one with no price. 2622-010: only a
// line with no price, and a quote. 100-001: only a request, so nothing to say about a price.
function priced_items() {
	return [
		{
			row_id: "item#417-080", item_code: "417-080", item_name: "FITTING, ELBOW", stock_uom: "Unit", states: ["ordered"], state: "ordered", needs_action: true,
			spent: 132.5, spent_lines: 2, no_price_lines: 1, no_charge_lines: 0, draft_spent: null, draft_no_price_lines: 0,
			unit_price_low: 10, unit_price_high: 12, unit_price_avg: 11.0417, received_value: 40, quote_low: null, quote_high: null, quote_count: 0,
		},
		{
			row_id: "item#2622-010", item_code: "2622-010", item_name: "VALVE, BALL", stock_uom: "Unit", states: ["ordered"], state: "ordered", needs_action: true,
			spent: null, spent_lines: 0, no_price_lines: 2, no_charge_lines: 0, draft_spent: null, draft_no_price_lines: 0,
			unit_price_low: null, unit_price_high: null, unit_price_avg: null, received_value: null, quote_low: 8, quote_high: 9.5, quote_count: 2,
		},
		{
			row_id: "item#100-001", item_code: "100-001", item_name: "PIPE, 1 IN", stock_uom: "Unit", states: ["to_order"], state: "to_order", needs_action: true,
			spent: null, spent_lines: 0, no_price_lines: 0, no_charge_lines: 0, draft_spent: null, draft_no_price_lines: 0,
			unit_price_low: null, unit_price_high: null, unit_price_avg: null, received_value: null, quote_low: null, quote_high: null, quote_count: 0,
		},
		{
			row_id: "item#300-300", item_code: "300-300", item_name: "SPONGE", stock_uom: "Unit", states: ["received"], state: "received", needs_action: false,
			spent: 20, spent_lines: 1, no_price_lines: 0, no_charge_lines: 1, draft_spent: 15, draft_no_price_lines: 0,
			unit_price_low: 4, unit_price_high: 4, unit_price_avg: 4, received_value: 20, quote_low: null, quote_high: null, quote_count: 0,
		},
	];
}

test("money reads as money, a rate to four places, and a missing value as a dash", () => {
	check("whole", pt.fmt_money(1234.5, "USD"), "$1,234.50");
	check("zero is a value", pt.fmt_money(0, "USD"), "$0.00");
	check("a rate", pt.fmt_money(0.0125, "USD"), "$0.0125");
	check("two places at least", pt.fmt_money(10, "USD"), "$10.00");
	check("blank", [pt.fmt_money(null), pt.fmt_money(undefined), pt.fmt_money(""), pt.fmt_money("x")], ["-", "-", "-", "-"]);
	check("an unknown currency still reads", pt.fmt_money(5, "ZZZZ?").includes("5.00"), true);
});

test("the price columns exist only for a person who was sent prices", () => {
	const keys = (registry, prices) => pt.visible_columns(registry, prices).map((c) => c.key);
	check("by item without", keys(pt.ITEM_COLUMNS, false), ["item", "requested", "ordered", "received", "status", "where", "due"]);
	check("by item with", keys(pt.ITEM_COLUMNS, true), ["item", "requested", "ordered", "received", "unit_price", "spent", "status", "where", "due"]);
	check("by document without", keys(pt.LINE_COLUMNS, false), ["item", "where", "requested", "ordered", "received", "status"]);
	check("by document with", keys(pt.LINE_COLUMNS, true), ["item", "where", "requested", "ordered", "received", "rate", "amount", "status"]);
	// Nothing is called billed or paid: ERPNext holds no bills.
	const labels = [...pt.visible_columns(pt.ITEM_COLUMNS, true), ...pt.visible_columns(pt.LINE_COLUMNS, true)].map((c) => c.label.toLowerCase()).join(" ");
	check("no column says billed or paid", /bill|paid/.test(labels), false);
});

test("the unit price is one price when the lines agree and a range when they differ", () => {
	const [elbow, valve, pipe, sponge] = priced_items();
	check("a range", pt.item_price_view(elbow, "USD").unit, "$10.00 – $12.00");
	check("with its average", pt.item_price_view(elbow, "USD").unit_notes, ["$11.0417 on average"]);
	check("one price", pt.item_price_view(sponge, "USD").unit, "$4.00");
	check("one price has no average line", pt.item_price_view(sponge, "USD").unit_notes, []);
	// A request has no price: its cells are empty, not a dash and not $0.00.
	check("a request", [pt.item_price_view(pipe, "USD").unit, pt.item_price_view(pipe, "USD").spent], ["", ""]);
	check("a quote on its own line", pt.item_price_view(valve, "USD").unit_notes, ["Quoted $8.00 – $9.50"]);
	// A range over lines written in different units may be two prices for two things (FT and Unit).
	check("units that differ are named", pt.item_price_view(Object.assign({}, elbow, { price_uoms: ["FT", "Unit"] }), "USD").unit_notes.includes("Priced per FT and per Unit"), true);
	check("one unit says nothing", pt.item_price_view(Object.assign({}, elbow, { price_uoms: ["Unit"] }), "USD").unit_notes.length, 1);
});

test("a missing price is 'No price', never $0.00, and a sum says how many lines it leaves out", () => {
	const [elbow, valve, , sponge] = priced_items();
	const view = pt.item_price_view(elbow, "USD");
	check("spent is the priced lines'", view.spent, "$132.50");
	check("and says what it left out", view.spent_notes.includes("1 line with no price"), true);
	check("and what has arrived", view.spent_notes.includes("Received $40.00"), true);
	const none = pt.item_price_view(valve, "USD");
	check("only unpriced lines", [none.unit, none.spent], ["No price", "No price"]);
	check("plural", none.spent_notes, ["2 lines with no price"]);
	check("never a zero", [none.unit, none.spent].some((t) => t.includes("$0")), false);
	// A deliberately free line reads No charge; a mix says so; a draft is not counted.
	check("a free line in a priced item", pt.item_price_view(sponge, "USD").spent_notes.includes("1 line no charge"), true);
	check("a draft is shown and not counted", pt.item_price_view(sponge, "USD").spent_notes.includes("$15.00 on draft orders, not counted"), true);
	check("only free", pt.item_price_view({ spent_lines: 0, no_charge_lines: 1 }, "USD").spent, "No charge");
	check("a draft with no price counts as no price", pt.item_price_view({ spent_lines: 0, draft_no_price_lines: 1 }, "USD").spent, "No price");
});

test("a line's price cells depend on the document it is shown in; a request has none", () => {
	const row = {
		po: "PO-1", pr: "PR-1", sq: "SQ-1", price_state: "priced", po_rate: 3, po_amount: 30, po_rate_uom: "Unit", received_value: 12,
		receipt_state: "priced", receipt_rate: 3, receipt_value: 12, sq_item: "SQI-1", quote_rate: 2.5, quote_amount: 25,
	};
	check("an order's own line", pt.price_cells(row, "Purchase Order"), { kind: "order", state: "priced", rate: 3, amount: 30, uom: "Unit", received: 12 });
	check("a receipt at the order's rate", pt.price_cells(row, "Purchase Receipt").amount, 12);
	check("a quote", [pt.price_cells(row, "Supplier Quotation").kind, pt.price_cells(row, "Supplier Quotation").amount], ["quote", 25]);
	for (const doctype of ["Material Request", "Request for Quotation", "Purchase Invoice"]) {
		check("no price in " + doctype, [pt.price_cells(row, doctype), pt.price_text(pt.price_cells(row, doctype), "rate", "USD"), pt.price_text(pt.price_cells(row, doctype), "amount", "USD")], [null, "", ""]);
	}
	check("a person sent no prices has nothing", pt.price_cells({ po: "PO-1" }, "Purchase Order"), null);
	const no_price = pt.price_cells({ price_state: "no_price", po_rate: null, po_amount: null }, "Purchase Order");
	check("a rate of 0 says so", [pt.price_text(no_price, "rate", "USD"), pt.price_text(no_price, "amount", "USD")], ["No price", "No price"]);
	const free = pt.price_cells({ price_state: "no_charge" }, "Purchase Order");
	check("a free line says so", pt.price_text(free, "rate", "USD"), "No charge");
	check("the tooltip says it is not billed or paid", pt.price_title(pt.price_cells(row, "Purchase Order"), "USD").includes("not what was billed or paid"), true);
	check("and what has arrived", pt.price_title(pt.price_cells(row, "Purchase Order"), "USD").includes("received so far: $12.00"), true);
	check("a quote is not a spend", pt.price_title(pt.price_cells(row, "Supplier Quotation"), "USD").includes("not added into what was spent"), true);
});

test("a document's total says it is this job's lines when the document has others", () => {
	const whole = { amount: 1200, lines: 3, no_price_lines: 0, no_charge_lines: 0, job_lines: 3, doc_lines: 3, partial: false, doc_total: null };
	check("a whole order", pt.doc_price_text(whole, "Purchase Order", "USD").text, "$1,200.00");
	const part = { amount: 500, lines: 1, no_price_lines: 0, no_charge_lines: 0, job_lines: 1, doc_lines: 4, partial: true, doc_total: 2100 };
	const said = pt.doc_price_text(part, "Purchase Order", "USD");
	check("a part", said.text, "$500.00 · this job's lines");
	check("and the whole in the tooltip", said.title.includes("1 of the document's 4 lines") && said.title.includes("$2,100.00"), true);
	const gaps = pt.doc_price_text({ amount: 300, lines: 2, no_price_lines: 3, no_charge_lines: 0, job_lines: 5, doc_lines: 5, partial: false }, "Purchase Order", "USD");
	check("what it leaves out", gaps.text, "$300.00 · 3 lines with no price");
	check("only unpriced", pt.doc_price_text({ amount: 0, lines: 0, no_price_lines: 2, no_charge_lines: 0, job_lines: 2, partial: false }, "Purchase Order", "USD").text, "No price");
	check("a receipt is what arrived", pt.doc_price_text(whole, "Purchase Receipt", "USD").text, "$1,200.00 received");
	check("a quotation is a quote", pt.doc_price_text(whole, "Supplier Quotation", "USD").text, "$1,200.00 quoted");
	check("a document with no price", pt.doc_price_text(null, "Material Request", "USD"), { text: "", title: "" });
});

test("sorting by Spent and Unit price: items with no price last in both directions", () => {
	const items = priced_items();
	const codes = (key, dir) => pt.sort_rows(items, pt.ITEM_COLUMNS, { key, dir }).map((r) => r.item_code);
	check("spent ascending", codes("spent", "asc"), ["300-300", "417-080", "2622-010", "100-001"]);
	check("spent descending", codes("spent", "desc"), ["417-080", "300-300", "2622-010", "100-001"]);
	check("unit price ascending", codes("unit_price", "asc"), ["300-300", "417-080", "2622-010", "100-001"]);
	check("unit price descending", codes("unit_price", "desc"), ["417-080", "300-300", "2622-010", "100-001"]);
	check("the click cycle works on the new columns", pt.next_sort(null, "spent", pt.ITEM_COLUMNS), { key: "spent", dir: "asc" });
	check("the original is untouched", items.map((r) => r.item_code), ["417-080", "2622-010", "100-001", "300-300"]);
});

test("sorting a document's lines by Rate and Amount follows the document it is in", () => {
	const lines = [
		{ row_id: "a", item_code: "A", price_state: "priced", po_rate: 9, po_amount: 90, receipt_state: "priced", receipt_rate: 9, receipt_value: 36 },
		{ row_id: "b", item_code: "B", price_state: "no_price", po_rate: null, po_amount: null, receipt_state: "no_price" },
		{ row_id: "c", item_code: "C", price_state: "priced", po_rate: 2, po_amount: 200, receipt_state: "priced", receipt_rate: 2, receipt_value: 8 },
		{ row_id: "d", item_code: "D" },
	];
	const ids = (key, dir, doctype) => pt.sort_rows(lines, pt.LINE_COLUMNS, { key, dir }, doctype).map((r) => r.row_id);
	check("order rate ascending, no price last", ids("rate", "asc", "Purchase Order"), ["c", "a", "b", "d"]);
	check("order rate descending, no price still last", ids("rate", "desc", "Purchase Order"), ["a", "c", "b", "d"]);
	check("order amount", ids("amount", "desc", "Purchase Order"), ["c", "a", "b", "d"]);
	check("receipt amount is what arrived, not the order's", ids("amount", "desc", "Purchase Receipt"), ["a", "c", "b", "d"]);
	check("a request has nothing to order by: rows come back as they were", ids("amount", "asc", "Material Request"), ["a", "b", "c", "d"]);
});

test("'No price only' finds the lines to fix, in both views", () => {
	const on = { noprice: true };
	check("items", pt.filter_items(priced_items(), on).map((r) => r.item_code), ["417-080", "2622-010"]);
	check("a draft's missing price counts too", pt.has_no_price({ draft_no_price_lines: 1 }), true);
	check("off", pt.filter_items(priced_items(), {}).length, 4);
	const lines = [
		{ row_id: "a", item_code: "A", price_state: "priced" },
		{ row_id: "b", item_code: "B", price_state: "no_price" },
		{ row_id: "c", item_code: "C", price_state: "no_charge" },
	];
	const groups = [{ doctype: "Purchase Order", documents: [{ name: "PO-1", items: lines }, { name: "PO-2", items: [lines[0]] }] }];
	const out = pt.filter_groups(groups, on);
	check("only the line, and only the document that has one", out.map((g) => g.documents.map((d) => [d.name, d.items.map((r) => r.item_code)])), [[["PO-1", ["B"]]]]);
	check("a free line is not a missing price", pt.has_no_price({ price_state: "no_charge" }), false);
	check("no prices sent, nothing is flagged", pt.has_no_price({ item_code: "X" }), false);
	check("it narrows, so Clear is offered", [pt.is_filtered(on), pt.is_narrowed(on)], [true, true]);
	check("it is said in words", pt.describe_filters({ noprice: true, action: true }, {}), "needs action · no price");
});

test("'No price only' and the view are in the address, and Back and Forward are not touched", () => {
	const state = { view: "items", noprice: true, status: "ordered" };
	const query = pt.write_query("", state);
	check("written", query, "?pt_view=items&pt_status=ordered&pt_noprice=1");
	check("read back", pt.read_query(query).noprice, true);
	check("off writes nothing", pt.write_query("?pt_noprice=1", { noprice: false }), "");
	check("the key is the widget's", pt.without_own_keys({ pt_noprice: "1", scroll_to: "x" }), { scroll_to: "x" });
	check("cleared keeps the view and drops the filter", pt.cleared(state), { view: "items", location: "", status: "", supplier: "", action: false, noprice: false, q: "" });
});

test("the total follows the filters and says what it leaves out", () => {
	const all = pt.spent_summary(priced_items(), "USD");
	check("everything", [all.amount, all.text], [152.5, "$152.50"]);
	check("counts", [all.lines, all.no_price_lines, all.no_charge_lines], [3, 3, 1]);
	check("says how many", all.notes, ["3 lines with no price", "1 line no charge"]);
	// With a filter on, the total is over the rows that are showing.
	const unpriced = pt.spent_summary(pt.filter_items(priced_items(), { noprice: true }), "USD");
	check("no price only", [unpriced.amount, unpriced.notes], [132.5, ["3 lines with no price"]]);
	const ordered_only = pt.spent_summary(pt.filter_items(priced_items(), { status: "ordered" }), "USD");
	check("by status", ordered_only.amount, 132.5);
	const searched = pt.spent_summary(pt.filter_items(priced_items(), { q: "sponge" }), "USD");
	check("by search", [searched.amount, searched.notes], [20, ["1 line no charge"]]);
	const none = pt.spent_summary([], "USD");
	check("nothing shown", [none.amount, none.notes], [0, []]);
	check("a request-only item adds nothing and says nothing", pt.spent_summary([priced_items()[2]], "USD").notes, []);
});

test("the printed list gains the price columns only for someone who was sent prices", () => {
	const items = priced_items().map((r) => Object.assign({ state_label: "Ordered", requested_qty: 5, ordered_qty: 5, received_qty: 1, suppliers: [], where_now: [] }, r));
	const plain = pt.print_html(items, { title: "PRJ-1 materials" }, esc);
	check("no price column", /Unit price|Spent|landscape/.test(plain), false);
	const priced = pt.print_html(items, { title: "PRJ-1 materials", prices: true, currency: "USD" }, esc);
	check("the columns", priced.includes("<th class=\"n\">Unit price</th>") && priced.includes("<th class=\"n\">Spent</th>"), true);
	check("a range and a total", priced.includes("$10.00 – $12.00") && priced.includes("$152.50"), true);
	check("the total says what it is", priced.includes("Total spent") && priced.includes("not billed or paid"), true);
	check("No price on paper", priced.includes("No price"), true);
	check("ten columns want a landscape page", priced.includes("@page{size:landscape}"), true);
	const hostile = pt.print_html([Object.assign({}, items[0], { item_name: "<img src=x onerror=alert(1)>" })], { prices: true, currency: "USD" }, esc);
	check("still escaped", hostile.includes("<img src=x") , false);
});

test("the form script draws the price columns from the registries, behind the server's say-so", () => {
	const code = SCRIPT.replace(/\/\/[^\n]*/g, "").replace(/\/\*[\s\S]*?\*\//g, "");
	check("columns follow the prices", code.includes("pt.visible_columns(pt.LINE_COLUMNS, this.hasPrices)") && code.includes("pt.visible_columns(pt.ITEM_COLUMNS, this.hasPrices)"), true);
	check("prices only if the server sent them", code.includes("const prices = data.prices || null;"), true);
	check("by-item cells only with prices", /<template v-if="hasPrices">[\s\S]*?data-label="Unit price"[\s\S]*?data-label="Spent"/.test(code), true);
	check("by-document cells only with prices", /<template v-if="hasPrices">[\s\S]*?data-label="Rate"[\s\S]*?data-label="Amount"/.test(code), true);
	check("the total is a footer that follows the filters", code.includes("spentSummary() {") && code.includes("pt.spent_summary(this.filteredItems, this.currency)") && code.includes('<tfoot v-if="spentSummary">'), true);
	check("the document total is in the header, for those who see prices", code.includes('v-if="hasPrices && docPrice(group.doctype, doc).text"'), true);
	check("the quick filter is offered only with prices", code.includes('<label v-if="hasPrices" class="pt-check"') && code.includes('v-model="view.noprice"'), true);
	check("a shared link cannot leave it on for someone who has no prices", code.includes("if (!prices) this.view.noprice = false;"), true);
	check("sorting is told the document's type", code.includes("this.sortFor(doctype, doc.name), doctype)"), true);
	check("the print hands over prices only when there are some", code.includes("prices: this.hasPrices,"), true);
	check("never bills or pays", /\b(billed|paid)\b/i.test(SCRIPT.slice(SCRIPT.indexOf("template: `"))) && !/<th[^>]*>\s*(billed|paid)/i.test(code), true);
	check("the colspan is not a hard-coded 7", code.includes(':colspan="lineColumns.length + 1"'), true);
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
