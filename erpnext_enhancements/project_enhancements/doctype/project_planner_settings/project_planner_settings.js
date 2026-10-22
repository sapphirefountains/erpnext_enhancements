// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Project Planner Settings: "Send me a preview" (Phase 3B). Emails the signed-in person what their
// own combined 6 AM message would say today, so the digest can be read before anyone else gets it.
// It never texts, never records the day as sent, and works with the digest switched off.
frappe.ui.form.on("Project Planner Settings", {
	refresh(frm) {
		if (!frappe.user.has_role(["System Manager", "Projects Manager"])) return;
		frm.add_custom_button(__("Send me a preview"), () => {
			frappe
				.call({
					method: "erpnext_enhancements.api.project_planner.send_digest_preview",
					freeze: true,
					freeze_message: __("Building your digest…"),
				})
				.then((r) => {
					const result = (r && r.message) || {};
					frappe.msgprint({
						title: result.sent ? __("Preview sent") : __("No preview sent"),
						message: frappe.utils.escape_html(result.message || ""),
						indicator: result.sent ? "green" : "orange",
					});
				});
		});
		// P9 (v1.715.0): the Monday weekly summary, to the signed-in person alone. It works with the
		// "Email the weekly summary on Mondays" box unticked, claims no week and mails nobody else, so the
		// email can be read before anyone is named to get it.
		frm.add_custom_button(__("Send me a weekly summary preview"), () => {
			frappe
				.call({
					method: "erpnext_enhancements.api.planner_overview.send_summary_preview",
					freeze: true,
					freeze_message: __("Building the summary…"),
				})
				.then((r) => {
					const result = (r && r.message) || {};
					frappe.msgprint({
						title: result.sent ? __("Preview sent") : __("No preview sent"),
						message: frappe.utils.escape_html(result.message || ""),
						indicator: result.sent ? "green" : "orange",
					});
				});
		});
	},
});
