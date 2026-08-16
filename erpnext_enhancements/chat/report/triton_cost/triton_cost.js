// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Triton Cost"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From"),
			fieldtype: "Date",
			// Defaults to the last seven days because the question this report exists to
			// answer is literally "what did Triton cost last week" — opening on the whole
			// table would make the common case the one that needs configuring.
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -7),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "model_used",
			label: __("Model"),
			fieldtype: "Data",
			// Data rather than a Select: the model ids come from Triton's router and change
			// without this app being redeployed, so a hardcoded option list would go stale
			// silently and hide whichever tier was added most recently.
		},
		{
			fieldname: "origin",
			label: __("Origin"),
			fieldtype: "Select",
			options: ["", "Chat", "Widget", "API"].join("\n"),
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);

		// Two columns are quality signals wearing cost clothing, so they are the two that get
		// colour. Everything else stays plain — a report where everything is highlighted is a
		// report where nothing is.
		if (column.fieldname === "citation_miss_pct" && data && data.citation_miss_pct > 5) {
			return `<span style="color: var(--text-on-red, #b91c1c); font-weight: 600">${formatted}</span>`;
		}
		if (column.fieldname === "errors" && data && data.errors > 0) {
			return `<span style="color: var(--text-on-red, #b91c1c)">${formatted}</span>`;
		}
		return formatted;
	},
};
