/**
 * Contact & Address quick-entry dialogs + in-place directory refresh.
 *
 * Loaded globally via erpnext_enhancements.bundle.js (must be global: the
 * list-view "+ New", awesome bar and link-field create paths fire outside any
 * doctype_js). Server half: contacts_ux.py.
 *
 * Frappe resolves `frappe.ui.form.<Doctype>QuickEntryForm` by name when
 * make_quick_entry is CALLED (quick_entry.js, the same at v16.36.1 and
 * v16.50.0), so registering these classes routes EVERY "new Contact/Address"
 * entry point (stock Contacts & Addresses section buttons, list + New, awesome
 * bar, link-field "Create a new…", our directory widget) through the dialog — no
 * meta/property-setter changes.
 *
 * frappe v16.50.0 ships its own ContactQuickEntryForm and AddressQuickEntryForm
 * (frappe/public/js/frappe/utils/address_and_contact.js, imported by
 * form.bundle.js, which loads before this app's bundle) and sets
 * quick_entry = 1 on Contact and Address. This file used to step aside when
 * either class existed, and it defined the contacts_ux helpers after that check,
 * so on 16.50 none of the dialog below ran and the directory widget's New
 * Contact / New Address threw a TypeError. The helpers now come first and
 * depend on nothing, and the classes are registered OVER frappe's on purpose.
 * They still extend frappe.ui.form.QuickEntryForm rather than frappe's classes:
 *   - frappe's Contact dialog adds Phone / Mobile / Email and writes them into
 *     the phone_nos / email_ids tables, which this site hides (Property Setters)
 *     in favour of custom_phone_number / custom_mobile_number / custom_email. It
 *     has no Account, and its other quick-entry fields (designation,
 *     company_name) are hidden here too.
 *   - frappe's insert() replaces doc.links with the one source form, so the
 *     Customer/party is no longer first (Contact.autoname names the record from
 *     links[0]), and its after-insert reloads that form, discarding unsaved edits.
 *   - The one thing it adds that matters is the source form: the stock section
 *     passes it as `source_frm` and points frappe.dynamic_link at it.
 *     resolve_party_context already covers that from the route and the same
 *     dynamic_link guard.
 *   - QuickEntryForm itself did not change between the two releases, so the
 *     dialog runs on the base it was written and tested against, with no frappe
 *     override in between to bypass method by method (party_quick_entry.js does
 *     that, because it extends erpnext's class to keep its toggle-off path stock;
 *     here the constructor hands that path over instead, below).
 *
 * Gated by frappe.boot.ee_contacts_ux (ERPNext Enhancements Settings →
 * Contacts & Addresses). Off, the constructor returns an instance of the class
 * frappe would have used without this file, so off is stock, whatever stock is:
 * frappe's own dialog from v16.50, and before it the base class, which routed to
 * the full form because quick_entry was 0.
 *
 * Opened from a party form, the dialog resolves that form as context —
 * explicitly from the current route, never from the stale `frappe.dynamic_link`
 * global (opportunity.js re-sets it on every refresh and nothing ever clears
 * it) — pre-fills the Account, injects the Dynamic Link rows client-side
 * BEFORE insert (core Contact.autoname names the record from links[0], so the
 * rows must exist at naming time and the Customer/party row must come first),
 * and refreshes the source form's contact/address surfaces in place.
 *
 * Since v1.575.0: a create from a Project Stakeholder row (frappe._from_link,
 * whose .doc is the row and whose set_route_args name the form) is filed under
 * that row's party; a suggested Customer is linked only while it is still the
 * Account; and New Address on a job or a Contact asks "Whose address is this?"
 * with no default.
 */

frappe.provide("erpnext_enhancements.contacts_ux");

