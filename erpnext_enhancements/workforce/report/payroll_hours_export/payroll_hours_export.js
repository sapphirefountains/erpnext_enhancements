// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Payroll Hours Export"] = {
	filters: [
		{
			// Left blank, the server defaults to the PREVIOUS semi-monthly period —
			// the one actually being submitted. Defaulting to the current, still-open
			// period would hand somebody a half-finished file.
			fieldname: "from_date",
			label: __("Period From"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("Period To"),
			fieldtype: "Date",
		},
		{
			fieldname: "pay_date",
			label: __("Pay Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "include_projects",
			label: __("Break down by project"),
			fieldtype: "Check",
			default: 0,
		},
	],

	onload(report) {
		// The real deliverable. A Query Report's own Excel export cannot reproduce
		// the provider's six-line header block, and that block is what makes the
		// file recognisable to the clerk who receives it.
		report.page.add_inner_button(__("Download Workbook"), () => {
			const filters = report.get_values();
			const params = new URLSearchParams({
				from_date: filters.from_date || "",
				to_date: filters.to_date || "",
				pay_date: filters.pay_date || "",
				include_projects: filters.include_projects ? "1" : "0",
			});
			window.open(
				"/api/method/erpnext_enhancements.workforce.payroll_export.download_payroll_workbook?" +
					params.toString()
			);
		});
	},

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		// An employee with no provider number exports with a blank first column.
		if (column.fieldname === "emp_num" && data && data.employee && !data.emp_num) {
			return `<span style="color: var(--red-500); font-weight: 600;">${__("not set")}</span>`;
		}
		return formatted;
	},
};
