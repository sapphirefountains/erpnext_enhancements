/**
 * Unified party tab controller (Contacts / Addresses / Map directory widget).
 *
 * Targets: the Customer, Supplier, Opportunity, Project, Master Project and
 * Contact forms.
 * Loaded via: hooks.py `doctype_js` for each of those doctypes.
 *
 * Renders aggregated Contact and Address directories into custom HTML fields
 * (contact_list_html / address_list_html) plus an embedded Google Map of the
 * primary address (location_map_html), and wires the custom comments field
 * (custom_comments_field) to ERPNext's CRMNotes widget.
 *
 * The key idea is `get_all_party_sources`: it gathers every related party for the
 * current doc — the doc itself, its customer/supplier/party links, and any
 * child-table rows referencing parties or Dynamic Links — then asks the backend
 * (`sync_contact.*`) for all contacts/addresses linked to ANY of them. This is
 * why, e.g., a Project shows contacts attached to its Customer. Link Existing /
 * Unlink round-trip through the same sync_contact API and re-render; New Contact /
 * New Address open the quick-entry dialogs (contact_address_quick_entry.js), which
 * re-render this widget after insert.
 *
 * Set Primary is the exception, and deliberately so: only Customer and Supplier
 * write the account-wide `is_primary_contact` / `is_primary_address` flags through
 * that API. Every other form records its primary in its own doc-local field and
 * touches nothing else — see the `party_context` block below for why.
 */
frappe.provide("erpnext_enhancements.unified_controller");

frappe.ui.form.on("Customer", {
	refresh: (frm) => erpnext_enhancements.unified_controller.init(frm),
});
frappe.ui.form.on("Supplier", {
	refresh: (frm) => erpnext_enhancements.unified_controller.init(frm),
});
frappe.ui.form.on("Opportunity", {
	refresh: (frm) => erpnext_enhancements.unified_controller.init(frm),
});
frappe.ui.form.on("Project", {
	refresh: (frm) => erpnext_enhancements.unified_controller.init(frm),
});
frappe.ui.form.on("Master Project", {
	refresh: (frm) => erpnext_enhancements.unified_controller.init(frm),
});
frappe.ui.form.on("Contact", {
	refresh: (frm) => erpnext_enhancements.unified_controller.init(frm),
});

