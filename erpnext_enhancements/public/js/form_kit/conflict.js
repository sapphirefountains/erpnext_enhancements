/*
 * form kit P4-B: comparing values, and settling a save that came back as a conflict.
 *
 * save_step refuses to write when the record's `modified` has moved and returns
 * { conflict: true, modified, modified_by, server: {fieldname: value} } (form_kit/saver.py), where
 * `server` holds the fields the page sent, as they now stand. Three versions of each field are
 * then in hand: `base` (what the page last loaded or saved), `mine` (what it sent) and `theirs`
 * (`server`). split_conflict() settles what it can without asking:
 *
 *   theirs equals mine            nothing to decide; nothing to resend
 *   theirs equals base            the other person did not touch it; mine is kept and resent
 *   otherwise                     a real conflict: the person sees both and picks
 *
 * so the person is only asked about a field both of them changed. resolve() turns their picks
 * into the values to save again, with the new `modified`.
 *
 * Pure: no frappe, no DOM.
 */

// Keys a table row carries for bookkeeping, never compared.
const ROW_META = new Set([
	"name",
	"idx",
	"doctype",
	"parent",
	"parenttype",
	"parentfield",
	"docstatus",
	"owner",
	"creation",
	"modified",
	"modified_by",
]);

function empty(value) {
	return value === null || value === undefined || (typeof value === "string" && value.trim() === "");
}

function numeric(value) {
	if (typeof value === "number") return Number.isFinite(value);
	return typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value));
}

function row_keys(row) {
	return Object.keys(row || {}).filter((key) => !ROW_META.has(key) && !key.startsWith("__"));
}

function same_rows(a, b) {
	const left = a || [];
	const right = b || [];
	if (left.length !== right.length) return false;
	return left.every((row, index) => {
		const other = right[index] || {};
		const keys = new Set([...row_keys(row), ...row_keys(other)]);
		return [...keys].every((key) => same_value((row || {})[key], other[key]));
	});
}

// Whether two values of one field mean the same thing: blank is blank whatever spells it
// (null, "", "  "), 1000 and "1000.0" are one number, and a table is its rows' contents in
// order, names and bookkeeping aside.
export function same_value(a, b) {
	if (Array.isArray(a) || Array.isArray(b)) {
		const left = Array.isArray(a) ? a : empty(a) ? [] : null;
		const right = Array.isArray(b) ? b : empty(b) ? [] : null;
		if (!left || !right) return false;
		return same_rows(left, right);
	}
	if (empty(a) && empty(b)) return true;
	if (empty(a) || empty(b)) return false;
	if (numeric(a) && numeric(b) && (typeof a === "number" || typeof b === "number")) {
		return Number(a) === Number(b);
	}
	return String(a) === String(b);
}

// { resend: {fieldname: mine}, conflicts: [{fieldname, mine, theirs}] }.
export function split_conflict(base, mine, theirs) {
	const resend = {};
	const conflicts = [];
	Object.keys(mine || {}).forEach((fieldname) => {
		const my_value = mine[fieldname];
		const has_theirs = theirs && Object.prototype.hasOwnProperty.call(theirs, fieldname);
		if (!has_theirs) {
			// The server did not report it (no longer offered to this user): leave it to the save.
			resend[fieldname] = my_value;
			return;
		}
		const their_value = theirs[fieldname];
		if (same_value(my_value, their_value)) return;
		if (base && Object.prototype.hasOwnProperty.call(base, fieldname) && same_value(base[fieldname], their_value)) {
			resend[fieldname] = my_value;
			return;
		}
		conflicts.push({ fieldname, mine: my_value, theirs: their_value });
	});
	return { resend, conflicts };
}

// The values to save after the person chose, per conflicting field, "mine" or "theirs".
// `choices` is { fieldname: "mine" | "theirs" }; a field with no choice keeps mine.
// Returns { save: {fieldname: value}, take: {fieldname: value} }: `save` goes back to the server
// with the new `modified`; `take` is what the page shows in place of mine (their value, which is
// already stored, so it is not resent).
export function resolve(resend, conflicts, choices) {
	const save = Object.assign({}, resend || {});
	const take = {};
	(conflicts || []).forEach((item) => {
		if ((choices || {})[item.fieldname] === "theirs") take[item.fieldname] = item.theirs;
		else save[item.fieldname] = item.mine;
	});
	return { save, take };
}

// A value as one line of text for the side-by-side view. A table becomes its rows' text.
export function value_text(value) {
	if (Array.isArray(value)) {
		return value
			.map((row) =>
				row_keys(row)
					.map((key) => row[key])
					.filter((part) => !empty(part))
					.join(" · ")
			)
			.filter(Boolean)
			.join("; ");
	}
	if (empty(value)) return "";
	return String(value);
}
