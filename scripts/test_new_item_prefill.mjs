#!/usr/bin/env node
/**
 * ER-2026-520743: a new record made from a grid keeps what was typed, executed against a stubbed
 * frappe rather than grepped.
 *
 *   1. buying/new_item_values.js: what a buying row says about a new Item. A row being filled in
 *      for the new item gives its name, description, units and price; a row that still holds
 *      another Item gives only the typed code (its fetched details describe the wrong part); a
 *      name that only repeats the code, an empty Text Editor and a zero price are left out.
 *   2. buying/new_item_prefill.js: the hook on each buying form's `items` item_code docfield, set
 *      on onload and refresh, once per docfield copy, behind any hook already there (whose values
 *      win), and failing open.
 *   3. global_enhancements/link_selector_create.js: "Add multiple" -> "Create a new X" opens the
 *      new record with the search text (name_field, plus the field: autoname field), clears a
 *      stale frappe._from_link, and adds the created record to the grid; it waits for app_ready
 *      when LinkSelector is not defined yet. The stubs follow frappe v16 (link_selector.js,
 *      quick_entry.js, create_new.js) where it matters. Every name here is invented.
 *
 * The modules are ES modules in a package without "type": "module", so they are copied to a temp
 * folder beside one that says so, the way tests/test_guided_item.py runs the form kit.
 * Run: node scripts/test_new_item_prefill.mjs
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const JS = path.join(HERE, "..", "erpnext_enhancements", "public", "js");

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ee-new-item-"));
fs.writeFileSync(path.join(tmp, "package.json"), '{"type": "module"}');
fs.mkdirSync(path.join(tmp, "buying"));
for (const name of ["new_item_values.js", "new_item_prefill.js"]) {
	fs.copyFileSync(path.join(JS, "buying", name), path.join(tmp, "buying", name));
}
const SELECTOR_JS = path.join(JS, "global_enhancements", "link_selector_create.js");
fs.copyFileSync(SELECTOR_JS, path.join(tmp, "link_selector_create.js"));
fs.copyFileSync(SELECTOR_JS, path.join(tmp, "link_selector_create_late.js"));
const load = (name) => import(pathToFileURL(path.join(tmp, name)).href);

let passed = 0;
async function check(label, fn) {
	try {
		await fn();
		passed += 1;
	} catch (e) {
		console.error(`FAIL ${label}`);
		throw e;
	}
}

// ---- the stubbed frappe -----------------------------------------------------------------------

const handlers = {};
const new_doc_calls = [];
const app_ready = [];
const metas = {
	Item: { autoname: "field:item_code" },
	Supplier: { autoname: "naming_series:", title_field: "supplier_name" },
};

class QuickEntryForm {
	constructor(after_insert) {
		this.after_insert = after_insert || null;
	}
}

class LinkSelector {
	make() {
		const listeners = [];
		this.dialog = {
			values: { txt: "" },
			$wrapper: [
				{
					listeners,
					addEventListener: (type, fn, capture) => listeners.push({ type, fn, capture }),
				},
			],
			get_value: (field) => this.dialog.values[field],
			fields_dict: { txt: { set_input: (value) => (this.dialog.values.txt = value) } },
		};
		this.made = (this.made || 0) + 1;
	}
}

function provide(dotted) {
	let obj = globalThis;
	for (const part of dotted.split(".")) obj = obj[part] = obj[part] || {};
	return obj;
}

globalThis.document = {};
globalThis.$ = () => ({ one: (event, fn) => app_ready.push({ event, fn }) });
globalThis.frappe = {
	provide,
	get_meta: (doctype) => metas[doctype] || null,
	new_doc: (doctype, opts, init_callback) => {
		new_doc_calls.push({ doctype, opts, init_callback, from_link: globalThis.frappe._from_link });
		return Promise.resolve();
	},
	_from_link: null,
	ui: {
		form: {
			QuickEntryForm,
			LinkSelector,
			on: (doctype, events) => {
				(handlers[doctype] = handlers[doctype] || []).push(events);
			},
		},
	},
};

const silenced = [];
console.warn = (...args) => silenced.push(args);

// ---- 1. what a row says -----------------------------------------------------------------------

const { BUYING_DOCTYPES, new_item_values, is_blank_html } = await load("buying/new_item_values.js");

await check("the buying documents", () => {
	assert.deepEqual(BUYING_DOCTYPES, [
		"Material Request",
		"Request for Quotation",
		"Supplier Quotation",
		"Purchase Order",
		"Purchase Receipt",
		"Purchase Invoice",
	]);
});

await check("a row filled in for the new item gives everything typed on it", () => {
	const row = {
		item_name: "Fitting, Elbow, 90, PVC",
		description: "<div>1-1/2 in. slip x slip</div>",
		uom: "Box",
		stock_uom: "",
		rate: 14.25,
		qty: 3,
	};
	assert.deepEqual(new_item_values(row, { supplier: "Acme Supply" }, " PVC-90-112 "), {
		item_code: "PVC-90-112",
		supplier: "Acme Supply",
		item_name: "Fitting, Elbow, 90, PVC",
		description: "<div>1-1/2 in. slip x slip</div>",
		stock_uom: "Box",
		uom: "Box",
		rate: 14.25,
	});
});

await check("a row that still holds another Item gives only the code and the supplier", () => {
	const row = { item_code: "OLD-1", item_name: "Old part", description: "<p>Old</p>", uom: "Unit", rate: 9 };
	assert.deepEqual(new_item_values(row, { supplier: "Acme Supply" }, "NEW-1"), {
		item_code: "NEW-1",
		supplier: "Acme Supply",
	});
});

await check("a name that only repeats the code is left for the person", () => {
	const out = new_item_values({ item_name: "pvc-90" }, {}, "PVC-90");
	assert.equal(out.item_name, undefined);
	assert.equal(out.item_code, "PVC-90");
});

// A real U+00A0, built rather than typed so no editor or linter can turn it into anything else.
const NBSP = String.fromCharCode(160);

await check("an empty Text Editor is not a description; a real one travels verbatim", () => {
	for (const blank of ["", "   ", "<div><br></div>", "<p>&nbsp;</p>", `<p>${NBSP}</p>`, `<p>&nbsp;${NBSP}</p>`, null, undefined]) {
		assert.equal(is_blank_html(blank), true, JSON.stringify(blank));
		assert.equal(new_item_values({ description: blank }, {}, "X").description, undefined);
	}
	const html = '<p>Brass <strong>1/2"</strong> &amp; teflon</p>';
	assert.equal(new_item_values({ description: html }, {}, "X").description, html);
});

await check("units: stock_uom kept when set, else the row's uom; nothing when neither", () => {
	assert.deepEqual(new_item_values({ stock_uom: "Unit", uom: "Box" }, {}, "X"), {
		item_code: "X",
		stock_uom: "Unit",
		uom: "Box",
	});
	assert.deepEqual(new_item_values({}, {}, "X"), { item_code: "X" });
});

await check("only a positive, finite price travels", () => {
	for (const rate of [0, -3, "abc", null, undefined, Infinity]) {
		assert.equal(new_item_values({ rate }, {}, "X").rate, undefined, String(rate));
	}
	assert.equal(new_item_values({ rate: "12.5" }, {}, "X").rate, 12.5);
});

await check("no row, no parent, nothing typed: nothing, and no throw", () => {
	assert.deepEqual(new_item_values(null, null, ""), {});
	assert.deepEqual(new_item_values(undefined, undefined, undefined), {});
});

// ---- 2. the hook on the buying forms -------------------------------------------------------

await load("buying/new_item_prefill.js");

function form(doctype, doc, df = {}) {
	return {
		doctype,
		doc,
		fields_dict: { items: { grid: {} } },
		get_docfield: (table, field) => (table === "items" && field === "item_code" ? df : null),
		df,
	};
}

const row = { item_name: "Valve, Ball, Brass", description: "<p>3/4 in.</p>", uom: "Unit", rate: 22 };
const control = (doc, typed) => ({ doc, get_label_value: () => typed });

await check("every buying document registers onload and refresh", () => {
	for (const doctype of BUYING_DOCTYPES) {
		assert.equal(handlers[doctype].length, 1, doctype);
		assert.equal(typeof handlers[doctype][0].onload, "function");
		assert.equal(handlers[doctype][0].refresh, handlers[doctype][0].onload);
	}
	assert.equal(handlers["Sales Order"], undefined);
});

await check("refresh sets the hook, and it returns the row's values", () => {
	const frm = form("Purchase Order", { supplier: "Acme Supply" });
	handlers["Purchase Order"][0].refresh(frm);
	assert.equal(typeof frm.df.get_route_options_for_new_doc, "function");
	assert.deepEqual(frm.df.get_route_options_for_new_doc(control(row, "VB-075")), {
		item_code: "VB-075",
		supplier: "Acme Supply",
		item_name: "Valve, Ball, Brass",
		description: "<p>3/4 in.</p>",
		stock_uom: "Unit",
		uom: "Unit",
		rate: 22,
	});
});

await check("the hook reads the document current when it is called", () => {
	const frm = form("Purchase Order", { supplier: "Acme Supply" });
	handlers["Purchase Order"][0].onload(frm);
	frm.doc = { supplier: "Beta Pipe" };
	assert.equal(frm.df.get_route_options_for_new_doc(control({}, "X")).supplier, "Beta Pipe");
});

await check("setting it again leaves the same hook", () => {
	const frm = form("Material Request", {});
	handlers["Material Request"][0].onload(frm);
	const first = frm.df.get_route_options_for_new_doc;
	handlers["Material Request"][0].refresh(frm);
	assert.equal(frm.df.get_route_options_for_new_doc, first);
});

await check("a hook already there runs first and its values win", () => {
	const seen = [];
	const theirs = function (ctl) {
		seen.push({ self: this, ctl });
		return { item_code: "THEIRS", brand: "Acme" };
	};
	const frm = form("Purchase Receipt", { supplier: "Acme Supply" }, { get_route_options_for_new_doc: theirs });
	handlers["Purchase Receipt"][0].refresh(frm);
	const ctl = control(row, "VB-075");
	const out = frm.df.get_route_options_for_new_doc(ctl);
	assert.equal(out.item_code, "THEIRS");
	assert.equal(out.brand, "Acme");
	assert.equal(out.description, "<p>3/4 in.</p>");
	assert.equal(seen.length, 1);
	assert.equal(seen[0].self, frm.df);
	assert.equal(seen[0].ctl, ctl);
});

await check("it fails open: a broken control gives the other hook's values, or none", () => {
	const frm = form("Purchase Invoice", {});
	handlers["Purchase Invoice"][0].refresh(frm);
	const broken = { doc: row, get_label_value: () => {
		throw new Error("boom");
	} };
	assert.deepEqual(frm.df.get_route_options_for_new_doc(broken), {});
	assert.ok(silenced.length >= 1);
});

await check("a form without an items grid is left alone", () => {
	const frm = form("Supplier Quotation", {});
	frm.fields_dict = {};
	handlers["Supplier Quotation"][0].refresh(frm);
	assert.equal(frm.df.get_route_options_for_new_doc, undefined);
	handlers["Supplier Quotation"][0].refresh(null);
});

// ---- 3. "Add multiple" -> "Create a new X" ----------------------------------------------------

await load("link_selector_create.js");
const ns = globalThis.erpnext_enhancements.link_selector_create;

function selector(doctype, { grid = true } = {}) {
	const sel = new LinkSelector();
	sel.doctype = doctype;
	sel.target = grid ? { is_grid: true } : { is_grid: false };
	sel.added = [];
	sel.searches = 0;
	sel.set_in_grid = (name) => {
		sel.added.push(name);
		return Promise.resolve();
	};
	sel.search = () => (sel.searches += 1);
	sel.make();
	return sel;
}

function click(sel, on_button) {
	const event = {
		target: { closest: (css) => (on_button && css === ".new-doc" ? {} : null) },
		prevented: false,
		stopped: false,
		preventDefault() {
			this.prevented = true;
		},
		stopPropagation() {
			this.stopped = true;
		},
	};
	for (const l of sel.dialog.$wrapper[0].listeners) l.fn(event);
	return event;
}

await check("make() is wrapped once and watches the dialog in the capture phase", () => {
	assert.equal(typeof LinkSelector.prototype.make, "function");
	const sel = selector("Item");
	const listeners = sel.dialog.$wrapper[0].listeners;
	assert.equal(listeners.length, 1);
	assert.equal(listeners[0].type, "click");
	assert.equal(listeners[0].capture, true);
	ns.watch(sel);
	assert.equal(listeners.length, 1, "watching twice adds nothing");
});

await check("Create a new Item opens it with the search text as the code", async () => {
	const sel = selector("Item");
	sel.dialog.values.txt = "  PVC-90-112 ";
	globalThis.frappe._from_link = { field_obj: "stale" };
	new_doc_calls.length = 0;
	const event = click(sel, true);
	assert.equal(event.prevented, true);
	assert.equal(event.stopped, true, "frappe's own blank new_doc never runs");
	assert.equal(new_doc_calls.length, 1);
	const call = new_doc_calls[0];
	assert.equal(call.doctype, "Item");
	assert.deepEqual(call.opts, { name_field: "PVC-90-112", item_code: "PVC-90-112" });
	assert.equal(call.from_link, null, "a stale _from_link is cleared before the dialog opens");

	// Quick Entry rendered as a dialog: the saved Item is added to the grid, then shown.
	const dialog = new QuickEntryForm();
	call.init_callback(dialog);
	assert.equal(typeof dialog.after_insert, "function");
	await dialog.after_insert({ name: "PVC-90-112" });
	assert.deepEqual(sel.added, ["PVC-90-112"]);
	assert.equal(sel.dialog.values.txt, "PVC-90-112");
	assert.equal(sel.searches, 1);
	assert.equal(sel.start, 0);
});

await check("the full-form fallback and an existing after_insert are left alone", () => {
	const sel = selector("Item");
	sel.dialog.values.txt = "X-1";
	new_doc_calls.length = 0;
	click(sel, true);
	const { init_callback } = new_doc_calls[0];
	const doc = { doctype: "Item", name: "new-item-1" };
	init_callback(doc);
	assert.equal(doc.after_insert, undefined);
	const theirs = () => "theirs";
	const dialog = new QuickEntryForm(theirs);
	init_callback(dialog);
	assert.equal(dialog.after_insert, theirs);
});

await check("an empty search opens a blank record, as frappe's own button does", () => {
	const sel = selector("Item");
	sel.dialog.values.txt = "   ";
	new_doc_calls.length = 0;
	click(sel, true);
	assert.deepEqual(new_doc_calls[0].opts, {});
});

await check("a doctype not named by a field gets name_field only (frappe maps it to the title)", () => {
	assert.deepEqual(ns.route_options("Supplier", "Acme Supply"), { name_field: "Acme Supply" });
	assert.deepEqual(ns.route_options("Unknown Doctype", "abc"), { name_field: "abc" });
});

await check("clicks anywhere else in the dialog pass through", () => {
	const sel = selector("Item");
	new_doc_calls.length = 0;
	const event = click(sel, false);
	assert.equal(event.prevented, false);
	assert.equal(event.stopped, false);
	assert.equal(new_doc_calls.length, 0);
});

await check("outside a grid the new record is shown in the results, not added", async () => {
	const sel = selector("Supplier", { grid: false });
	await ns.use_created(sel, "Acme Supply");
	assert.deepEqual(sel.added, []);
	assert.equal(sel.dialog.values.txt, "Acme Supply");
	assert.equal(sel.searches, 1);
	await ns.use_created(sel, "");
	assert.equal(sel.searches, 1, "no name, nothing to show");
});

await check("loaded before LinkSelector exists, it waits for app_ready", async () => {
	const saved = globalThis.frappe.ui.form.LinkSelector;
	delete globalThis.frappe.ui.form.LinkSelector;
	app_ready.length = 0;
	await load("link_selector_create_late.js");
	assert.equal(app_ready.length, 1);
	assert.equal(app_ready[0].event, "app_ready");
	class Later {
		make() {
			this.dialog = { $wrapper: [{ addEventListener: () => (this.watched = true) }] };
		}
	}
	globalThis.frappe.ui.form.LinkSelector = Later;
	app_ready[0].fn();
	const sel = new Later();
	sel.make();
	assert.equal(sel.watched, true);
	globalThis.frappe.ui.form.LinkSelector = saved;
});

fs.rmSync(tmp, { recursive: true, force: true });
console.log(`new item prefill: ${passed} checks passed`);
