/**
 * Customer & Supplier quick entry: the account in the dialog, addresses on the form.
 *
 * Loaded globally via erpnext_enhancements.bundle.js — list "+ Add", the awesome
 * bar and a link field's "Create a new …" all open this dialog, and none of them
 * load a doctype_js. Gated by frappe.boot.ee_contacts_ux, the same toggle as the
 * Contact/Address dialogs (contact_address_quick_entry.js): off, every method here
 * defers to ERPNext's stock dialog.
 *
 * Why it replaces ERPNext's dialog rather than adding to it. erpnext v16 maps both
 * doctypes to `ContactAddressQuickEntryForm`, which appends "Primary Contact
 * Details" and "Primary Address Details" sections that exist only in the dialog.
 * On insert the server turns them into a separate Address and Contact
 * (Customer.create_primary_address / make_contact) — and on this site every field
 * that would show them on the saved form is hidden (address_html, primary_address,
 * section_break_map, the Supplier tax tab), so what was typed appeared to vanish.
 * Its contact half was dead code here besides: First/Last Name only show for
 * customer_type "Company", which this site renamed to "Commercial". And Industry,
 * which `data_quality.enforce_industry` requires on a new commercial account, was
 * never offered at all — Save failed with no field to fix it in.
 *
 * So the dialog asks for account fields only, with Industry shown and required
 * under exactly the server's rule (shipped in frappe.boot.ee_industry_rule), and a
 * saved record opens its full form, where the Address Directory
 * (unified_tab_controller.js) creates or links addresses properly. A blue banner on
 * that first visit points at the directory — only on a record just created, since
 * ~70% of Customers and ~97% of Suppliers have no address and a banner on all of
 * them would be wallpaper. Created from a link field on another form, the record
 * fills the field and the user stays put; a toast offers the address instead.
 */

frappe.provide("erpnext_enhancements.party_quick_entry");

