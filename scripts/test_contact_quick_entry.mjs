#!/usr/bin/env node
/**
 * New Contact / New Address from a party form, and the directory's Link Existing, executed
 * against a stubbed frappe rather than grepped (v1.575.0).
 *
 * What these guard, each found on prod by the 2026-10-06 link audit or its code review:
 *
 *   1. A Contact made from a Project is filed under the project's Customer only while the
 *      user leaves that Customer as its Account. On the Customer's OWN form the Customer is
 *      never dropped (a cleared Account there once meant a contact linked to nothing).
 *   2. A Contact or Address created from a Project Stakeholder row belongs to THAT ROW's
 *      party (plus the job), not the project's Customer: the audited Nathan Brooks shape.
 *   3. An Address made from a job or a Contact form must say whose it is: a required choice
 *      with no default ("This Project only" / "Also <Customer>'s address"), except from a
 *      stakeholder row, where the row's party is pre-chosen. From a Contact form the Contact
 *      is always kept. It is never left with no link at all (frappe's Address.link_address
 *      would then file it under its creator's own Contact links).
 *   4. The banner ("Will be linked to …") follows the user's edits. frappe swaps each
 *      control's df for a per-doc copy of the meta docfield at render, which dropped the
 *      dialog's onchange; the stub below does the same swap, so that cannot hide again.
 *   5. Link Existing links the open form, and the form's own Customer only when the user
 *      ticks the (unticked) box. Never a stakeholder.
 *
 * The QuickEntryForm stub follows frappe v16 where it matters (quick_entry.js, field_group.js,
 * layout.js attach_doc_and_docfields): controls get the per-doc meta df after build, defaults
 * arrive asynchronously through set_value (firing onchange), get_values() leaves empty fields
 * out, update_doc() copies only non-empty values, and a user's edit writes the doc model.
 * Every name here is invented. Run: node scripts/test_contact_quick_entry.mjs
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DIR = path.join(HERE, "..", "erpnext_enhancements", "public", "js", "global_enhancements");
const QUICK_ENTRY_JS = path.join(DIR, "contact_address_quick_entry.js");
const CONTROLLER_JS = path.join(DIR, "unified_tab_controller.js");

const TESTS = [];
const test = (name, fn) => TESTS.push({ name, fn });
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

// ------------------------------------------------------------------ desk stubs

/** Meta docfields the dialogs clone (fieldtype matters little; presence in meta does). */
const META = {
	Contact: ["first_name", "last_name", "custom_title", "custom_account", "custom_email", "custom_phone_number", "custom_mobile_number"],
	Address: [
		"address_line1",
		"address_line2",
		"pincode",
		"city",
		"state",
		"country",
		"address_type",
		"address_title",
		"custom_latitude",
		"custom_longitude",
	],
};
const DOC_COPIES = {};

function meta_df(doctype, fieldname) {
	if (!(META[doctype] || []).includes(fieldname)) return undefined;
	// As on prod: none of these is read-only or required in meta (tabCustom Field / tabDocField).
	return { fieldname, fieldtype: "Data", label: fieldname, read_only: 0, reqd: 0 };
}

const is_null = (v) => v === null || v === undefined || v === "";

class Control {
	constructor(df, dialog) {
		this.df = df;
		this.dialog = dialog;
		this.value = undefined;
	}
	get_value() {
		return this.value;
	}
	set_input(value) {
		this.value = value;
	}
	refresh() {}
	/** Control.set_value -> validate_and_set_in_model: model write, then df.onchange. */
	set_value(value) {
		this.value = value;
		this.dialog.doc[this.df.fieldname] = value;
		if (this.df.onchange) this.df.onchange();
	}
	/** What a user typing, clearing or ticking does. */
	user_sets(value) {
		this.set_value(value);
	}
}

