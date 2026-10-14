/*
 * sf_kit: the one HTML-escaping helper.
 *
 * Everything a person typed (a name, a note, an error the server sent back) goes through `esc`
 * before it becomes HTML. Six characters, so the result is safe in element text AND in a quoted
 * attribute of either kind; the backtick too, for old IE attribute parsing and template strings.
 *
 * Pure, and deliberately import-free: three planner suites (test_planner_phase6a, 6c, 6d_ui) load
 * this file as plain script text alongside planner_kit/escape.js, which re-exports it as `escape`.
 * The table has a name no planner module uses for the same reason.
 */

const SF_ESC_ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;" };

export function esc(value) {
	return (value === null || value === undefined ? "" : String(value)).replace(
		/[&<>"'`]/g,
		(ch) => SF_ESC_ENTITIES[ch]
	);
}
