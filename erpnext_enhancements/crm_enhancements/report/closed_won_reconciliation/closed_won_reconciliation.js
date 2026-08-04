// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Closed Won Reconciliation"] = {
	filters: [
		{
			// The backlog runs to 2014. A deal from then has no bearing on current
			// reporting, so the report opens on the part that does.
			fieldname: "from_date",
			label: __("Closed From"),
			fieldtype: "Date",
			default: "2024-01-01",
		},
		{ fieldname: "to_date", label: __("Closed To"), fieldtype: "Date" },
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{
			fieldname: "only_with_candidates",
			label: __("Only where a candidate exists"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "min_score",
			label: __("Minimum score"),
			fieldtype: "Int",
			default: 0,
		},
	],

	onload(report) {
		const currentRow = () => {
			const checked = report.datatable?.rowmanager?.getCheckedRows() || [];
			if (checked.length !== 1) {
				frappe.msgprint(
					__("Tick exactly one opportunity. Linking is deliberately one at a time.")
				);
				return null;
			}
			return report.data[checked[0]] || null;
		};

		report.page.add_inner_button(__("Show Candidates"), () => {
			const row = currentRow();
			if (!row) return;

			frappe.call({
				method: "erpnext_enhancements.crm_enhancements.reconciliation.get_candidates",
				args: { opportunity: row.opportunity, limit: 8 },
				freeze: true,
				callback: ({ message }) => {
					const candidates = message || [];
					if (!candidates.length) {
						frappe.msgprint({
							title: __("No candidates"),
							message: __(
								"This customer owns no Project. This opportunity needs a Project creating, not linking."
							),
						});
						return;
					}
					showCandidateDialog(report, row, candidates);
				},
			});
		});
	},
};

function showCandidateDialog(report, row, candidates) {
	// A table the reviewer reads, with the reasoning visible per row. The top
	// suggestion being wrong is an expected outcome, not an edge case — so the
	// runners-up and their reasons have to be on the same screen.
	const rows = candidates
		.map(
			(c) => `
		<tr>
			<td><a href="/app/project/${encodeURIComponent(c.project)}" target="_blank">${frappe.utils.escape_html(
				c.project
			)}</a><br><span class="text-muted small">${frappe.utils.escape_html(c.project_name || "")}</span></td>
			<td>${frappe.datetime.str_to_user(c.start_date) || ""}</td>
			<td>${format_currency(c.value)}</td>
			<td><b>${c.score}</b></td>
			<td class="small text-muted">${frappe.utils.escape_html(c.reasons.join("; "))}</td>
			<td><button class="btn btn-xs btn-primary ee-link-btn" data-project="${frappe.utils.escape_html(
				c.project
			)}" ${c.already_linked ? "disabled" : ""}>${
				c.already_linked ? __("Taken") : __("Link")
			}</button></td>
		</tr>`
		)
		.join("");

	const dialog = new frappe.ui.Dialog({
		title: __("Candidates for {0}", [row.opportunity]),
		size: "extra-large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "candidates",
				options: `
					<p class="text-muted">${__(
						"Ranked by date and value proximity within the same customer. The score is a hint, not a decision — read the reasons before linking."
					)}</p>
					<div style="overflow-x:auto;">
					<table class="table table-bordered" style="min-width:720px;">
						<thead><tr>
							<th>${__("Project")}</th><th>${__("Starts")}</th><th>${__("Value")}</th>
							<th>${__("Score")}</th><th>${__("Why")}</th><th></th>
						</tr></thead>
						<tbody>${rows}</tbody>
					</table></div>`,
			},
		],
	});

	dialog.show();

	dialog.$wrapper.on("click", ".ee-link-btn", function () {
		const project = $(this).data("project");
		frappe.confirm(
			__("Link {0} to {1}? This sets revenue attribution and is not undone from here.", [
				row.opportunity,
				project,
			]),
			() => {
				frappe.call({
					method:
						"erpnext_enhancements.crm_enhancements.reconciliation.link_opportunity_to_project",
					args: { opportunity: row.opportunity, project },
					freeze: true,
					callback: ({ message }) => {
						if (message && message.status === "linked") {
							frappe.show_alert({
								message: __("Linked {0} to {1}", [row.opportunity, project]),
								indicator: "green",
							});
							dialog.hide();
							report.refresh();
						}
					},
				});
			}
		);
	});
}
