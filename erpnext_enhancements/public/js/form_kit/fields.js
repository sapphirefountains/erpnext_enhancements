/*
 * form kit P4-B: per-field decisions the guided page makes before it draws a control.
 *
 *   inputmode_for(df, registry, doctype)   the phone keyboard a field asks for
 *   text_list_column(field)                a table drawn as a list of text boxes, or not
 *   add_row / remove_row / move_row        that list's edits, as new arrays (rows keep `name`)
 *   clean_rows(rows, columns)              a table's rows as save_step takes them
 *   link_query / resolve_query             a Link's get_query: the form scripts' set_query, as data
 *   label_for(field, label_from, values)   a label another field's value sets (party_name)
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

// Contacts and addresses are picked from the record's own party: the same whitelisted queries
// ERPNext's forms use (erpnext/public/js/queries.js contact_query / address_query, at version-16).
// A saved record also offers its own (link_query's `sources`, below), as the Desk form's Contact &
// Address directory does.
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

// The names the Contact & Address directory searches by (unified_tab_controller.js
// get_all_party_sources): the record's own sources (query_context.directory: the saved record, the
// parties its tables name) and the live party, each once.
export function directory_names(sources, party) {
	const out = [];
	[...(sources || []), party && party.name].forEach((name) => {
		const text = name === null || name === undefined ? "" : String(name).trim();
		if (text && !out.includes(text)) out.push(text);
	});
	return out;
}

// The get_query answer for a Link to Contact or Address, or null to leave the Link alone.
// v1.685.0: with `sources` (a saved record's own, query_context.directory), the Desk directory's
// filter: Dynamic Link link_name in the record and its party. On prod 36 contacts and 12 addresses
// were linked only to their Opportunity (a general contractor's job site), and the party-only query
// could not find them again once cleared. Without sources (a new record) it is the party's own.
export function link_query(df, party, sources) {
	if (!df || df.fieldtype !== "Link" || !PARTY_QUERIES[df.options]) return null;
	if (sources && sources.length) {
		const names = directory_names(sources, party);
		return { filters: [["Dynamic Link", "link_name", "in", names]] };
	}
	if (!party) return null;
	return { query: PARTY_QUERIES[df.options], filters: { link_doctype: party.doctype, link_name: party.name } };
}

// ---------------------------------------------------------------------------- queries (v1.685.0)
// The form scripts' set_query, as data: the config's `queries` (form_kit/config/__init__.py), sent
// by get_flow. The guided page's controls are a FieldGroup with no frm, so frm.set_query never
// reaches them; the editor gives each control `get_query: () => resolve_query(spec, ctx)` instead.
//
//   ctx = { values, party, directory }   the record's values (plus query_context.fields), its live
//                                        party ({ doctype, name } or null) and the directory's
//                                        link sources (query_context.directory)
//   ctx.row                              for a child table's column ("table.field" in `queries`),
//                                        the grid row being edited
//
// Returns what a Link's get_query returns ({ query?, filters? }), or null for no filter.

function blank_value(value) {
	return value === null || value === undefined || (typeof value === "string" && value.trim() === "");
}

// A filter value: { field } is the record's value of that field, { row } the value of a column of
// the grid row being edited (a child table's query, as the form reads locals[cdt][cdn]); anything
// else is itself.
function filter_value(value, values, row) {
	if (value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === 1) {
		let read = value;
		if ("field" in value) read = (values || {})[value.field];
		else if ("row" in value) read = (row || {})[value.row];
		else return value;
		return blank_value(read) ? undefined : read;
	}
	return value;
}

// Filters with every { field } read from the record and every { row } from the grid row. A filter
// whose value is still empty is left out, as a form's Link drops an empty Link value from its
// filters (link.js set_custom_query).
export function resolve_filters(filters, values, row) {
	if (Array.isArray(filters)) {
		const out = [];
		filters.forEach((filter) => {
			if (!Array.isArray(filter) || !filter.length) return;
			const value = filter_value(filter[filter.length - 1], values, row);
			if (value === undefined) return;
			out.push([...filter.slice(0, -1), value]);
		});
		return out;
	}
	const out = {};
	Object.keys(filters || {}).forEach((key) => {
		const spec = filters[key];
		if (Array.isArray(spec) && spec.length === 2) {
			const value = filter_value(spec[1], values, row);
			if (value !== undefined) out[key] = [spec[0], value];
			return;
		}
		const value = filter_value(spec, values, row);
		if (value !== undefined) out[key] = value;
	});
	return out;
}

export function resolve_query(spec, ctx) {
	if (!spec || typeof spec !== "object") return null;
	const c = ctx || {};
	const values = c.values || {};
	if (spec.by) {
		const key = values[spec.by];
		const cases = spec.cases || {};
		const chosen = !blank_value(key) && Object.prototype.hasOwnProperty.call(cases, key) ? cases[key] : null;
		return chosen ? resolve_query(chosen, ctx) : null;
	}
	if (spec.links === "directory") {
		const names = directory_names(c.directory, c.party);
		return names.length ? { filters: [["Dynamic Link", "link_name", "in", names]] } : null;
	}
	const out = {};
	if (spec.query) out.query = spec.query;
	if (spec.filters) {
		const filters = resolve_filters(spec.filters, values, c.row);
		if (Array.isArray(filters) ? filters.length : Object.keys(filters).length) out.filters = filters;
	}
	return Object.keys(out).length ? out : null;
}

// A field's label as the form shows it: another field's value where the config's `label_from` says
// so (ERPNext's set_dynamic_field_label: party_name reads "Customer", "Lead" or "Prospect"), else
// its own. The control translates it (__), as the form does.
export function label_for(field, label_from, values) {
	if (!field) return "";
	const from = (label_from || {})[field.fieldname];
	const value = from ? (values || {})[from] : null;
	return blank_value(value) ? field.label || field.fieldname || "" : String(value);
}
