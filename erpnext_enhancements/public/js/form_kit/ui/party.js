/*
 * form kit P3.2 (ERPNext TASK-2026-02755): the Party entry component. The information blocks' Party
 * and Site entry face (form_kit/blocks.py, public/js/info_blocks/blocks.js): the account, the person
 * we deal with, the billing address and the job site, each found among the account's own records or
 * made on the spot and linked to it.
 *
 * Built once, in the record editor both shells run (./editor.js): the guided page and the board's
 * side panel get it the same way. The editor makes one PartyEntry and calls three things:
 *
 *   reset()                          a record (or a new one) was loaded
 *   decorate(step, editable)         a step's fields were drawn: add "+ New" to the party fields
 *   changed(fieldname, before, now)  the person changed a field (editor.field_changed)
 *
 * What it does, field by field, by the roles the blocks give each field (form_kit/party.js):
 *
 *   - Account (Opportunity's party_name, typed by opportunity_from). Frappe's own search. "+ New
 *     account" opens the app's Customer quick entry (party_quick_entry.js) and picks what it made.
 *     A Lead or a Prospect has no quick entry on this site, so its "+ New" opens that DocType's form
 *     in a new tab and says so: pick it here once it is saved.
 *   - Contact, billing address, site. The search is already the account's own (fields.js
 *     link_query, ERPNext's contact_query / address_query). "+ New" opens the app's Contact or
 *     Address quick entry (contact_address_quick_entry.js, contacts_ux.create_for) with the record
 *     as its context, so the new Contact or Address is linked to the account (and to the record,
 *     as the Opportunity form's own New Contact does) and then picked. The Address dialog still asks
 *     "Whose address is this?" (the 2026-10-06 rule: a job site may be a general contractor's, not
 *     the account's); for the billing address it starts on "Also <account>'s address", for the site
 *     it has no default. A site address starts as type Shipping, which is what the next deal's site
 *     suggestion reads. Frappe's own "Create a new Contact" in the field's dropdown takes the same
 *     path (the control's new_doc is replaced), so no record is ever made unlinked from here.
 *   - When the account changes on a saved record, party_details says which of the current
 *     contact and addresses are the new account's, and what the account would suggest
 *     (form_kit/party.py, ERPNext's own defaults). plan_account_change: an EMPTY field takes the
 *     account's default; a stored value is never overwritten or emptied. One of the new account's,
 *     or linked to the record itself (a general contractor's job site), just stays; any other stays
 *     and is flagged under its field ("not one of <account>'s", with "Use <suggestion>" one tap
 *     away). On a new record nothing is suggested here: the contact and addresses are on later
 *     steps, and the insert's own Opportunity.map_fields fills them from the account.
 *
 * Phones and emails typed into the dialogs are normalized by the server on insert
 * (utils/normalize_hook.normalize_doc, "*" before_validate), exactly once; nothing here reformats
 * them. ERPNext's hidden contact_person follows Primary Contact on the server
 * (crm_enhancements/party_mirrors.py), and save_step reports it back.
 *
 * Back and Forward (Nik's rule, 2026-09-24). A quick-entry dialog is a frappe Dialog, which frappe's
 * router closes on any route change; on its own, Back would close it AND leave the step (or close the
 * side panel). So the dialog joins the planner kit's history guard (planner_kit/history.js) as an
 * overlay: Back closes the dialog and nothing else; Save or Cancel steps back off the guard's entry.
 * The guided page loads planner_kit.bundle.js the first time a dialog opens (the side panel's page
 * has it already). Where the guard cannot work (no window.event during popstate), Back closes the
 * dialog and moves back, frappe's own behavior.
 *
 * Browser only: it draws with jQuery and frappe. Its decisions are ../party.js, pure.
 */

import { BLOCKS } from "../../info_blocks/blocks.js";
import {
	party_roles,
	role_of,
	account_of,
	create_kind,
	details_args,
	dialog_of,
	plan_account_change,
	same_details_args,
} from "../party.js";

const API = "erpnext_enhancements.form_kit.api.";
// How long the guided page waits for planner_kit.bundle.js before opening a dialog without it.
const GUARD_WAIT_MS = 4000;
// "+ New" is held while a dialog opens; after this long it is let go whatever happened (frappe's
// with_doctype never calls back when its request fails, so the promise may never settle).
const OPEN_WAIT_MS = 8000;

