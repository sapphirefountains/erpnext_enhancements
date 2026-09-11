// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["OSHA 300A Summary"] = {
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
		{
			fieldname: "total_hours",
			label: __("Total hours worked"),
			fieldtype: "Int",
			// NOT derived. Payroll runs through QuickBooks and an outside bureau, so the
			// number is not in ERPNext -- and a plausible figure from headcount x 2,080
			// would be wrong by exactly the overtime this crew works. It is the
			// denominator of every incidence rate an insurer computes.
			description: __("From the payroll report. The form asks for it and we do not hold it."),
		},
	],
};