class QuickEntryForm {
	constructor(doctype, after_insert, init_callback, doc) {
		this.doctype = doctype;
		this.after_insert = after_insert;
		this.doc = doc || { doctype, __islocal: 1, name: "new-" + doctype.toLowerCase() + "-1" };
		this.dialog = this;
		this.intro = null;
	}
	render_dialog() {
		this.fields_dict = {};
		(this.docfields || []).forEach((df) => {
			if (df && df.fieldname) this.fields_dict[df.fieldname] = new Control(df, this);
		});
		// layout.js attach_doc_and_docfields: the per-doc meta copy replaces the df.
		Object.values(this.fields_dict).forEach((control) => {
			control.df = frappe.meta.get_docfield(this.doc.doctype, control.df.fieldname, this.doc.name) || control.df;
		});
		// FieldGroup.make: defaults through set_values, asynchronously.
		const defaults = Object.values(this.fields_dict).filter((c) => c.df.default !== undefined);
		Promise.resolve().then(() => defaults.forEach((c) => c.set_value(c.df.default)));
		// QuickEntryForm.set_defaults: the doc's own values, synchronously.
		Object.values(this.fields_dict).forEach((c) => {
			if (!is_null(this.doc[c.df.fieldname])) c.set_input(this.doc[c.df.fieldname]);
		});
	}
	get_values() {
		const ret = {};
		Object.values(this.fields_dict).forEach((f) => {
			const v = f.get_value();
			if (!is_null(v)) ret[f.df.fieldname] = v;
		});
		return ret;
	}
	update_doc() {
		const data = this.get_values(true);
		Object.entries(data).forEach(([k, v]) => {
			if (!is_null(v)) this.dialog.doc[k] = v;
		});
		return this.doc;
	}
	set_intro(txt, color) {
		// Dialog.set_alert renders alert-<color>; v16 styles only these (alert.scss).
		if (txt) assert.ok(["info", "success", "warning", "danger"].includes(color), `unstyled alert colour '${color}'`);
		this.intro = txt || null;
	}
	is_quick_entry() {
		return false;
	}
}

let ROUTE = [];
const FORMS = {};
const CALLS = [];
let PROMPT = null;
let FAIL_ON = null;
const ALERTS = [];

globalThis.window = globalThis;
globalThis.__ = (text, args) => String(text).replace(/\{(\d+)\}/g, (_, i) => (args && args[i] !== undefined ? args[i] : ""));
globalThis.cint = (v) => parseInt(v, 10) || 0;
globalThis.$ = () => ({ one: () => {} });
globalThis.erpnext_enhancements = {};
globalThis.frappe = {
	provide: (ns) => {
		let obj = globalThis;
		ns.split(".").forEach((part) => {
			obj[part] = obj[part] || {};
			obj = obj[part];
		});
	},
	ui: { form: { QuickEntryForm, on: () => {} } },
	boot: { ee_contacts_ux: 1 },
	sys_defaults: { country: "United States" },
	get_route: () => ROUTE,
	views: { formview: FORMS },
	meta: {
		get_docfield: (doctype, fieldname, docname) => {
			const base = meta_df(doctype, fieldname);
			if (!base || !docname) return base;
			const key = `${doctype}::${docname}::${fieldname}`;
			return (DOC_COPIES[key] = DOC_COPIES[key] || base);
		},
	},
	model: {
		add_child: (doc, child_doctype, field) => {
			const row = { doctype: child_doctype };
			(doc[field] = doc[field] || []).push(row);
			return row;
		},
	},
	utils: { get_link_title: () => undefined },
	prompt: (fields, callback) => {
		PROMPT = { fields, callback };
	},
	call: (opts) => {
		CALLS.push(opts.args);
		if (FAIL_ON && opts.args.link_doctype === FAIL_ON) {
			if (opts.error) opts.error({});
			return;
		}
		if (opts.callback) opts.callback({ message: true });
	},
	msgprint: () => {},
	show_alert: (a) => ALERTS.push(a),
};

vm.runInThisContext(fs.readFileSync(QUICK_ENTRY_JS, "utf8"), { filename: QUICK_ENTRY_JS });
vm.runInThisContext(fs.readFileSync(CONTROLLER_JS, "utf8"), { filename: CONTROLLER_JS });