// The "+ New" label per kind of record. Customer is "Account" on this site (a label only).
function new_label(doctype) {
	if (doctype === "Customer") return __("New account");
	if (doctype === "Contact") return __("New contact");
	if (doctype === "Address") return __("New address");
	return __("New {0}", [__(doctype)]);
}

function can_create(doctype) {
	try {
		return !!(frappe.model && frappe.model.can_create && frappe.model.can_create(doctype));
	} catch (e) {
		return false;
	}
}

export class PartyEntry {
	constructor(editor) {
		this.ed = editor;
		this.roles = null;
		this.flags = {};
		this.seq = 0;
	}

	// A record (or a new one) was loaded: the roles of its DocType, and no notes yet.
	reset() {
		this.roles = this.ed.doctype ? party_roles(BLOCKS, this.ed.doctype) : null;
		this.flags = {};
		this.seq += 1;
	}

	same_record(doctype, name) {
		return this.ed.doctype === doctype && (this.ed.name || null) === (name || null);
	}

	// ------------------------------------------------------------------ drawing

	decorate(step, editable) {
		if (!this.roles || !this.ed.fg) return;
		(step.fields || []).forEach((field) => {
			const role = role_of(this.roles, field.fieldname);
			if (!role || role === "account_type") return;
			const control = this.ed.fg.fields_dict[field.fieldname];
			if (!control || !control.$wrapper) return;
			control.$wrapper.addClass("fk-party-field");
			if (editable && !field.read_only) {
				// Frappe's own "Create a new …" in the dropdown comes here too: never an unlinked record.
				control.new_doc = () => {
					this.create(field.fieldname);
					return false;
				};
				this.draw_tools(field.fieldname);
			}
			this.draw_flag(field.fieldname);
		});
	}

	control(fieldname) {
		return this.ed.fg && this.ed.fg.fields_dict ? this.ed.fg.fields_dict[fieldname] : null;
	}

	// The "+ New" button under a party field; redrawn when the account's kind changes.
	draw_tools(fieldname) {
		const control = this.control(fieldname);
		if (!control || !control.$wrapper) return;
		control.$wrapper.find(".fk-party-tools").remove();
		const kind = create_kind(this.roles, fieldname, this.ed.current_values());
		if (!kind || !can_create(kind.doctype)) return;
		const label = new_label(kind.doctype);
		const $tools = $('<div class="fk-party-tools"></div>');
		$('<button type="button" class="sf-btn sf-btn--quiet fk-party-new"></button>')
			.text(`+ ${label}`)
			.attr("aria-label", __("{0} for {1}", [label, __((this.ed.defs[fieldname] || {}).label || fieldname)]))
			.on("click", (event) => {
				event.preventDefault();
				this.create(fieldname);
			})
			.appendTo($tools);
		const $group = control.$wrapper.find(".form-group").first();
		($group.length ? $group : control.$wrapper).append($tools);
	}

	// "Not one of <account>'s": a stored value the new account does not have, kept, never overwritten.
	draw_flag(fieldname) {
		const control = this.control(fieldname);
		if (!control || !control.$wrapper) return;
		control.$wrapper.find(".fk-party-flag").remove();
		const flag = this.flags[fieldname];
		if (!flag) return;
		const what = role_of(this.roles, fieldname) === "contact" ? __("contacts") : __("addresses");
		const $flag = $('<div class="fk-party-flag" role="status"></div>');
		$('<p class="fk-party-flag-text"></p>')
			.text(__("Kept as it was, but it is not one of {0}'s {1}.", [flag.account, what]))
			.appendTo($flag);
		if (flag.suggestion && this.ed.flow && this.ed.flow.can_write) {
			$('<button type="button" class="sf-btn sf-btn--secondary fk-party-use"></button>')
				.text(__("Use {0}", [flag.suggestion]))
				.on("click", (event) => {
					event.preventDefault();
					this.select(fieldname, flag.suggestion);
				})
				.appendTo($flag);
		}
		const $group = control.$wrapper.find(".form-group").first();
		($group.length ? $group : control.$wrapper).append($flag);
	}

	// ------------------------------------------------------------------ changes

	changed(fieldname, before, value) {
		const role = role_of(this.roles, fieldname);
		if (!role) return;
		if (role === "account_type") {
			if (this.roles.account) this.draw_tools(this.roles.account);
			return;
		}
		if (role === "account") {
			this.account_changed(value);
			return;
		}
		// The contact or an address, changed by the person: a note about the old value no longer applies.
		if (this.flags[fieldname]) {
			delete this.flags[fieldname];
			this.draw_flag(fieldname);
		}
	}

