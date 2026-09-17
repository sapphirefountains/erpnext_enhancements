// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// Approve / Decline for a Time Correction Request, drawn for the people the
// server will let decide: HR Manager, Projects Manager, System Manager, or the
// employee's reports_to user. The client check decides which buttons to DRAW;
// erpnext_enhancements.workforce.corrections.approve_request / decline_request
// decide whether the action is allowed. A button the server refuses is a
// confusing button, never a hole.

const EE_TCR_REVIEW_ROLES = ["System Manager", "HR Manager", "Projects Manager"];

frappe.ui.form.on("Time Correction Request", {
	refresh(frm) {
		frm.trigger("ee_indicator");
		frm.trigger("ee_review_buttons");
	},

	ee_indicator(frm) {
		if (frm.is_new()) return;
		const colours = { Requested: "orange", Approved: "green", Declined: "red", Canceled: "gray" };
		frm.page.set_indicator(__(frm.doc.status), colours[frm.doc.status] || "gray");
	},

	ee_review_buttons(frm) {
		if (frm.is_new() || frm.doc.status !== "Requested") return;

		const draw = () => {
			frm.add_custom_button(__("Approve"), () => frm.trigger("ee_approve"), __("Review"));
			frm.add_custom_button(__("Decline"), () => frm.trigger("ee_decline"), __("Review"));
		};

		if (EE_TCR_REVIEW_ROLES.some((r) => frappe.user.has_role(r))) {
			draw();
			return;
		}
		// Not a manager: draw the buttons only if this user supervises the employee.
		frappe.db
			.get_value("Employee", frm.doc.employee, "reports_to")
			.then((r) => {
				const reports_to = r && r.message && r.message.reports_to;
				if (!reports_to) return;
				return frappe.db.get_value("Employee", reports_to, "user_id").then((u) => {
					if (u && u.message && u.message.user_id === frappe.session.user) draw();
				});
			});
	},

	ee_approve(frm) {
		frappe.prompt(
			[
				{
					fieldname: "note",
					fieldtype: "Small Text",
					label: __("Note to the technician (optional)"),
				},
			],
			(values) => {
				frappe.call({
					method: "erpnext_enhancements.workforce.corrections.approve_request",
					args: { name: frm.doc.name, note: values.note || "" },
					freeze: true,
					freeze_message: __("Applying the correction…"),
					callback: () => {
						frappe.show_alert({ message: __("Approved and applied."), indicator: "green" });
						frm.reload_doc();
					},
				});
			},
			__("Approve {0}", [frm.doc.name]),
			__("Approve")
		);
	},

	ee_decline(frm) {
		frappe.prompt(
			[
				{
					fieldname: "note",
					fieldtype: "Small Text",
					label: __("Why (the technician will see this)"),
					reqd: 1,
				},
			],
			(values) => {
				frappe.call({
					method: "erpnext_enhancements.workforce.corrections.decline_request",
					args: { name: frm.doc.name, note: values.note || "" },
					freeze: true,
					callback: () => {
						frappe.show_alert({ message: __("Declined."), indicator: "red" });
						frm.reload_doc();
					},
				});
			},
			__("Decline {0}", [frm.doc.name]),
			__("Decline")
		);
	},
});
