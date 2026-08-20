// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Party Naming Audit"] = {
	filters: [
		{
			// Three doctypes, one report. The columns are deliberately generic — Record,
			// Doctype, the name it carries, the party it belongs to — because the question
			// is the same one in all three cases and three reports would be three places to
			// keep in step.
			fieldname: "target_doctype",
			label: __("Doctype"),
			fieldtype: "Select",
			options: ["Project", "Opportunity", "Address"].join("\n"),
			default: "Project",
			reqd: 1,
		},
		{
			fieldname: "severity",
			label: __("Severity"),
			fieldtype: "Select",
			options: ["Any", "STOP", "FIX"].join("\n"),
			default: "Any",
		},
		{
			// Free text rather than a Select: the codes are declared in
			// party_naming_rules.SEVERITY and a hard-coded list here would be a second copy
			// that drifts. The Findings column shows them, so they are copy-pasteable.
			fieldname: "finding_code",
			label: __("Finding code"),
			fieldtype: "Data",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "severity" && data && data.severity) {
			const colour = data.severity === "STOP" ? "var(--red-500)" : "var(--orange-500)";
			return `<span style="color: ${colour}; font-weight: 600;">${formatted}</span>`;
		}
		if (column.fieldname === "party" && data && data.party === "—") {
			return `<span style="color: var(--text-muted);" title="${__(
				"No party is linked — the name may well be right and the link is what is missing"
			)}">${formatted}</span>`;
		}
		return formatted;
	},
};
