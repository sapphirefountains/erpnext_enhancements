/*
 * form kit P4-B: the guided page's decisions, as pure ES modules (no DOM, no frappe at import
 * time), so tests/test_guided_entry.py runs every one under node. The page that uses them is
 * enhancements_core/page/guided_entry/guided_entry.js; the bundle that carries them to it is
 * public/js/guided_entry.bundle.js (window.sf_form_kit).
 */

export { PAGE, NEW, parse_route, build_route, route_key } from "./route.js";
export {
	LAYOUT_TYPES,
	is_layout,
	rule_applies,
	step_active,
	active_steps,
	find_step,
	neighbor,
	resolve_step,
	position,
	field_steps,
	missing_by_step,
	missing_required,
	editable_fieldnames,
} from "./steps.js";
export {
	inputmode_for,
	TEXT_TYPES,
	text_list_column,
	add_row,
	remove_row,
	move_row,
	set_row_text,
	clean_rows,
	PARTY_QUERIES,
	party_of,
	link_query,
} from "./fields.js";
export { same_value, split_conflict, resolve, value_text } from "./conflict.js";
export { STATES, create_save_queue } from "./save_queue.js";
export { STREAM_PILLAR, SUMMARY, pillar_for, streams_of, lead_pillar, summary_model } from "./summary.js";
export { is_new_name, full_form_key, carry_over, redirect_for, split_prefill } from "./opening.js";
export {
	JOURNAL_PREFIX,
	JOURNAL_MAX_AGE_MS,
	NEW_PREFIX,
	journal_key,
	new_key,
	new_key_prefix,
	stale_entry,
	usable_new,
	try_later,
	make_entry,
	usable_entry,
	reconcile,
} from "./journal.js";
// P4.4 / P6.6 / P6.7: the side panel's decisions. The panel itself (./ui/side_panel.js) and the
// record editor both shells run (./ui/editor.js) draw with frappe, so they are not exported here:
// the bundle adds them (guided_entry.bundle.js).
export {
	field_defs,
	panel_step,
	gate_for,
	gate_missing,
	lost_fields,
	create_extras,
	quick_start,
	panel_key,
	undo_allowed,
	undo_values,
} from "./panel.js";
// P3.2: the Party entry component's decisions. The component (./ui/party.js) draws with frappe and
// reads the information blocks; the bundle adds it.
export {
	FOLLOWERS,
	party_roles,
	role_of,
	account_type_of,
	account_of,
	create_kind,
	details_args,
	plan_account_change,
} from "./party.js";
