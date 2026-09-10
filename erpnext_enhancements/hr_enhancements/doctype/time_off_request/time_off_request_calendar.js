// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// "Who is out that week" — the half of Nik's PTO ask that is not the approval
// flow, and the half dispatch actually opens. Frappe's own calendar view, so it
// costs a config object rather than a page: /app/time-off-request/view/calendar.
//
// Colour carries the status, and only Approved is green. A Requested day is not a
// day off yet, and a calendar that showed it as one would have somebody scheduling
// around a request that later gets declined.

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
	style_map: {
		Draft: "standard",
		Requested: "warning",
		Approved: "success",
		Declined: "danger",
		Canceled: "standard",
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
