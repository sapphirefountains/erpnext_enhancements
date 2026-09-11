// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// Checking out, and extending. Two buttons, and both have to work with one hand
// in a truck.
//
// The escalation stage is shown in words rather than as the integer it is stored
// as, because "2" tells a supervisor opening this record nothing and "your
// supervisor has been told" tells them what has already happened without them.

frappe.ui.form.on("Lone Work Session", {
	refresh(frm) {
		frm.trigger("ee_state");
		frm.trigger("ee_actions");
	},

	ee_state(frm) {
		if (frm.is_new()) return;
		const tone = { Open: "blue", Closed: "green", Overdue: "orange", Escalated: "red" };
		frm.page.set_indicator(__(frm.doc.status), tone[frm.doc.status] || "gray");

		if (frm.doc.status === "Closed") return;
		const said = {
			1: __("They have been asked whether they are out."),
			2: __("Their supervisor has been told."),
			3: __("The supervisor and the executives have been told."),
		}[frm.doc.escalation_stage];
		if (said) frm.dashboard.add_comment(said, "orange", true);
	},

	ee_actions(frm) {
		if (frm.is_new() || frm.doc.status === "Closed") return;
		frm.add_custom_button(__("I am out"), () => frm.trigger("ee_out")).addClass("btn-primary");
		frm.add_custom_button(__("Still working"), () => frm.trigger("ee_extend"));
	},

	ee_out(frm) {
		frm.call({
			method: "erpnext_enhancements.hr_enhancements.lonework.check_out",
			args: { session: frm.doc.name },
			freeze: true,
		}).then(() => {
			frappe.show_alert({ message: __("Checked out."), indicator: "green" });
			frm.reload_doc();
		});
	},

	ee_extend(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Still working"),
			fields: [
				{
					fieldtype: "Datetime",
					fieldname: "expected_out_by",
					label: __("Out by"),
					reqd: 1,
					// Default forward from now rather than from the old deadline, which has
					// usually passed by the time somebody presses this.
					default: frappe.datetime.add_minutes(frappe.datetime.now_datetime(), 60),
				},
			],
			primary_action_label: __("Extend"),
			primary_action: (values) => {
				dialog.hide();
				frm.call({
					method: "erpnext_enhancements.hr_enhancements.lonework.extend",
					args: { session: frm.doc.name, expected_out_by: values.expected_out_by },
					freeze: true,
				}).then(() => {
					// The stage resets server-side: somebody who was chased, answered and
					// extended must not be escalated to their supervisor five minutes later
					// on the strength of the old counter.
					frappe.show_alert({ message: __("Clock moved."), indicator: "blue" });
					frm.reload_doc();
				});
			},
		});
		dialog.show();
	},
});
