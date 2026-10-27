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
 * tests/test_contact_endpoint_permissions.py), by the rule in party_sources.js
 * (`account_entries`), which the guided page's People & places list follows too
 * (tests/test_guided_record_tools.py runs the rule itself). This runs the real method, with the
 * real party_sources.js installed the way erpnext_enhancements.bundle.js installs it, against the
 * v16 helpers it calls: on a Project form, on the Customer and Supplier forms, where the open
 * party is kept quiet, and on a tab still holding a bundle from before the rule. Every name here
 * is invented.
 *
 * Run: node scripts/test_directory_account_column.mjs
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
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

// party_sources.js is an ES module in a package that is not "type": "module", so it is imported from a
// copy beside a package.json that says it is (as scripts/test_party_sources.mjs does).
const SOURCES_JS = path.join(path.dirname(CONTROLLER_JS), "party_sources.js");
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "account-column-"));
fs.writeFileSync(path.join(tmp, "package.json"), '{"type": "module"}');
fs.copyFileSync(SOURCES_JS, path.join(tmp, "party_sources.js"));
const party_sources = await import(pathToFileURL(path.join(tmp, "party_sources.js")).href);
fs.rmSync(tmp, { recursive: true, force: true });
const RULES = Object.assign({}, party_sources);

globalThis.erpnext_enhancements = { party_sources: RULES };
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

/** The cell for one row, drawn for a form of this doctype and name (a Project unless said). */
function cell(record, doctype = "Project", name = "PROJ-0001") {
	return controller.accounts_cell(record, { doctype, doc: { name } });
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

// A tab still holding a bundle from before v1.746.0 (party_sources without account_entries, or none).

test("a tab on an older bundle gets an empty cell until it reloads, not a TypeError", () => {
	const row = { accounts: [account("Customer", "CUST-0042", "Bluebird Water Gardens")] };
	try {
		globalThis.erpnext_enhancements.party_sources = Object.assign({}, RULES, { account_entries: undefined });
		assert.equal(cell(row), "");
		globalThis.erpnext_enhancements.party_sources = undefined;
		assert.equal(cell(row), "");
	} finally {
		globalThis.erpnext_enhancements.party_sources = RULES;
	}
	assert.equal(entries(cell(row)).length, 1, "the rules were not restored");
});

// Both tables use the one cell.

for (const [table, row] of [
	["render_contact_table", "this.accounts_cell(c, frm)"],
	["render_address_table", "this.accounts_cell(a, frm)"],
]) {
	test(`${table} heads the column Account / Supplier and fills it from accounts_cell`, () => {
		const source = controller[table].toString();
		assert.ok(source.includes('${__("Account / Supplier")}'), "the header is not Account / Supplier");
		assert.ok(!source.includes("Linked To"), "the table still has a Linked To column");
		assert.ok(source.includes(row), "the cell is not drawn by accounts_cell");
	});
}

// ------------------------------------------- the tables themselves (the 56d0cdd4 review's fixes)
//
// Each render empties the field, shows a loading line, asks the server, and draws the table when
// the reply lands. A fake jQuery keeps the field as an HTML string, which is all these two need:
// what the reply removes, and which form the cell is drawn for.

const FIELD = {}; // what frm.fields_dict.<x>_list_html.wrapper is
let field = null; // that field's fake jQuery
const replies = []; // frappe.call options, answered by the test

class FakeField {
	constructor() {
		this.content = "";
	}
	empty() {
		this.content = "";
		return this;
	}
	html(markup) {
		this.content = markup;
		return this;
	}
	append(markup) {
		this.content += markup;
		return this;
	}
	find(selector) {
		const cls = selector.replace(/^\./, "");
		const self = this;
		// Removes each element carrying the class; none of the ones that matter here nest.
		const element = new RegExp(`<(\\w+) class="(?:[^"]*\\s)?${cls}(?:\\s[^"]*)?"[^>]*>[^<]*</\\1>`, "g");
		return {
			remove() {
				self.content = self.content.replace(element, "");
				return this;
			},
			on() {
				return this;
			},
		};
	}
}

globalThis.$ = (arg) => {
	if (arg === FIELD) return field;
	const node = {
		appendTo: (target) => {
			if (target === field) field.append(String(arg));
			return node;
		},
		on: () => node,
	};
	return node;
};
globalThis.frappe.call = (options) => replies.push(options);

function form(doctype, name, html_field) {
	return {
		doctype,
		doc: { name },
		meta: { fields: [] },
		fields_dict: { [html_field]: { wrapper: FIELD } },
		is_new: () => false,
	};
}

const TABLES = [
	["render_contact_table", "contact_list_html"],
	["render_address_table", "address_list_html"],
];

for (const [table, html_field] of TABLES) {
	test(`${table}: an overlapping render keeps the first table's muted cells`, () => {
		field = new FakeField();
		replies.length = 0;
		controller.frm = form("Customer", "CUST-0042", html_field);
		controller[table]();
		controller[table]();
		assert.equal(replies.length, 2);
		const rows = [
			{ name: "ROW-1", accounts: [account("Customer", "CUST-0042", "Bluebird Water Gardens")] },
			{ name: "ROW-2", accounts: [] },
		];
		replies[0].callback({ message: rows });
		replies[1].callback({ message: rows });
		const count = (text) => field.content.split(text).length - 1;
		assert.equal(count("Fetching"), 0, "the loading line is still there");
		assert.equal(count('<div class="text-muted">This account</div>'), 2, "a This account cell was stripped");
		assert.equal(count('<span class="text-muted">None</span>'), 2, "a None cell was stripped");
	});

	test(`${table}: the cell is drawn for the form it was requested for`, () => {
		field = new FakeField();
		replies.length = 0;
		controller.frm = form("Customer", "CUST-0042", html_field);
		controller[table]();
		// The person moves to a Supplier before the reply lands.
		controller.frm = form("Supplier", "SUP-0007", html_field);
		replies[0].callback({
			message: [
				{
					name: "ROW-1",
					accounts: [
						account("Customer", "CUST-0042", "Bluebird Water Gardens"),
						account("Supplier", "SUP-0007", "Acme Pump Supply"),
					],
				},
			],
		});
		assert.ok(field.content.includes('<div class="text-muted">This account</div>'), "the requesting Customer is not quiet");
		assert.ok(!field.content.includes("This supplier"), "the form opened since was treated as the open party");
		assert.ok(field.content.includes(">Acme Pump Supply</a>"), "the Supplier lost its link");
	});
}

console.log("");
if (failures) {
	console.error(`directory account column: ${failures} of ${checks} FAILED`);
	process.exit(2);
}
console.log(`directory account column: ${checks} assertions passed`);
