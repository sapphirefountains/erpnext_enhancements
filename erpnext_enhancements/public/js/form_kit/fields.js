/*
 * form kit P4-B: per-field decisions the guided page makes before it draws a control.
 *
 *   inputmode_for(df, registry, doctype)   the phone keyboard a field asks for
 *   text_list_column(field)                a table drawn as a list of text boxes, or not
 *   add_row / remove_row / move_row        that list's edits, as new arrays (rows keep `name`)
 *   clean_rows(rows, columns)              a table's rows as save_step takes them
 *
 * Pure: no frappe, no DOM.
 */

// The keyboard each kind of field asks a phone for. A registered field (frappe.boot.sf_normalize,
// utils/normalize_fields.py) is matched first: its kind is what the server will normalize it as,
// so a Data field holding a phone number gets the phone pad even though its fieldtype says text.
const KIND_MODES = { phone: "tel", email: "email", website: "url" };
const TYPE_MODES = {
	Int: "numeric",
	Float: "decimal",
	Currency: "decimal",
	Percent: "decimal",
	Phone: "tel",
};
const OPTION_MODES = { Email: "email", URL: "url", Phone: "tel" };

function registered_kind(df, registry, doctype) {
	if (!df || !df.fieldname || !registry || !registry.fields) return null;
	const parent = df.parent || doctype;
	const fields = parent && Object.prototype.hasOwnProperty.call(registry.fields, parent) ? registry.fields[parent] : null;
	return fields && Object.prototype.hasOwnProperty.call(fields, df.fieldname) ? fields[df.fieldname] : null;
}

export function inputmode_for(df, registry, doctype) {
	if (!df) return null;
	const kind = registered_kind(df, registry, doctype);
	if (kind && KIND_MODES[kind]) return KIND_MODES[kind];
	if (TYPE_MODES[df.fieldtype]) return TYPE_MODES[df.fieldtype];
	if (df.fieldtype === "Data" && OPTION_MODES[df.options]) return OPTION_MODES[df.options];
	return null;
}

export const TEXT_TYPES = ["Data", "Small Text", "Text", "Long Text", "Text Editor"];

// The one text column of a table drawn as a list of text boxes (Customer Requests,
// Deliverables), or null for a table that keeps the grid. Only a plain Table whose child shows
// exactly one column, and that column is text: a second column would be lost from a list.
export function text_list_column(field) {
	if (!field || field.fieldtype !== "Table") return null;
	const columns = field.child_fields || [];
	if (columns.length !== 1) return null;
	const only = columns[0];
	return only && TEXT_TYPES.includes(only.fieldtype) ? only.fieldname : null;
}

function renumber(rows) {
	return rows.map((row, index) => Object.assign({}, row, { idx: index + 1 }));
}

export function add_row(rows, column, text) {
	const row = { [column]: text === undefined ? "" : text };
	return renumber([...(rows || []), row]);
}

export function remove_row(rows, index) {
	const list = [...(rows || [])];
	if (index < 0 || index >= list.length) return renumber(list);
	list.splice(index, 1);
	return renumber(list);
}

// Move the row at `index` one place up (dir -1) or down (dir 1). Out of range: unchanged.
export function move_row(rows, index, dir) {
	const list = [...(rows || [])];
	const to = index + dir;
	if (index < 0 || index >= list.length || to < 0 || to >= list.length) return renumber(list);
	const [row] = list.splice(index, 1);
	list.splice(to, 0, row);
	return renumber(list);
}

export function set_row_text(rows, index, column, text) {
	return renumber(
		(rows || []).map((row, at) => (at === index ? Object.assign({}, row, { [column]: text }) : row))
	);
}

// A table's rows as save_step takes them: the stored row's `name` (so it is updated, not
// replaced) and the child columns, nothing else. A grid row carries its own bookkeeping
// (doctype, __islocal, parent...) that the server would ignore anyway. A row added on the page
// has no stored name: a FieldGroup grid gives it a random one and marks it __islocal, and that
// name is dropped here (the server would drop it too, and append the row).
export function clean_rows(rows, columns) {
	return (rows || []).map((row) => {
		const out = {};
		if (row && row.name && !row.__islocal && !String(row.name).startsWith("new-")) out.name = row.name;
		(columns || []).forEach((column) => {
			if (row && Object.prototype.hasOwnProperty.call(row, column)) out[column] = row[column];
		});
		return out;
	});
}

// Contacts and addresses are picked from the record's own party, as the Desk form's Contact &
// Address directory offers them: the same whitelisted queries ERPNext's forms use
// (erpnext/public/js/queries.js contact_query / address_query, at version-16).
export const PARTY_QUERIES = {
	Contact: "frappe.contacts.doctype.contact.contact.contact_query",
	Address: "frappe.contacts.doctype.address.address.address_query",
};

// The record's party: the first Dynamic Link the flow shows (Opportunity's party_name, whose
// type is opportunity_from), as { doctype, name }, or null while either half is empty.
export function party_of(steps, values) {
	for (const step of steps || []) {
		for (const field of step.fields || []) {
			if (field && field.fieldtype === "Dynamic Link" && field.options) {
				const doctype = (values || {})[field.options];
				const name = (values || {})[field.fieldname];
				return doctype && name ? { doctype: String(doctype), name: String(name) } : null;
			}
		}
	}
	return null;
}

// The get_query answer for a Link to Contact or Address, or null to leave the Link alone.
export function link_query(df, party) {
	if (!df || df.fieldtype !== "Link" || !party || !PARTY_QUERIES[df.options]) return null;
	return { query: PARTY_QUERIES[df.options], filters: { link_doctype: party.doctype, link_name: party.name } };
}
