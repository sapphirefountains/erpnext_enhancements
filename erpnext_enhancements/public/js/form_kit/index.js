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
	ends_core,
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
	directory_names,
	resolve_filters,
	resolve_query,
	label_for,
} from "./fields.js";
export { same_value, split_conflict, resolve, value_text } from "./conflict.js";
export { STATES, create_save_queue } from "./save_queue.js";
export { STREAM_PILLAR, SUMMARY, pillar_for, streams_of, lead_pillar, summary_model } from "./summary.js";
export { is_new_name, full_form_key, carry_over, redirect_for, split_prefill, opens_by_default } from "./opening.js";
export { register_extension, extension_for, has_extension } from "./extensions.js";
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
// v1.686.0: the record tools' decisions (attachments, activity, connections, the Create menu). The
// tools themselves (./ui/record_tools.js) draw with frappe; the editor mounts them.
export {
	RECORD_TOOLS,
	tools_config,
	create_items,
	recipient_for,
	email_subject,
	email_start,
	with_emails,
	file_url,
	unique_files,
	size_text,
	kind_text,
	can_remove_files,
	can_add_file,
	version_value,
	describe_version,
	activity_feed,
	clean_tag,
	connection_groups,
	connection_doctypes,
	connection_counts,
	connection_filters,
	can_print,
	directory_widgets,
} from "./record_tools.js";
// v1.684.0: how a saved record is laid out for each person (view settings), and the set of
// FieldGroups the One page and Tabs layouts draw.
export {
	VIEW_SCHEMA,
	VIEW_DEFAULTS,
	SETUP_VIEW,
	SECTION_LAYOUTS,
	MAX_PINNED,
	MAX_HIDDEN,
	MAX_SUMMARY_FIELDS,
	MAX_PRESETS,
	MAX_PRESET_NAME,
	MAX_LAST,
	LANDINGS,
	PINNED,
	SHORTCUTS,
	clean_view,
	clean_keys,
	clean_landing,
	clean_presets,
	clean_last,
	clean_entry,
	entry_for,
	view_for,
	effective_view,
	same_view,
	is_default_view,
	shell_attrs,
	view_tabs,
	pinned_group,
	tab_of,
	tab_step,
	neighbor_tab,
	tab_missing,
	section_keys,
	neighbor_section,
	spy_pick,
	toggle_key,
	remember_last,
	last_step,
	start_step,
	has_value,
	must_show,
	hidden_when_filled_only,
	field_hidden,
	filled_snapshot,
	step_hidden,
	can_hide,
	save_preset,
	delete_preset,
	current_preset,
	shortcut_for,
} from "./view.js";
export { FieldGroups } from "./field_groups.js";
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
	same_details_args,
} from "./party.js";
