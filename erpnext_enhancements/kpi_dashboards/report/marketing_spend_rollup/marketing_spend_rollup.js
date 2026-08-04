// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Marketing Spend Rollup"] = {
	filters: [
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Month", "Channel"].join("\n"),
			default: "Month",
		},
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date" },
	],

	onload(report) {
		report.page.add_inner_button(__("Import Spend"), () => importDialog(report));
	},
};

function importDialog(report) {
	const dialog = new frappe.ui.Dialog({
		title: __("Import Marketing Spend"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				options: `<p class="text-muted">${__(
					"Paste CSV with columns for month, channel and amount. Headings are matched loosely — 'Month'/'Period'/'Date', 'Channel'/'Source'/'Line Item', 'Amount'/'Spend'/'Cost' all work. Optional: value stream, notes."
				)}</p>
				<p class="text-muted"><b>${__("Re-importing is safe")}</b>${__(
					": an existing month + channel row is updated in place, not duplicated."
				)}</p>`,
			},
			{
				fieldname: "content",
				label: __("CSV"),
				fieldtype: "Code",
				reqd: 1,
				options: "Text",
			},
			{
				fieldname: "batch_label",
				label: __("Batch label"),
				fieldtype: "Data",
				description: __("Stamped on every row so a bad import can be found and undone."),
			},
		],
		primary_action_label: __("Preview"),
		// Preview first, always. Spend is the denominator of every marketing
		// figure in the business; nobody should find out a column was misread by
		// looking at a dashboard next week.
		primary_action: (values) => {
			frappe.call({
				method: "erpnext_enhancements.kpi_dashboards.marketing_spend_import.preview_import",
				args: { content: values.content },
				freeze: true,
				callback: ({ message }) => message && showPreview(report, dialog, values, message),
			});
		},
	});
	dialog.show();
}

function showPreview(report, dialog, values, preview) {
	const problems = preview.problems || [];
	const renamed = preview.renamed || [];

	let html = `<p>${__("{0} rows read · {1} new · {2} will update · total {3}", [
		preview.rows.length,
		preview.will_create,
		(preview.will_update || []).length,
		format_currency(preview.total),
	])}</p>`;

	if (renamed.length) {
		// Surfaced, not silent: a wrong alias silently merges two budgets.
		html += `<p><b>${__("Channel names normalised")}</b></p><ul>${renamed
			.slice(0, 20)
			.map(
				(r) =>
					`<li>${__("line {0}", [r.line])}: <code>${frappe.utils.escape_html(
						r.from
					)}</code> → <code>${frappe.utils.escape_html(r.to)}</code></li>`
			)
			.join("")}</ul>`;
	}

	if (problems.length) {
		html += `<p style="color: var(--red-500);"><b>${__("Problems")}</b></p><ul>${problems
			.slice(0, 20)
			.map((p) => `<li>${frappe.utils.escape_html(p)}</li>`)
			.join("")}</ul>`;
	}

	frappe.confirm(
		html + `<p>${__("Import these rows?")}</p>`,
		() => {
			frappe.call({
				method: "erpnext_enhancements.kpi_dashboards.marketing_spend_import.run_import",
				args: { content: values.content, batch_label: values.batch_label },
				freeze: true,
				freeze_message: __("Importing spend…"),
				callback: ({ message }) => {
					if (!message) return;
					frappe.show_alert({
						message: __("{0} created, {1} updated", [message.created, message.updated]),
						indicator: "green",
					});
					dialog.hide();
					report.refresh();
				},
			});
		}
	);
}
