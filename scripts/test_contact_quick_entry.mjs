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
 *   6. frappe v16.50.0 defines its own ContactQuickEntryForm / AddressQuickEntryForm
 *      (utils/address_and_contact.js, form.bundle.js) before this app's bundle loads. The
 *      dialogs used to step aside when those existed, with the contacts_ux helpers defined
 *      after that check, so on prod none of 1-4 ran and the directory's New Contact / New
 *      Address threw a TypeError. Ours must be the classes make_quick_entry finds, must not
 *      inherit frappe's phone_nos / email_ids fields, must hand the toggle-off path to
 *      frappe's own class, and the helpers must exist before anything can return early.
 *
 * The QuickEntryForm stub follows frappe v16 where it matters (quick_entry.js, field_group.js,
 * layout.js attach_doc_and_docfields): controls get the per-doc meta df after build, defaults
 * arrive asynchronously through set_value (firing onchange), get_values() leaves empty fields
 * out, update_doc() copies only non-empty values, and a user's edit writes the doc model.
 * make_quick_entry resolves the class by name when it is called, as quick_entry.js does.
 *
 * The suite runs twice. The default run models frappe v16.50.0 (what prod runs since
 * 2026-10-06): frappe's own Contact/Address classes, modelled on address_and_contact.js:4-79,
 * are registered BEFORE this app's file loads, and Contact/Address carry quick_entry = 1. It
 * then re-runs itself as v16.36.1 (no frappe classes, quick_entry = 0), the shape the dialogs
 * were written against. Every name here is invented.
 * Run: node scripts/test_contact_quick_entry.mjs
 */

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DIR = path.join(HERE, "..", "erpnext_enhancements", "public", "js", "global_enhancements");
const QUICK_ENTRY_JS = path.join(DIR, "contact_address_quick_entry.js");
const CONTROLLER_JS = path.join(DIR, "unified_tab_controller.js");

/** Which frappe the stub models: v16.50.0 by default, v16.36.1 in the re-run. */
const SHAPE = process.env.EE_QE_FRAPPE_SHAPE || "v16.50.0";
const FRAPPE_DIALOGS = SHAPE === "v16.50.0";

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

/** Contact/Address meta.quick_entry: 1 from v16.50.0 (contact.json, address.json), 0 before. */
const META_QUICK_ENTRY = FRAPPE_DIALOGS ? 1 : 0;
/** set_meta_and_mandatory_fields: meta fields that are reqd or allow_in_quick_entry. */
const QUICK_ENTRY_FIELDS = FRAPPE_DIALOGS
	? {
			Contact: ["first_name", "last_name", "designation", "company_name"],
			Address: ["address_title", "address_line1", "city", "state", "pincode", "country", "address_type"],
		}
	: { Contact: [], Address: ["address_title", "address_line1", "city", "country", "address_type"] };
const ROUTED = [];
const ROUTED_DOCS = [];
const INSERTED = [];