const { ContactQuickEntryForm, AddressQuickEntryForm } = globalThis.frappe.ui.form;
assert.ok(ContactQuickEntryForm && AddressQuickEntryForm, "the dialogs did not register");

/** Put the user on a saved form, the way the Desk route and formview cache would. */
function on_form(doctype, doc, dynamic_link = null) {
	ROUTE = ["Form", doctype, doc.name];
	for (const key of Object.keys(FORMS)) delete FORMS[key];
	FORMS[doctype] = { frm: { doc, is_new: () => false } };
	delete frappe._from_link;
	// ERPNext's own form scripts set this on transactions (sales_common.js, buying.js).
	frappe.dynamic_link = dynamic_link;
}

/** frappe.new_doc (the directory buttons) passes no after_insert; a link-field create does. */
async function open(Dialog, doctype, { from_link_create = false } = {}) {
	const after_insert = from_link_create ? () => {} : undefined;
	const dialog = new Dialog(doctype, after_insert);
	dialog.render_dialog();
	await tick();
	return dialog;
}

/** What link.js new_doc sets before it opens quick entry from a Project Stakeholder row. */
function create_from_row(form_doctype, form_name, row) {
	const child = Object.assign({ doctype: "Project Stakeholder", parenttype: form_doctype, parent: form_name }, row);
	frappe._from_link = { field_obj: {}, doc: child, set_route_args: ["Form", form_doctype, form_name], scrollY: 0 };
}

/** The same, from a field on the form's own doc (e.g. Payment Entry's Contact Person). */
function create_from_form_field(form_doctype, doc) {
	frappe._from_link = { field_obj: {}, doc: doc, set_route_args: ["Form", form_doctype, doc.name], scrollY: 0 };
}

const links_of = (doc) => (doc.links || []).map((l) => [l.link_doctype, l.link_name]);

// --------------------------------------------------------------- New Contact

test("from a Project, an untouched Account files the person under the project's Customer", async () => {
	on_form("Project", { name: "PRJ-0001", customer: "Harbor Plaza" });
	const dialog = await open(ContactQuickEntryForm, "Contact");
	assert.equal(dialog.fields_dict.custom_account.get_value(), "Harbor Plaza", "Account not pre-filled");
	dialog.fields_dict.first_name.user_sets("Dee");
	const doc = dialog.update_doc();
	assert.deepEqual(links_of(doc), [
		["Customer", "Harbor Plaza"],
		["Project", "PRJ-0001"],
	]);
	assert.equal(doc.custom_account, "Harbor Plaza");
	assert.match(dialog.intro, /Harbor Plaza/);
});

test("clearing the Account keeps the person on the Project and off the Customer, banner included", async () => {
	on_form("Project", { name: "PRJ-0001", customer: "Harbor Plaza" });
	const dialog = await open(ContactQuickEntryForm, "Contact");
	dialog.fields_dict.first_name.user_sets("Nate");
	dialog.fields_dict.custom_account.user_sets("");
	assert.doesNotMatch(dialog.intro || "", /Harbor Plaza/, "the banner still names the Customer (onchange lost at render)");
	assert.match(dialog.intro || "", /PRJ-0001/);
	const doc = dialog.update_doc();
	assert.deepEqual(links_of(doc), [["Project", "PRJ-0001"]]);
	assert.ok(!doc.custom_account, "the cleared Account came back");
});

test("changing the Account files the person under the chosen Customer only", async () => {
	on_form("Project", { name: "PRJ-0001", customer: "Harbor Plaza" });
	const dialog = await open(ContactQuickEntryForm, "Contact");
	dialog.fields_dict.first_name.user_sets("Kim");
	dialog.fields_dict.custom_account.user_sets("Northwind Fountains");
	assert.match(dialog.intro || "", /Northwind Fountains/, "the banner does not name the chosen Account");
	const doc = dialog.update_doc();
	assert.deepEqual(links_of(doc), [
		["Customer", "Northwind Fountains"],
		["Project", "PRJ-0001"],
	]);
});

