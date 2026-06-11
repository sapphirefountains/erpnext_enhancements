/**
 * Travel Trip calendar configuration.
 *
 * Targets: the "Travel Trip" doctype calendar view ("who is traveling when").
 * Loaded via: hooks.py `doctype_calendar_js["Travel Trip"]`.
 *
 * The server event source (erpnext_enhancements.api.travel.get_events) emits
 * one all-day event per (trip, traveler) — a crew of three shows as three
 * rows on the same dates, so coordinator scheduling conflicts are visible
 * per person. Colors map to trip status (Planning=orange, Booked=blue,
 * In Progress=green, Completed/Closed=gray).
 */
frappe.views.calendar["Travel Trip"] = {
	field_map: {
		start: "start",
		end: "end",
		id: "name",
		title: "title",
		allDay: "allDay",
		color: "color",
	},
	get_events_method: "erpnext_enhancements.api.travel.get_events",
	filters: [
		{
			fieldtype: "Select",
			fieldname: "status",
			options: "Planning\nBooked\nIn Progress\nCompleted\nClosed",
			label: __("Status"),
		},
		{
			fieldtype: "Select",
			fieldname: "travel_type",
			options: "Domestic\nInternational\nLocal Site Visit",
			label: __("Travel Type"),
		},
		{
			fieldtype: "Link",
			fieldname: "project",
			options: "Project",
			label: __("Project"),
		},
	],
};
