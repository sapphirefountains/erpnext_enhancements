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
	check("columns come from the view, through the registries", code.includes("pt.shown_columns(this.layout.columns, this.mode, this.hasPrices)"), true);
	check("reads the address", code.includes("search: window.location.search"), true);
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
	const shown = (mode, prices) => pt.shown_columns(pt.STANDARD_COLUMNS[mode], mode, prices).columns.map((c) => c.key);
	check("by item without", shown("items", false), ["item", "requested", "ordered", "received", "status", "where", "due", "supplier", "documents"]);
	check("by item with", shown("items", true), ["item", "requested", "ordered", "received", "unit_price", "spent", "status", "where", "due", "supplier", "documents"]);
	check("by document without", shown("documents", false), ["item", "where", "requested", "ordered", "received", "status", "doc_chain"]);
	check("by document with", shown("documents", true), ["item", "where", "requested", "ordered", "received", "rate", "amount", "status", "doc_chain"]);
	// The picker's list: every column, less the price ones for a person who was sent none.
	check("the picker offers no price column without prices", keys(pt.ITEM_COLUMNS, false).filter((k) => pt.ITEM_COLUMNS[k].price), []);
	check("and every column with them", keys(pt.ITEM_COLUMNS, true), Object.keys(pt.ITEM_COLUMNS));
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
	check("an order's own line", pt.price_cells(row, "Purchase Order"), { kind: "order", state: "priced", rate: 3, amount: 30, uom: "Unit", received: 12, draft: false });
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