test("the dialog's own field settings survive frappe's per-doc df swap", async () => {
	on_form("Project", { name: "PRJ-0001", customer: "Harbor Plaza" });
	const dialog = await open(ContactQuickEntryForm, "Contact");
	assert.equal(dialog.fields_dict.first_name.df.reqd, 1, "first name is no longer required in the dialog");
	assert.equal(typeof dialog.fields_dict.custom_account.df.onchange, "function");
});

test("on the Customer's own form the Customer is never dropped", async () => {
	on_form("Customer", { name: "Harbor Plaza" });
	let dialog = await open(ContactQuickEntryForm, "Contact");
	dialog.fields_dict.first_name.user_sets("Ida");
	dialog.fields_dict.custom_account.user_sets("");
	assert.deepEqual(links_of(dialog.update_doc()), [["Customer", "Harbor Plaza"]], "a contact linked to nothing");

	on_form("Customer", { name: "Harbor Plaza" });
	dialog = await open(ContactQuickEntryForm, "Contact");
	dialog.fields_dict.first_name.user_sets("Jo");
	dialog.fields_dict.custom_account.user_sets("Parent Co");
	assert.deepEqual(links_of(dialog.update_doc()), [
		["Customer", "Parent Co"],
		["Customer", "Harbor Plaza"],
	]);
});

test("an Opportunity's Customer follows the job rule; its Lead stays linked", async () => {
	on_form("Opportunity", { name: "OPP-0001", opportunity_from: "Customer", party_name: "Harbor Plaza" });
	let dialog = await open(ContactQuickEntryForm, "Contact");
	dialog.fields_dict.first_name.user_sets("Lou");
	dialog.fields_dict.custom_account.user_sets("");
	assert.deepEqual(links_of(dialog.update_doc()), [["Opportunity", "OPP-0001"]]);

	on_form("Opportunity", { name: "OPP-0002", opportunity_from: "Lead", party_name: "LEAD-0001" });
	dialog = await open(ContactQuickEntryForm, "Contact");
	dialog.fields_dict.first_name.user_sets("Max");
	assert.deepEqual(links_of(dialog.update_doc()), [
		["Lead", "LEAD-0001"],
		["Opportunity", "OPP-0002"],
	]);
});

test("from a Supplier, the person is the Supplier's and gets no Account", async () => {
	on_form("Supplier", { name: "County Health" });
	const dialog = await open(ContactQuickEntryForm, "Contact");
	dialog.fields_dict.first_name.user_sets("Ned");
	const doc = dialog.update_doc();
	assert.deepEqual(links_of(doc), [["Supplier", "County Health"]]);
	assert.ok(!doc.custom_account);
});

// --------------------------------------------------- created from a stakeholder row

test("a Contact created in a Supplier stakeholder row belongs to that Supplier and the job", async () => {
	on_form("Project", { name: "PRJ-0001", customer: "Harbor Plaza" });
	create_from_row("Project", "PRJ-0001", { party_type: "Supplier", party_name: "County Health", parent: "PRJ-0001" });
	const dialog = await open(ContactQuickEntryForm, "Contact", { from_link_create: true });
	assert.ok(!dialog.fields_dict.custom_account.get_value(), "pre-filled the project's Customer as the Account");
	dialog.fields_dict.first_name.user_sets("Ned");
	const doc = dialog.update_doc();
	assert.deepEqual(links_of(doc), [
		["Supplier", "County Health"],
		["Project", "PRJ-0001"],
	]);
	assert.ok(!links_of(doc).some(([dt, nm]) => dt === "Customer" && nm === "Harbor Plaza"));
});

test("a Contact created in a Customer stakeholder row suggests that Customer, not the project's", async () => {
	on_form("Project", { name: "PRJ-0001", customer: "Harbor Plaza" });
	create_from_row("Project", "PRJ-0001", { party_type: "Customer", party_name: "Layton Builders" });
	const dialog = await open(ContactQuickEntryForm, "Contact", { from_link_create: true });
	assert.equal(dialog.fields_dict.custom_account.get_value(), "Layton Builders");
	dialog.fields_dict.first_name.user_sets("Gus");
	assert.deepEqual(links_of(dialog.update_doc()), [
		["Customer", "Layton Builders"],
		["Project", "PRJ-0001"],
	]);
});