erpnext_enhancements.unified_controller = {
	init: function (frm) {
		this.frm = frm;
		this.setup_queries();
		this.render_all();
		this.setup_events();
		this.setup_comments();
	},

	setup_comments: function () {
		const frm = this.frm;
		if (!frm.fields_dict.custom_comments_field || frm.is_new()) return;

		// ERPNext's CRMNotes widget needs the controller-side CRMNote mixin
		// (the add_note/edit_note/delete_note doc methods + the `notes` child
		// table), which only the CRM doctypes have. Mounting it on the other
		// wired doctypes crashed its New Note button with e.g.
		// "'EmployeeProject' object has no attribute 'add_note'" on Project —
		// and stomped the same field the threaded Comments App renders into.
		const CRM_NOTE_DOCTYPES = ["Lead", "Opportunity", "Prospect"];
		if (CRM_NOTE_DOCTYPES.includes(frm.doctype)) {
			if (window.erpnext && window.erpnext.utils && window.erpnext.utils.CRMNotes) {
				if (!frm.crm_notes) {
					frm.crm_notes = new window.erpnext.utils.CRMNotes({
						frm: frm,
						notes_wrapper: $(frm.fields_dict.custom_comments_field.wrapper),
					});
				}
				frm.crm_notes.refresh();
			}
		} else if (window.erpnext_enhancements && erpnext_enhancements.render_comments_app) {
			// Everything else gets the app's own Comments App (threaded notes).
			erpnext_enhancements.render_comments_app(frm, "custom_comments_field");
		}
	},

	// The Address LINK field differs per doctype: stock Customer/Supplier keep
	// the docname in customer_primary_address / supplier_primary_address —
	// their `primary_address` is the read-only TEXT display (HTML), which is
	// why the map used to show "Invalid address reference format" there —
	// while Project / Master Project use the app's custom `primary_address`
	// Link field.
	primary_address_link_field: function () {
		const frm = this.frm;
		if (frm.fields_dict.customer_primary_address) return "customer_primary_address";
		if (frm.fields_dict.supplier_primary_address) return "supplier_primary_address";
		const df = frm.fields_dict.primary_address && frm.fields_dict.primary_address.df;
		if (df && df.fieldtype === "Link") return "primary_address";
		return null;
	},

	primary_address_name: function () {
		const link_field = this.primary_address_link_field();
		return (
			(link_field && this.frm.doc[link_field]) ||
			this.frm.doc.customer_address ||
			this.frm.doc.supplier_address
		);
	},

	primary_contact_name: function () {
		return this.frm.doc.primary_contact || null;
	},

	// ---------------------------------------------------------------- primaries
	//
	// `Contact.is_primary_contact` and `Address.is_primary_address` are columns on
	// the CONTACT/ADDRESS record — not on the Dynamic Link row. Setting one is
	// therefore a statement about an entire account, and there is physically
	// nowhere in that scheme to record "primary for THIS Project". Only Customer
	// and Supplier are accounts in that sense.
	//
	// Everything else records its primary in its own doc-local Link field
	// (`primary_contact` / `primary_address`, provisioned by
	// setup/custom_fields.py). Until v1.198.0 this file derived the account as
	// `frm.doc.customer || frm.doc.supplier || frm.doc.party_name || frm.doc.name`,
	// which meant:
	//
	//   - on a **Project**, one "Set Primary" click re-pointed the CUSTOMER's
	//     primary across every contact on that account — a project-level decision
	//     silently rewriting a company-level fact;
	//   - on an **Opportunity**, it produced the pair ("Opportunity", <a Customer
	//     id>), because Opportunity's discriminator is `opportunity_from`, not
	//     `party_type`. The "unset the others" Dynamic Link query matched nothing,
	//     so the flag was set without clearing the previous one and several
	//     contacts ended up flagged primary for the same account at once.
	PARTY_DOCTYPES: ["Customer", "Supplier"],

	is_party_form: function () {
		return this.PARTY_DOCTYPES.indexOf(this.frm.doctype) !== -1;
	},

	// The account whose GLOBAL flag this form may write, or null. Always the open
	// document itself — never a related Customer or Lead.
	party_context: function () {
		if (!this.is_party_form()) return null;
		return { doctype: this.frm.doctype, name: this.frm.doc.name };
	},

	primary_scope_label: function () {
		const party = this.party_context();
		return party ? party.name : `${__(this.frm.doctype)} ${this.frm.doc.name}`;
	},

	setup_queries: function () {
		const frm = this.frm;
		const sources = this.get_all_party_sources();

		if (sources.length === 0) return;

		if (frm.fields_dict.primary_contact) {
			frm.set_query("primary_contact", () => {
				return {
					filters: [["Dynamic Link", "link_name", "in", sources.map((s) => s.name)]],
				};
			});
		}

		const address_link_field = this.primary_address_link_field();
		if (address_link_field) {
			frm.set_query(address_link_field, () => {
				return {
					filters: [["Dynamic Link", "link_name", "in", sources.map((s) => s.name)]],
				};
			});
		}
	},

	get_all_party_sources: function () {
		const frm = this.frm;
		let sources = [];

		sources.push({ doctype: frm.doctype, name: frm.doc.name });

		if (frm.doc.customer) sources.push({ doctype: "Customer", name: frm.doc.customer });
		if (frm.doc.supplier) sources.push({ doctype: "Supplier", name: frm.doc.supplier });
		if (frm.doc.party_name && frm.doc.party_type) {
			sources.push({ doctype: frm.doc.party_type, name: frm.doc.party_name });
		}
		// Opportunity's party discriminator is opportunity_from, not party_type —
		// without this its party (Customer/Lead/Prospect) was missing entirely.
		if (frm.doc.party_name && frm.doc.opportunity_from) {
			sources.push({ doctype: frm.doc.opportunity_from, name: frm.doc.party_name });
		}

		(frm.meta.fields || []).forEach((f) => {
			if (f.fieldtype === "Table" && frm.doc[f.fieldname]) {
				const grid_rows = frm.doc[f.fieldname];
				grid_rows.forEach((row) => {
					if (row.customer) sources.push({ doctype: "Customer", name: row.customer });
					if (row.supplier) sources.push({ doctype: "Supplier", name: row.supplier });
					if (row.party_name && row.party_type) {
						sources.push({ doctype: row.party_type, name: row.party_name });
					}
					// Handle standard Dynamic Link child table fields (link_doctype, link_name)
					if (row.link_doctype && row.link_name) {
						sources.push({ doctype: row.link_doctype, name: row.link_name });
					}
				});
			}
		});

		const unique_sources = [];
		const map = new Map();
		for (const item of sources) {
			if (item.name && !map.has(item.name)) {
				map.set(item.name, true);
				unique_sources.push(item);
			}
		}

		return unique_sources;
	},

	render_all: function () {
		this.render_contact_table();
		this.render_address_table();
		this.render_google_map();
	},

	setup_events: function () {
		const frm = this.frm;

		// frappe.ui.form.on APPENDS handlers — registering on every refresh
		// piled up duplicates that all fired on each field change.
		if (frm.__ee_utc_events_bound) return;
		frm.__ee_utc_events_bound = true;

		frappe.ui.form.on(frm.doctype, {
			customer: (frm) => this.render_all(),
			supplier: (frm) => this.render_all(),
			party_name: (frm) => this.render_all(),
			primary_address: (frm) => {
				this.render_google_map();
				this.render_address_table();
			},
		});
	},

	render_contact_table: function () {
		const frm = this.frm;
		if (!frm.fields_dict.contact_list_html) return;

		const sources = this.get_all_party_sources();
		const wrapper = $(frm.fields_dict.contact_list_html.wrapper);
		wrapper.empty();

		if (sources.length === 0) {
			wrapper.html(
				'<div class="alert alert-warning">No linked parties found to display contacts.</div>',
			);
			return;
		}

		wrapper.html('<div class="text-muted">Fetching aggregated contacts...</div>');

		const btn_container = $(
			'<div style="margin-bottom: 10px; display: flex; gap: 10px;"></div>',
		).appendTo(wrapper);

		// Quick-entry create (context self-resolves from the open form; falls
		// back to the stock full form when the toggle is off).
		$('<button class="btn btn-sm btn-primary">New Contact</button>')
			.appendTo(btn_container)
			.on("click", () => erpnext_enhancements.contacts_ux.new_contact());

		$('<button class="btn btn-sm btn-default">Link Existing</button>')
			.appendTo(btn_container)
			.on("click", () => this.link_existing_record("Contact"));

		frappe.call({
			method: "erpnext_enhancements.sync_contact.get_contacts_for_context",
			args: {
				sources: sources,
				context_doctype: frm.doctype,
				context_name: frm.doc.name,
			},
			callback: (r) => {
				wrapper.find(".text-muted").remove();
				if (!r.message || r.message.length === 0) {
					wrapper.append(
						'<div class="alert alert-warning">No contacts linked to any related parties yet.</div>',
					);
					return;
				}

				let table = `
					<div class="table-responsive">
					<table class="table table-bordered table-hover" style="background: var(--card-bg);">
						<thead>
							<tr>
								<th>Name</th>
								<th>Title</th>
								<th>Email</th>
								<th>Phone</th>
								<th>Linked To</th>
								<th>Actions</th>
							</tr>
						</thead>
						<tbody>
				`;

				const primary_contact_name = this.primary_contact_name();
				const on_party_form = this.is_party_form();
				r.message.forEach((c) => {
					const first_name = c.first_name || "";
					const last_name = c.last_name || "";
					const phone = c.custom_phone_number || c.custom_mobile_number || "";
					// This document's own answer. The global is_primary_contact flag only
					// means "primary for the Customer/Supplier", so it earns the badge on
					// an account form and nowhere else.
					const is_doc_primary =
						c.name === primary_contact_name || (on_party_form && !!c.is_primary_contact);
					const is_primary = is_doc_primary
						? `<span class="badge badge-info" style="font-size: 10px; margin-left: 8px; vertical-align: middle;">Primary</span>`
						: "";
					// A Project lists its Customer's contacts too, and which one the rest
					// of the business treats as primary is worth knowing — it is just a
					// different fact from this document's own primary. Second, quieter
					// badge rather than dropping the information.
					const account_primary =
						!is_doc_primary && !on_party_form && c.is_primary_contact
							? `<span class="badge badge-light" style="font-size: 10px; margin-left: 8px; vertical-align: middle;" title="${__("Primary for the account, not for this document")}">${__("Account primary")}</span>`
							: "";

					const contact_url = frappe.urllib.get_full_url(`/app/contact/${c.name}`);
					const email_link = c.custom_email
						? `<a href="mailto:${c.custom_email}">${c.custom_email}</a>`
						: "";
					const phone_link = phone ? `<a href="tel:${phone}">${phone}</a>` : "";

					const linked_to_links = (c.links || [])
						.map((l) => {
							const url = frappe.urllib.get_full_url(
								`/app/${frappe.router.slug(l.doctype)}/${l.name}`,
							);
							return `<a href="${url}" target="_blank">${l.name} (${l.doctype})</a>`;
						})
						.join(", ");

					table += `
						<tr data-name="${c.name}">
							<td>
								<a href="${contact_url}" target="_blank"><b>${first_name} ${last_name}</b></a>
								${is_primary}${account_primary}
							</td>
							<td>${c.custom_title || ""}</td>
							<td>${email_link}</td>
							<td>${phone_link}</td>
							<td><span style="font-size: 12px;">${linked_to_links}</span></td>
							<td>
								<button class="btn btn-xs btn-default edit-contact" data-name="${c.name}" title="Edit">
									<i class="fa fa-pencil"></i>
								</button>
								${
									primary_contact_name !== c.name
										? `
								<button class="btn btn-xs btn-primary set-primary-contact" data-name="${c.name}" style="margin-left: 5px;">
									Set Primary
								</button>`
										: ""
								}
								<button class="btn btn-xs btn-danger unlink-contact" data-name="${c.name}" style="margin-left: 5px;" title="Unlink">
									<i class="fa fa-unlink"></i>
								</button>
							</td>
						</tr>
					`;
				});

				table += "</tbody></table>";
				wrapper.append(table);

				wrapper.find(".edit-contact").on("click", (e) => {
					const name = $(e.currentTarget).data("name");
					window.open(frappe.urllib.get_full_url(`/app/contact/${name}`), "_blank");
				});

				wrapper.find(".set-primary-contact").on("click", (e) => {
					const name = $(e.currentTarget).data("name");
					this.set_primary_contact(name);
				});

				wrapper.find(".unlink-contact").on("click", (e) => {
					const name = $(e.currentTarget).data("name");
					this.unlink_record("Contact", name);
				});
			},
		});
	},

	render_address_table: function () {
		const frm = this.frm;
		if (!frm.fields_dict.address_list_html) return;

		const sources = this.get_all_party_sources();
		const wrapper = $(frm.fields_dict.address_list_html.wrapper);
		wrapper.empty();

		if (sources.length === 0) {
			wrapper.html(
				'<div class="alert alert-warning">No linked parties found to display addresses.</div>',
			);
			return;
		}

		wrapper.html('<div class="text-muted">Fetching aggregated addresses...</div>');

		const btn_container = $(
			'<div style="margin-bottom: 10px; display: flex; gap: 10px;"></div>',
		).appendTo(wrapper);

		// Quick-entry create; respects the Geolocation autocomplete dialog when
		// that feature is enabled (stock-section parity).
		$('<button class="btn btn-sm btn-primary">New Address</button>')
			.appendTo(btn_container)
			.on("click", () => erpnext_enhancements.contacts_ux.new_address(frm));

		$('<button class="btn btn-sm btn-default">Link Existing</button>')
			.appendTo(btn_container)
			.on("click", () => this.link_existing_record("Address"));

		frappe.call({
			method: "erpnext_enhancements.sync_contact.get_addresses_for_context",
			args: {
				sources: sources,
				context_doctype: frm.doctype,
				context_name: frm.doc.name,
			},
			callback: (r) => {
				wrapper.find(".text-muted").remove();
				if (!r.message || r.message.length === 0) {
					wrapper.append(
						'<div class="alert alert-warning">No addresses linked to any related parties yet.</div>',
					);
					return;
				}

				let table = `
					<div class="table-responsive">
					<table class="table table-bordered table-hover" style="background: var(--card-bg);">
						<thead>
							<tr>
								<th>Address</th>
								<th>Type</th>
								<th>Address Title</th>
								<th>Linked To</th>
								<th>Actions</th>
							</tr>
						</thead>
						<tbody>
				`;

				const primary_address_name = this.primary_address_name();
				const on_party_form = this.is_party_form();
				r.message.forEach((a) => {
					const full_address =
						a.custom_full_address ||
						[a.address_line1, a.address_line2].filter(Boolean).join(", ");
					// Same rule as the contact table: the doc-local field is this
					// document's answer; the global flag only speaks for the account.
					const is_doc_primary =
						a.name === primary_address_name || (on_party_form && !!a.is_primary_address);
					const is_primary = is_doc_primary
						? `<span class="badge badge-info" style="font-size: 10px; margin-left: 8px; vertical-align: middle;">Primary</span>`
						: "";
					const account_primary =
						!is_doc_primary && !on_party_form && a.is_primary_address
							? `<span class="badge badge-light" style="font-size: 10px; margin-left: 8px; vertical-align: middle;" title="${__("Primary for the account, not for this document")}">${__("Account primary")}</span>`
							: "";
					const address_url = frappe.urllib.get_full_url(`/app/address/${a.name}`);

					const linked_to_links = (a.links || [])
						.map((l) => {
							const url = frappe.urllib.get_full_url(
								`/app/${frappe.router.slug(l.doctype)}/${l.name}`,
							);
							return `<a href="${url}" target="_blank">${l.name} (${l.doctype})</a>`;
						})
						.join(", ");

					table += `
						<tr data-name="${a.name}">
							<td>
								<a href="${address_url}" target="_blank"><b>${full_address}</b></a>
								${is_primary}${account_primary}
							</td>
							<td>${a.address_type || ""}</td>
							<td>${a.address_title || ""}</td>
							<td><span style="font-size: 12px;">${linked_to_links}</span></td>
							<td>
								<button class="btn btn-xs btn-default edit-address" data-name="${a.name}" title="Edit">
									<i class="fa fa-pencil"></i>
								</button>
								${
									primary_address_name !== a.name
										? `
								<button class="btn btn-xs btn-primary set-primary-address" data-name="${a.name}" style="margin-left: 5px;">
									Set Primary
								</button>`
										: ""
								}
								<button class="btn btn-xs btn-danger unlink-address" data-name="${a.name}" style="margin-left: 5px;" title="Unlink">
									<i class="fa fa-unlink"></i>
								</button>
							</td>
						</tr>
					`;
				});

				table += "</tbody></table>";
				wrapper.append(table);

				wrapper.find(".edit-address").on("click", (e) => {
					const name = $(e.currentTarget).data("name");
					window.open(frappe.urllib.get_full_url(`/app/address/${name}`), "_blank");
				});

				wrapper.find(".set-primary-address").on("click", (e) => {
					const name = $(e.currentTarget).data("name");
					this.set_primary_address(name);
				});

				wrapper.find(".unlink-address").on("click", (e) => {
					const name = $(e.currentTarget).data("name");
					this.unlink_record("Address", name);
				});
			},
		});
	},

	link_existing_record: function (doctype) {
		frappe.prompt(
			[
				{
					label: `Select ${doctype}`,
					fieldname: "record",
					fieldtype: "Link",
					options: doctype,
					reqd: 1,
				},
			],
			(values) => {
				frappe.call({
					method: "erpnext_enhancements.sync_contact.link_existing_record",
					args: {
						doctype: doctype,
						docname: values.record,
						links: JSON.stringify(this.get_base_links()),
					},
					callback: (r) => {
						this.render_all();
						frappe.show_alert({
							message: `${doctype} linked successfully`,
							indicator: "green",
						});
					},
				});
			},
			`Add ${doctype}`,
			"Add",
		);
	},

	set_primary_address: function (address_name) {
		const frm = this.frm;
		// Write the docname into the doctype's actual Address LINK field — on
		// Customer/Supplier `primary_address` is the read-only TEXT display, which
		// the server fills on save (for Supplier: Address.custom_full_address).
		const link_field = this.primary_address_link_field();
		const party = this.party_context();

		frappe.confirm(
			__("Set this as the primary address for {0}?", [this.primary_scope_label()]),
			() => {
				const sync_party = () => {
					const done = () => {
						this.render_address_table();
						this.render_google_map();
						frappe.show_alert({
							message: __("Primary address updated"),
							indicator: "green",
						});
					};
					// Only an account carries the global flag. On every other form
					// the doc-local write above is the whole operation.
					if (!party) return done();
					frappe.call({
						method: "erpnext_enhancements.sync_contact.set_primary_address",
						args: {
							account_doctype: party.doctype,
							account_name: party.name,
							address_name: address_name,
						},
						callback: done,
					});
				};

				if (link_field) {
					frm.set_value(link_field, address_name);
					// `frm.save("Save", cb)` is the documented signature. The old code
					// called `frm.save().done(cb)` — frm.save() returns a native
					// Promise, which has no .done, so the re-render and the
					// confirmation toast never ran.
					frm.save("Save", sync_party);
				} else {
					sync_party();
				}
			},
		);
	},

	set_primary_contact: function (contact_name) {
		const frm = this.frm;
		if (!frm.fields_dict.primary_contact) return;
		const party = this.party_context();

		frappe.confirm(
			__("Set {0} as the primary contact for {1}?", [contact_name, this.primary_scope_label()]),
			() => {
				// Doc-local first: it is the authoritative per-document answer, and a
				// save that fails validation must not leave a global flag flipped
				// behind it. The old order did exactly that. On a non-account form
				// this is the ONLY write.
				frm.set_value("primary_contact", contact_name);
				frm.save("Save", () => {
					const done = () => {
						this.render_contact_table();
						frappe.show_alert({
							message: __("Primary contact updated"),
							indicator: "green",
						});
					};
					if (!party) return done();
					frappe.call({
						method: "erpnext_enhancements.sync_contact.set_primary_contact",
						args: {
							account_doctype: party.doctype,
							account_name: party.name,
							contact_name: contact_name,
						},
						callback: done,
					});
				});
			},
		);
	},

	get_base_links: function () {
		const sources = this.get_all_party_sources();
		return sources.map((s) => ({
			link_doctype: s.doctype,
			link_name: s.name,
		}));
	},

	unlink_record: function (doctype, docname) {
		const frm = this.frm;
		frappe.confirm(`Are you sure you want to unlink this ${doctype} from this document?`, () => {
			frappe.call({
				method: "erpnext_enhancements.sync_contact.unlink_record",
				args: {
					doctype: doctype,
					docname: docname,
					link_doctype: frm.doctype,
					link_name: frm.doc.name,
				},
				callback: (r) => {
					if (r.message) {
						this.render_all();
						frappe.show_alert({
							message: `${doctype} unlinked successfully`,
							indicator: "green",
						});
					}
				},
			});
		});
	},

	render_google_map: function () {
		const frm = this.frm;
		if (!frm.fields_dict.location_map_html) return;

		const wrapper = $(frm.fields_dict.location_map_html.wrapper);
		wrapper.empty();

		// Resolve the Address DOCNAME from the per-doctype Link field; the
		// fetched Address's custom_full_address is what feeds the embed below.
		const address_name = this.primary_address_name();
		if (!address_name) {
			wrapper.append(
				'<div class="alert alert-secondary">Select a Primary Address to view the map.</div>',
			);
			return;
		}

		// Ensure it's not an HTML string fallback by mistake (basic sanity check)
		if (address_name.includes('<br>') || address_name.includes('\n')) {
			wrapper.append(
				'<div class="alert alert-warning">Invalid address reference format.</div>',
			);
			return;
		}

		wrapper.append('<div class="text-muted">Loading map...</div>');

		frappe.db.exists("Address", address_name).then((exists) => {
			if (!exists) {
				wrapper.find(".text-muted").remove();
				wrapper.append(
					'<div class="alert alert-warning">Primary Address record not found or invalid.</div>',
				);
				return;
			}

			frappe.db.get_doc("Address", address_name).then((addr) => {
				wrapper.find(".text-muted").remove();
				if (addr) {
					const full_address =
						addr.custom_full_address ||
						[
							addr.address_line1,
							addr.address_line2,
							addr.city,
							addr.state,
							addr.pincode,
							addr.country,
						]
							.filter(Boolean)
							.join(", ");
					const encoded_address = encodeURIComponent(full_address);
					wrapper.append(`
						<div style="width: 100%; height: 250px;">
							<iframe width="100%" height="100%" frameborder="0" style="border:0" 
								src="https://maps.google.com/maps?q=${encoded_address}&output=embed" allowfullscreen>
							</iframe>
						</div>
					`);
				}
			});
		});
	},
};