(function register() {
	if (!(frappe.ui && frappe.ui.form && frappe.ui.form.QuickEntryForm)) {
		$(document).one("app_ready", register);
		return;
	}

	const pqe = erpnext_enhancements.party_quick_entry;
	const enabled = () => !!cint(frappe.boot.ee_contacts_ux);

	// Mirrors data_quality.COMMERCIAL_TYPES; the boot payload is authoritative and
	// this is only the fallback for a boot that predates it.
	function industry_rule() {
		const rule = frappe.boot.ee_industry_rule || {};
		return {
			required: !!cint(rule.required),
			types: rule.types || ["Commercial", "Company", "Partnership"],
		};
	}

	function clone_meta_field(doctype, fieldname, overrides) {
		const df = frappe.meta.get_docfield(doctype, fieldname);
		if (!df) return null;
		return Object.assign({}, df, overrides || {});
	}

	// Wording is per doctype, not __(doctype): this site translates "Customer" to
	// "Accounts" (plural), which reads "New Accounts" and "this Accounts".
	const SPECS = {
		Customer: {
			title: () => __("New Account"),
			no_address: () => __("This account has no address yet."),
			fields() {
				const rule = industry_rule();
				const commercial = `eval:${JSON.stringify(rule.types)}.includes(doc.customer_type)`;
				return [
					clone_meta_field("Customer", "customer_name", { reqd: 1 }),
					clone_meta_field("Customer", "customer_type", { reqd: 1 }),
					clone_meta_field("Customer", "industry", {
						hidden: 0,
						depends_on: commercial,
						mandatory_depends_on: rule.required ? commercial : "",
					}),
					{ fieldtype: "Section Break" },
					clone_meta_field("Customer", "custom_account_status"),
					clone_meta_field("Customer", "territory"),
					clone_meta_field("Customer", "custom_value_stream"),
					{ fieldtype: "Section Break" },
					clone_meta_field("Customer", "custom_accounts_phone_number"),
					clone_meta_field("Customer", "custom_accounts_email_address"),
				];
			},
			finalize(doc) {
				// A homeowner has no industry. Picked under Commercial and then
				// switched to Residential, the value would still ride along
				// (controls write straight into this.doc), invisible in the dialog.
				if (!industry_rule().types.includes(doc.customer_type)) {
					doc.industry = null;
				}
			},
		},
		Supplier: {
			title: () => __("New Supplier"),
			no_address: () => __("This supplier has no address yet."),
			fields() {
				// Tax ID is left out on purpose although the v1.145.0 sweep put it in
				// the stock dialog: it lives on the Tax tab, which this site hides, so
				// it is the same typed-and-vanished trap as the address fields.
				return [
					clone_meta_field("Supplier", "supplier_name", { reqd: 1 }),
					clone_meta_field("Supplier", "supplier_type", { reqd: 1 }),
					clone_meta_field("Supplier", "supplier_group"),
					clone_meta_field("Supplier", "country"),
					{ fieldtype: "Section Break" },
					clone_meta_field("Supplier", "custom_phone_number"),
					clone_meta_field("Supplier", "custom_email"),
				];
			},
			finalize() {},
		},
	};

	function make_class(doctype) {
		// ERPNext's class when it exists, so the toggle-off path is exactly stock.
		const Stock = frappe.ui.form[doctype + "QuickEntryForm"];
		const Base = Stock || frappe.ui.form.QuickEntryForm;
		const Plain = frappe.ui.form.QuickEntryForm.prototype;
		const spec = SPECS[doctype];

		return class PartyQuickEntryForm extends Base {
			constructor(doctype, after_insert, init_callback, doc, force, skip_insert) {
				super(doctype, after_insert, init_callback, doc, force, skip_insert);
				if (!enabled()) return;
				// erpnext's constructor takes five arguments and drops this one.
				this.skip_insert = skip_insert ? skip_insert : false;
				// A server rejection keeps the dialog open with every field still
				// in it, rather than dumping the user into a half-filled full form.
				this.skip_redirect_on_error = true;
			}

			is_quick_entry() {
				if (!enabled()) return super.is_quick_entry();
				// The field list is ours, so the meta-driven checks (mandatory
				// child table, empty docfields) have nothing to say about it.
				return true;
			}

			render_dialog() {
				if (!enabled()) return super.render_dialog();
				this.docfields = spec.fields(this).filter(Boolean);
				this.title = this.title || spec.title();
				// Plain, not super: erpnext's render_dialog is the one that appends
				// the contact and address sections.
				Plain.render_dialog.call(this);
			}

			insert() {
				if (!enabled()) return super.insert();
				// Plain, not super: erpnext's insert renames its dialog-only aliases
				// (email_address -> email_id …), which this dialog does not have.
				return Plain.insert.call(this);
			}

			update_doc() {
				const doc = super.update_doc();
				if (enabled()) spec.finalize(doc);
				return doc;
			}

			process_after_insert(r) {
				if (!enabled()) return super.process_after_insert(r);

				// Read before super: update_calling_link deletes it.
				const from_link = frappe._from_link;
				if (!from_link && !this.after_insert) {
					// Stock only opens the form when the user is NOT on this
					// doctype's list, so "+ Add" from the list left them there.
					this.after_insert = (doc) => pqe.open_new_party(doc.doctype, doc.name);
				}
				super.process_after_insert(r);
				if (from_link) pqe.offer_address_later(this.doc);
			}
		};
	}

	Object.keys(SPECS).forEach((doctype) => {
		frappe.ui.form[doctype + "QuickEntryForm"] = make_class(doctype);
	});

	// ------------------------------------------------------------ address prompt

	// "Doctype::name" of records created in this page session whose first visit
	// should point at the Address Directory. In memory on purpose: it is a
	// one-time nudge, not a data-quality flag.
	pqe.pending = new Set();

	const key = (doctype, name) => doctype + "::" + name;

	pqe.open_new_party = function (doctype, name) {
		pqe.pending.add(key(doctype, name));
		frappe.set_route("Form", doctype, name);
	};

	pqe.clear_prompt = function (frm) {
		pqe.pending.delete(key(frm.doctype, frm.doc.name));
		if (frm.__ee_address_prompt) {
			frm.__ee_address_prompt.remove();
			frm.__ee_address_prompt = null;
		}
	};

	/** Called by the Address Directory each time it renders (unified_tab_controller.js). */
	pqe.addresses_rendered = function (frm, count) {
		if (count && frm && frm.doc) pqe.clear_prompt(frm);
	};

	pqe.render_prompt = function (frm) {
		if (!enabled() || frm.is_new() || !frm.fields_dict.address_list_html) return;
		if (!pqe.pending.has(key(frm.doctype, frm.doc.name))) return;

		// A Customer converted from a Lead inherits the Lead's addresses in
		// on_update, so "just created" does not mean "has none".
		const addresses = (frm.doc.__onload && frm.doc.__onload.addr_list) || [];
		if (addresses.length) {
			pqe.clear_prompt(frm);
			return;
		}
		// render_form empties the message area on every refresh; anything still
		// attached is from this render.
		if (frm.__ee_address_prompt && $.contains(document, frm.__ee_address_prompt[0])) return;

		const $message = frm.layout.show_message(
			`<div class="ee-address-prompt" style="display: flex; align-items: center; gap: var(--padding-sm); flex-wrap: wrap;">
				<span>${SPECS[frm.doctype].no_address()}</span>
				<button class="btn btn-xs btn-primary ee-add-address">${__("Add Address")}</button>
			</div>`,
			"blue"
		);
		if (!$message) return;
		frm.__ee_address_prompt = $message;

		$message.find(".ee-add-address").on("click", () => {
			pqe.clear_prompt(frm);
			// Opens the Contacts & Addresses tab and highlights the directory,
			// whose New Address / Link Existing buttons do the rest.
			frm.scroll_to_field("address_list_html", false);
		});
		$message.find(".close-message").on("click", () => pqe.clear_prompt(frm));
	};

	/** Link-field create: the user stays on the form they came from, so the
	 *  address becomes an offer — a link that opens the new record in its own
	 *  tab, scrolled to the directory, leaving the calling form untouched. */
	pqe.offer_address_later = function (doc) {
		if (!doc || !doc.name) return;
		const url = frappe.utils.get_form_link(doc.doctype, doc.name, false, null, {
			scroll_to: "address_list_html",
		});
		const title = doc.customer_name || doc.supplier_name || doc.name;
		// After the stock "saved" alert, so the two do not arrive as one blur.
		setTimeout(() => {
			frappe.show_alert(
				{
					message: __("{0} has no address yet. {1}", [
						frappe.utils.escape_html(title),
						`<a href="${url}" target="_blank" rel="noopener">${__("Add one")}</a>`,
					]),
					indicator: "blue",
				},
				10
			);
		}, 800);
	};

	Object.keys(SPECS).forEach((doctype) => {
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				pqe.render_prompt(frm);
			},
			// "Edit Full Form", a Lead conversion or a plain new form: the first
			// save of a record gets the same one-time prompt as the dialog path.
			before_save(frm) {
				frm.__ee_party_was_new = frm.is_new();
			},
			after_save(frm) {
				if (!frm.__ee_party_was_new) return;
				frm.__ee_party_was_new = false;
				if (!enabled()) return;
				pqe.pending.add(key(frm.doctype, frm.doc.name));
				pqe.render_prompt(frm);
			},
		});
	});
})();