test("a stakeholder row on a Customer form links the row's party only, never the form's Customer", async () => {
	on_form("Customer", { name: "Gladson Residence" });
	create_from_row("Customer", "Gladson Residence", { party_type: "Supplier", party_name: "Stone Works" });
	const dialog = await open(ContactQuickEntryForm, "Contact", { from_link_create: true });
	dialog.fields_dict.first_name.user_sets("Sam");
	assert.deepEqual(links_of(dialog.update_doc()), [["Supplier", "Stone Works"]]);
});

test("a stale row context from a cancelled create is ignored by the directory's New Contact", async () => {
	on_form("Project", { name: "PRJ-0001", customer: "Harbor Plaza" });
	create_from_row("Project", "PRJ-0001", { party_type: "Supplier", party_name: "County Health" });
	const dialog = await open(ContactQuickEntryForm, "Contact"); // frappe.new_doc: no after_insert
	assert.equal(dialog.fields_dict.custom_account.get_value(), "Harbor Plaza");
	dialog.fields_dict.first_name.user_sets("Pat");
	assert.ok(!links_of(dialog.update_doc()).some(([dt]) => dt === "Supplier"), "read a stale _from_link");
});

test("a row context from another form is ignored", async () => {
	on_form("Project", { name: "PRJ-0002", customer: "Harbor Plaza" });
	create_from_row("Project", "PRJ-0001", { party_type: "Supplier", party_name: "County Health" });
	const dialog = await open(ContactQuickEntryForm, "Contact", { from_link_create: true });
	dialog.fields_dict.first_name.user_sets("Pat");
	assert.ok(!links_of(dialog.update_doc()).some(([dt]) => dt === "Supplier"));
});

test("a form whose own doc has party_type/party_name is not a stakeholder row", async () => {
	const pe = { name: "ACC-PAY-0001", party_type: "Customer", party_name: "State of Utah Dept (display name)" };
	on_form("Payment Entry", pe);
	create_from_form_field("Payment Entry", Object.assign({ doctype: "Payment Entry" }, pe));
	const dialog = await open(ContactQuickEntryForm, "Contact", { from_link_create: true });
	dialog.fields_dict.first_name.user_sets("Una");
	const doc = dialog.update_doc();
	assert.ok(!links_of(doc).some(([, nm]) => /display name/.test(nm)), "filed under a display name taken as a Customer ID");
	assert.ok(!doc.custom_account);
});

test("a stakeholder row with no party yet gives the job only, never the job's Customer", async () => {
	on_form("Project", { name: "PRJ-0001", customer: "Harbor Plaza" });
	create_from_row("Project", "PRJ-0001", { party_type: "Supplier", party_name: "" });
	const dialog = await open(ContactQuickEntryForm, "Contact", { from_link_create: true });
	assert.ok(!dialog.fields_dict.custom_account.get_value(), "pre-filled the job's Customer");
	dialog.fields_dict.first_name.user_sets("Ned");
	assert.deepEqual(links_of(dialog.update_doc()), [["Project", "PRJ-0001"]]);
});

// --------------------------------------------------------------- New Address

test("from a Project, the address must say whose it is: no default, required", async () => {
	on_form("Project", { name: "PRJ-0001", customer: "Harbor Plaza" });
	const dialog = await open(AddressQuickEntryForm, "Address");
	const choice = dialog.fields_dict.ee_file_under_party;
	assert.ok(choice, "no 'whose address' choice on a Project");
	assert.equal(choice.df.fieldtype, "Select");
	assert.equal(choice.df.reqd, 1, "the choice can be skipped");
	assert.ok(!choice.get_value(), "the choice has a default");
	assert.ok(choice.df.options.some((o) => /Harbor Plaza/.test(o)));
	assert.doesNotMatch(dialog.intro || "", /Harbor Plaza/, "the banner files it under the Customer before anyone chose");
});

