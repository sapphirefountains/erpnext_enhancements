#!/usr/bin/env node
/**
 * The directory's Account / Supplier column, executed rather than grepped (v1.746.0,
 * TASK-2026-02942).
 *
 * Until v1.746.0 the contact and address tables in unified_tab_controller.js each had a
 * "Linked To" column listing every party a record is linked to, by ID, so someone on five
 * jobs showed five Projects and Opportunities beside the one company they work for. Nik
 * asked for "this contact belongs to this customer, or this contact belongs to this
 * supplier", and then for the address table to match. Both tables now draw the column with
 * `accounts_cell`, from the `accounts` that `sync_contact.get_contacts_for_context` and
 * `get_addresses_for_context` put on each row (the server half is `AccountColumnTest` in
 * tests/test_contact_endpoint_permissions.py). This runs the real method against the v16
 * helpers it calls, on a Project form and on the Customer and Supplier forms, where the open
 * party is kept quiet. Every name here is invented.
 *
 * Run: node scripts/test_directory_account_column.mjs
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CONTROLLER_JS = path.join(
	HERE,
	"..",
	"erpnext_enhancements",
	"public",
	"js",
	"global_enhancements",
	"unified_tab_controller.js"
);

let failures = 0;
let checks = 0;

function test(name, fn) {
	checks += 1;
	try {
		fn();
		console.log("  ok    " + name);
	} catch (err) {
		failures += 1;
		console.error("  FAIL  " + name);
		console.error("        " + (err && err.message ? err.message : err));
	}
}

// ------------------------------------------------------------------ desk stubs

// frappe.utils.escape_html, frappe.router.slug and frappe.urllib.get_full_url as frappe v16.53.0
// has them (utils.js, router.js, urllib.js). escape_html is copied, not approximated: the test
// is about what reaches the page.
const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#x60;", "=": "&#x3D;" };
globalThis.erpnext_enhancements = {};
globalThis.__ = (text, args) =>
	(args || []).reduce((out, value, i) => out.replace(`{${i}}`, value), text);
globalThis.frappe = {
	provide: () => {},
	ui: { form: { on: () => {} } },
	utils: {
		escape_html: (txt) => (txt == null ? "" : String(txt).replace(/[&<>"'`=]/g, (c) => ESCAPES[c] || c)),
	},
	router: { slug: (name) => name.toLowerCase().replace(/ /g, "-") },
	urllib: { get_full_url: (url) => "https://erp.example.com" + url },
};

vm.runInThisContext(fs.readFileSync(CONTROLLER_JS, "utf8"), { filename: CONTROLLER_JS });

const controller = globalThis.erpnext_enhancements.unified_controller;
assert.ok(controller, "the controller did not register itself");
assert.equal(typeof controller.accounts_cell, "function");

/** The cell for one row, drawn on a form of this doctype and name (a Project unless said). */
function cell(record, doctype = "Project", name = "PROJ-0001") {
	controller.frm = { doctype, doc: { name } };
	return controller.accounts_cell(record);
}
const account = (doctype, name, title) => ({ doctype, name, title });

/** The cell's linked entries: what a person sees and where each one goes. */
function entries(html) {
	return [...html.matchAll(/<div><a href="([^"]*)" target="_blank"(?: title="([^"]*)")?>(.*?)<\/a>(.*?)<\/div>/g)].map(
		(m) => ({ href: m[1], hover: m[2] || null, text: m[3], tag: m[4] })
	);
}

/** The cell's quiet entries (the open party on its own form). */
function quiet(html) {
	return [...html.matchAll(/<div class="text-muted">(.*?)<\/div>/g)].map((m) => m[1]);
}

// -------------------------------------------------------------------- the cases

test("a row on no Customer or Supplier shows a muted None", () => {
	assert.equal(cell({ name: "C-1", accounts: [] }), '<span class="text-muted">None</span>');
	assert.equal(cell({ name: "C-1" }), '<span class="text-muted">None</span>', "a row with no accounts key");
});

test("a Customer shows its display name, links to the record, and keeps the ID on hover", () => {
	const [only] = entries(cell({ accounts: [account("Customer", "CUST-0042", "Bluebird Water Gardens")] }));
	assert.deepEqual(only, {
		href: "https://erp.example.com/desk/customer/CUST-0042",
		hover: "CUST-0042",
		text: "Bluebird Water Gardens",
		tag: "",
	});
});

test("a name that is its own title carries no hover", () => {
	const [only] = entries(cell({ accounts: [account("Customer", "Northwind Fountains", "Northwind Fountains")] }));
	assert.equal(only.hover, null);
	assert.equal(only.text, "Northwind Fountains");
});

test("a missing title falls back to the name", () => {
	const [only] = entries(cell({ accounts: [account("Customer", "Northwind Fountains", "")] }));
	assert.equal(only.text, "Northwind Fountains");
	assert.equal(only.hover, null);
});

