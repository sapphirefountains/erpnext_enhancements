// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// "Who is out that week" — the half of Nik's PTO ask that is not the approval
// flow, and the half dispatch actually opens. Frappe's own calendar view, so it
// costs a config object rather than a page: /app/time-off-request/view/calendar.
//
// Colour carries the status, and only Approved is green. A Requested day is not a
// day off yet, and a calendar that showed it as one would have somebody scheduling
// around a request that later gets declined. `status` has to be in field_map for
// get_css_class to receive it.

frappe.views.calendar["Time Off Request"] = {
	field_map: {
		start: "from_date",
		end: "to_date",
		id: "name",
		title: "employee_name",
		status: "status",
		allDay: "all_day",
	},
	get_events_method: "frappe.desk.calendar.get_events",
	options: {
		// All-day blocks: a day off has no start time, and rendering one at midnight
		// would be a small lie repeated on every row.
		defaultView: "dayGridMonth",
	},
	// `get_css_class`, NOT `style_map`. The latter looks like the right key and is
	// dead config in v16: calendar.js's prepare_colors() branches only on
	// get_css_class and otherwise falls back to `d.color`, and grepping
	// origin/version-16 finds style_map declared in two places and consumed in
	// none. Shipping it would have coloured every status identically while the
	// comment above claimed only Approved was green -- exactly the sort of silent
	// no-op this repo keeps paying for.
	get_css_class: function (data) {
		if (data.status === "Approved") return "success";
		if (data.status === "Requested") return "warning";
		if (data.status === "Declined") return "danger";
		return "standard";
	},
	filters: [
		{
			fieldtype: "Link",
			fieldname: "employee",
			options: "Employee",
			label: __("Employee"),
		},
		{
			fieldtype: "Select",
			fieldname: "status",
			options: ["", "Draft", "Requested", "Approved", "Declined", "Canceled"].join("\n"),
			label: __("Status"),
			default: "Approved",
		},
	],
};