test("'Also the Customer's' links the Customer and the Project", async () => {
	on_form("Project", { name: "PRJ-0001", customer: "Harbor Plaza" });
	const dialog = await open(AddressQuickEntryForm, "Address");
	dialog.fields_dict.address_line1.user_sets("1 Main St");
	dialog.fields_dict.ee_file_under_party.user_sets(dialog.ee_choice.also);
	assert.match(dialog.intro || "", /Harbor Plaza/);
	const doc = dialog.update_doc();
	assert.deepEqual(links_of(doc), [
		["Customer", "Harbor Plaza"],
		["Project", "PRJ-0001"],
	]);
	assert.ok(!("ee_file_under_party" in doc), "the dialog-only answer leaked onto the Address");
});

test("'This Project only' keeps a job site off the Customer", async () => {
	on_form("Project", { name: "PRJ-0001", customer: "Harbor Plaza" });
	const dialog = await open(AddressQuickEntryForm, "Address");
	dialog.fields_dict.address_line1.user_sets("Lot 12");
	dialog.fields_dict.ee_file_under_party.user_sets(dialog.ee_choice.only);
	assert.doesNotMatch(dialog.intro || "", /Harbor Plaza/);
	const doc = dialog.update_doc();
	assert.deepEqual(links_of(doc), [["Project", "PRJ-0001"]]);
	assert.ok(!("ee_file_under_party" in doc));
});

test("an Opportunity asks the same way", async () => {
	on_form("Opportunity", { name: "OPP-0001", opportunity_from: "Customer", party_name: "Harbor Plaza" });
	const dialog = await open(AddressQuickEntryForm, "Address");
	assert.equal(dialog.fields_dict.ee_file_under_party.df.reqd, 1);
	assert.ok(!dialog.fields_dict.ee_file_under_party.get_value());
});

test("from a Contact form the Contact is always kept; the company only when chosen", async () => {
	on_form("Contact", { name: "Dee Example-Harbor Plaza", custom_account: "Harbor Plaza" });
	let dialog = await open(AddressQuickEntryForm, "Address");
	assert.ok(!dialog.fields_dict.ee_file_under_party.get_value(), "the person's address defaults to their company");
	dialog.fields_dict.address_line1.user_sets("2 Side St");
	dialog.fields_dict.ee_file_under_party.user_sets(dialog.ee_choice.also);
	assert.deepEqual(links_of(dialog.update_doc()), [
		["Customer", "Harbor Plaza"],
		["Contact", "Dee Example-Harbor Plaza"],
	]);

	on_form("Contact", { name: "Dee Example-Harbor Plaza", custom_account: "Harbor Plaza" });
	dialog = await open(AddressQuickEntryForm, "Address");
	dialog.fields_dict.address_line1.user_sets("2 Side St");
	dialog.fields_dict.ee_file_under_party.user_sets(dialog.ee_choice.only);
	assert.deepEqual(links_of(dialog.update_doc()), [["Contact", "Dee Example-Harbor Plaza"]]);
});

test("an Address created in a stakeholder row is that party's (plus the job), pre-chosen", async () => {
	on_form("Project", { name: "PRJ-0001", customer: "Harbor Plaza" });
	create_from_row("Project", "PRJ-0001", { party_type: "Customer", party_name: "Layton Builders" });
	const dialog = await open(AddressQuickEntryForm, "Address", { from_link_create: true });
	const choice = dialog.fields_dict.ee_file_under_party;
	assert.equal(choice.get_value(), dialog.ee_choice.also, "the row's party is not pre-chosen");
	assert.match(dialog.ee_choice.also, /Layton Builders/);
	dialog.fields_dict.address_line1.user_sets("9 Yard Rd");
	assert.deepEqual(links_of(dialog.update_doc()), [
		["Customer", "Layton Builders"],
		["Project", "PRJ-0001"],
	]);
});