test("a Supplier carries the Supplier tag and a Customer does not", () => {
	const shown = entries(
		cell({
			accounts: [
				account("Customer", "Harbor Plaza", "Harbor Plaza"),
				account("Supplier", "Harbor Plaza", "Harbor Plaza"),
			],
		})
	);
	assert.equal(shown.length, 2);
	assert.equal(shown[0].tag, "", "a Customer was tagged");
	assert.match(shown[1].tag, /^<span class="badge badge-light"[^>]*>Supplier<\/span>$/);
	assert.equal(shown[1].href, "https://erp.example.com/desk/supplier/Harbor%20Plaza");
});

test("every account is shown, one per line, in the order the server sent", () => {
	const shown = entries(
		cell({
			accounts: [
				account("Customer", "CUST-0042", "Bluebird Water Gardens"),
				account("Customer", "Northwind Fountains", "Northwind Fountains"),
				account("Supplier", "SUP-0007", "Acme Pump Supply"),
			],
		})
	);
	assert.deepEqual(
		shown.map((e) => e.text),
		["Bluebird Water Gardens", "Northwind Fountains", "Acme Pump Supply"]
	);
});

test("names are escaped, in the text, the hover and the URL", () => {
	const html = cell({ accounts: [account("Supplier", 'X"><img src=x onerror=alert(1)>', "Smith & <b>Sons</b>")] });
	assert.ok(!html.includes("<img"), "a name became markup");
	assert.ok(!html.includes("<b>"), "a title became markup");
	const [only] = entries(html);
	assert.equal(only.text, "Smith &amp; &lt;b&gt;Sons&lt;/b&gt;");
	assert.equal(only.hover, "X&quot;&gt;&lt;img src&#x3D;x onerror&#x3D;alert(1)&gt;");
	assert.ok(!only.href.includes('"') && !only.href.includes("<"), "the URL broke out of its attribute");
});

// The open party on its own form: kept, quiet, first and unlinked.

test("on a Customer form, that Customer reads a quiet, unlinked This account", () => {
	const html = cell(
		{ accounts: [account("Customer", "CUST-0042", "Bluebird Water Gardens")] },
		"Customer",
		"CUST-0042"
	);
	assert.deepEqual(quiet(html), ["This account"]);
	assert.deepEqual(entries(html), [], "the open record was linked to itself");
	assert.ok(!html.includes("None"), "a row of this account's own read None");
});

test("on a Customer form, other companies keep their links, after This account", () => {
	const html = cell(
		{
			accounts: [
				account("Customer", "CUST-0042", "Bluebird Water Gardens"),
				account("Customer", "CUST-0099", "Zephyr Plaza Owners"),
				account("Supplier", "SUP-0007", "Acme Pump Supply"),
			],
		},
		"Customer",
		"CUST-0099"
	);
	assert.ok(html.indexOf("This account") < html.indexOf("Bluebird"), "the open party is not first");
	assert.deepEqual(quiet(html), ["This account"]);
	assert.deepEqual(
		entries(html).map((e) => e.text),
		["Bluebird Water Gardens", "Acme Pump Supply"]
	);
});

test("on a Supplier form, that Supplier reads This supplier", () => {
	const html = cell(
		{ accounts: [account("Supplier", "SUP-0007", "Acme Pump Supply")] },
		"Supplier",
		"SUP-0007"
	);
	assert.deepEqual(quiet(html), ["This supplier"]);
	assert.deepEqual(entries(html), []);
});

test("a Customer sharing the open Supplier's name is not the open party", () => {
	const html = cell(
		{
			accounts: [
				account("Customer", "Harbor Plaza", "Harbor Plaza"),
				account("Supplier", "Harbor Plaza", "Harbor Plaza"),
			],
		},
		"Supplier",
		"Harbor Plaza"
	);
	assert.deepEqual(quiet(html), ["This supplier"]);
	assert.deepEqual(
		entries(html).map((e) => e.href),
		["https://erp.example.com/desk/customer/Harbor%20Plaza"]
	);
});

test("on a Project or Opportunity, the job's Customer is a normal link", () => {
	for (const doctype of ["Project", "Opportunity", "Master Project", "Contact"]) {
		const html = cell({ accounts: [account("Customer", "CUST-0042", "Bluebird Water Gardens")] }, doctype, "CUST-0042");
		assert.deepEqual(quiet(html), [], `${doctype}: a non-party form kept its party quiet`);
		assert.equal(entries(html).length, 1, doctype);
	}
});

// Both tables use the one cell.

for (const [table, row] of [
	["render_contact_table", "this.accounts_cell(c)"],
	["render_address_table", "this.accounts_cell(a)"],
]) {
	test(`${table} heads the column Account / Supplier and fills it from accounts_cell`, () => {
		const source = controller[table].toString();
		assert.ok(source.includes('${__("Account / Supplier")}'), "the header is not Account / Supplier");
		assert.ok(!source.includes("Linked To"), "the table still has a Linked To column");
		assert.ok(source.includes(row), "the cell is not drawn by accounts_cell");
	});
}

console.log("");
if (failures) {
	console.error(`directory account column: ${failures} of ${checks} FAILED`);
	process.exit(2);
}
console.log(`directory account column: ${checks} assertions passed`);
