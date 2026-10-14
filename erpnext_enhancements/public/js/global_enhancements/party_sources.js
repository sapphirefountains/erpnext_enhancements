/*
 * The Contact and Address directory's rules, in one place for both of its faces (v1.686.0):
 *
 *   - the Desk form's directory widget (unified_tab_controller.js, a doctype_js that cannot import:
 *     it calls these through erpnext_enhancements.party_sources, which erpnext_enhancements.bundle.js
 *     installs), and
 *   - the guided page's record tools (form_kit/ui/record_tools.js, which imports them).
 *
 * They were the controller's own methods until v1.686.0 and moved here unchanged, so the guided page
 * lists exactly the contacts and addresses the form lists, through the same sync_contact endpoints,
 * without a second copy of the rules. scripts/test_party_sources.mjs runs them through the
 * controller.
 *
 * Pure: no DOM, no frappe. `doc` is the record's values (with its `name`); `fields` its DocType's
 * fields ([{fieldname, fieldtype, options}]).
 */

// Only an account carries the global is_primary_contact / is_primary_address flags (see the
// controller's "primaries" note); every other record keeps its primary in its own Link field.
export const ACCOUNT_DOCTYPES = ["Customer", "Supplier"];

export function is_account_doctype(doctype) {
	return ACCOUNT_DOCTYPES.indexOf(doctype) !== -1;
}

/**
 * Every party the record draws its directory from: the record itself, its Customer or Supplier, the
 * deal a Project was won from, its party (party_type, or an Opportunity's opportunity_from), and each
 * table row naming a party or a Dynamic Link. De-duplicated on the (doctype, name) PAIR: keying on
 * the name alone dropped the second of two parties that share a name (v1.561.1).
 */
export function party_sources(doctype, doc, fields) {
	const d = doc || {};
	const sources = [];

	sources.push({ doctype: doctype, name: d.name });

	if (d.customer) sources.push({ doctype: "Customer", name: d.customer });
	if (d.supplier) sources.push({ doctype: "Supplier", name: d.supplier });
	// A Project shows the people on the deal it was won from. Since v1.575.0 Link Existing on an
	// Opportunity links the Opportunity only (no longer its Customer as well), so without this a
	// person added on the deal vanished from the job. Display and Import only, like every source here.
	if (d.custom_opportunity) {
		sources.push({ doctype: "Opportunity", name: d.custom_opportunity });
	}
	if (d.party_name && d.party_type) {
		sources.push({ doctype: d.party_type, name: d.party_name });
	}
	// Opportunity's party discriminator is opportunity_from, not party_type: without this its party
	// (Customer/Lead/Prospect) was missing entirely.
	if (d.party_name && d.opportunity_from) {
		sources.push({ doctype: d.opportunity_from, name: d.party_name });
	}

	(fields || []).forEach((f) => {
		if (f && f.fieldtype === "Table" && Array.isArray(d[f.fieldname])) {
			d[f.fieldname].forEach((row) => {
				if (!row) return;
				if (row.customer) sources.push({ doctype: "Customer", name: row.customer });
				if (row.supplier) sources.push({ doctype: "Supplier", name: row.supplier });
				if (row.party_name && row.party_type) {
					sources.push({ doctype: row.party_type, name: row.party_name });
				}
				// Standard Dynamic Link child table fields (link_doctype, link_name).
				if (row.link_doctype && row.link_name) {
					sources.push({ doctype: row.link_doctype, name: row.link_name });
				}
			});
		}
	});

	const unique_sources = [];
	const seen = new Set();
	for (const item of sources) {
		const key = `${item.doctype}\u0000${item.name}`;
		if (item.name && !seen.has(key)) {
			seen.add(key);
			unique_sources.push(item);
		}
	}
	return unique_sources;
}

/**
 * The record's Address LINK field. Stock Customer/Supplier keep the docname in
 * customer_primary_address / supplier_primary_address (their `primary_address` is the read-only TEXT
 * display); Project, Master Project and Opportunity use the app's `primary_address` Link.
 */
export function primary_address_field(fields) {
	const by_name = {};
	(fields || []).forEach((f) => {
		if (f && f.fieldname) by_name[f.fieldname] = f;
	});
	if (by_name.customer_primary_address) return "customer_primary_address";
	if (by_name.supplier_primary_address) return "supplier_primary_address";
	if (by_name.primary_address && by_name.primary_address.fieldtype === "Link") return "primary_address";
	return null;
}

/** The record's primary Address: its Link field, else ERPNext's customer_address / supplier_address. */
export function primary_address_name(doc, fields) {
	const d = doc || {};
	const link_field = primary_address_field(fields);
	return (link_field && d[link_field]) || d.customer_address || d.supplier_address || null;
}

/**
 * The record's own party, offered as an explicit opt-in on Link Existing: a Project's Customer, an
 * Opportunity's Customer, Lead or Prospect. Null on an account, a Master Project, an unsaved record.
 */
export function form_party(doctype, doc) {
	const d = doc || {};
	if (!d.name) return null;
	if (doctype === "Project" && d.customer) {
		return { link_doctype: "Customer", link_name: d.customer };
	}
	if (doctype === "Opportunity" && d.opportunity_from && d.party_name) {
		return { link_doctype: d.opportunity_from, link_name: d.party_name };
	}
	return null;
}
