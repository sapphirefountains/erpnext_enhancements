// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Item Naming Audit"] = {
	filters: [
		{
			fieldname: "severity",
			label: __("Severity"),
			fieldtype: "Select",
			options: ["Any", "STOP", "FIX"].join("\n"),
			default: "Any",
		},
		{
			fieldname: "family",
			label: __("Code family"),
			fieldtype: "Select",
			options: ["Any", "vendor", "consumable", "product", "service", "unknown"].join("\n"),
			default: "Any",
		},
		{
			// Free text rather than a Select: the finding codes are declared in
			// item_naming_rules.SEVERITY and a hard-coded list here would be a second copy
			// that drifts. The Findings column shows the codes, so they are copy-pasteable.
			fieldname: "finding_code",
			label: __("Finding code"),
			fieldtype: "Data",
		},
		{
			// The 135-row QuickBooks shadow master: every one is item_code = item_name, so
			// they fail nearly every check and would be most of this list. One batch
			// retirement (WI-070 bucket A), not 135 decisions.
			fieldname: "include_deleted",
			label: __("Include '(deleted)' tombstones"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "severity" && data && data.severity) {
			const colour = data.severity === "STOP" ? "var(--red-500)" : "var(--orange-500)";
			return `<span style="color: ${colour}; font-weight: 600;">${formatted}</span>`;
		}
		if (column.fieldname === "item_code" && data && data.is_tombstone) {
			return `<span style="opacity: .6;" title="${__(
				"QuickBooks migration artefact — do not transact against it and do not rename it"
			)}">${formatted}</span>`;
		}
		return formatted;
	},
};