test("what Spent is and is not is said on the column, in the cell and in the order's tooltip", () => {
	const spent = pt.visible_columns(pt.ITEM_COLUMNS, true).find((c) => c.key === "spent");
	check("the column's hint is the note", spent.hint, pt.SPENT_NOTE);
	for (const words of ["sum of the order lines' own amounts", "discount on the whole order", "taxes and freight", "closed short in full", "not billed or paid"]) {
		check("the note says: " + words, pt.SPENT_NOTE.includes(words), true);
	}
	check("the amount column says it too", pt.visible_columns(pt.LINE_COLUMNS, true).find((c) => c.key === "amount").hint.includes("discount on the whole order"), true);
	check("the order line's tooltip carries it", pt.price_title(pt.price_cells({ price_state: "priced", po_amount: 100, po_rate: 10 }, "Purchase Order"), "USD").includes("taxes and freight"), true);
	check("no other column carries a hint", pt.visible_columns(pt.ITEM_COLUMNS, true).filter((c) => c.hint).map((c) => c.key), ["spent"]);
	const code = SCRIPT.replace(/\/\/[^\n]*/g, "").replace(/\/\*[\s\S]*?\*\//g, "");
	check("the headers show the hint", (code.match(/\(col\.hint \? '\. ' \+ col\.hint : ''\)/g) || []).length, 2);
	check("the Spent cell shows the note", code.includes(':title="spentNote"') && code.includes("spentNote() {"), true);
});

test("a draft quotation or receipt is labeled a draft, and a canceled one never reaches the widget priced", () => {
	const row = { po: "PO-1", pr: "PR-1", sq: "SQ-1", receipt_state: "priced", receipt_rate: 3, receipt_value: 12, receipt_docstatus: 0, sq_item: "SQI-1", quote_rate: 2.5, quote_amount: 25, quote_docstatus: 0 };
	check("a draft quote", pt.price_cells(row, "Supplier Quotation").draft, true);
	check("its tooltip", pt.price_title(pt.price_cells(row, "Supplier Quotation"), "USD").includes("draft quotation"), true);
	check("a draft receipt", pt.price_cells(row, "Purchase Receipt").draft, true);
	check("its tooltip says nothing has arrived", pt.price_title(pt.price_cells(row, "Purchase Receipt"), "USD").includes("nothing has been received"), true);
	const live = Object.assign({}, row, { receipt_docstatus: 1, quote_docstatus: 1 });
	check("a submitted one is not", [pt.price_cells(live, "Supplier Quotation").draft, pt.price_cells(live, "Purchase Receipt").draft], [false, false]);
	const total = { amount: 80, lines: 1, no_price_lines: 0, no_charge_lines: 0, job_lines: 1, partial: false };
	check("a draft quotation's total", pt.doc_price_text(Object.assign({ docstatus: 0 }, total), "Supplier Quotation", "USD").text, "$80.00 quoted (draft)");
	check("a draft receipt's total is not 'received'", pt.doc_price_text(Object.assign({ docstatus: 0 }, total), "Purchase Receipt", "USD").text, "$80.00 on a draft receipt");
	check("a submitted receipt's is", pt.doc_price_text(Object.assign({ docstatus: 1 }, total), "Purchase Receipt", "USD").text, "$80.00 received");
	const item = { spent_lines: 0, quote_count: 2, quote_low: 8, quote_high: 9, quote_draft_count: 1 };
	check("some drafts", pt.item_price_view(item, "USD").unit_notes, ["Quoted $8.00 – $9.00 (1 draft)"]);
	check("all drafts", pt.item_price_view(Object.assign({}, item, { quote_draft_count: 2 }), "USD").unit_notes, ["Quoted $8.00 – $9.00 (draft)"]);
	check("no drafts", pt.item_price_view(Object.assign({}, item, { quote_draft_count: 0 }), "USD").unit_notes, ["Quoted $8.00 – $9.00"]);
	check("a canceled one has no keys, so no cells", [pt.price_cells({ sq: "SQ-1" }, "Supplier Quotation"), pt.price_cells({ pr: "PR-1" }, "Purchase Receipt")], [null, null]);
});

test("the form script draws the price columns from the registries, behind the server's say-so", () => {
	const code = SCRIPT.replace(/\/\/[^\n]*/g, "").replace(/\/\*[\s\S]*?\*\//g, "");
	check("columns follow the prices", code.includes("pt.shown_columns(this.layout.columns, this.mode, this.hasPrices)"), true);
	check("prices only if the server sent them", code.includes("const prices = data.prices || null;"), true);
	check("by-item price cells are a column of the view, which has none without prices", /col\.key === 'unit_price'[\s\S]*?data-label="Unit price"[\s\S]*?col\.key === 'spent'[\s\S]*?data-label="Spent"/.test(code), true);
	check("by-document price cells too", /col\.key === 'rate'[\s\S]*?data-label="Rate"[\s\S]*?col\.key === 'amount'[\s\S]*?data-label="Amount"/.test(code), true);
	check("the total is a footer that follows the filters", code.includes("spentSummary() {") && code.includes("pt.spent_summary(this.filteredItems, this.currency)") && code.includes('<tfoot v-if="spentSummary">'), true);
	check("the document total is in the header, for those who see prices and have a price column on", code.includes('v-if="showDocPrice && docPrice(group.doctype, doc).text"') && code.includes("return this.hasPrices && this.cols.some("), true);
	check("the quick filter is offered only with prices", code.includes('<label v-if="hasPrices" class="pt-check"') && code.includes('v-model="view.noprice"'), true);
	check("a shared link cannot leave it on for someone who has no prices (resolve_start gets the answer)", code.includes("prices: !!prices,"), true);
	check("sorting is told the document's type", code.includes("this.sortFor(doctype, doc.name), doctype)"), true);
	check("the print hands over prices only when there are some", code.includes("prices: this.hasPrices,"), true);
	check("never bills or pays", /\b(billed|paid)\b/i.test(SCRIPT.slice(SCRIPT.indexOf("template: `"))) && !/<th[^>]*>\s*(billed|paid)/i.test(code), true);
	check("the colspan is the columns on screen", code.includes(':colspan="cols.length"'), true);
});

// ------------------------------------------------------------------ saved views (v1.816.0)

const CATALOG = JSON.parse(fs.readFileSync(path.join(APP, "data/procurement_tracker_views.json"), "utf8"));
const VIEWS_PY = fs.readFileSync(path.join(APP, "procurement_views.py"), "utf8");

// A few made-up rows, in the keys the feed carries.
function view_rows() {
	return [
		{ row_id: "i1", item_code: "AA-1", item_name: "Alpha", requested_qty: 10, ordered_qty: 10, received_qty: 4, to_order_qty: 0, waiting_qty: 6, state: "late", states: ["late", "partly_received"], state_label: "Late", where_now_text: "4 at the Project Shelf", location_kinds: ["project_shelf"], expected_date: "2026-11-01", suppliers: ["North Supply"], line_count: 1, stock_uom: "Unit", received_at: ["Main"], unit_price_avg: 2.5, spent: 25, no_price_lines: 0, draft_no_price_lines: 0, received_value: 10 },
		{ row_id: "i2", item_code: "BB-2", item_name: "Bravo", requested_qty: 3, ordered_qty: 3, received_qty: 3, to_order_qty: 0, waiting_qty: 0, state: "received", states: ["received"], state_label: "Received", where_now_text: "3 with no place recorded", location_kinds: [], expected_date: null, suppliers: [], line_count: 2, stock_uom: "FT", received_at: [], unit_price_avg: null, spent: null, no_price_lines: 1, draft_no_price_lines: 0 },
		{ row_id: "i3", item_code: "CC-3", item_name: "Charlie", requested_qty: null, ordered_qty: 7, received_qty: 0, to_order_qty: 7, waiting_qty: 7, state: "to_order", states: ["to_order"], state_label: "To order", where_now_text: "Not received yet", location_kinds: ["not_received"], expected_date: "2026-12-15", suppliers: ["East Parts", "North Supply"], line_count: 1, stock_uom: "Unit", received_at: [], unit_price_avg: 12, spent: 84, no_price_lines: 0, draft_no_price_lines: 0, received_value: 0 },
	];
}
const mine_view = (id, mode, layout, extra) => Object.assign({ id: id, name: "Mine " + id, mode: mode, layout: layout, preset: false }, extra || {});
const all_views = (mine) => pt.merge_views({ presets: CATALOG.presets }, mine || []);

test("the browser's column registries are the server's catalog, key for key", () => {
	for (const [mode, registry] of [["documents", pt.LINE_COLUMNS], ["items", pt.ITEM_COLUMNS]]) {
		const listed = CATALOG.modes[mode].columns;
		check(`${mode}: same keys, same order`, Object.keys(registry), listed.map((c) => c.key));
		listed.forEach((c) => {
			const r = registry[c.key];
			check(`${mode}.${c.key}: label`, r.label, c.label);
			check(`${mode}.${c.key}: filter kind`, r.filter || null, c.filter || null);
			check(`${mode}.${c.key}: price`, !!r.price, !!c.price);
			check(`${mode}.${c.key}: sortable`, r.sortable !== false, c.sortable !== false);
		});
	}
	check("operators", pt.OPERATORS, CATALOG.operators);
	for (const key of Object.keys(pt.LIMITS)) check("limit " + key, pt.LIMITS[key], CATALOG.limits[key]);
});

test("the standard views are the tracker as it was, and every shipped preset is a layout the tracker accepts", () => {
	for (const mode of ["documents", "items"]) {
		const std = CATALOG.presets.find((p) => p.id === pt.STANDARD_VIEW_IDS[mode]);
		check(`${mode}: columns`, std.layout.columns, pt.STANDARD_COLUMNS[mode]);
		check(`${mode}: the built-in fallback is the shipped standard`, pt.standard_layout(mode), std.layout);
	}
	// What the tables had before views: the columns the price-column release drew, in its order.
	check("by item, as before", pt.STANDARD_COLUMNS.items, ["item", "requested", "ordered", "received", "unit_price", "spent", "status", "where", "due", "supplier", "documents"]);
	check("by document, as before", pt.STANDARD_COLUMNS.documents, ["item", "where", "requested", "ordered", "received", "rate", "amount", "status", "doc_chain"]);
	CATALOG.presets.forEach((p) => {
		const cleaned = pt.clean_layout(p.layout, p.mode);
		check(`${p.id}: accepted as shipped`, cleaned.repaired, false);
		check(`${p.id}: unchanged by cleaning`, cleaned.layout, p.layout);
	});
	check("ids are unique", new Set(CATALOG.presets.map((p) => p.id)).size, CATALOG.presets.length);
	check("a preset says what it is for", CATALOG.presets.every((p) => p.name && p.description), true);
});

test("a view that is out of date or damaged is mended, and nothing a person stored can throw", () => {
	const mended = pt.clean_layout({ columns: ["item", "retired_column", "item", "status"], sort: { key: "retired_column", dir: "asc" }, filters: { action: "yes", rules: [{ column: "received", op: "contains", value: "x" }, { column: "ordered", op: "between", min: 1, max: 5 }] } }, "items");
	check("unknown and repeated columns dropped", mended.layout.columns, ["item", "status"]);
	check("a sort on a missing column dropped", mended.layout.sort, null);
	check("a loose flag is off", mended.layout.filters.action, false);
	check("a test that does not suit the column dropped, the rest kept", mended.layout.filters.rules, [{ column: "ordered", op: "between", min: 1, max: 5 }]);
	check("and it says so", mended.repaired, true);
	check("a view with no usable column is the standard one", pt.clean_layout({ columns: ["nope"] }, "items").layout, pt.standard_layout("items"));
	for (const junk of [null, undefined, "text", 42, [], { columns: "item" }, { columns: [1, 2] }, { columns: ["item"], filters: "x" }, { columns: ["item"], filters: { rules: "x" } }]) {
		const out = pt.clean_layout(junk, "documents");
		check("garbage still draws: " + JSON.stringify(junk), out.layout.columns.length > 0, true);
	}
	check("a column that cannot be sorted is not a sort", pt.clean_layout({ columns: ["item", "doc_chain"], sort: { key: "doc_chain", dir: "asc" } }, "documents").layout.sort, null);
	check("a grouping that does not exist is By document", pt.clean_layout({ columns: ["rate"] }, "nonsense").layout.columns, ["rate"]);
	check("the same layout is a different view in the other grouping", pt.clean_layout({ columns: ["unit_price"] }, "documents").layout, pt.standard_layout("documents"));
});

test("a column filter needs a column that has it, a test that suits it, and a value", () => {
	const rule = (r) => pt.clean_rule(r, "items");
	check("contains", rule({ column: "item", op: "contains", value: "  alp " }), { column: "item", op: "contains", value: "alp" });
	check("no text, no filter", rule({ column: "item", op: "contains", value: "  " }), null);
	check("any of", rule({ column: "status", op: "any_of", values: ["late", "late", "to_order", ""] }), { column: "status", op: "any_of", values: ["late", "to_order"] });
	check("any of nothing", rule({ column: "status", op: "any_of", values: [] }), null);
	check("a range", rule({ column: "received", op: "between", min: "1", max: 5 }), { column: "received", op: "between", min: 1, max: 5 });
	check("one end is enough", rule({ column: "received", op: "between", min: null, max: 5 }), { column: "received", op: "between", min: null, max: 5 });
	check("no end, no filter", rule({ column: "received", op: "between", min: "", max: "" }), null);
	check("an upside-down range", rule({ column: "received", op: "between", min: 9, max: 1 }), null);
	check("not a number", rule({ column: "received", op: "between", min: "abc" }), null);
	check("dates are dates", [rule({ column: "due", op: "between", min: "2026-11-01" }), rule({ column: "due", op: "between", min: "next week" })], [{ column: "due", op: "between", min: "2026-11-01", max: null }, null]);
	check("blank needs nothing", rule({ column: "supplier", op: "blank" }), { column: "supplier", op: "blank" });
	check("a number column has no 'contains'", rule({ column: "received", op: "contains", value: "1" }), null);
	check("a column that cannot be filtered or does not exist", [rule({ column: "nope", op: "blank" }), rule(null), rule("x")], [null, null, null]);
	check("a by-item column is not a by-document one", pt.clean_rule({ column: "spent", op: "blank" }, "documents"), null);
	check("at most the server's limit", pt.ready_rules(new Array(30).fill({ column: "item", op: "blank" }), "items").length, pt.LIMITS.max_rules);
});

test("a column filter narrows lines and items, each test as its name says", () => {
	const rows = view_rows();
	const ctx = (r, extra) => Object.assign({ rules: [r], mode: "items", prices: true }, extra || {});
	const codes = (r, extra) => pt.filter_items(rows, pt.default_state(), ctx(r, extra)).map((x) => x.item_code);
	check("contains reads what the cell says", codes({ column: "where", op: "contains", value: "no place recorded" }), ["BB-2"]);
	check("contains is not case-sensitive", codes({ column: "item", op: "contains", value: "BRAVO" }), ["BB-2"]);
	check("any of a status", codes({ column: "status", op: "any_of", values: ["late", "to_order"] }), ["AA-1", "CC-3"]);
	check("any of a place", codes({ column: "where", op: "any_of", values: ["not_received"] }), ["CC-3"]);
	check("any of a supplier, when the item has two", codes({ column: "supplier", op: "any_of", values: ["East Parts"] }), ["CC-3"]);
	check("a number range keeps both ends", codes({ column: "received", op: "between", min: 3, max: 4 }), ["AA-1", "BB-2"]);
	check("zero is a number, not blank", codes({ column: "received", op: "between", min: 0, max: 0 }), ["CC-3"]);
	check("a blank is outside any range", codes({ column: "requested", op: "between", min: 0 }), ["AA-1", "BB-2"]);
	check("a date range", codes({ column: "due", op: "between", min: "2026-11-01", max: "2026-11-30" }), ["AA-1"]);
	check("a date with nothing in it is outside a range", codes({ column: "due", op: "between", max: "2030-01-01" }), ["AA-1", "CC-3"]);
	check("blank", codes({ column: "due", op: "blank" }), ["BB-2"]);
	check("not blank", codes({ column: "supplier", op: "not_blank" }), ["AA-1", "CC-3"]);
	check("a blank place counts the words, not the kinds", codes({ column: "where", op: "blank" }), []);
	check("lines with no price", codes({ column: "no_price", op: "between", min: 1 }), ["BB-2"]);
	check("a half-made filter filters nothing", codes({ column: "item", op: "contains", value: "" }), ["AA-1", "BB-2", "CC-3"]);
	check("several filters all have to hold", pt.filter_items(rows, pt.default_state(), { rules: [{ column: "received", op: "between", min: 3 }, { column: "status", op: "any_of", values: ["received"] }], mode: "items", prices: true }).map((x) => x.item_code), ["BB-2"]);
	check("they combine with the toolbar's filters", pt.filter_items(rows, Object.assign(pt.default_state(), { supplier: "North Supply" }), ctx({ column: "received", op: "between", min: 1 })).map((x) => x.item_code), ["AA-1"]);
	check("no filter at all returns the list untouched", pt.filter_items(rows, pt.default_state(), { rules: [], mode: "items", prices: true }) === rows, true);
});

test("a filter on a price column does nothing for a person who was sent no prices", () => {
	const rows = view_rows();
	const rule = { column: "spent", op: "between", min: 50 };
	check("with prices it narrows", pt.filter_items(rows, pt.default_state(), { rules: [rule], mode: "items", prices: true }).map((x) => x.item_code), ["CC-3"]);
	check("without, it is not applied", pt.filter_items(rows, pt.default_state(), { rules: [rule], mode: "items", prices: false }).length, 3);
	const usable = pt.usable_rules([rule, { column: "item", op: "blank" }], "items", false);
	check("usable_rules sets it aside and names it", [usable.active, usable.withheld], [[{ column: "item", op: "blank" }], ["Spent"]]);
	check("and says so in words", pt.withheld_text(["Spent", "Unit price"]), "Spent, Unit price are not shown to you: prices are for people who may read Purchase Orders.");
	check("one column", pt.withheld_text(["Spent"]), "Spent is not shown to you: prices are for people who may read Purchase Orders.");
	check("nothing withheld, nothing said", pt.withheld_text([]), "");
});

test("a line's rate and amount filter by the document it sits in, and by-document lines filter too", () => {
	const groups = [
		{ doctype: "Purchase Order", documents: [{ name: "PO-1", items: [{ row_id: "a", price_state: "priced", po_rate: 4, po_amount: 40, received_qty: 1 }, { row_id: "b", price_state: "no_price", received_qty: 1 }] }] },
		{ doctype: "Material Request", documents: [{ name: "MR-1", items: [{ row_id: "c", received_qty: 0 }] }] },
	];
	const out = pt.filter_groups(groups, pt.default_state(), { rules: [{ column: "rate", op: "between", min: 1 }], mode: "documents", prices: true });
	check("only the priced order line is left, and the request's document is not shown", out.map((g) => [g.doctype, g.documents.map((d) => d.items.map((r) => r.row_id))]), [["Purchase Order", [["a"]]]]);
	check("the lines that are left are the same objects", out[0].documents[0].items[0] === groups[0].documents[0].items[0], true);
	check("count_lines follows", pt.count_lines(out), 1);
	const none = pt.filter_groups(groups, pt.default_state(), { rules: [{ column: "received", op: "between", min: 99 }], mode: "documents", prices: true });
	check("nothing matches, nothing is shown", none, []);
});

test("applying a view sets the columns, the sort, the toolbar filters and the filters on columns", () => {
	const view = CATALOG.presets.find((p) => p.id === "to-chase");
	const state = pt.state_from_layout(view.layout, "items", { id: view.id, q: "pipe", prices: true });
	check("the grouping", state.view.view, "items");
	check("the toolbar filter", state.view.action, true);
	check("the search stays", state.view.q, "pipe");
	check("the columns, in order", state.layout.columns, view.layout.columns);
	check("the sort", state.layout.sort, { key: "status", dir: "asc" });
	check("the id", state.layout.id, "to-chase");
	const gaps = CATALOG.presets.find((p) => p.id === "location-gaps");
	check("a filter on a column comes with it", pt.state_from_layout(gaps.layout, "items", { id: gaps.id, prices: true }).layout.rules, gaps.layout.filters.rules);
	const missing = CATALOG.presets.find((p) => p.id === "missing-prices");
	check("the no-price filter is on for a person who sees prices", pt.state_from_layout(missing.layout, "items", { id: missing.id, prices: true }).view.noprice, true);
	check("and off for one who does not: there is no checkbox for it", pt.state_from_layout(missing.layout, "items", { id: missing.id, prices: false }).view.noprice, false);
	check("what is on screen writes back as the view's layout", pt.layout_of(state.view, state.layout), view.layout);
	check("the same layout in another order of keys is the same view", pt.same_layout({ filters: view.layout.filters, sort: view.layout.sort, columns: view.layout.columns }, view.layout, "items"), true);
});

test("a view says whether it was changed, and the no-price filter is not a change for somebody who cannot see prices", () => {
	const view = CATALOG.presets.find((p) => p.id === "money-items");
	const state = pt.state_from_layout(view.layout, "items", { id: view.id, prices: true });
	check("untouched", pt.view_changed(state.view, state.layout, view.layout, "items", true), false);
	state.layout.columns = pt.with_column(state.layout.columns, "unit", true, "items");
	check("a column added", pt.view_changed(state.view, state.layout, view.layout, "items", true), true);
	const again = pt.state_from_layout(view.layout, "items", { id: view.id, prices: true });
	again.view.status = "late";
	check("a toolbar filter", pt.view_changed(again.view, again.layout, view.layout, "items", true), true);
	const third = pt.state_from_layout(view.layout, "items", { id: view.id, prices: true });
	third.layout.rules.push({ column: "item", op: "contains", value: "" });
	check("a half-made filter is not a change", pt.view_changed(third.view, third.layout, view.layout, "items", true), false);
	const missing = CATALOG.presets.find((p) => p.id === "missing-prices");
	const blind = pt.state_from_layout(missing.layout, "items", { id: missing.id, prices: false });
	check("no price access: filter forced off, still not 'changed'", pt.view_changed(blind.view, blind.layout, missing.layout, "items", false), false);
});

test("columns: shown in the view's order, hidden when the person may not see them, never none", () => {
	const names = (keys, mode, prices) => pt.shown_columns(keys, mode, prices).columns.map((c) => c.key);
	check("the view's order, not the registry's", names(["status", "item", "received"], "items", true), ["status", "item", "received"]);
	const money = CATALOG.presets.find((p) => p.id === "money-items");
	check("a money view for somebody who may see prices", names(money.layout.columns, "items", true), money.layout.columns);
	const blind = pt.shown_columns(money.layout.columns, "items", false);
	check("without prices: the columns it can show", blind.columns.map((c) => c.key), ["item", "ordered", "received", "supplier", "status"]);
	check("and the ones it cannot, by name, in the view's order", blind.withheld, ["Unit price", "Spent", "Received value", "Lines with no price"]);
	check("an unknown or repeated key is skipped", names(["item", "gone", "item"], "documents", true), ["item"]);
	check("nothing usable shows the standard columns the person may see", names(["spent"], "items", false), ["item", "requested", "ordered", "received", "status", "where", "due", "supplier", "documents"]);
	check("a column that cannot be sorted says so", pt.shown_columns(["item", "doc_chain"], "documents", true).columns.map((c) => c.sortable), [true, false]);
	check("show a column: it goes last", pt.with_column(["item", "status"], "due", true, "items"), ["item", "status", "due"]);
	check("hide a column", pt.with_column(["item", "status"], "status", false, "items"), ["item"]);
	check("the last column stays", pt.with_column(["item"], "item", false, "items"), ["item"]);
	check("a column the grouping does not have is ignored", pt.with_column(["item"], "rate", true, "items"), ["item"]);
	check("move earlier", pt.move_column(["a", "b", "c"], "c", -1), ["a", "c", "b"]);
	check("move later", pt.move_column(["a", "b", "c"], "a", 1), ["b", "a", "c"]);
	check("the ends stay", [pt.move_column(["a", "b"], "a", -1), pt.move_column(["a", "b"], "b", 1), pt.move_column(["a", "b"], "z", 1)], [["a", "b"], ["a", "b"], ["a", "b"]]);
	check("hiding the sorted column drops the sort", pt.fix_sort({ key: "status", dir: "asc" }, ["item"], "items"), null);
	check("a sort on a shown column stays", pt.fix_sort({ key: "status", dir: "asc" }, ["item", "status"], "items"), { key: "status", dir: "asc" });
});

test("a header click cannot sort a column that has no single value, and a view's sort puts blanks last", () => {
	check("Documents is not sortable", pt.next_sort(null, "documents", pt.ITEM_COLUMNS), null);
	check("Doc Chain stays unsorted", pt.sort_rows(view_rows(), pt.LINE_COLUMNS, { key: "doc_chain", dir: "asc" }).map((r) => r.row_id), ["i1", "i2", "i3"]);
	const rows = view_rows();
	check("lines with no price: zero is a count, and a row sent no price keys is blank, last either way", [pt.sort_rows(rows, pt.ITEM_COLUMNS, { key: "no_price", dir: "asc" }).map((r) => r.item_code), pt.sort_rows(rows.concat([{ row_id: "i4", item_code: "DD-4" }]), pt.ITEM_COLUMNS, { key: "no_price", dir: "desc" }).map((r) => r.item_code)], [["AA-1", "CC-3", "BB-2"], ["BB-2", "AA-1", "CC-3", "DD-4"]]);
	check("an item that has received nothing at the order's rate is zero; one with no figure is blank", pt.sort_rows(rows, pt.ITEM_COLUMNS, { key: "received_value", dir: "desc" }).map((r) => r.item_code), ["AA-1", "CC-3", "BB-2"]);
	check("the new columns sort", pt.sort_rows(rows, pt.ITEM_COLUMNS, { key: "waiting", dir: "desc" }).map((r) => r.item_code), ["CC-3", "AA-1", "BB-2"]);
});

test("the filters in words, and what they hide", () => {
	const options = { states: [{ value: "late", label: "Late" }], locations: [{ value: "project_shelf", label: "Project Shelf" }], suppliers: ["North Supply"] };
	const d = (rule) => pt.describe_rule(rule, "items", options);
	check("contains", d({ column: "item", op: "contains", value: "pipe" }), 'Item contains "pipe"');
	check("any of, with the server's words", d({ column: "status", op: "any_of", values: ["late", "to_order"] }), "Status is Late or to_order");
	check("a place", d({ column: "where", op: "any_of", values: ["project_shelf"] }), "Where it is now is Project Shelf");
	check("both ends", d({ column: "received", op: "between", min: 1, max: 5 }), "Received is between 1 and 5");
	check("one end", [d({ column: "received", op: "between", min: 2, max: null }), d({ column: "received", op: "between", min: null, max: 2 })], ["Received is at least 2", "Received is at most 2"]);
	check("dates", d({ column: "due", op: "between", min: "2026-11-01", max: null }), "Due is on or after 2026-11-01");
	check("blank", [d({ column: "due", op: "blank" }), d({ column: "due", op: "not_blank" })], ["Due is blank", "Due is not blank"]);
	const state = Object.assign(pt.default_state(), { status: "late", action: true });
	check("the toolbar's and the columns' together", pt.describe_filters(state, options, [{ column: "received", op: "between", min: 1, max: null }], "items"), "Late · needs action · Received is at least 1");
	check("the bar says how many it hides", pt.filter_bar_text(state, [], "items", options, 3, 40, "items"), "Late · needs action (hiding 37 of 40 items)");
	check("or that it hides nothing", pt.filter_bar_text(state, [], "items", options, 40, 40, "lines"), "Late · needs action (hiding nothing)");
	check("no filter, no bar", pt.filter_bar_text(pt.default_state(), [], "items", options, 40, 40, "items"), "");
});

test("the address names a view; what the browser remembered fills in the rest", () => {
	const money = CATALOG.presets.find((p) => p.id === "money-items");
	const audit = CATALOG.presets.find((p) => p.id === "location-audit");
	const views = all_views();
	const memory = (mode, id, layout, other) => ({ v: 1, mode: mode, modes: Object.assign({ [mode]: { id: id, layout: layout } }, other || {}) });
	const start = (opts) => pt.resolve_start(Object.assign({ search: "", views: views, kept: null, memory: null, prices: true }, opts));
	check("nothing at all: the standard view by document", [start({}).view.view, start({}).layout.id, start({}).layout.columns], ["documents", "std-documents", pt.STANDARD_COLUMNS.documents]);
	const remembered = start({ memory: memory("items", "money-items", money.layout) });
	check("a plain address opens the last layout used", [remembered.view.view, remembered.layout.id, remembered.layout.columns], ["items", "money-items", money.layout.columns]);
	const named = start({ search: "?pt_view=items&pt_layout=location-audit", memory: memory("items", "money-items", money.layout) });
	check("an address that names a view wins over the memory", [named.layout.id, named.layout.columns], ["location-audit", audit.layout.columns]);
	const flipped = start({ search: "?pt_layout=money-items", memory: memory("documents", "std-documents", pt.standard_layout("documents")) });
	check("the view names its own grouping", flipped.view.view, "items");
	// The address says WHICH view; the changes made on top of that view come back from the memory.
	const edited = Object.assign({}, pt.standard_layout("items"), { columns: ["item", "status"] });
	const reload = start({ search: "?pt_view=items", memory: memory("items", "std-items", edited) });
	check("a reload of the same view keeps the changes on top of it", reload.layout.columns, ["item", "status"]);
	const other = start({ search: "?pt_layout=location-audit", memory: memory("items", "std-items", edited) });
	check("a link to another view does not take them", other.layout.columns, audit.layout.columns);
	const kept = start({ search: "?pt_view=items", kept: { mode: "items", id: "std-items", layout: Object.assign({}, edited, { columns: ["item"] }) }, memory: memory("items", "std-items", edited) });
	check("what the form kept across a refresh is newer than the memory", kept.layout.columns, ["item"]);
	const unknown = start({ search: "?pt_layout=PTV-99999&pt_view=items" });
	check("a view that is not the person's: the standard view, and it says so", [unknown.layout.id, unknown.notes.length], ["std-items", 1]);
	const mineList = all_views([mine_view("PTV-00001", "items", { columns: ["item", "unit"], sort: null, filters: { location: "", status: "", supplier: "", action: true, noprice: false, rules: [] } })]);
	const own = pt.resolve_start({ search: "?pt_layout=PTV-00001", views: mineList, prices: true });
	check("a view of the person's own", [own.layout.id, own.layout.columns, own.view.action], ["PTV-00001", ["item", "unit"], true]);
	check("its filters can be cleared and stay cleared on reload", pt.resolve_start({ search: "?pt_layout=PTV-00001&pt_action=0", views: mineList, prices: true }).view.action, false);
	check("and an address's filter is the address's", pt.resolve_start({ search: "?pt_status=late", views: views, prices: true }).view.status, "late");
	check("the no-price filter from a link, for somebody who sees no prices, is off", pt.resolve_start({ search: "?pt_view=items&pt_noprice=1", views: views, prices: false }).view.noprice, false);
	const deleted = pt.resolve_start({ search: "", views: views, memory: memory("items", "PTV-00007", edited), prices: true });
	check("a remembered view that has been deleted goes with its layout: the standard view, and it says so", [deleted.layout.id, deleted.layout.columns, deleted.notes.length], ["std-items", pt.STANDARD_COLUMNS.items, 1]);
	check("the search comes from the address", pt.resolve_start({ search: "?pt_q=pipe", views: views, prices: true }).view.q, "pipe");
});

test("the address says which view and which filters are off, and writes nothing for the standard view", () => {
	const base = pt.standard_layout("items").filters;
	const money = CATALOG.presets.find((p) => p.id === "money-items");
	const chase = CATALOG.presets.find((p) => p.id === "to-chase");
	check("the standard view is a plain address", pt.write_view_query("", pt.default_state(), "std-documents", base), "");
	check("by item is just the grouping", pt.write_view_query("", Object.assign(pt.default_state(), { view: "items" }), "std-items", base), "?pt_view=items");
	check("another view is named", pt.write_view_query("", Object.assign(pt.default_state(), { view: "items" }), "money-items", money.layout.filters), "?pt_view=items&pt_layout=money-items");
	const cleared = Object.assign(pt.default_state(), { view: "items", action: false });
	check("a filter the view has on, turned off, is written as off", pt.write_view_query("", cleared, "to-chase", chase.layout.filters), "?pt_view=items&pt_action=0&pt_layout=to-chase");
	const on = Object.assign(pt.default_state(), { view: "items", action: true, status: "late" });
	check("filters that are on are written as always", pt.write_view_query("", on, "to-chase", chase.layout.filters), "?pt_view=items&pt_status=late&pt_action=1&pt_layout=to-chase");
	check("other people's keys stay", pt.write_view_query("?a=1", pt.default_state(), "std-documents", base), "?a=1");
	check("going back to the standard view takes the name out", pt.write_view_query("?pt_layout=money-items&pt_view=items", Object.assign(pt.default_state(), { view: "items" }), "std-items", base), "?pt_view=items");
	check("the view's id is the widget's key, handed back to frappe", pt.without_own_keys({ pt_layout: "x", scroll_to: "y" }), { scroll_to: "y" });
	check("only the widget's keys name a view", [pt.address_names_view("?a=1"), pt.address_names_view(""), pt.address_names_view("?pt_layout=x"), pt.address_names_view("?pt_q=a")], [false, false, true, true]);
	// A write, and a read of what was written, give the same view: Back and Forward land where they were.
	const round = pt.resolve_start({ search: pt.write_view_query("", cleared, "to-chase", chase.layout.filters), views: all_views(), prices: true });
	check("the round trip", [round.layout.id, round.view.action, round.view.view], ["to-chase", false, "items"]);
});

test("the browser's memory is per person, per grouping, and never trusted", () => {
	check("one key per person", [pt.storage_key("a@x.test") !== pt.storage_key("b@x.test"), pt.storage_key("a@x.test").includes("a@x.test")], [true, true]);
	const money = CATALOG.presets.find((p) => p.id === "money-items");
	const state = pt.state_from_layout(money.layout, "items", { id: "money-items", prices: true });
	const first = pt.remember(null, state.view, state.layout);
	check("what is stored", [first.v, first.mode, first.modes.items.id, first.modes.items.layout], [1, "items", "money-items", money.layout]);
	const doc_state = pt.state_from_layout(pt.standard_layout("documents"), "documents", { id: "std-documents", prices: true });
	const both = pt.remember(first, doc_state.view, doc_state.layout);
	check("going to By document keeps By item as it was", [both.mode, both.modes.items.id, both.modes.documents.id], ["documents", "money-items", "std-documents"]);
	const back = pt.parse_remembered(pt.remembered_text(both));
	check("it reads back", [back.mode, back.modes.items.layout, back.modes.documents.id], ["documents", money.layout, "std-documents"]);
	check("the search is not part of it", JSON.stringify(first).includes("pipe"), false);
	check("unreadable text is nothing", [pt.parse_remembered(null), pt.parse_remembered(""), pt.parse_remembered("{"), pt.parse_remembered("[]"), pt.parse_remembered('{"v":2}'), pt.parse_remembered("x".repeat(100000))], [null, null, null, null, null, null]);
	const stale = pt.parse_remembered(JSON.stringify({ v: 1, mode: "items", modes: { items: { id: "std-items", layout: { columns: ["item", "retired"], sort: { key: "retired", dir: "asc" } } }, documents: "junk" } }));
	check("a layout in it from an older release is mended, a damaged entry dropped", [stale.modes.items.layout.columns, stale.modes.items.layout.sort, "documents" in stale.modes], [["item"], null, false]);
	check("a grouping that is not stored is not invented", pt.parse_remembered(JSON.stringify({ v: 1, mode: "documents", modes: { items: { id: "std-items", layout: pt.standard_layout("items") } } })).mode, "items");
});

test("the views a person can pick: built-in ones first, their own by name, and the standard two always", () => {
	const mine = [mine_view("PTV-2", "items", pt.standard_layout("items"), { name: "zebra" }), mine_view("PTV-1", "documents", pt.standard_layout("documents"), { name: "Apple" })];
	const merged = all_views(mine);
	check("every preset is marked built-in and every saved one is not", [merged.filter((v) => v.preset).length, merged.filter((v) => !v.preset).length], [CATALOG.presets.length, 2]);
	const listed = pt.views_for(merged);
	check("their own, by name", listed.mine.map((v) => v.name), ["Apple", "zebra"]);
	check("the built-in ones keep their order", listed.presets.map((v) => v.id), CATALOG.presets.map((p) => p.id));
	check("without the server's list the tracker still has the two standard views", pt.fallback_views().map((v) => v.id), ["std-documents", "std-items"]);
	check("a catalog that lost them is topped up", pt.merge_views({ presets: [] }, []).map((v) => v.id), ["std-documents", "std-items"]);
	check("a new column filter starts empty", [pt.new_rule("received", "items"), pt.new_rule("status", "items"), pt.new_rule("item", "items"), pt.new_rule("nope", "items")], [{ column: "received", op: "between", min: null, max: null }, { column: "status", op: "any_of", values: [] }, { column: "item", op: "contains", value: "" }, null]);
	check("and a change of test keeps what still fits", pt.rule_with_op({ column: "status", op: "any_of", values: ["late"] }, "contains"), { column: "status", op: "contains", value: "" });
});

test("the form script keeps views off the form, off Redis, in try/catch storage, and out of the history", () => {
	const code = SCRIPT.replace(/\/\/[^\n]*/g, "").replace(/\/\*[\s\S]*?\*\//g, "");
	const mount = code.slice(code.indexOf("function mountProcurementTracker"), code.indexOf('frappe.ui.form.on("Project"'));
	// Opening or changing a view must not mark the Project form as changed.
	check("the widget never writes the form", /frm\.(set_value|dirty|save|doc\.\w+\s*=[^=])|frappe\.model\.set_value/.test(mount), false);
	check("nor does it touch the form's field", /frm\.set_df_property|frm\.refresh_field|frm\.toggle/.test(mount), false);
	check("not Frappe's user settings, which live in Redis", /user_settings|frappe\.model\.user_settings/.test(code), false);
	const uses = [...code.matchAll(/localStorage/g)].map((m) => m.index);
	check("storage is touched", uses.length > 0, true);
	check("every touch of it is inside a try", uses.every((i) => { const t = code.lastIndexOf("try {", i); return t >= 0 && i - t < 200 && !code.slice(t, i).includes("catch"); }), true);
	check("the memory is per person", code.includes("pt.storage_key(frappe.session.user)"), true);
	check("the layout is chosen by the same function the tests run", code.includes("pt.resolve_start({"), true);
	check("the view in the address is written without a history entry", code.includes("pt.write_view_query(window.location.search"), true);
	check("the views are fetched beside the feed, and the tracker draws without them", code.includes("procurement_views.get_views") && code.includes("error: () => settle(null)"), true);
	check("a pending memory write is made, not dropped, when the app goes", /beforeUnmount\(\) \{[\s\S]*?if \(memory_timer\) flushMemory\(\);/.test(code), true);
	check("the columns are the view's, by the shown_columns rule", code.includes("pt.shown_columns(this.layout.columns, this.mode, this.hasPrices)"), true);
	check("the price filter and columns are behind the server's say-so", code.includes("prices: !!prices,") && code.includes("pt.usable_rules(this.layout.rules, this.mode, this.hasPrices)"), true);
	check("the filter bar and the one control that clears", code.includes('class="pt-filterbar"') && code.includes('@click="clearFilters">Clear filters</button>'), true);
	check("a view's name is escaped where frappe shows HTML", code.includes("[esc(v.name)]"), true);
	check("a built-in view cannot be saved over", code.includes("return this.viewsOk && !this.currentView.preset;"), true);
	// Every endpoint the widget calls is one the server has, by name.
	for (const name of ["save_view", "rename_view", "delete_view"]) {
		check("endpoint " + name, code.includes(`"${name}"`) && new RegExp(`def ${name}\\(`).test(VIEWS_PY), true);
	}
	check("the writes are POSTs, on the server and from here", /whitelist\(methods=\["POST"\]\)\s*\ndef save_view/.test(VIEWS_PY) && code.includes('type: "POST"'), true);
});

// ------------------------------------------------------------------ review fixes (PR 1452)

const memory_of = (mode, id, layout, other) => ({ v: 1, mode: mode, modes: Object.assign({ [mode]: { id: id, layout: layout } }, other || {}) });
const noisy_layout = () => ({
	columns: ["item", "status"],
	sort: null,
	filters: { location: "project_shelf", status: "ordered", supplier: "North Supply", action: true, noprice: false, rules: [{ column: "received", op: "between", min: 5, max: null }] },
});

test("M1: a link shows its sender's rows: every filter comes from the address, never from the recipient's memory", () => {
	const views = all_views();
	const memory = memory_of("items", "std-items", noisy_layout());
	const old_link = pt.resolve_start({ search: "?pt_view=items&pt_status=late", views: views, memory: memory, prices: true });
	check("the link's own filter", old_link.view.status, "late");
	check("none of the recipient's toolbar filters", [old_link.view.location, old_link.view.supplier, old_link.view.action], ["", "", false]);
	check("and no column filter of theirs", old_link.layout.rules, []);
	check("their columns are only a look, and stay", old_link.layout.columns, ["item", "status"]);
	const bare = pt.resolve_start({ search: "?pt_view=items", views: views, memory: memory, prices: true });
	check("a bare link opens unfiltered", [bare.view.supplier, bare.view.action, bare.layout.rules], ["", false, []]);
	const link_to_money = pt.resolve_start({ search: "?pt_layout=money-items&pt_view=items", views: views, memory: memory_of("items", "money-items", noisy_layout()), prices: true });
	check("a view's link: the view's own filters (none), not the memory's", [link_to_money.view.supplier, link_to_money.view.action, link_to_money.layout.rules], ["", false, []]);
	// A reload of one's own page is an address too: what is filtering is in it.
	const filtering = Object.assign(pt.default_state(), { view: "items", supplier: "North Supply", action: true });
	const rules = noisy_layout().filters.rules;
	const std = pt.standard_layout("items").filters;
	const written = pt.write_view_query("", filtering, "std-items", std, rules);
	const reload = pt.resolve_start({ search: written, views: views, memory: memory, prices: true });
	check("the address carries the toolbar filters and the column filters", [reload.view.supplier, reload.view.action, reload.layout.rules], ["North Supply", true, rules]);
	check("a column filter is written only where it differs from the view's own", [pt.write_view_query("", pt.default_state(), "std-items", std, []).includes("pt_rules"), written.includes("pt_rules")], [false, true]);
	const chase = CATALOG.presets.find((p) => p.id === "to-chase");
	const gaps = CATALOG.presets.find((p) => p.id === "location-gaps");
	const cleared_rules = pt.write_view_query("", Object.assign(pt.default_state(), { view: "items" }), "location-gaps", gaps.layout.filters, []);
	check("a view's own column filter, removed, is written as removed", pt.resolve_start({ search: cleared_rules, views: views, prices: true }).layout.rules, []);
	check("and left alone, the view's own comes back", pt.resolve_start({ search: "?pt_layout=location-gaps&pt_view=items", views: views, prices: true }).layout.rules, gaps.layout.filters.rules);
	check("an address that cannot be read keeps the view's own", pt.resolve_start({ search: "?pt_view=items&pt_layout=location-gaps&pt_rules=%7Bnot", views: views, prices: true }).layout.rules, gaps.layout.filters.rules);
	const huge = new Array(12).fill({ column: "item", op: "contains", value: "x".repeat(120) });
	check("too long for an address: left out, not cut in half", pt.write_view_query("", Object.assign(pt.default_state(), { view: "items" }), "std-items", std, huge).includes("pt_rules"), false);
	check("the rules key is the widget's, handed back to frappe", pt.without_own_keys({ pt_rules: "[]", a: 1 }), { a: 1 });
	check("a person without prices never gets the price filter from a link", pt.resolve_start({ search: "?pt_view=items&pt_noprice=1&pt_rules=" + encodeURIComponent(JSON.stringify([{ column: "spent", op: "between", min: 1 }])), views: views, prices: false }).view.noprice, false);
	check("no address: the memory is the whole answer, as before", pt.resolve_start({ search: "", views: views, memory: memory, prices: true }).view.supplier, "North Supply");
	check("the shipped to-chase link keeps its filter off when turned off", pt.resolve_start({ search: pt.write_view_query("", Object.assign(pt.default_state(), { view: "items" }), "to-chase", chase.layout.filters, chase.layout.filters.rules), views: views, prices: true }).view.action, false);
});

test("M2: switching grouping carries every toolbar filter, an unticked one stays unticked", () => {
	const views = all_views();
	const memory = memory_of("items", "std-items", noisy_layout(), { documents: { id: "std-documents", layout: pt.standard_layout("documents") } });
	const now = Object.assign(pt.default_state(), { view: "items", action: false, supplier: "", q: "pipe" });
	const to_docs = pt.switch_grouping({ views: views, memory: memory, view: now, name: "documents", prices: true });
	check("the grouping", to_docs.view.view, "documents");
	const back = pt.switch_grouping({ views: views, memory: memory, view: Object.assign({}, to_docs.view, { action: false }), name: "items", prices: true });
	check("By item again: the filters that were turned off are off, though By item's memory had them on", [back.view.action, back.view.supplier, back.view.status, back.view.location], [false, "", "", ""]);
	check("the layout is the grouping's own memory (columns), the search comes along", [back.layout.columns, back.view.q], [["item", "status"], "pipe"]);
	const on = pt.switch_grouping({ views: views, memory: null, view: Object.assign(pt.default_state(), { view: "documents", action: true, status: "late" }), name: "items", prices: true });
	check("filters that are on come across", [on.view.action, on.view.status], [true, "late"]);
	check("a grouping with nothing remembered opens its standard view", [on.layout.id, on.layout.columns], ["std-items", pt.STANDARD_COLUMNS.items]);
	check("without prices the no-price filter stays off", pt.switch_grouping({ views: views, view: Object.assign(pt.default_state(), { noprice: true }), name: "items", prices: false }).view.noprice, false);
	check("a view that could not be looked up (views not loaded) keeps its id", pt.switch_grouping({ views: pt.fallback_views(), views_ok: false, memory: memory_of("items", "PTV-1", noisy_layout()), view: pt.default_state(), name: "items", prices: true }).layout.id, "PTV-1");
	check("the widget uses it", SCRIPT.includes("pt.switch_grouping({"), true);
});

test("M3: the printed list says what filters its rows, columns included", () => {
	const options = { states: [], locations: [], suppliers: [] };
	const rules = [{ column: "received", op: "between", min: 2, max: null }];
	const words = pt.describe_filters(Object.assign(pt.default_state(), { view: "items" }), options, rules, "items");
	check("the words", words, "Received is at least 2");
	const page = pt.print_html(view_rows(), { title: "T", filters: words, prices: false }, (v) => String(v));
	check("the sheet's heading carries them", page.includes("Showing: Received is at least 2"), true);
	const code = SCRIPT.replace(/\/\/[^\n]*/g, "");
	check("the form script hands the column filters to the heading", code.includes("filters: pt.describe_filters(this.view, this.options, this.filterCtx.rules, this.mode),"), true);
});

test("M4: the tracker never waits on the saved views, and a save that never answers does not stay busy", () => {
	const code = SCRIPT.replace(/\/\/[^\n]*/g, "").replace(/\/\*[\s\S]*?\*\//g, "");
	check("a time limit", code.includes("const SAVED_VIEWS_WAIT_MS = 4000;") && code.includes("setTimeout(() => settle(null), SAVED_VIEWS_WAIT_MS)"), true);
	check("it settles on the error, and on the promise frappe.call returns", code.includes("error: () => settle(null)") && code.includes("call.then(null, () => settle(null))"), true);
	check("a throw settles it too", /\} catch \(e\) \{\s*settle\(null\);/.test(code), true);
	check("it settles once", code.includes("if (settled) return;"), true);
	check("the feed is not held up by anything else", code.includes("Promise.all([logic, feed, saved])"), true);
	check("busy lets go after a while, and on any answer", code.includes("}, 30000);") && code.includes("error: finish,"), true);
	check("the server says when the person's own views could not be read", code.includes("saved.unavailable"), true);
	// Without the views the tracker uses the standard ones and offers no saving.
	check("fallback views", pt.fallback_views().length, 2);
});

test("L1: a saved view that is gone falls back to Standard, and one that could not be looked up keeps its place", () => {
	const views = all_views();
	const edited = noisy_layout();
	const gone = pt.resolve_start({ search: "", views: views, memory: memory_of("items", "PTV-9", edited), prices: true });
	check("gone: the standard view, its layout, a note", [gone.layout.id, gone.layout.columns, gone.notes.length], ["std-items", pt.STANDARD_COLUMNS.items, 1]);
	const down = pt.resolve_start({ search: "", views: pt.fallback_views(), views_ok: false, memory: memory_of("items", "PTV-9", edited), prices: true });
	check("views not loaded: the id and the layout are kept, so the memory is not overwritten", [down.layout.id, down.layout.columns], ["PTV-9", ["item", "status"]]);
	const link = pt.resolve_start({ search: "?pt_view=items&pt_layout=PTV-9", views: pt.fallback_views(), views_ok: false, prices: true });
	check("a link to a view while views are not loaded keeps the id (the address is not rewritten) and says why", [link.layout.id, link.notes[0].includes("could not be loaded")], ["PTV-9", true]);
	const stranger = pt.resolve_start({ search: "?pt_view=items&pt_layout=PTV-9", views: views, prices: true });
	check("with the views loaded it is somebody else's or gone: Standard, and a note", [stranger.layout.id, stranger.notes.length], ["std-items", 1]);
	check("the widget says the view is not loaded rather than naming Standard", SCRIPT.includes('"Saved view (not loaded)"'), true);
});

test("L2: a column named after something every object has is not a column", () => {
	const out = pt.clean_layout({ columns: ["constructor", "__proto__", "toString", "hasOwnProperty", "item"], sort: { key: "constructor", dir: "asc" } }, "items");
	check("only the real one", out.layout.columns, ["item"]);
	check("and it says it was mended", [out.repaired, out.layout.sort], [true, null]);
	check("shown_columns skips them", pt.shown_columns(["constructor", "item", "__proto__"], "documents", true).columns.map((c) => c.key), ["item"]);
	check("a filter on one is no filter", [pt.clean_rule({ column: "constructor", op: "blank" }, "items"), pt.clean_rule({ column: "__proto__", op: "blank" }, "items")], [null, null]);
	check("hide, show and sort ignore them", [pt.with_column(["item"], "constructor", true, "items"), pt.fix_sort({ key: "constructor", dir: "asc" }, ["constructor"], "items"), pt.next_sort(null, "toString", pt.ITEM_COLUMNS)], [["item"], null, null]);
	// A value from storage that is not text never reaches String().
	const evil = { column: "item", op: "contains", value: { toString: null } };
	check("an object where text should be is no filter, and does not throw", pt.clean_rule(evil, "items"), null);
	check("a layout holding it still draws", pt.clean_layout({ columns: ["item"], filters: { status: { toString: null }, rules: [evil] } }, "items").layout.columns, ["item"]);
	check("memory holding it is mended, not thrown", pt.parse_remembered(JSON.stringify({ v: 1, mode: "items", modes: { items: { id: "std-items", layout: { columns: ["item"], filters: { rules: [evil] } } } } })).modes.items.layout.filters.rules, []);
	check("the registries have no prototype", [Object.getPrototypeOf(pt.LINE_COLUMNS), Object.getPrototypeOf(pt.ITEM_COLUMNS)], [null, null]);
});

test("a view that filters on 'no price' says so to a person who is shown no prices", () => {
	const missing = CATALOG.presets.find((p) => p.id === "missing-prices");
	check("for them, a sentence", pt.noprice_note(missing.layout.filters, false), "This view lists the items with no price, which needs permission to read Purchase Orders, so it lists every item.");
	check("for a person who sees prices, nothing", pt.noprice_note(missing.layout.filters, true), "");
	check("a view without that filter, nothing", pt.noprice_note(pt.standard_layout("items").filters, false), "");
});

test("L5 and L6: no claim that nobody else can see a view, and a column move does not reopen closed documents", () => {
	const code = SCRIPT.replace(/\/\/[^\n]*/g, "").replace(/\/\*[\s\S]*?\*\//g, "");
	check("the wording", code.includes("Only you can see your views"), false);
	check("private to you, except administrators", code.includes("private to you; only an administrator can see them in the system"), true);
	const layout_watch = code.slice(code.indexOf("layout: {\n\t\t\t\tdeep: true"), code.indexOf('"layout.rules"'));
	check("the layout watcher does not open folders", layout_watch.includes("expandMatches"), false);
	check("the column filters' watcher does", /"layout\.rules": \{[\s\S]*?expandMatches\(\)/.test(code), true);
});

test("round 2: a link that cannot carry the column filters says so, a late answer is ignored, the total has a label", () => {
	const code = SCRIPT.replace(/\/\/[^\n]*/g, "").replace(/\/\*[\s\S]*?\*\//g, "");
	const huge = new Array(12).fill({ column: "item", op: "contains", value: "x".repeat(120) });
	const small = [{ column: "received", op: "between", min: 1, max: null }];
	check("short filters fit", pt.rules_fit_address(small, [], "items"), true);
	check("filters the view already has always fit: nothing is written", pt.rules_fit_address(huge, huge, "items"), true);
	check("too long and different from the view's own: they do not", pt.rules_fit_address(huge, [], "items"), false);
	check("what is said and where", [pt.LINK_RULES_NOTE.includes("will not carry them"), code.includes('<small v-if="linkNote"'), code.includes("pt.rules_fit_address(this.layout.rules, this.currentView.layout.filters.rules, this.mode)")], [true, true, true]);
	check("it agrees with what the address writes", [pt.write_view_query("", Object.assign(pt.default_state(), { view: "items" }), "std-items", pt.standard_layout("items").filters, huge).includes("pt_rules"), pt.rules_fit_address(huge, [], "items")], [false, false]);
	check("a late answer to a save, rename or delete is dropped", code.includes("let late = false;") && code.includes("late = true;") && code.includes("if (!late && r && r.message) done(r.message);"), true);
	check("the built-in views come with the catalog even when the person's own could not be read", code.includes("saved && saved.catalog ? pt.merge_views(saved.catalog, saved.mine || []) : pt.fallback_views()"), true);
	check("the unavailable answer still disables saving", code.includes("!saved.unavailable"), true);
	check("the total is labelled when Spent is the first column", code.includes('<small v-if="spentIndex === 0" class="pt-where-note">Total spent on these items'), true);
});

// ------------------------------------------------------------------ the form script against an older bundle (v1.816.1)

// The form script (doctype_js) and the logic bundle reach a Desk tab by different roads, so after a deploy a
// tab can hold the new script and the old logic. The script's own check, lifted out of the file and run.
const BUNDLE = fs.readFileSync(path.join(APP, "public/js/procurement_tracker.bundle.js"), "utf8");
const levelGuard = (() => {
	const a = SCRIPT.indexOf("const REQUIRED_LOGIC_LEVEL");
	const b = SCRIPT.indexOf("\n}\n", SCRIPT.indexOf("function procurementLogicProblem")) + 3;
	if (a < 0 || b < a) throw new Error("the level check is not in project_enhancements.js");
	return new Function(SCRIPT.slice(a, b) + "\nreturn { REQUIRED_LOGIC_LEVEL, procurementLogicProblem };")();
})();

test("the level the form script needs is the level the bundle installs", () => {
	const bundled = Number((BUNDLE.match(/^const VERSION = (\d+);$/m) || [])[1]);
	check("the bundle declares a VERSION", Number.isInteger(bundled), true);
	check("REQUIRED_LOGIC_LEVEL equals the bundle's VERSION", levelGuard.REQUIRED_LOGIC_LEVEL, bundled);
	check("a bundle at the current level replaces an older copy in the same window", BUNDLE.includes("window.sf_procurement_tracker.version >= VERSION"), true);
	const installed = Object.assign({ version: bundled }, pt);
	check("the logic as the bundle installs it passes the form script's check", levelGuard.procurementLogicProblem(installed), "");
});

test("a too-old or missing logic is told apart, and nothing is thrown", () => {
	const need = levelGuard.REQUIRED_LOGIC_LEVEL;
	const guard = levelGuard.procurementLogicProblem;
	const v1 = Object.assign({}, pt, { version: need - 1 });
	delete v1.merge_views;
	delete v1.fallback_views;
	check("the old bundle (level below, no merge_views) is old", guard(v1), "old");
	check("an old level is old even when the functions happen to be there", guard(Object.assign({}, pt, { version: need - 1 })), "old");
	check("no level at all is old", guard(Object.assign({}, pt, { version: undefined })), "old");
	check("the right level without merge_views is old", guard(Object.assign({}, pt, { version: need, merge_views: undefined })), "old");
	check("the right level without fallback_views is old", guard(Object.assign({}, pt, { version: need, fallback_views: "x" })), "old");
	check("nothing installed is missing", guard(undefined), "missing");
	check("null is missing", guard(null), "missing");
	check("a string is missing", guard("nope"), "missing");
	check("a newer logic than the script needs is fine", guard(Object.assign({}, pt, { version: need + 1 })), "");
});

test("an old logic draws a reload note and no dialog; the rest of the form is left alone", () => {
	const at = SCRIPT.indexOf("const problem = procurementLogicProblem(pt);");
	check("the check runs before anything is mounted", at > 0 && at < SCRIPT.indexOf("mountProcurementTracker(frm, mount, pt, data"), true);
	check("an old logic calls outdated() and returns", /if \(problem\) \{\s*outdated\(\);\s*return;\s*\}/.test(SCRIPT), true);
	check("the note says what happened in plain words", SCRIPT.includes('__("The Procurement Tracker was updated. Reload this page to use it.")'), true);
	check("its button reloads the page", SCRIPT.includes('.on("click", () => window.location.reload())'), true);
	const tail = SCRIPT.slice(SCRIPT.indexOf("Promise.all([logic, feed, saved])"));
	const catchAt = tail.indexOf("} catch (e) {");
	const mountCatch = tail.slice(catchAt, tail.indexOf("},", catchAt));
	check("a failure inside the mount keeps its console line and its red text", mountCatch.includes("console.error(e);") && mountCatch.includes('failed("Error: " + e.message)'), true);
	check("and raises no modal", mountCatch.includes("msgprint"), false);
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