test("an Address from a stakeholder row on a Customer form is that party's, with nothing to choose", async () => {
	on_form("Customer", { name: "Gladson Residence" });
	create_from_row("Customer", "Gladson Residence", { party_type: "Supplier", party_name: "Stone Works" });
	const dialog = await open(AddressQuickEntryForm, "Address", { from_link_create: true });
	assert.ok(!dialog.fields_dict.ee_file_under_party, "asked, and could fall back to the form's own Customer");
	dialog.fields_dict.address_line1.user_sets("4 Quarry Rd");
	assert.deepEqual(links_of(dialog.update_doc()), [["Supplier", "Stone Works"]]);
});

test("New Address from a Quotation files it under the quotation's Customer and asks nothing", async () => {
	const qtn = { name: "SAL-QTN-0001", party_name: "Harbor Plaza" };
	on_form("Quotation", qtn, { doc: qtn, fieldname: "party_name", doctype: "Customer" });
	const dialog = await open(AddressQuickEntryForm, "Address", { from_link_create: true });
	assert.ok(!dialog.fields_dict.ee_file_under_party, "asked, and 'only' would save an address linked to nothing");
	dialog.fields_dict.address_line1.user_sets("5 Market St");
	assert.deepEqual(links_of(dialog.update_doc()), [["Customer", "Harbor Plaza"]]);
});

test("a stakeholder row with no party on a Supplier form is the Supplier's own person", async () => {
	on_form("Supplier", { name: "Trim Works" });
	create_from_row("Supplier", "Trim Works", { party_type: "Customer", party_name: "" });
	const dialog = await open(ContactQuickEntryForm, "Contact", { from_link_create: true });
	dialog.fields_dict.first_name.user_sets("Dave");
	assert.deepEqual(links_of(dialog.update_doc()), [["Supplier", "Trim Works"]], "a contact linked to nothing");
});

test("a stakeholder row with no party on a Customer form is the Customer's own person", async () => {
	on_form("Customer", { name: "Gladson Residence" });
	create_from_row("Customer", "Gladson Residence", { party_type: "Customer", party_name: null });
	const dialog = await open(ContactQuickEntryForm, "Contact", { from_link_create: true });
	assert.equal(dialog.fields_dict.custom_account.get_value(), "Gladson Residence");
	dialog.fields_dict.first_name.user_sets("Elena");
	assert.deepEqual(links_of(dialog.update_doc()), [["Customer", "Gladson Residence"]]);
});

test("on a Customer form there is nothing to ask: the address is the Customer's", async () => {
	on_form("Customer", { name: "Harbor Plaza" });
	const dialog = await open(AddressQuickEntryForm, "Address");
	assert.ok(!dialog.fields_dict.ee_file_under_party, "asked whether a Customer's address is the Customer's");
	dialog.fields_dict.address_line1.user_sets("3 Bay Rd");
	assert.deepEqual(links_of(dialog.update_doc()), [["Customer", "Harbor Plaza"]]);
});

test("a point typed into the coordinates is still marked Manual after render", async () => {
	on_form("Customer", { name: "Harbor Plaza" });
	const dialog = await open(AddressQuickEntryForm, "Address");
	assert.equal(typeof dialog.fields_dict.custom_latitude.df.onchange, "function", "the coordinate onchange was dropped at render");
	assert.equal(typeof dialog.fields_dict.custom_longitude.df.onchange, "function");
});

// ------------------------------------------------------- directory Link Existing

const controller = globalThis.erpnext_enhancements.unified_controller;

function directory(frm) {
	const ctx = Object.create(controller);
	ctx.frm = Object.assign({ is_new: () => false, meta: { fields: [] } }, frm);
	ctx.render_all = () => {};
	return ctx;
}

const PROJECT = {
	doctype: "Project",
	doc: {
		name: "PRJ-0001",
		customer: "Harbor Plaza",
		custom_contacts__address_table: [{ party_type: "Customer", party_name: "Layton Builders" }],
	},
	meta: { fields: [{ fieldname: "custom_contacts__address_table", fieldtype: "Table" }] },
};

