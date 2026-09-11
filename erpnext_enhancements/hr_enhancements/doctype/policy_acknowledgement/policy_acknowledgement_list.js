// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// The entry point for asking. Without it `request_acknowledgement` is another
// correct endpoint with no caller, which this app has now shipped three times.

frappe.listview_settings["Policy Acknowledgement"] = {
	add_fields: ["status"],

	get_indicator(doc) {
		const tone = { Requested: "orange", Signed: "green", Declined: "red" };
		return [__(doc.status), tone[doc.status] || "gray", `status,=,${doc.status}`];
	},

	onload(listview) {
		if (!frappe.user.has_role("HR Manager") && !frappe.user.has_role("System Manager")) return;
		listview.page.add_inner_button(__("Ask everybody to sign"), () => {
			const dialog = new frappe.ui.Dialog({
				title: __("Ask for an acknowledgement"),
				fields: [
					{ fieldtype: "Data", fieldname: "policy", label: __("Policy"), reqd: 1 },
					{
						fieldtype: "Data",
						fieldname: "version_label",
						label: __("Version"),
						// Without it, "he signed the handbook" says nothing about WHICH
						// handbook -- and the version people argue about is the one that
						// changed.
						description: __("Which revision. A new version asks everybody again."),
					},
					{ fieldtype: "Data", fieldname: "document_url", label: __("Link to it") },
					{
						fieldtype: "MultiSelectList",
						fieldname: "employees",
						label: __("Who"),
						reqd: 1,
						get_data: (txt) =>
							frappe.db.get_link_options("Employee", txt, { status: "Active" }),
					},
				],
				primary_action_label: __("Ask"),
				primary_action: (values) => {
					dialog.hide();
					frappe.call({
						method: "erpnext_enhancements.hr_enhancements.policies.request_acknowledgement",
						args: {
							policy: values.policy,
							version_label: values.version_label,
							document_url: values.document_url,
							employees: (values.employees || []).map((e) => e.value || e),
						},
						freeze: true,
						callback: (r) => {
							const made = ((r && r.message && r.message.created) || []).length;
							frappe.show_alert({
								message: made
									? __("Asked {0} person(s).", [made])
									: __("Everybody already has one for that version."),
								indicator: made ? "green" : "blue",
							});
							listview.refresh();
						},
					});
				},
			});
			dialog.show();
		});
	},
};