	account_changed(value, retries) {
		const seq = ++this.seq;
		// Notes about the previous account no longer apply (redrawn where they are on screen).
		const flagged = Object.keys(this.flags);
		this.flags = {};
		flagged.forEach((fieldname) => this.draw_flag(fieldname));
		if (!value || this.ed.is_new || !this.ed.name || !this.ed.flow) return;
		const party = account_of(this.roles, this.ed.values);
		if (!party) return;
		const doctype = this.ed.doctype;
		const name = this.ed.name;
		const current = details_args(this.roles, this.ed.values);
		this.ed.sf
			.call(
				API + "party_details",
				{
					doctype,
					// The record: a contact or address linked to it (a general contractor's job site) is kept.
					name,
					party_type: party.doctype,
					party: party.name,
					contacts: JSON.stringify(current.contacts),
					addresses: JSON.stringify(current.addresses),
				},
				{ quiet: true, method: "GET" }
			)
			.then((details) => {
				const now = account_of(this.roles, this.ed.values);
				if (seq !== this.seq || !this.same_record(doctype, name) || !now || now.name !== party.name) return;
				// The answer is about the values sent: one changed meanwhile is asked about again.
				if (!same_details_args(current, details_args(this.roles, this.ed.values))) {
					if ((retries || 0) < 2) this.account_changed(value, (retries || 0) + 1);
					return;
				}
				this.apply(party, details || {});
			})
			.catch(() => {
				if (seq !== this.seq) return;
				this.ed.sf.toast(__("Could not check {0}'s contacts and addresses. Check them on the next steps.", [party.name]), {
					tone: "warning",
					timeout: 8000,
				});
			});
	}

	// The fields the person may change anywhere in the flow.
	editable() {
		const out = new Set();
		((this.ed.flow && this.ed.flow.steps) || []).forEach((step) => {
			if (!step.read_only) this.ed.kit.editable_fieldnames(step).forEach((fieldname) => out.add(fieldname));
		});
		return out;
	}

	apply(party, details) {
		const plan = plan_account_change({
			roles: this.roles,
			values: this.ed.values,
			details,
			editable: this.ed.flow.can_write ? this.editable() : new Set(),
		});
		Object.keys(plan.set).forEach((fieldname) => {
			this.ed.set_value_local(fieldname, plan.set[fieldname]);
			this.ed.show_value(fieldname, plan.set[fieldname]);
		});
		plan.kept.forEach((item) => {
			this.flags[item.fieldname] = { account: party.name, suggestion: item.suggestion };
			this.draw_flag(item.fieldname);
		});
		const label = (fieldname) => __((this.ed.defs[fieldname] || {}).label || fieldname);
		const followed = Object.keys(plan.set).map(label).join(", ");
		const said = [];
		if (followed) said.push(__("Filled from {0}: {1}.", [party.name, followed]));
		if (plan.kept.length) {
			said.push(
				__("Kept the {0}, which is not {1}'s: check it.", [
					plan.kept.map((item) => label(item.fieldname)).join(", "),
					party.name,
				])
			);
		}
		if (said.length) {
			this.ed.sf.toast(said.join(" "), {
				tone: plan.kept.length ? "warning" : "info",
				timeout: plan.kept.length ? 10000 : 6000,
			});
		}
	}

	// Put `name` in a party field as if the person had picked it: through the control when it is on
	// screen (its onchange is the editor's field_changed), else straight into the values.
	select(fieldname, name) {
		const control = this.control(fieldname);
		if (control && control.set_value && this.ed.defs[fieldname]) {
			if (control.$input) control.$input._created_new_doc = true; // the link's search cache is stale now
			control.set_value(name);
			return;
		}
		this.ed.set_value_local(fieldname, name);
		this.changed(fieldname, null, name);
	}

	// ------------------------------------------------------------------ "+ New"