test("Link Existing links the open Project only, unless the box is ticked", () => {
	const ctx = directory(PROJECT);
	CALLS.length = 0;
	ctx.link_existing_record("Contact");
	const box = PROMPT.fields.find((f) => f.fieldname === "also_party");
	assert.ok(box, "no opt-in for the project's Customer");
	assert.equal(box.default, 0, "the opt-in is ticked by default");
	assert.match(box.label, /Harbor Plaza/);
	assert.ok(!PROMPT.fields.some((f) => /Layton/.test(f.label || "")), "a stakeholder is offered");
	PROMPT.callback({ record: "C-1", also_party: 0 });
	assert.deepEqual(CALLS, [{ doctype: "Contact", docname: "C-1", link_doctype: "Project", link_name: "PRJ-0001" }]);
});

test("ticked, it makes a second single-link call for the project's Customer only", () => {
	const ctx = directory(PROJECT);
	CALLS.length = 0;
	ctx.link_existing_record("Contact");
	PROMPT.callback({ record: "C-1", also_party: 1 });
	assert.deepEqual(CALLS, [
		{ doctype: "Contact", docname: "C-1", link_doctype: "Project", link_name: "PRJ-0001" },
		{ doctype: "Contact", docname: "C-1", link_doctype: "Customer", link_name: "Harbor Plaza" },
	]);
});

test("if the Customer half fails, the directory still refreshes and says which half failed", () => {
	const ctx = directory(PROJECT);
	let rendered = 0;
	ctx.render_all = () => (rendered += 1);
	CALLS.length = 0;
	ALERTS.length = 0;
	FAIL_ON = "Customer";
	try {
		ctx.link_existing_record("Contact");
		PROMPT.callback({ record: "C-1", also_party: 1 });
	} finally {
		FAIL_ON = null;
	}
	assert.equal(rendered, 1, "the saved Project link is not shown");
	assert.match(ALERTS.at(-1).message, /not added to Harbor Plaza/);
});

test("a Master Project offers no Customer opt-in (it has no customer field)", () => {
	const ctx = directory({ doctype: "Master Project", doc: { name: "MP-0001" } });
	ctx.link_existing_record("Contact");
	assert.ok(!PROMPT.fields.some((f) => f.fieldname === "also_party"));
});

test("on a Customer form Link Existing links the Customer and offers nothing else", () => {
	const ctx = directory({ doctype: "Customer", doc: { name: "Harbor Plaza" } });
	CALLS.length = 0;
	ctx.link_existing_record("Contact");
	assert.ok(!PROMPT.fields.some((f) => f.fieldname === "also_party"));
	PROMPT.callback({ record: "C-1" });
	assert.deepEqual(CALLS, [{ doctype: "Contact", docname: "C-1", link_doctype: "Customer", link_name: "Harbor Plaza" }]);
});

test("Link Existing does nothing on an unsaved form", () => {
	const ctx = directory({ doctype: "Project", doc: { name: "new-project-1" }, is_new: () => true });
	CALLS.length = 0;
	PROMPT = null;
	ctx.link_existing_record("Contact");
	assert.equal(PROMPT, null);
	assert.deepEqual(CALLS, []);
});

test("a Project's directory shows the people on the deal it came from", () => {
	const ctx = directory({ doctype: "Project", doc: { name: "PRJ-0001", customer: "Harbor Plaza", custom_opportunity: "OPP-0001" } });
	const sources = ctx.get_all_party_sources();
	assert.ok(sources.some((s) => s.doctype === "Opportunity" && s.name === "OPP-0001"));
	assert.equal(controller.get_base_links, undefined, "the fan-out helper is back");
});

// ------------------------------------------------------------------- runner

let failures = 0;
for (const { name, fn } of TESTS) {
	try {
		await fn();
		console.log("  ok    " + name);
	} catch (err) {
		failures += 1;
		console.error("  FAIL  " + name);
		console.error("        " + (err && err.message ? err.message : err));
	}
}
console.log(`\n${TESTS.length - failures}/${TESTS.length} passed`);
process.exit(failures ? 1 : 0);