// The helpers first, ahead of anything that can return early: the directory widget
// (unified_tab_controller.js) and the Contact / Address form scripts call them whether
// or not the dialogs below are registered.
(function define_helpers() {
	const ux = erpnext_enhancements.contacts_ux;

	/** ERPNext Enhancements Settings → Contacts & Addresses, from the boot payload. */
	ux.enabled = () => !!cint(frappe.boot && frappe.boot.ee_contacts_ux);

	/**
	 * Refresh a party form's contact/address surfaces WITHOUT reloading it.
	 * Pushes fresh __onload lists (contacts_ux.get_directory_onload) into the
	 * cached form and re-renders the stock section + our directory widget —
	 * frm.reload_doc() would discard unsaved edits and race the link-field
	 * route restore. Forms not in the formview cache are skipped (nothing
	 * stale exists for them).
	 */
	ux.refresh_directory_surfaces = function (targets) {
		const seen = new Set();
		(Array.isArray(targets) ? targets : [targets]).forEach((target) => {
			if (!target || !target.doctype || !target.name) return;
			const key = target.doctype + "::" + target.name;
			if (seen.has(key)) return;
			seen.add(key);

			const view = frappe.views.formview[target.doctype];
			const frm = view && view.frm;
			if (!frm || !frm.doc || frm.doc.name !== target.name || frm.is_new()) return;
			const has_stock = frm.fields_dict.contact_html || frm.fields_dict.address_html;
			const has_widget = frm.fields_dict.contact_list_html || frm.fields_dict.address_list_html;
			if (!has_stock && !has_widget) return;

			frappe.call({
				method: "erpnext_enhancements.contacts_ux.get_directory_onload",
				args: { doctype: target.doctype, name: target.name },
				callback(r) {
					if (!r.message || !frm.doc) return;
					frm.doc.__onload = Object.assign({}, frm.doc.__onload, r.message);
					if (has_stock && frappe.contacts && frappe.contacts.render_address_and_contact) {
						frappe.contacts.render_address_and_contact(frm);
					}
					// The widget is a singleton holding this.frm — only re-render
					// for the form on screen; a background form re-renders it on
					// its own next refresh anyway (from the pushed-fresh server data).
					if (
						has_widget &&
						window.cur_frm === frm &&
						erpnext_enhancements.unified_controller
					) {
						erpnext_enhancements.unified_controller.init(frm);
					}
				},
			});
		});
	};

	/**
	 * The init_callback frappe's own Contacts & Addresses section passes since v16.50:
	 * the open form as `source_frm`, which frappe's dialog (the one that opens with the
	 * toggle off) links the new record to. Ours resolves its context itself and ignores
	 * it. Set on a dialog only: when quick entry is off, frappe calls init_callback with
	 * the bare new doc instead, and a form object on a doc cannot be serialised on save.
	 */
	function source_form_callback(frm) {
		// An unsaved form has no name a link can point at (frappe's dialog would
		// write links=[{Doctype, "new-…"}]), so only a saved form is offered.
		if (!frm || !frm.doc || frm.is_new()) return undefined;
		return (quick_entry) => {
			const Dialog = frappe.ui.form.QuickEntryForm;
			if (Dialog && quick_entry instanceof Dialog) quick_entry.source_frm = frm;
		};
	}

	/** "New Contact" from our directory widget (context self-resolves from the route). */
	ux.new_contact = function (frm) {
		frappe.new_doc("Contact", null, source_form_callback(frm));
	};

	/** "New Address" from our directory widget — stock parity with the section
	 *  button: the Geolocation autocomplete dialog wins when it's enabled. */
	ux.new_address = function (frm) {
		if (
			frappe.boot.enable_address_autocompletion === 1 &&
			frm &&
			!frm.is_new() &&
			frappe.ui.AddressAutocompleteDialog
		) {
			new frappe.ui.AddressAutocompleteDialog({
				title: __("New Address"),
				link_doctype: frm.doctype,
				link_name: frm.doc.name,
				after_insert: () =>
					ux.refresh_directory_surfaces({ doctype: frm.doctype, name: frm.doc.name }),
			}).show();
			return;
		}
		frappe.new_doc("Address", null, source_form_callback(frm));
	};

	/** Contact/Address after_save (wired in their doctype_js): push fresh
	 *  directory data at every cached form the record links to — this is what
	 *  fixes the stale Contacts & Addresses section after the full-form
	 *  save + route-back flow (deliberately NOT gated: it is a data-staleness
	 *  bug fix, not a UX experiment). */
	ux.refresh_linked_sources = function (frm) {
		ux.refresh_directory_surfaces(
			(frm.doc.links || []).map((l) => ({ doctype: l.link_doctype, name: l.link_name }))
		);
	};
})();

