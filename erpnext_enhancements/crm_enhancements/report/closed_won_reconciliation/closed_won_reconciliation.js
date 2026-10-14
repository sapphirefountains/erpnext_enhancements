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
		{
			// Historical = created before 2025-08-01 and nobody's activity in 90 days
			// (crm_enhancements/historical.py). Off by default: those deals are Zoho
			// history, excluded from every won-without-project warning.
			fieldname: "include_historical",
			label: __("Include historical"),
			fieldtype: "Check",
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

	formatter(value, row, column, data, default_formatter) {
		// The customer, the close date and the amount: the report face (form kit P3.3).
		const face = window.erpnext_enhancements?.report_face;
		return face?.format
			? face.format(value, row, column, data, default_formatter)
			: default_formatter(value, row, column, data);
	},
};

function showCandidateDialog(report, row, candidates) {
	// A table the reviewer reads, with the reasoning visible per row. The top
	// suggestion being wrong is an expected outcome, not an edge case — so the
	// runners-up and their reasons have to be on the same screen.
	// The link, the start date and the value come from the report face (form kit P3.3), so
	// they read exactly as the same things do in the report behind the dialog.
	// A Desk tab opened before the face shipped has no face: then the cells are the plain,
	// escaped values (unlinked), never an error that leaves the dialog closed.
	const face = window.erpnext_enhancements?.report_face;
	const plain = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	const cell = {
		project: (c) => (face?.party ? face.party("Project", c.project, c.project, { new_tab: true }) : plain(c.project)),
		date: (c) => (face?.date ? face.date(c.start_date) : plain(c.start_date)),
		money: (c) => (face?.money ? face.money(c.value) : plain(c.value)),
	};
	const rows = candidates
		.map(
			(c) => `
		<tr>
			<td>${cell.project(c)}<br><span class="text-muted small">${frappe.utils.escape_html(
				c.project_name || ""
			)}</span></td>
			<td>${cell.date(c)}</td>
			<td>${cell.money(c)}</td>
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
