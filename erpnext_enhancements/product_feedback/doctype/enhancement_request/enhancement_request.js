// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Enhancement Request form: the "Claude Code Brief" button (WI-079 slice 4, ADR 0016 §5).
 *
 * Once a request has Tasks on the board, a reviewer can take the work to a Claude Code
 * session. The button asks `api.feedback.claude_code_brief` for the brief (Markdown in the
 * work-item shape plus a JSON block) and shows it read-only, with a Copy button.
 *
 * Shown to System Managers only, which is the reviewer role (`REVIEWER_ROLE` in
 * `api/feedback.py`), and only when the request has created Tasks: status `Tasks Created`, or
 * a proposal row stamped with `created_task`, which a confirm that partly failed leaves on a
 * request still in `Breakdown Ready`. The server checks both again; this only hides a button
 * that would be refused.
 *
 * `frappe.call` posts by default, which the endpoint requires (every feedback endpoint is
 * POST-only). Frappe loads this file for the form by its folder name, so it needs no
 * `doctype_js` entry in hooks.py.
 */

const BRIEF_METHOD = "erpnext_enhancements.api.feedback.claude_code_brief";

function has_created_tasks(frm) {
	if (frm.doc.status === "Tasks Created") return true;
	return (frm.doc.proposed_tasks || []).some((row) => Boolean(row.created_task));
}

function may_brief() {
	return frappe.user_roles.includes("System Manager");
}

function show_brief(frm) {
	frappe.call({
		method: BRIEF_METHOD,
		args: { name: frm.doc.name },
		freeze: true,
		freeze_message: __("Writing the brief..."),
		callback: (r) => {
			const brief = (r.message && r.message.markdown) || "";
			const dialog = new frappe.ui.Dialog({
				title: __("Claude Code Brief"),
				size: "extra-large",
				fields: [
					{
						fieldname: "brief",
						fieldtype: "Code",
						options: "Markdown",
						label: __("Brief for {0}", [frm.doc.name]),
						read_only: 1,
						default: brief,
					},
				],
				primary_action_label: __("Copy"),
				primary_action: () => frappe.utils.copy_to_clipboard(brief, __("Brief copied.")),
			});
			dialog.show();
		},
	});
}

frappe.ui.form.on("Enhancement Request", {
	refresh(frm) {
		if (frm.is_new() || !may_brief() || !has_created_tasks(frm)) return;
		frm.add_custom_button(__("Claude Code Brief"), () => show_brief(frm));
	},
});