	create(fieldname) {
		const values = this.ed.current_values();
		const kind = create_kind(this.roles, fieldname, values);
		if (!kind) {
			this.ed.sf.toast(__("Choose what kind of account it is first."), { tone: "warning" });
			return;
		}
		const control = this.control(fieldname);
		let typed = "";
		try {
			typed = String((control && control.$input && control.$input.val()) || "").trim();
		} catch (e) {
			typed = "";
		}
		const doctype = this.ed.doctype;
		const name = this.ed.is_new ? null : this.ed.name;
		const after_insert = (doc) => {
			if (!doc || !doc.name || !this.same_record(doctype, name)) return;
			this.select(fieldname, doc.name);
		};
		if (kind.role === "account") {
			this.create_account(kind.doctype, typed, after_insert);
			return;
		}
		if (!account_of(this.roles, values)) {
			this.ed.sf.toast(__("Choose the account first: a new {0} is linked to it.", [__(kind.doctype)]), { tone: "warning" });
			return;
		}
		const record = Object.assign({}, values, { doctype, name });
		const ux = window.erpnext_enhancements && erpnext_enhancements.contacts_ux;
		if (!ux || typeof ux.create_for !== "function") {
			this.ed.sf.toast(__("New contacts and addresses are made on the full form for now."), { tone: "warning" });
			return;
		}
		this.open_dialog(() =>
			ux.create_for(kind.doctype, record, {
				after_insert,
				typed,
				// The billing address is the account's by definition; the site may not be (see the head).
				prefer_party: kind.role === "billing",
				address_type: kind.role === "site" ? "Shipping" : null,
			})
		);
	}

	create_account(doctype, typed, after_insert) {
		if (doctype !== "Customer") {
			// No quick entry for a Lead or Prospect here: its own form, in its own tab, so nothing on
			// this page is lost.
			const href = frappe.utils.get_form_link(doctype, "new");
			window.open(href, "_blank", "noopener");
			this.ed.sf.toast(__("A new {0} opens in its own tab. Save it there, then pick it here.", [__(doctype)]), {
				tone: "info",
				timeout: 10000,
			});
			return;
		}
		this.open_dialog(
			() =>
				new Promise((resolve) => {
					frappe.model.with_doctype(doctype, () => {
						const doc = frappe.model.get_new_doc(doctype, null, null, true);
						if (typed) doc.customer_name = typed;
						frappe._from_link = null;
						// A refused insert keeps the dialog open rather than opening the full form over this
						// page (quick_entry.js insert's error path).
						const keep_open = (dialog) => {
							if (dialog && typeof dialog === "object" && dialog.$wrapper) dialog.skip_redirect_on_error = true;
						};
						resolve(frappe.ui.form.make_quick_entry(doctype, after_insert, keep_open, doc));
					});
				})
		);
	}

	// ------------------------------------------------------------------ Back closes the dialog

	// planner_kit's history guard, loading the kit on the guided page the first time; null when it
	// cannot be had in time or cannot work here.
	guard() {
		const have = () => (window.planner_kit && window.planner_kit.guard) || null;
		if (have()) return Promise.resolve(have());
		return new Promise((resolve) => {
			const timer = setTimeout(() => resolve(have()), GUARD_WAIT_MS);
			try {
				frappe.require("planner_kit.bundle.js", () => {
					clearTimeout(timer);
					resolve(have());
				});
			} catch (e) {
				clearTimeout(timer);
				resolve(null);
			}
		});
	}

	open_dialog(open) {
		if (this.opening) return;
		// Held against a double tap, and always let go: when the promise settles, or after OPEN_WAIT_MS
		// if it never does (a failed with_doctype request never calls back).
		const token = {};
		this.opening = token;
		const release = () => {
			if (this.opening === token) this.opening = null;
		};
		const timer = setTimeout(release, OPEN_WAIT_MS);
		this.guard()
			.then((guard) => Promise.resolve(open()).then((dialog) => this.track(dialog, guard)))
			.catch(() => {})
			.finally(() => {
				clearTimeout(timer);
				release();
			});
	}

	// The dialog as an overlay of the kit's: Back closes it (and only it); closing it any other way
	// steps back off the kit's history entry. make_quick_entry resolves with the QuickEntryForm, not
	// its dialog (v16 QuickEntryForm.setup() resolves `this`; the frappe Dialog is its .dialog), so
	// the entry is unwrapped first. Taking the entry as the dialog skipped the guard on prod, and Back
	// closed the dialog AND left the step (v1.677.1).
	track(entry, guard) {
		const dialog = dialog_of(entry);
		if (!dialog || !dialog.$wrapper || !guard || !guard.enabled) return;
		if (dialog.display === false) return; // it did not open (the full form instead)
		const overlay = {
			close: (how) => {
				if (how !== "user" && dialog.display) dialog.hide();
			},
		};
		guard.open(overlay);
		dialog.$wrapper.one("hidden.bs.modal", () => guard.close(overlay, "user"));
	}
}
