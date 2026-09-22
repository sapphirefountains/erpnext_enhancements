// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Ad Spend ROAS. Rows are cohorts by lead month: a month's spend set against the deals its
// leads produce, whenever they close -- so the newest months read low and fill in. The rules
// live in marketing/core/roas.py. Deliberately no ad_spend_roas.html: a report print template
// is compiled whole, prose included (CLAUDE.md, "a report's .html print format").

frappe.query_reports["Ad Spend ROAS"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("Lead Month From"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.month_start(), -11),
		},
		{
			fieldname: "to_date",
			label: __("Lead Month To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "platform",
			label: __("Platform"),
			fieldtype: "Select",
			options: ["", "Google Ads", "Meta Ads", "LinkedIn Ads"],
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Campaign", "Platform"],
			default: "Campaign",
		},
		{
			fieldname: "window_days",
			label: __("Attribution Window (days)"),
			fieldtype: "Int",
			default: 365,
			description: __("How long after the ad touched the customer an Opportunity still counts."),
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		// The coverage gap reads as a gap: paid leads no campaign could be matched to.
		if (data && data.campaign_name === "(paid, not joinable)" && column.fieldname === "campaign_name") {
			return `<span style="color: var(--orange-600); font-weight: 600;">${formatted}</span>`;
		}
		return formatted;
	},
};