class QuickEntryForm {
	constructor(doctype, after_insert, init_callback, doc) {
		this.doctype = doctype;
		this.after_insert = after_insert;
		this.init_callback = init_callback;
		this.doc = doc || { doctype, __islocal: 1, name: "new-" + doctype.toLowerCase() + "-1" };
		this.dialog = this;
		this.intro = null;
	}
	/** quick_entry.js setup(): the dialog, or the full form with init_callback(doc). */
	setup() {
		this.docfields = (QUICK_ENTRY_FIELDS[this.doctype] || []).map((f) => ({ fieldname: f, fieldtype: "Data" }));
		if (this.is_quick_entry()) {
			this.render_dialog();
		} else {
			frappe.quick_entry = null;
			ROUTED.push(["Form", this.doctype, this.doc.name]);
			ROUTED_DOCS.push(this.doc);
			if (this.init_callback) this.init_callback(this.doc);
		}
		return Promise.resolve(this);
	}
	is_quick_entry() {
		return META_QUICK_ENTRY === 1 && (this.docfields || []).length > 0;
	}
	/** insert(): update_doc(), then frappe.client.save of this.doc (recorded, not sent). */
	insert() {
		this.update_doc();
		INSERTED.push(JSON.parse(JSON.stringify(this.doc)));
		return Promise.resolve(this.doc);
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
		if (this.init_callback) this.init_callback(this);
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
}

/**
 * frappe v16.50.0's own classes (frappe/public/js/frappe/utils/address_and_contact.js:4-79),
 * modelled on the stub above. Contact extends Address there too.
 */
const UPSTREAM = {};
if (FRAPPE_DIALOGS) {
	UPSTREAM.Address = class AddressQuickEntryForm extends QuickEntryForm {
		insert() {
			if (this.source_frm) {
				this.dialog.doc.links = [{ link_doctype: this.source_frm.doctype, link_name: this.source_frm.docname }];
			}
			return super.insert();
		}
		open_form_if_not_list() {
			this.source_frm.reload_doc();
		}
	};
	UPSTREAM.Contact = class ContactQuickEntryForm extends UPSTREAM.Address {
		render_dialog() {
			const fields = this.get_detail_fields().map(({ table, value_field, primary_flag, ...field }) => field);
			this.docfields = this.docfields.concat({ fieldtype: "Column Break" }, ...fields);
			super.render_dialog();
		}
		update_doc() {
			const doc = super.update_doc();
			for (const { fieldname, table, value_field, primary_flag } of this.get_detail_fields()) {
				const value = doc[fieldname];
				delete doc[fieldname];
				if (!value) continue;
				doc[table] = doc[table] || [];
				doc[table].push({ [value_field]: value, [primary_flag]: 1 });
			}
			return doc;
		}
		get_detail_fields() {
			return [
				{ fieldname: "contact_phone", fieldtype: "Data", table: "phone_nos", value_field: "phone", primary_flag: "is_primary_phone" },
				{ fieldname: "contact_mobile_no", fieldtype: "Data", table: "phone_nos", value_field: "phone", primary_flag: "is_primary_mobile_no" },
				{ fieldname: "contact_email", fieldtype: "Data", table: "email_ids", value_field: "email_id", primary_flag: "is_primary" },
			];
		}
	};
}

let ROUTE = [];
let RELOADED = 0;
const FORMS = {};
const CALLS = [];
let PROMPT = null;
let FAIL_ON = null;
const ALERTS = [];

globalThis.window = globalThis;
globalThis.__ = (text, args) => String(text).replace(/\{(\d+)\}/g, (_, i) => (args && args[i] !== undefined ? args[i] : ""));
globalThis.cint = (v) => parseInt(v, 10) || 0;
/** jQuery, as far as the directory's button rows use it: chainable, with click handlers kept. */
const CLICKS = [];
globalThis.$ = (html) => {
	const node = {};
	["one", "empty", "html", "append", "appendTo", "find", "remove"].forEach((m) => (node[m] = () => node));
	node.on = (event, fn) => {
		if (event === "click") CLICKS.push({ html: String(html), fn });
		return node;
	};
	return node;
};
const click = (label) => {
	const button = CLICKS.filter((c) => c.html.includes(`>${label}<`)).at(-1);
	assert.ok(button, `no '${label}' button was rendered`);
	button.fn();
};
const NEW_DOCS = [];
globalThis.erpnext_enhancements = {};
globalThis.frappe = {
	provide: (ns) => {
		let obj = globalThis;
		ns.split(".").forEach((part) => {
			obj[part] = obj[part] || {};
			obj = obj[part];
		});
	},
	ui: {
		form: Object.assign(
			{
				QuickEntryForm,
				on: () => {},
				// quick_entry.js: the class is looked up by name when this is called.
				make_quick_entry: (doctype, after_insert, init_callback, doc, force, skip_insert) => {
					const name = doctype.replace(/ /g, "") + "QuickEntryForm";
					const Dialog = frappe.ui.form[name] || frappe.ui.form.QuickEntryForm;
					frappe.quick_entry = new Dialog(doctype, after_insert, init_callback, doc, force, skip_insert);
					return frappe.quick_entry.setup();
				},
			},
			// form.bundle.js loads frappe's classes before any app bundle.
			FRAPPE_DIALOGS ? { AddressQuickEntryForm: UPSTREAM.Address, ContactQuickEntryForm: UPSTREAM.Contact } : {}
		),
	},
	/** create_new.js: no create_routes here, so straight to make_quick_entry. */
	new_doc: (doctype, opts, init_callback) => {
		NEW_DOCS.push({ doctype, init_callback });
		return frappe.ui.form.make_quick_entry(doctype, null, init_callback);
	},
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
		// The directory's own list fetches: nothing linked yet.
		if (/_for_context$/.test(opts.method || "")) {
			if (opts.callback) opts.callback({ message: [] });
			return;
		}
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
const ux = globalThis.erpnext_enhancements.contacts_ux;

/** Put the user on a saved form, the way the Desk route and formview cache would. */
function on_form(doctype, doc, dynamic_link = null) {
	ROUTE = ["Form", doctype, doc.name];
	for (const key of Object.keys(FORMS)) delete FORMS[key];
	FORMS[doctype] = { frm: { doctype, docname: doc.name, doc, is_new: () => false, reload_doc: () => (RELOADED += 1) } };
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

// ------------------------------------- frappe's own dialogs (v16.50.0) and the toggle

test("ours are the classes make_quick_entry finds, built on the base QuickEntryForm", () => {
	assert.equal(frappe.ui.form.ContactQuickEntryForm, ContactQuickEntryForm);
	assert.ok(ContactQuickEntryForm.prototype instanceof QuickEntryForm);
	assert.ok(AddressQuickEntryForm.prototype instanceof QuickEntryForm);
	if (FRAPPE_DIALOGS) {
		assert.notEqual(ContactQuickEntryForm, UPSTREAM.Contact, "frappe's own Contact dialog is still registered");
		assert.notEqual(AddressQuickEntryForm, UPSTREAM.Address, "frappe's own Address dialog is still registered");
		assert.ok(!(ContactQuickEntryForm.prototype instanceof UPSTREAM.Address), "ours inherits frappe's dialog");
		assert.ok(!(AddressQuickEntryForm.prototype instanceof UPSTREAM.Address), "ours inherits frappe's dialog");
	}
	assert.equal(ContactQuickEntryForm.ee_stock, FRAPPE_DIALOGS ? UPSTREAM.Contact : null);
	assert.equal(AddressQuickEntryForm.ee_stock, FRAPPE_DIALOGS ? UPSTREAM.Address : null);
});

test("the directory's New Contact opens our dialog: Account, custom_* fields, Customer first", async () => {
	const customer = { name: "Harbor Plaza" };
	on_form("Customer", customer);
	const frm = FORMS.Customer.frm;
	INSERTED.length = 0;
	RELOADED = 0;
	ux.new_contact(frm);
	await tick();
	const dialog = frappe.quick_entry;
	assert.ok(dialog instanceof ContactQuickEntryForm, "make_quick_entry did not build ours");
	assert.equal(dialog.fields_dict.custom_account.get_value(), "Harbor Plaza", "Account not pre-filled");
	for (const stock of ["contact_phone", "contact_mobile_no", "contact_email", "designation", "company_name"]) {
		assert.ok(!dialog.fields_dict[stock], `frappe's '${stock}' field is in the dialog`);
	}
	dialog.fields_dict.first_name.user_sets("Dee");
	dialog.fields_dict.custom_email.user_sets("dee@example.com");
	dialog.fields_dict.custom_phone_number.user_sets("555-0100");
	await dialog.insert();
	const saved = INSERTED.at(-1);
	assert.deepEqual(links_of(saved), [["Customer", "Harbor Plaza"]]);
	assert.equal(saved.custom_email, "dee@example.com");
	assert.equal(saved.custom_phone_number, "555-0100");
	assert.ok(!saved.email_ids && !saved.phone_nos, "wrote the tables this site hides");
	assert.equal(RELOADED, 0, "reloaded the source form (discards its unsaved edits)");
});

test("the directory's New Address opens our dialog and asks whose it is", async () => {
	on_form("Project", { name: "PRJ-0001", customer: "Harbor Plaza" });
	ux.new_address(FORMS.Project.frm);
	await tick();
	const dialog = frappe.quick_entry;
	assert.ok(dialog instanceof AddressQuickEntryForm, "make_quick_entry did not build ours");
	assert.equal(dialog.fields_dict.ee_file_under_party.df.reqd, 1);
	dialog.fields_dict.address_line1.user_sets("Lot 12");
	dialog.fields_dict.ee_file_under_party.user_sets(dialog.ee_choice.only);
	INSERTED.length = 0;
	await dialog.insert();
	assert.deepEqual(links_of(INSERTED.at(-1)), [["Project", "PRJ-0001"]]);
});

test("toggle off: frappe's own create flow, still linked to the open form", async () => {
	on_form("Project", { name: "PRJ-0001", customer: "Harbor Plaza" });
	const frm = FORMS.Project.frm;
	ROUTED.length = 0;
	INSERTED.length = 0;
	frappe.boot.ee_contacts_ux = 0;
	try {
		ux.new_contact(frm);
		await tick();
		if (FRAPPE_DIALOGS) {
			const dialog = frappe.quick_entry;
			assert.ok(dialog instanceof UPSTREAM.Contact, "toggle off did not open frappe's own dialog");
			assert.ok(!(dialog instanceof ContactQuickEntryForm));
			assert.ok(dialog.fields_dict.contact_phone, "frappe's dialog lost its own fields");
			assert.equal(dialog.source_frm, frm, "frappe's dialog was not told the open form");
			dialog.fields_dict.first_name.user_sets("Off");
			await dialog.insert();
			assert.deepEqual(links_of(INSERTED.at(-1)), [["Project", "PRJ-0001"]]);
		} else {
			assert.deepEqual(ROUTED.at(-1), ["Form", "Contact", "new-contact-1"], "toggle off did not open the full form");
			assert.equal(frappe.quick_entry, null);
		}
		// Whatever frappe builds, a form object never lands on the doc (it could not be saved).
		const doc = FRAPPE_DIALOGS ? frappe.quick_entry.doc : ROUTED_DOCS.at(-1);
		assert.ok(doc && !("source_frm" in doc), "source_frm was set on the new doc");
	} finally {
		frappe.boot.ee_contacts_ux = 1;
	}
});

test("the directory's buttons work, and never throw when the helpers are missing", async () => {
	const ctx = directory({ doctype: "Customer", doc: { name: "Harbor Plaza" }, fields_dict: { contact_list_html: {}, address_list_html: {} } });
	on_form("Customer", { name: "Harbor Plaza" });
	CLICKS.length = 0;
	NEW_DOCS.length = 0;
	ctx.render_contact_table();
	ctx.render_address_table();
	click("New Contact");
	click("New Address");
	assert.deepEqual(NEW_DOCS.map((n) => n.doctype), ["Contact", "Address"]);
	assert.equal(typeof NEW_DOCS[0].init_callback, "function", "the helper was bypassed");

	const saved = globalThis.erpnext_enhancements.contacts_ux;
	NEW_DOCS.length = 0;
	try {
		delete globalThis.erpnext_enhancements.contacts_ux;
		click("New Contact");
		click("New Address");
		globalThis.erpnext_enhancements.contacts_ux = {};
		click("New Contact");
		click("New Address");
	} finally {
		globalThis.erpnext_enhancements.contacts_ux = saved;
	}
	assert.deepEqual(NEW_DOCS.map((n) => n.doctype), ["Contact", "Address", "Contact", "Address"]);
	await tick();
});

test("the helpers exist before the dialogs can register; a second load keeps frappe's class", () => {
	let queued = null;
	const sandbox = {
		cint: globalThis.cint,
		__: globalThis.__,
		$: () => ({ one: (event, fn) => (queued = event === "app_ready" ? fn : queued) }),
	};
	sandbox.window = sandbox;
	sandbox.document = {};
	sandbox.frappe = {
		provide: (ns) => {
			let obj = sandbox;
			ns.split(".").forEach((part) => {
				obj[part] = obj[part] || {};
				obj = obj[part];
			});
		},
		ui: { form: {} },
		boot: { ee_contacts_ux: 1 },
	};
	vm.createContext(sandbox);
	const source = fs.readFileSync(QUICK_ENTRY_JS, "utf8");
	vm.runInContext(source, sandbox, { filename: QUICK_ENTRY_JS });
	const helpers = sandbox.erpnext_enhancements.contacts_ux;
	for (const name of ["new_contact", "new_address", "refresh_linked_sources", "refresh_directory_surfaces", "enabled"]) {
		assert.equal(typeof helpers[name], "function", `contacts_ux.${name} is not defined before the dialogs register`);
	}
	assert.equal(typeof queued, "function", "registration was not deferred to app_ready");

	Object.assign(sandbox.frappe.ui.form, { QuickEntryForm }, FRAPPE_DIALOGS ? { ContactQuickEntryForm: UPSTREAM.Contact, AddressQuickEntryForm: UPSTREAM.Address } : {});
	queued();
	const first = sandbox.frappe.ui.form.ContactQuickEntryForm;
	assert.notEqual(first, UPSTREAM.Contact || null);
	assert.equal(first.ee_stock, FRAPPE_DIALOGS ? UPSTREAM.Contact : null);
	vm.runInContext(source, sandbox, { filename: QUICK_ENTRY_JS });
	const second = sandbox.frappe.ui.form.ContactQuickEntryForm;
	assert.notEqual(second, first);
	assert.equal(second.ee_stock, FRAPPE_DIALOGS ? UPSTREAM.Contact : null, "a reload took our own class as frappe's");
});

// ------------------------------------------------------------------- runner

console.log(`frappe ${SHAPE} shape${FRAPPE_DIALOGS ? " (frappe's own Contact/Address dialogs present)" : ""}`);
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
console.log(`\n${TESTS.length - failures}/${TESTS.length} passed (frappe ${SHAPE} shape)`);

// The same suite against frappe v16.36.1's shape, in a fresh process (the stubs are globals).
let child_failed = false;
if (!process.env.EE_QE_FRAPPE_SHAPE) {
	console.log("");
	const run = spawnSync(process.execPath, [fileURLToPath(import.meta.url)], {
		env: Object.assign({}, process.env, { EE_QE_FRAPPE_SHAPE: "v16.36.1" }),
		stdio: "inherit",
	});
	child_failed = run.status !== 0;
}
process.exit(failures || child_failed ? 1 : 0);
