// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["OSHA Privacy Case List"] = {
	filters: [
		{
			fieldname: "year",
			label: __("Year"),
			fieldtype: "Int",
			default: new Date().getFullYear(),
			reqd: 1,
			// The log is kept per CALENDAR year and a case belongs to the year of the
			// injury, not of the record -- so a case opened in January for a December
			// injury sits on the previous year's form.
			description: __("Calendar year of the injury."),
		},
	],
};