(function register() {
	if (!(frappe.ui && frappe.ui.form && frappe.ui.form.QuickEntryForm)) {
		$(document).one("app_ready", register);
		return;
	}

	const ux = erpnext_enhancements.contacts_ux;

	// frappe's own classes (v16.50+; null before), captured before ours replace them: they
	// are what the toggle-off path builds. A second run of this file finds ours registered
	// and takes what they captured, never our own class.
	const stock_class = (name) => {
		const current = frappe.ui.form[name];
		if (current && "ee_stock" in current) return current.ee_stock;
		return current || null;
	};
	const STOCK = {
		Contact: stock_class("ContactQuickEntryForm"),
		Address: stock_class("AddressQuickEntryForm"),
	};

	// Per-doctype context: which Dynamic Links a Contact/Address created from
	// this form should carry ([party first] — link order drives Contact naming)
	// and which Customer pre-fills the Account field.
	const PARTY_CONTEXT = {
		Customer: (d) => ({ account: d.name, links: [["Customer", d.name]] }),
		Supplier: (d) => ({ account: null, links: [["Supplier", d.name]] }),
		Lead: (d) => ({ account: null, links: [["Lead", d.name]] }),
		Prospect: (d) => ({ account: null, links: [["Prospect", d.name]] }),
		Opportunity: (d) => ({
			account: d.opportunity_from === "Customer" ? d.party_name : null,
			links: [
				...(d.opportunity_from && d.party_name ? [[d.opportunity_from, d.party_name]] : []),
				["Opportunity", d.name],
			],
		}),
		Project: (d) => ({
			account: d.customer || null,
			links: [...(d.customer ? [["Customer", d.customer]] : []), ["Project", d.name]],
		}),
		"Master Project": (d) => ({
			account: d.customer || null,
			links: [...(d.customer ? [["Customer", d.customer]] : []), ["Master Project", d.name]],
		}),
		// "New Address" from a Contact form: always that Contact; its Account is an
		// option (the Address dialog asks), so a person's own address is never filed
		// as their company's instead of theirs.
		Contact: (d) => ({
			account: d.custom_account || null,
			links: [...(d.custom_account ? [["Customer", d.custom_account]] : []), ["Contact", d.name]],
		}),
	};

	//: Forms that are a JOB rather than a party. A record created from one of their
	//: Project Stakeholder rows is linked to the job as well as to the row's party.
	const JOB_DOCTYPES = ["Project", "Master Project", "Opportunity"];

	/**
	 * The Project Stakeholder row the user is creating this record from, or null.
	 *
	 * "Create a new Contact" in a stakeholder row's Contact field (or its Address
	 * field) means "a person (address) of THAT row's party". Until v1.575.0 the
	 * dialog took its context from the route alone, so on a Harwood project a new
	 * contact typed into the Summit County Health row was pre-filled with Account
	 * Harwood and filed under Harwood, the exact shape of the audited Nathan
	 * Brooks record. frappe's link control sets frappe._from_link (the doc it was
	 * called from) synchronously before it opens quick entry, and always passes an
	 * after_insert; frappe.new_doc (the directory's own New Contact) passes none.
	 * Requiring both, and a Project Stakeholder row of the form on screen, keeps a
	 * stale _from_link from a cancelled create from ever being read, and keeps
	 * forms whose own doc happens to carry party_type/party_name out (Payment
	 * Entry's party_name is a display name, not a Customer ID).
	 */
	function stakeholder_row(after_insert, doctype, name) {
		const from = frappe._from_link;
		const row = from && from.doc;
		if (typeof after_insert !== "function" || !row) return null;
		if (row.doctype !== "Project Stakeholder" || row.parenttype !== doctype || row.parent !== name) return null;
		const route_args = from.set_route_args || [];
		if (route_args[1] !== doctype || route_args[2] !== name) return null;
		return row;
	}

	function resolve_party_context(after_insert) {
		const route = frappe.get_route();
		if (!route || route[0] !== "Form" || route.length < 3) return null;
		const doctype = route[1];
		const name = route.slice(2).join("/");
		const frm = frappe.views.formview[doctype] && frappe.views.formview[doctype].frm;
		if (!frm || frm.is_new() || !frm.doc || frm.doc.name !== name) return null;

		const row = stakeholder_row(after_insert, doctype, name);
		const row_party = row && row.party_type && row.party_name ? [row.party_type, row.party_name] : null;
		const on_job = JOB_DOCTYPES.includes(doctype);
		// A row with no party yet: on a job it gives the job only (falling back to the route
		// would file the person under the job's Customer, the audited shape); on a Customer
		// or Supplier form it is how people list that company's OWN staff, so it falls
		// through to the form's own context below.
		if (row && (row_party || on_job)) {
			const party = row_party;
			return {
				doctype: doctype,
				name: name,
				from_row: true,
				account: party && party[0] === "Customer" ? party[1] : null,
				// The row's party, and the job when the form is one. A stakeholder row on a
				// Customer or Supplier form links to that party only: filing the stakeholder's
				// person under the form's own company is the fan-out this release removes.
				links: [...(party ? [party] : []), ...(on_job ? [[doctype, name]] : [])],
			};
		}

		let resolved = null;
		if (PARTY_CONTEXT[doctype]) {
			resolved = PARTY_CONTEXT[doctype](frm.doc);
		} else {
			// Doctypes outside the map (Sales Partner, Company, Bank…) still get
			// stock-section parity: honor frappe.dynamic_link, but only under the
			// same guard frappe's own contact.js uses — it must point at the form
			// the user is actually on.
			const dl = frappe.dynamic_link;
			if (dl && dl.doc && dl.doc.name === frm.doc.name) {
				resolved = { account: null, links: [[dl.doctype, dl.doc[dl.fieldname]]] };
			}
		}
		if (!resolved) return null;
		return Object.assign({ doctype: doctype, name: name }, resolved);
	}

	function clone_meta_field(doctype, fieldname, overrides) {
		const df = frappe.meta.get_docfield(doctype, fieldname);
		if (!df) return null;
		return Object.assign({}, df, overrides || {});
	}

	class ContactAddressQuickEntry extends frappe.ui.form.QuickEntryForm {
		constructor(doctype, after_insert, init_callback, doc, force, skip_insert) {
			if (!ux.enabled()) {
				// Toggle off: exactly what make_quick_entry builds without this file —
				// frappe's own class from v16.50, the base class (the full form) before.
				// A derived constructor may return an object instead of calling super();
				// `new` then yields that object.
				const Stock = new.target.ee_stock || frappe.ui.form.QuickEntryForm;
				return new Stock(doctype, after_insert, init_callback, doc, force, skip_insert);
			}
			super(doctype, after_insert, init_callback, doc, force, skip_insert);
			this.ee_context = resolve_party_context(after_insert);
			if (!this.after_insert && this.ee_context) {
				// Also suppresses open_form_if_not_list(), which would route away
				// from the party form to the freshly created record.
				this.after_insert = () => this.ee_refresh_surfaces();
			}
		}

		is_quick_entry() {
			// Always: the field list is ours, so meta's checks (quick_entry, a mandatory
			// child table, no docfields) have nothing to say about it, and the toggle was
			// settled in the constructor. (Deliberately not this.force: force hides the
			// Edit Full Form link.)
			return true;
		}

		render_dialog() {
			if (this.ee_is_new()) {
				this.ee_prepare_new_doc();
			}
			const fields = this.ee_dialog_fields();
			if (fields) {
				this.docfields = fields.filter(Boolean);
			}
			super.render_dialog();
			this.ee_reapply_df_overrides();
			this.ee_show_context_intro();
		}

		/**
		 * frappe's Layout replaces each control's df with the per-doc copy of its meta
		 * docfield right after building the controls (layout.js attach_doc_and_docfields,
		 * reached from FieldGroup.make's refresh). That copy is built from meta, so it drops
		 * whatever this dialog set on its clone for a field that exists in meta: an
		 * onchange, reqd, read_only. Put those back on the copy the control now holds. That
		 * copy is keyed by this new doc's name, so it reaches nothing but this dialog and,
		 * if the user picks Edit Full Form, the full form of the same unsaved doc (where a
		 * required first name and a no-op banner refresh are harmless). A dialog-only field
		 * (not in meta) keeps its clone and is skipped.
		 */
		ee_reapply_df_overrides() {
			(this.docfields || []).forEach((df) => {
				const control = df && df.fieldname && this.fields_dict && this.fields_dict[df.fieldname];
				if (!control || !control.df || control.df === df) return;
				let changed = false;
				["onchange", "reqd", "read_only"].forEach((key) => {
					if (key in df && control.df[key] !== df[key]) {
						control.df[key] = df[key];
						changed = true;
					}
				});
				if (changed && typeof control.refresh === "function") control.refresh();
			});
		}

		update_doc() {
			const doc = super.update_doc();
			if (this.ee_is_new()) {
				this.ee_apply_links(doc);
				this.ee_finalize_doc(doc);
			}
			return doc;
		}

		open_doc(set_hooks) {
			// "Edit Full Form": frappe's contact.js/address.js wipe a local doc's
			// links grid when frappe.dynamic_link matches route history — null it
			// so every injected row survives into the full form.
			frappe.dynamic_link = null;
			super.open_doc(set_hooks);
		}

		process_after_insert(r) {
			// update_calling_link (link-field create) takes precedence over
			// after_insert in the base class — refresh the source form ourselves
			// on that branch.
			const from_link = frappe._from_link;
			super.process_after_insert(r);
			if (from_link && this.ee_context) {
				this.ee_refresh_surfaces();
			}
		}

		ee_is_new() {
			return !!(this.doc && this.doc.__islocal);
		}

		/** Rebuild doc.links as real child docs: Account/party first (drives
		 *  Contact.autoname), then context links, then anything already on the
		 *  doc (e.g. route_options links from erpnext's Create > buttons). */
		ee_apply_links(doc) {
			const desired = [];
			const seen = new Set();
			const push = (link_doctype, link_name) => {
				if (!link_doctype || !link_name) return;
				const key = link_doctype + "::" + link_name;
				if (seen.has(key)) return;
				seen.add(key);
				desired.push([link_doctype, link_name]);
			};

			this.ee_leading_links(doc).forEach((l) => push(l[0], l[1]));
			this.ee_context_links(doc).forEach((l) => push(l[0], l[1]));
			(doc.links || []).forEach((row) => push(row.link_doctype, row.link_name));

			if (!desired.length) return;
			doc.links = [];
			desired.forEach(([link_doctype, link_name]) => {
				const row = frappe.model.add_child(doc, "Dynamic Link", "links");
				row.link_doctype = link_doctype;
				row.link_name = link_name;
			});
		}

		ee_refresh_surfaces() {
			if (!this.ee_context) return;
			const targets = [{ doctype: this.ee_context.doctype, name: this.ee_context.name }];
			(this.ee_context.links || []).forEach(([link_doctype, link_name]) =>
				targets.push({ doctype: link_doctype, name: link_name })
			);
			ux.refresh_directory_surfaces(targets);
		}

		/** What the dialog will save right now: the doc overlaid with every field's current
		 *  value. Read field by field because get_values() leaves out empty fields, and an
		 *  emptied field is exactly the case that matters here. */
		ee_current_values() {
			const values = Object.assign({}, this.doc);
			Object.values(this.fields_dict || {}).forEach((field) => {
				if (field && field.df && field.df.fieldname && typeof field.get_value === "function") {
					values[field.df.fieldname] = field.get_value();
				}
			});
			return values;
		}

		ee_show_context_intro() {
			if (!this.ee_context || !this.ee_is_new()) return;
			const values = this.ee_current_values();
			const parts = this.ee_leading_links(values)
				.concat(this.ee_context_links(values))
				.filter((l, i, all) => all.findIndex((m) => m[0] === l[0] && m[1] === l[1]) === i)
				.map(([link_doctype, link_name]) => `${__(link_doctype)} ${link_name}`);
			// An empty text clears the banner (QuickEntryForm.set_intro), so it never keeps
			// naming a company the user has just removed.
			// "info": v16 styles only info/success/warning/danger alerts.
			this.set_intro(parts.length ? __("Will be linked to {0}", [parts.join(", ")]) : "", "info");
		}

		// Subclass hooks.
		/** The context's links that this record should still carry, given what the user chose. */
		ee_context_links() {
			return (this.ee_context && this.ee_context.links) || [];
		}
		ee_prepare_new_doc() {}
		ee_dialog_fields() {
			return null;
		}
		ee_leading_links() {
			return [];
		}
		ee_finalize_doc() {}
	}

	class ContactQuickEntryForm extends ContactAddressQuickEntry {
		ee_prepare_new_doc() {
			// Link-field create writes the typed text into the field: autoname
			// target (custom_full_name_and_role), where the server would discard
			// it — harvest it into the name fields instead.
			const typed = (this.doc.custom_full_name_and_role || "").trim();
			if (typed && !this.doc.first_name && !this.doc.last_name) {
				const cut = typed.lastIndexOf(" ");
				this.doc.first_name = cut === -1 ? typed : typed.slice(0, cut);
				this.doc.last_name = cut === -1 ? "" : typed.slice(cut + 1);
			}
			this.doc.custom_full_name_and_role = "";

			if (this.ee_context && this.ee_context.account && !this.doc.custom_account) {
				this.doc.custom_account = this.ee_context.account;
			}
		}

		ee_dialog_fields() {
			// The site hides stock email_id/phone/mobile_no in favor of the
			// custom_* fields, so the dialog uses those directly. first_name is
			// required dialog-only (a name-less Contact fails core autoname with
			// an opaque error).
			return [
				clone_meta_field("Contact", "first_name", { reqd: 1 }),
				clone_meta_field("Contact", "last_name"),
				{ fieldtype: "Column Break" },
				clone_meta_field("Contact", "custom_title"),
				clone_meta_field("Contact", "custom_account", {
					onchange: () => this.ee_show_context_intro(),
				}),
				{ fieldtype: "Section Break", label: __("Contact Details") },
				clone_meta_field("Contact", "custom_email"),
				{ fieldtype: "Column Break" },
				clone_meta_field("Contact", "custom_phone_number"),
				clone_meta_field("Contact", "custom_mobile_number"),
			];
		}

		ee_leading_links(doc) {
			// The Account drives the Customer link even with zero context (typed
			// directly in the dialog) — first, so it names the record.
			return doc.custom_account ? [["Customer", doc.custom_account]] : [];
		}

		update_doc() {
			// The control writes the doc on change (set_model_value), so an emptied Account
			// normally reaches the doc already. update_doc itself copies only non-empty
			// values, though, so make the emptied field authoritative here rather than
			// depend on a change event having fired.
			const field = this.fields_dict && this.fields_dict.custom_account;
			if (field && this.ee_is_new() && !field.get_value()) {
				this.doc.custom_account = "";
			}
			return super.update_doc();
		}

		ee_context_links(doc) {
			// On a Project / Customer-sourced Opportunity the Account is
			// pre-filled with that form's Customer as a SUGGESTION — usually right (the
			// customer's own people), but not for the GC's super, the architect or the
			// county inspector on the job. Until v1.575.0 the context still pushed that
			// Customer link after the user cleared or changed the Account, so the person
			// was filed under the customer anyway and the Account came straight back. The
			// user's choice in the Account field is the answer: keep the suggested Customer
			// only while it is still the Account.
			const ctx = this.ee_context;
			// A Contact is never linked to another Contact (the Contact context's
			// self-link exists for New Address on a Contact form).
			const links = super.ee_context_links(doc).filter(([link_doctype]) => link_doctype !== "Contact");
			const suggested = ctx && ctx.account;
			if (!suggested || (doc.custom_account || "") === suggested) return links;
			// The form the user is standing on is never optional: on a Customer form the
			// "suggestion" IS that Customer, and dropping it saved a contact linked to
			// nothing at all.
			return links.filter(
				([link_doctype, link_name]) =>
					!(link_doctype === "Customer" && link_name === suggested) ||
					(link_doctype === ctx.doctype && link_name === ctx.name)
			);
		}
	}

	class AddressQuickEntryForm extends ContactAddressQuickEntry {
		ee_prepare_new_doc() {
			if (!this.doc.address_type) {
				this.doc.address_type = "Billing";
			}
			if (!this.doc.country && frappe.sys_defaults.country) {
				this.doc.country = frappe.sys_defaults.country;
			}
		}

		ee_dialog_fields() {
			return [
				clone_meta_field("Address", "address_line1"),
				clone_meta_field("Address", "address_line2"),
				clone_meta_field("Address", "pincode"),
				{ fieldtype: "Column Break" },
				clone_meta_field("Address", "city"),
				clone_meta_field("Address", "state"),
				clone_meta_field("Address", "country"),
				{ fieldtype: "Section Break", label: __("Details") },
				clone_meta_field("Address", "address_type"),
				{ fieldtype: "Column Break" },
				clone_meta_field("Address", "address_title"),
				this.ee_party_choice_field(),
				// Coordinates last, collapsed: needed only when the address text
				// cannot locate the site (new construction, a lot number), which
				// is rare — but this dialog is the main creation path, so leaving
				// them out would mean creating such a site here and then having to
				// open the full form to place it.
				{
					fieldtype: "Section Break",
					label: __("Coordinates"),
					collapsible: 1,
					description: __(
						"Only needed when the address above cannot locate the site. Every map prefers a point entered here over the address text, and it is kept when the address is edited."
					),
				},
				clone_meta_field("Address", "custom_latitude", {
					read_only: 0,
					onchange: () => this.ee_coordinates_edited(),
				}),
				{ fieldtype: "Column Break" },
				clone_meta_field("Address", "custom_longitude", {
					read_only: 0,
					onchange: () => this.ee_coordinates_edited(),
				}),
			];
		}

		/** The context links that name a PARTY rather than the open form itself: on a
		 *  Project, its Customer; on an Opportunity, its Customer/Lead/Prospect; on a
		 *  Contact, its Account. */
		ee_party_links() {
			const ctx = this.ee_context;
			if (!ctx) return [];
			return (ctx.links || []).filter(([link_doctype, link_name]) => !(link_doctype === ctx.doctype && link_name === ctx.name));
		}

		/**
		 * "Whose address is this?": a required choice between this form only and also
		 * the form's party, or null when there is nothing to ask (a Customer/Supplier
		 * form IS the party).
		 *
		 * Until v1.575.0 an address made from a job always went to its Customer/party as
		 * well, with no way to say no. When that Customer is the general contractor rather
		 * than the owner, the job site is filed under the wrong company: the Saltair site
		 * became Wadman Corporation's primary address and the address on a draft invoice,
		 * and the Stenmark lot became a Hess Construction address. Those were the default
		 * case, so there is no default: the dialog will not save until the user answers
		 * (decided 2026-10-06). The one exception is a create from a Project Stakeholder
		 * row on a job, where the user already chose the party by starting from its row;
		 * it is pre-answered "Also" and can still be changed.
		 */
		ee_party_choice_field() {
			const party = this.ee_party_links();
			if (!this.ee_is_new() || !party.length) return null;
			// Ask only where the open form is itself one of the links (a job, or a Contact):
			// "this form only" then means something. Elsewhere — a transaction form whose
			// script sets frappe.dynamic_link (Quotation, Sales Order, Purchase Order…), or a
			// stakeholder row on a Customer/Supplier form — the party is the only link there
			// is, and answering "only" would save an address linked to nothing.
			const ctx_self = this.ee_context;
			if (!(ctx_self.links || []).some(([dt, nm]) => dt === ctx_self.doctype && nm === ctx_self.name)) return null;
			const names = party
				.map(([link_doctype, link_name]) => frappe.utils.get_link_title(link_doctype, link_name) || link_name)
				.join(", ");
			const ctx = this.ee_context;
			this.ee_choice = {
				only: __("This {0} only", [__(ctx.doctype)]),
				also: __("Also {0}'s address", [names]),
			};
			return {
				fieldtype: "Select",
				fieldname: "ee_file_under_party",
				label: __("Whose address is this?"),
				options: ["", this.ee_choice.only, this.ee_choice.also],
				reqd: 1,
				default: ctx.from_row ? this.ee_choice.also : undefined,
				description: __(
					"A job site that belongs to someone else (e.g. a general contractor's project) is this {0}'s only.",
					[__(ctx.doctype)]
				),
				onchange: () => this.ee_show_context_intro(),
			};
		}

		ee_context_links(doc) {
			const links = super.ee_context_links(doc);
			const field = this.fields_dict && this.fields_dict.ee_file_under_party;
			if (!field || (this.ee_choice && field.get_value() === this.ee_choice.also)) return links;
			// Not answered yet, or "this form only": the party is not added. An unanswered
			// dialog cannot save (reqd), so this only shapes the banner until then.
			const party = this.ee_party_links();
			const kept = links.filter(([link_doctype, link_name]) => !party.some((p) => p[0] === link_doctype && p[1] === link_name));
			// "This <form> only" keeps the form's own link: the job, or the Contact, which every
			// context with a choice carries. No fallback is needed or wanted: falling back to the
			// open form is how a stakeholder's address used to land on a Customer form's own
			// company.
			return kept;
		}

		update_doc() {
			const doc = super.update_doc();
			// Dialog-only answer: never a field on the Address.
			delete doc.ee_file_under_party;
			return doc;
		}

		/** A hand edit makes the point the user's, so it must survive from here on. */
		ee_coordinates_edited() {
			const places =
				window.erpnext_enhancements && erpnext_enhancements.address_autocomplete;
			if (!places) return;
			const point = places.usable_point(
				this.dialog.get_value("custom_latitude"),
				this.dialog.get_value("custom_longitude")
			);
			this.doc.custom_location_source = point ? "Manual" : "";
		}

		render_dialog() {
			super.render_dialog();
			this.ee_attach_address_autocomplete();
			this.ee_guard_coordinate_paste();
		}

		/** Same pasted-pair guard the full form uses — see parse_point_paste. */
		ee_guard_coordinate_paste() {
			const places =
				window.erpnext_enhancements && erpnext_enhancements.address_autocomplete;
			if (!places || !places.bind_point_paste || !this.dialog) return;

			["custom_latitude", "custom_longitude"].forEach((fieldname) => {
				places.bind_point_paste(this.dialog.fields_dict[fieldname], (point) => {
					this.doc.custom_latitude = point.lat;
					this.doc.custom_longitude = point.lng;
					this.doc.custom_location_source = "Manual";
					["custom_latitude", "custom_longitude"].forEach((f) => {
						const control = this.dialog.fields_dict[f];
						if (control && control.set_input) control.set_input(this.doc[f]);
					});
				});
			});
		}

		/** Google Places on the dialog's address_line1, same widget the full
		 *  Address form uses. It has to be wired here rather than by a form
		 *  script because this is a frappe.ui.Dialog — `frappe.ui.form.on`
		 *  never fires for it — and with ee_contacts_ux on, this dialog is
		 *  where list+New, the awesomebar and link-field creates all land. */
		ee_attach_address_autocomplete() {
			const places =
				window.erpnext_enhancements && erpnext_enhancements.address_autocomplete;
			const field = this.dialog && this.dialog.fields_dict.address_line1;
			if (!places || !field || !field.$input) return;

			const control = places.attach(field.$input.get(0), {
				get_country: () => this.dialog.get_value("country"),
				on_pick: (values, meta) => {
					this.dialog.set_values(values);
					// The Place ID has no dialog field, so it rides on the doc —
					// update_doc() copies dialog values over the same object and
					// leaves keys it does not know about alone.
					this.doc.custom_google_place_id = meta.place_id || "";

					// set_input, not set_value: the latter routes through the
					// control's own change path and would fire the onchange
					// above, restamping this pick as a hand edit.
					const write = (lat, lng, source) => {
						this.doc.custom_location_source = source;
						[["custom_latitude", lat], ["custom_longitude", lng]].forEach(
							([field, value]) => {
								const control = this.dialog.fields_dict[field];
								this.doc[field] = value;
								if (control && control.set_input) control.set_input(value);
							}
						);
					};

					const picked = places.usable_point(meta.latitude, meta.longitude);
					if (picked) {
						write(picked.lat, picked.lng, "Google");
					} else if (!places.point_survives_text_edit(this.doc)) {
						// A Place can resolve without a location. Any point on the
						// doc belongs to the address being replaced — picking a
						// second suggestion must not leave the first one's
						// coordinates under the new text.
						write(0, 0, "");
					}
					// No point from the pick but a Manual one on the doc: left
					// alone on purpose. It was typed because the address cannot
					// locate the site.

					// What the pick actually claims, so a later hand edit can be
					// detected on save (see ee_finalize_doc).
					this.ee_picked_line1 = values.address_line1 || "";
				},
			});
			if (!control) return;

			// The listbox lives on document.body, so it outlives the dialog's own
			// DOM unless it is torn down with it.
			this.dialog.$wrapper.on("hidden.bs.modal", () => control.destroy());
		}

		ee_finalize_doc(doc) {
			// Zero-context safety: Address.autoname throws "Address Title is
			// mandatory." when there is no title and no link to fall back on.
			if (!doc.address_title && !(doc.links || []).length) {
				doc.address_title = doc.address_line1;
			}

			// The dialog watches no component field for edits, so the check
			// happens once, here: if line 1 no longer reads what the pick filled
			// in, the Place ID describes an address that is no longer on the doc.
			if (doc.custom_google_place_id && doc.address_line1 !== this.ee_picked_line1) {
				doc.custom_google_place_id = "";
				// The point goes with it only if Google derived it from that text.
				// A typed point exists *because* the text cannot locate the site,
				// so editing the text must not discard it.
				if (doc.custom_location_source !== "Manual") {
					doc.custom_latitude = 0;
					doc.custom_longitude = 0;
					doc.custom_location_source = "";
				}
			}
		}
	}

	// Registered OVER frappe's own classes on purpose (see the header). make_quick_entry
	// looks the name up when it is called, and this runs after frappe's form.bundle.js,
	// so these assignments are the ones it finds. `ee_stock` is the toggle-off class.
	ContactQuickEntryForm.ee_stock = STOCK.Contact;
	AddressQuickEntryForm.ee_stock = STOCK.Address;
	frappe.ui.form.ContactQuickEntryForm = ContactQuickEntryForm;
	frappe.ui.form.AddressQuickEntryForm = AddressQuickEntryForm;
})();
