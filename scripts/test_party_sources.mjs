#!/usr/bin/env node
/**
 * The directory widget's party sources, executed rather than grepped.
 *
 * `get_all_party_sources` in unified_tab_controller.js gathers every party the open
 * form draws its Contact and Address directories from (the form itself, its Customer
 * or Supplier, an Opportunity's party, each stakeholder or Dynamic Link row) and posts
 * the list to `sync_contact.get_contacts_for_context` / `get_addresses_for_context`.
 *
 * Until the v1.561.1 review it de-duplicated that list on the NAME alone, so of two
 * parties sharing a name only the first reached the server. That was harmless while
 * the server matched `Dynamic Link.link_name` alone, whatever the doctype. v1.561.1
 * made the server match the (doctype, name) pair, so the dropped party's contacts and
 * addresses silently left the directory: a Customer form whose stakeholder Supplier
 * shares its name, or a Contact named after the Customer it is linked to. This runs
 * the real function on those forms. Every name here is invented.
 *
 * Run: node scripts/test_party_sources.mjs
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

// Loading the file only registers form handlers and assigns the controller object;
// nothing else runs at the top level, so these two are all it needs.
globalThis.erpnext_enhancements = {};
globalThis.frappe = {
	provide: () => {},
	ui: { form: { on: () => {} } },
};

vm.runInThisContext(fs.readFileSync(CONTROLLER_JS, "utf8"), { filename: CONTROLLER_JS });

const controller = globalThis.erpnext_enhancements.unified_controller;
assert.ok(controller, "the controller did not register itself");
assert.equal(typeof controller.get_all_party_sources, "function");

/** The sources a form with this doctype, doc and Table fields would post. */
function sourcesFor(doctype, doc, tables = {}) {
	const fields = Object.keys(tables).map((fieldname) => ({ fieldname, fieldtype: "Table" }));
	const frm = { doctype, doc: { ...doc, ...tables }, meta: { fields } };
	return controller.get_all_party_sources.call({ frm });
}

const pair = (doctype, name) => ({ doctype, name });

// -------------------------------------------------------------------- the cases

test("a Customer form keeps a stakeholder Supplier of the same name", () => {
	const sources = sourcesFor(
		"Customer",
		{ name: "Harbor Plaza" },
		{ custom_contacts__address_table: [{ party_type: "Supplier", party_name: "Harbor Plaza" }] }
	);
	assert.deepEqual(sources, [pair("Customer", "Harbor Plaza"), pair("Supplier", "Harbor Plaza")]);
});

test("a Contact form keeps the Customer it is named after", () => {
	const sources = sourcesFor(
		"Contact",
		{ name: "Northwind Fountains" },
		{ links: [{ link_doctype: "Customer", link_name: "Northwind Fountains" }] }
	);
	assert.deepEqual(sources, [
		pair("Contact", "Northwind Fountains"),
		pair("Customer", "Northwind Fountains"),
	]);
});

test("a Project keeps a stakeholder Supplier named like its Customer", () => {
	const sources = sourcesFor(
		"Project",
		{ name: "PROJ-0001", customer: "Harbor Plaza" },
		{ custom_stakeholders: [{ supplier: "Harbor Plaza" }] }
	);
	assert.deepEqual(sources, [
		pair("Project", "PROJ-0001"),
		pair("Customer", "Harbor Plaza"),
		pair("Supplier", "Harbor Plaza"),
	]);
});

test("the same party named twice is still sent once, first place kept", () => {
	const sources = sourcesFor(
		"Project",
		{ name: "PROJ-0001", customer: "Northwind Fountains" },
		{
			custom_stakeholders: [
				{ customer: "Northwind Fountains" },
				{ party_type: "Customer", party_name: "Northwind Fountains" },
				{ link_doctype: "Customer", link_name: "Northwind Fountains" },
			],
		}
	);
	assert.deepEqual(sources, [pair("Project", "PROJ-0001"), pair("Customer", "Northwind Fountains")]);
});

test("an Opportunity's party still comes from opportunity_from", () => {
	const sources = sourcesFor("Opportunity", {
		name: "CRM-OPP-0001",
		opportunity_from: "Lead",
		party_name: "LEAD-0001",
	});
	assert.deepEqual(sources, [pair("Opportunity", "CRM-OPP-0001"), pair("Lead", "LEAD-0001")]);
});

test("a party with no name is never sent", () => {
	const sources = sourcesFor(
		"Project",
		{ name: "", customer: "Northwind Fountains" },
		{ custom_stakeholders: [{ customer: "" }, { link_doctype: "Customer", link_name: "" }] }
	);
	assert.deepEqual(sources, [pair("Customer", "Northwind Fountains")]);
});

console.log("");
if (failures) {
	console.error(`party sources: ${failures} of ${checks} FAILED`);
	process.exit(2);
}
console.log(`party sources: ${checks} assertions passed`);
