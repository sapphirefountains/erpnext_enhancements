/*
 * form kit P5.2: where a record of a guided DocType opens. Nik, 2026-10-13: "I want these guided
 * setups to be the default for new and existing documents of the DocTypes we are making guided
 * setups for." So a Form route for one of them goes to the guided page instead, unless the
 * person asked for the full form. Pure: no frappe, no DOM. public/js/guided_default.js applies it
 * to the Desk's router; the guided page uses the carry-over helpers.
 */

import { build_route } from "./route.js";

// Keys every document has that are not data a person entered.
const STANDARD_KEYS = new Set([
	"name",
	"doctype",
	"owner",
	"creation",
	"modified",
	"modified_by",
	"docstatus",
	"idx",
	"parent",
	"parentfield",
	"parenttype",
	"amended_from",
]);

function text(value) {
	return value === null || value === undefined ? "" : String(value);
}

function blank(value) {
	if (Array.isArray(value)) return value.length === 0;
	return text(value).trim() === "";
}

// A row as the guided page can send it: its own columns, none of the stored row's bookkeeping.
function plain_row(row) {
	const out = {};
	Object.keys(row || {}).forEach((key) => {
		if (!key.startsWith("__") && !STANDARD_KEYS.has(key)) out[key] = row[key];
	});
	return out;
}

// The Desk names an unsaved record "new-<doctype>-<random>"; "/desk/<doctype>/new" asks for one.
export function is_new_name(name) {
	const value = text(name);
	return value === "new" || value.startsWith("new-");
}

// One string per record, for the tab's list of records opened in the full form on purpose.
export function full_form_key(doctype, name) {
	return `${text(doctype)}/${text(name)}`;
}

// What an unsaved record holds beyond a fresh one: the values whatever made it put there
// (frappe.new_doc's options, a mapped document such as ERPNext's Create > Opportunity on a
// Customer). Defaults, internal keys, zeros and `ignore` (read-only and fetched fields, which the
// new record fills itself) do not count: a document mapped on the server carries 0 in every
// number field where one started in the browser has nothing (seen on prod, 2026-10-13). Returns
// { prefill, left }: `left` names what the guided page could not take, a field outside `accepts`.
// A table inside `accepts` comes along as plain rows (the Customer mapping copies its value
// streams onto the new Opportunity).
export function carry_over(doc, fresh, accepts, ignore) {
	const accepted = new Set(accepts || []);
	const skipped = new Set(ignore || []);
	const start = fresh || {};
	const prefill = {};
	const left = [];
	Object.keys(doc || {}).forEach((key) => {
		if (key.startsWith("__") || STANDARD_KEYS.has(key) || skipped.has(key)) return;
		const value = doc[key];
		if (blank(value)) return;
		// 0 where a fresh record has nothing (or 0) is the server's filler, not a value.
		if (value === 0 && (blank(start[key]) || Number(start[key]) === 0)) return;
		if (Array.isArray(value)) {
			if (accepted.has(key)) prefill[key] = value.map(plain_row);
			else left.push(key);
			return;
		}
		if (value !== null && typeof value === "object") return;
		if (text(value) === text(start[key])) return;
		if (accepted.has(key)) prefill[key] = value;
		else left.push(key);
	});
	return { prefill, left: left.sort() };
}

// What to do with a Form route: { route, prefill } to open the guided page instead, or null to
// leave the form alone. `ctx`:
//   guided     {doctype: {accepts: [...]}}, frappe.boot.sf_guided
//   page_open  whether this person may open the guided page at all
//   full_form  full_form_key()s this tab opened in the full form on purpose
//   hash       location.hash: a link into one tab or field of the form
//   from_link  a Link field's "Create a new ..." is waiting for the record to come back to it
//   renamed    names the Desk just gave unsaved records it saved (frappe.model.new_names)
//   options    the route options of the address (/desk/opportunity/new?party_name=...)
//   local      the unsaved record the Desk holds under this name, or null
//   fresh      a new record of the DocType as the Desk starts one, to compare `local` with
//   ignore     fields `carry_over` leaves out
export function redirect_for(route, ctx) {
	if (!Array.isArray(route) || route[0] !== "Form") return null;
	const doctype = text(route[1]);
	const name = route.slice(2).map(text).join("/");
	const c = ctx || {};
	const config = (c.guided || {})[doctype];
	if (!doctype || !name || !config || !c.page_open) return null;
	if (text(c.hash).replace(/^#/, "")) return null;
	if ((c.full_form || []).includes(full_form_key(doctype, name))) return null;
	if ((c.renamed || []).includes(name)) return null;
	if (c.local) {
		if (c.from_link) return null;
		const { prefill, left } = carry_over(c.local, c.fresh, config.accepts, c.ignore);
		// A start the guided page cannot take whole stays on the form, where nothing is lost.
		if (left.length) return null;
		return { route: build_route(doctype, null, null), prefill };
	}
	if (is_new_name(name)) {
		// What the address asked the new record to start with comes along.
		const options = c.options || {};
		const prefill = {};
		Object.keys(options).forEach((key) => {
			const value = options[key];
			if (key.startsWith("__") || STANDARD_KEYS.has(key) || blank(value) || typeof value === "object") return;
			prefill[key] = value;
		});
		return { route: build_route(doctype, null, null), prefill };
	}
	return { route: build_route(doctype, name, null), prefill: null };
}

// The guided page's own reading of what it was started with (route options): step 1's fields go
// into the step, the other fields `prefill_fields` names are sent with the create, and the rest
// is reported back so the page can say what it could not carry.
export function split_prefill(options, prefill_fields, create_fields) {
	const accepted = new Set(prefill_fields || []);
	const first = new Set(create_fields || []);
	const values = {};
	const carry = {};
	const dropped = [];
	Object.keys(options || {}).forEach((key) => {
		const value = options[key];
		if (key.startsWith("__") || STANDARD_KEYS.has(key) || blank(value)) return;
		if (first.has(key)) values[key] = value;
		else if (accepted.has(key)) carry[key] = value;
		else dropped.push(key);
	});
	return { values, carry, dropped: dropped.sort() };
}
