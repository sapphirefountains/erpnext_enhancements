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
						// 1. Dry Run / Simulation
						frappe.call({
							method: "erpnext_enhancements.project_merge.get_merge_stats",
							args: {
								source_project: frm.doc.name,
								target_project: values.target_project,
							},
							freeze: true,
							freeze_message: __("Analyzing Merge Impact..."),
							callback: function (r) {
								if (r.message) {
									const stats = r.message;
									show_merge_confirmation(frm, values.target_project, stats);
								} else {
									frappe.msgprint(__("No documents found to merge."));
								}
							},
						});
					},
					__("Merge Project"),
					__("Analyze")
				);
			});
		}
	},
});

function show_merge_confirmation(frm, target_project, stats) {
	let total_docs = 0;
	let stats_html = `
        <table class="table table-bordered table-condensed">
            <thead>
                <tr>
                    <th>${__("DocType")}</th>
                    <th class="text-right">${__("Count")}</th>
                </tr>
            </thead>
            <tbody>
    `;

	// Build Table Rows
	for (const [doctype, data] of Object.entries(stats)) {
		total_docs += data.count;
		stats_html += `
            <tr>
                <td>${doctype}</td>
                <td class="text-right"><strong>${data.count}</strong></td>
            </tr>
        `;
	}

	stats_html += `
            </tbody>
        </table>
    `;

	// Detail View (Hidden by default)
	let details_html = `<div id="merge-details" style="display:none; margin-top: 15px; max-height: 200px; overflow-y: auto; background: var(--control-bg); padding: 10px; border-radius: 4px;">`;
	for (const [doctype, data] of Object.entries(stats)) {
		details_html += `<h6>${doctype}</h6><ul>`;
		data.items.forEach((item) => {
			details_html += `<li>${item}</li>`;
		});
		details_html += `</ul>`;
	}
	details_html += `</div>`;

	const content = `
        <div>
            <p>${__("You are about to merge <b>{0}</b> into <b>{1}</b>.", [
							frm.doc.name,
							target_project,
						])}</p>
            <p>${__("This will update <b>{0}</b> linked documents:", [total_docs])}</p>
            ${stats_html}
            <div class="text-right">
                <a class="text-muted text-small" onclick="$('#merge-details').toggle()">${__(
									"View Details"
								)}</a>
            </div>
            ${details_html}
            <p class="text-danger mt-4 small">
                ${__("Warning: This action cannot be undone. The source project will be cancelled.")}
            </p>
        </div>
    `;

	const d = new frappe.ui.Dialog({
		title: __("Confirm Merge"),
		width: 600,
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "preview_html",
				options: content,
			},
		],
		primary_action_label: __("Confirm Merge"),
		primary_action: function () {
			d.hide();
			execute_merge(frm, target_project);
		},
	});

	d.show();
}

function execute_merge(frm, target_project) {
	frappe.call({
		method: "erpnext_enhancements.project_merge.merge_projects",
		args: {
			source_project: frm.doc.name,
			target_project: target_project,
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
