#!/usr/bin/env node
/**
 * The contact directory's Account / Supplier column, executed rather than grepped (v1.746.0).
 *
 * Until v1.746.0 the contact table in unified_tab_controller.js had a "Linked To" column
 * listing every party a Contact is linked to, by ID, so someone on five jobs showed five
 * Projects and Opportunities beside the one company they work for. Nik asked for "this
 * contact belongs to this customer, or this contact belongs to this supplier". The column
 * is now `contact_accounts_cell`, fed by the `accounts` that
 * `sync_contact.get_contacts_for_context` puts on each row (the server half is
 * `AccountColumnTest` in tests/test_contact_endpoint_permissions.py). This runs the real
 * method against the v16 helpers it calls. Every name here is invented.
 *
 * Run: node scripts/test_contact_account_column.mjs
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
assert.equal(typeof controller.contact_accounts_cell, "function");

const cell = (contact) => controller.contact_accounts_cell(contact);
const account = (doctype, name, title) => ({ doctype, name, title });

/** The cell parsed into its entries: what a person sees and where each one goes. */
function entries(html) {
	return [...html.matchAll(/<div><a href="([^"]*)" target="_blank"(?: title="([^"]*)")?>(.*?)<\/a>(.*?)<\/div>/g)].map(
		(m) => ({ href: m[1], hover: m[2] || null, text: m[3], tag: m[4] })
	);
}

// -------------------------------------------------------------------- the cases

test("a Contact on no Customer or Supplier shows a muted None", () => {
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

test("the contact table heads the column Account / Supplier and fills it from this method", () => {
	const source = controller.render_contact_table.toString();
	assert.ok(source.includes('${__("Account / Supplier")}'), "the header is not Account / Supplier");
	assert.ok(!source.includes("Linked To"), "the contact table still has a Linked To column");
	assert.ok(source.includes("this.contact_accounts_cell(c)"), "the cell is not drawn by contact_accounts_cell");
});

console.log("");
if (failures) {
	console.error(`contact account column: ${failures} of ${checks} FAILED`);
	process.exit(2);
}
console.log(`contact account column: ${checks} assertions passed`);
