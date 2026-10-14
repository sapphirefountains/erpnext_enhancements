// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// A dry run of the Party mirror fix (form kit P3.2): Opportunities whose shown Primary Contact and
// hidden Contact Person disagree, with what the save rule would make them
// (crm_enhancements/party_mirrors.py). Showing the report writes nothing, and there is no apply
// button: the fix ships as a patch once the list is approved.
frappe.query_reports["Party Mirror Preview"] = {
	filters: [
		{
			fieldname: "status",
			label: __("Show"),
			fieldtype: "Select",
			options: [
				"Every difference",
				"Both set, different",
				"Only the hidden field set",
				"Only the visible field set",
			].join("\n"),
			default: "Every difference",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		// The record, account and contacts are linked and shown by name: the report face (form kit
		// P3.3). The status color is ours.
		const face = window.erpnext_enhancements?.report_face;
		const formatted = face?.format
			? face.format(value, row, column, data, default_formatter)
			: default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data && data.status) {
			const color = data.status === "Both set, different" ? "var(--orange-600)" : "var(--text-muted)";
			return `<span style="color: ${color}; font-weight: 600;">${formatted}</span>`;
		}
		return formatted;
	},
};
