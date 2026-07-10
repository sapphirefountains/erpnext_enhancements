/**
 * Contact & Address quick-entry dialogs + in-place directory refresh.
 *
 * Loaded globally via erpnext_enhancements.bundle.js (must be global: the
 * list-view "+ New", awesome bar and link-field create paths fire outside any
 * doctype_js). Server half: contacts_ux.py.
 *
 * Frappe resolves `frappe.ui.form.<Doctype>QuickEntryForm` by naming
 * convention in make_quick_entry, so registering these classes routes EVERY
 * "new Contact/Address" entry point (stock Contacts & Addresses section
 * buttons, list + New, awesome bar, link-field "Create a new…", our directory
 * widget) through the dialog — no meta/property-setter changes. Behavior is
 * gated by frappe.boot.ee_contacts_ux (ERPNext Enhancements Settings →
 * Contacts & Addresses): off, is_quick_entry() falls back to the base class,
 * which routes to the stock full form because Contact/Address have
 * meta.quick_entry = 0.
 *
 * Opened from a party form, the dialog resolves that form as context —
 * explicitly from the current route, never from the stale `frappe.dynamic_link`
 * global (opportunity.js re-sets it on every refresh and nothing ever clears
 * it) — pre-fills the Account, injects the Dynamic Link rows client-side
 * BEFORE insert (core Contact.autoname names the record from links[0], so the
 * rows must exist at naming time and the Customer/party row must come first),
 * and refreshes the source form's contact/address surfaces in place.
 */

frappe.provide("erpnext_enhancements.contacts_ux");

(function register() {
	if (!(frappe.ui && frappe.ui.form && frappe.ui.form.QuickEntryForm)) {
		$(document).one("app_ready", register);
		return;
	}
	// If a future frappe/erpnext ships its own Contact/Address quick entry,
	// skip ours and re-evaluate on upgrade (nothing defines these as of v16).
	if (frappe.ui.form.ContactQuickEntryForm || frappe.ui.form.AddressQuickEntryForm) {
		return;
	}

	const ux = erpnext_enhancements.contacts_ux;

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
		// "New Address" from a Contact form: attach to its account when set.
		Contact: (d) => ({
			account: d.custom_account || null,
			links: [d.custom_account ? ["Customer", d.custom_account] : ["Contact", d.name]],
		}),
	};

	function resolve_party_context() {
		const route = frappe.get_route();
		if (!route || route[0] !== "Form" || route.length < 3) return null;
		const doctype = route[1];
		const name = route.slice(2).join("/");
		const frm = frappe.views.formview[doctype] && frappe.views.formview[doctype].frm;
		if (!frm || frm.is_new() || !frm.doc || frm.doc.name !== name) return null;

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

	/** "New Contact" from our directory widget (context self-resolves from the route). */
	ux.new_contact = function () {
		frappe.new_doc("Contact");
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
		frappe.new_doc("Address");
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

	function clone_meta_field(doctype, fieldname, overrides) {
		const df = frappe.meta.get_docfield(doctype, fieldname);
		if (!df) return null;
		return Object.assign({}, df, overrides || {});
	}

	class ContactAddressQuickEntry extends frappe.ui.form.QuickEntryForm {
		constructor(doctype, after_insert, init_callback, doc, force, skip_insert) {
			super(doctype, after_insert, init_callback, doc, force, skip_insert);
			this.ee_context = resolve_party_context();
			if (!this.after_insert && this.ee_context) {
				// Also suppresses open_form_if_not_list(), which would route away
				// from the party form to the freshly created record.
				this.after_insert = () => this.ee_refresh_surfaces();
			}
		}

		is_quick_entry() {
			// Toggle off -> base behavior: Contact/Address have meta.quick_entry=0,
			// so this returns false and setup() routes to the stock full form.
			// (Deliberately not this.force: force hides the Edit Full Form link.)
			if (!cint(frappe.boot.ee_contacts_ux)) {
				return super.is_quick_entry();
			}
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
			this.ee_show_context_intro();
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
			((this.ee_context && this.ee_context.links) || []).forEach((l) => push(l[0], l[1]));
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

		ee_show_context_intro() {
			if (!this.ee_context || !this.ee_is_new()) return;
			const parts = (this.ee_context.links || []).map(
				([link_doctype, link_name]) => `${__(link_doctype)} ${link_name}`
			);
			if (parts.length) {
				this.set_intro(__("Will be linked to {0}", [parts.join(", ")]), "blue");
			}
		}

		// Subclass hooks.
		ee_prepare_new_doc() {}
		ee_dialog_fields() {
			return null;
		}
		ee_leading_links() {
			return [];
		}
		ee_finalize_doc() {}
	}

	frappe.ui.form.ContactQuickEntryForm = class ContactQuickEntryForm extends (
		ContactAddressQuickEntry
	) {
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
				clone_meta_field("Contact", "custom_account"),
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
	};

	frappe.ui.form.AddressQuickEntryForm = class AddressQuickEntryForm extends (
		ContactAddressQuickEntry
	) {
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
			];
		}

		ee_finalize_doc(doc) {
			// Zero-context safety: Address.autoname throws "Address Title is
			// mandatory." when there is no title and no link to fall back on.
			if (!doc.address_title && !(doc.links || []).length) {
				doc.address_title = doc.address_line1;
			}
		}
	};
})();
