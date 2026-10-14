/*
 * form kit P4.4, P6.6, P6.7: the side panel's decisions, as a pure module (no frappe, no DOM), run
 * under node by tests/test_opportunity_board_panel.py. The panel itself is ./ui/side_panel.js.
 *
 * The panel draws fields the flow has in no single step: a move's required fields (Closed Won's
 * ranks, from the config's `won`) and the board's quick add (the config's `quick`). Both are a step
 * of the panel's own, built from the flow's own field definitions, so a field keeps its label, hint,
 * help, options and permission exactly as the guided page shows it.
 */

import { is_layout } from "./steps.js";
import { same_value } from "./conflict.js";

// The flow's definition of each named field, from whichever step holds it, in the order asked,
// each with `anchor`: that step's key (where its "Learn more" lands). A field the flow does not
// send (a level the person cannot read) is left out.
export function field_defs(flow, fieldnames) {
	const found = {};
	((flow || {}).steps || []).forEach((step) => {
		(step.fields || []).forEach((field) => {
			if (!field || !field.fieldname || is_layout(field) || found[field.fieldname]) return;
			found[field.fieldname] = Object.assign({}, field, { anchor: step.key });
		});
	});
	return (fieldnames || []).filter((fieldname) => found[fieldname]).map((fieldname) => found[fieldname]);
}

// A step of the panel's own: { key, title, intro, fields, synthetic }.
export function panel_step(key, title, intro, fields) {
	return { key, title: title || "", intro: intro || "", fields: fields || [], synthetic: 1, read_only: 0 };
}

// What moving a record into `status` must carry, from its flow:
//   { kind: "lost", status }                  the config's `lost` (reasons, competitors, detail)
//   { kind: "won", status, fieldnames: [...] } the config's `won` (on Opportunity, the ranks)
//   null                                      nothing beyond the status
export function gate_for(flow, status) {
	const f = flow || {};
	if (!status) return null;
	if (f.lost && f.lost.status === status) return { kind: "lost", status };
	if (f.won && f.won.status === status && Array.isArray(f.won.fields) && f.won.fields.length) {
		return { kind: "won", status, fieldnames: f.won.fields.slice() };
	}
	return null;
}

// What Set as Lost asks for (the config's `lost`, as get_flow sends it): the Desk form's dialog's
// three fields, for frappe.ui. `t` translates (frappe's __). One list for the guided page's dialog,
// the side panel's Lost and the board's bulk Lost.
export function lost_fields(lost, t) {
	const tr = typeof t === "function" ? t : (s) => s;
	const l = lost || {};
	return [
		{ fieldtype: "Table MultiSelect", label: tr("Lost Reasons"), fieldname: "reasons", options: l.reasons, reqd: 1 },
		{ fieldtype: "Table MultiSelect", label: tr("Competitors"), fieldname: "competitors", options: l.competitors },
		{ fieldtype: "Small Text", label: tr("Detailed Reason"), fieldname: "detail" },
	];
}

function blank(value) {
	if (Array.isArray(value)) return value.length === 0;
	return value === null || value === undefined || String(value).trim() === "";
}

// A gate's fields still blank in `values`: every one is required for the move, whatever its own
// `reqd` says (Closed Won's ranks are optional on the form until the status is Closed Won).
export function gate_missing(fields, values) {
	return (fields || []).filter((field) => field && !is_layout(field) && blank((values || {})[field.fieldname]));
}

// Step 1's fields a create does not assign (create_record takes only the "create" ones) whose value
// differs from the new record's own default: { fieldname: value }. These go with the create as
// prefill, which the server takes for any field the flow lets the person write.
export function create_extras(step, values, defaults) {
	const out = {};
	((step || {}).fields || []).forEach((field) => {
		if (!field || !field.fieldname || is_layout(field) || field.create || field.read_only) return;
		if (field.fieldtype === "Table" || field.fieldtype === "Table MultiSelect") return;
		const fieldname = field.fieldname;
		if (!Object.prototype.hasOwnProperty.call(values || {}, fieldname)) return;
		const value = values[fieldname];
		if (same_value(value, (defaults || {})[fieldname])) return;
		out[fieldname] = value;
	});
	return out;
}

// What quick add starts from, for a new record's flow (get_flow with no name, which sends `quick`):
// { fieldnames, values, carry }. `values` are step 1's: the record's defaults, then `fixed`, then
// the person as owner when the default leaves it blank. `carry` goes with the create as prefill:
// the column's stage. null when the flow has no quick add.
export function quick_start(flow, user, stage) {
	const f = flow || {};
	const quick = f.quick;
	if (!quick || !Array.isArray(quick.fields) || !quick.fields.length) return null;
	const values = Object.assign({}, f.values || {}, quick.fixed || {});
	if (quick.owner_field && blank(values[quick.owner_field]) && user) values[quick.owner_field] = user;
	const carry = {};
	if (quick.stage_field && stage) carry[quick.stage_field] = stage;
	return { fieldnames: quick.fields.slice(), values, carry };
}

// The panel's key for what it shows, so the page can tell "already showing this" from a new one:
// "edit:<name>", "move:<name>:<status>", "new:<stage>".
export function panel_key(spec) {
	const s = spec || {};
	if (s.kind === "edit") return `edit:${s.name || ""}`;
	if (s.kind === "move") return `move:${s.name || ""}:${s.status || ""}`;
	if (s.kind === "new") return `new:${s.stage || ""}`;
	return "";
}

// The fields a panel session changed and saved, and what each was when the panel opened: the
// Undo toast puts these back. `before` is the record as the panel first loaded it, `saved` each
// saved field's value now. A field saved back to its opening value is not a change.
export function undo_values(before, saved) {
	const out = {};
	Object.keys(saved || {}).forEach((fieldname) => {
		if (!Object.prototype.hasOwnProperty.call(before || {}, fieldname)) return;
		if (same_value(before[fieldname], saved[fieldname])) return;
		out[fieldname] = before[fieldname];
	});
	return out;
}
