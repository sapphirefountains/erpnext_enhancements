/*
 * form kit P4-B: which steps the guided page walks, and what is left to fill on each.
 *
 * A step's `applies` rule is data from the engine (form_kit/steps.py `applies`):
 * { field, row_field?, in: [values] }. The page re-evaluates it against the values on screen, so
 * picking Design in the value streams offers the Design scope step at once, with no round trip.
 * rule_applies() is the Python function line for line, and test_guided_entry holds the two to
 * the same answers.
 *
 * Pure: no frappe, no DOM.
 */

export const LAYOUT_TYPES = ["Section Break", "Column Break", "Tab Break"];

export function is_layout(field) {
	return !!field && LAYOUT_TYPES.includes(field.fieldtype);
}

// form_kit/steps.py applies(rule, doc), for a plain object of values.
export function rule_applies(rule, values) {
	if (!rule) return true;
	if (!values) return false;
	const value = values[rule.field];
	let chosen;
	if (Array.isArray(value)) {
		chosen = value.map((row) => (rule.row_field ? (row || {})[rule.row_field] : null));
	} else {
		chosen = [value];
	}
	return (rule.in || []).some((choice) => chosen.includes(choice));
}

export function step_active(step, values) {
	if (!step) return false;
	return step.applies ? rule_applies(step.applies, values) : true;
}

// The steps the page walks, in order: the ones whose rule holds for `values`.
export function active_steps(steps, values) {
	return (steps || []).filter((step) => step_active(step, values));
}

export function find_step(steps, key) {
	return (steps || []).find((step) => step.key === key) || null;
}

// The active step before (dir -1) or after (dir 1) `key`, or null at either end. Works from a
// step that has just stopped applying too (its position in the full list still counts).
export function neighbor(steps, values, key, dir) {
	const all = steps || [];
	const at = all.findIndex((step) => step.key === key);
	if (at < 0) return null;
	for (let i = at + dir; i >= 0 && i < all.length; i += dir) {
		if (step_active(all[i], values)) return all[i].key;
	}
	return null;
}

// Whether `key` is the last core step: the next active step is a "more" one (form_kit/config,
// `more`), so the footer offers Done, and the rest stays behind More details and in the rail.
// False on a "more" step itself, and on a flow without "more" steps (Opportunity), whose last
// step offers Done the ordinary way.
export function ends_core(steps, values, key) {
	const step = find_step(steps, key);
	if (!step || step.more) return false;
	const next = neighbor(steps, values, key, 1);
	return !!next && !!(find_step(steps, next) || {}).more;
}

// The step the address should show: the one asked for when it exists and applies; otherwise the
// next active step after it (a stream's page whose stream was just removed), else the first.
// `replace` says the address must be corrected in place, never pushed.
export function resolve_step(steps, values, key) {
	const active = active_steps(steps, values);
	if (!active.length) return { key: null, replace: false };
	if (!key) return { key: active[0].key, replace: true };
	const asked = find_step(steps, key);
	if (asked && step_active(asked, values)) return { key, replace: false };
	if (asked) {
		const next = neighbor(steps, values, key, 1) || neighbor(steps, values, key, -1);
		return { key: next || active[0].key, replace: true };
	}
	return { key: active[0].key, replace: true };
}

// "Step 3 of 9", counted over the active steps.
export function position(steps, values, key) {
	const active = active_steps(steps, values);
	const index = active.findIndex((step) => step.key === key);
	return { index, number: index + 1, total: active.length };
}

// { fieldname: step key } for every value field the flow shows.
export function field_steps(steps) {
	const out = {};
	(steps || []).forEach((step) => {
		(step.fields || []).forEach((field) => {
			if (field && field.fieldname && !is_layout(field) && !(field.fieldname in out)) {
				out[field.fieldname] = step.key;
			}
		});
	});
	return out;
}

// completeness.missing ([{fieldname, label}]) grouped by the step that holds each field:
// { step_key: [{fieldname, label}] }. A requirement whose field no step shows (a level the user
// cannot read) lands under "" so the overall count still adds up.
export function missing_by_step(steps, missing) {
	const where = field_steps(steps);
	const out = {};
	(missing || []).forEach((item) => {
		const key = (item && where[item.fieldname]) || "";
		(out[key] = out[key] || []).push(item);
	});
	return out;
}

// The required fields of `fields` (a step's field defs) that are empty in `values`, editable
// ones only: the first step checks these before it asks the server to create the record.
export function missing_required(fields, values) {
	return (fields || []).filter((field) => {
		if (!field || is_layout(field) || !field.reqd || field.read_only) return false;
		const value = (values || {})[field.fieldname];
		if (Array.isArray(value)) return value.length === 0;
		return value === null || value === undefined || String(value).trim() === "";
	});
}

// The step's fields to save: every value field that is not read-only.
export function editable_fieldnames(step) {
	return ((step && step.fields) || [])
		.filter((field) => field && field.fieldname && !is_layout(field) && !field.read_only)
		.map((field) => field.fieldname);
}
