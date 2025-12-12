frappe.ui.form.on("Project", {
	refresh: function (frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Merge Project"), function () {
				frappe.prompt(
					[
						{
							label: __("Target Project"),
							fieldname: "target_project",
							fieldtype: "Link",
							options: "Project",
							reqd: 1,
							get_query: function () {
								return {
									filters: [["Project", "name", "!=", frm.doc.name]],
								};
							},
						},
					],
					function (values) {
						frappe.confirm(
							__(
								"Are you sure you want to merge this project into {0}? This action cannot be undone.",
								[values.target_project]
							),
							function () {
								frappe.call({
									method: "erpnext_enhancements.erpnext_enhancements.project_merge.merge_projects",
									args: {
										source_project: frm.doc.name,
										target_project: values.target_project,
									},
									freeze: true,
									freeze_message: __("Merging Projects..."),
									callback: function (r) {
										if (!r.exc) {
											frappe.msgprint(r.message);
											frm.reload_doc();
										}
									},
								});
							}
						);
					},
					__("Merge Project"),
					__("Merge")
				);
			});
		}
	},
});
