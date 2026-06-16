/**
 * Asset Booking calendar configuration.
 *
 * Targets: the "Asset Booking" doctype calendar view.
 * Loaded via: hooks.py `doctype_calendar_js["Asset Booking"]`.
 *
 * Registers the calendar field-map (start/end/id/title) and points the view at
 * the server-side event source
 * erpnext_enhancements.asset_management.doctype.asset_booking.asset_booking.get_events.
 */
frappe.views.calendar["Asset Booking"] = {
	field_map: {
		"start": "from_datetime",
		"end": "to_datetime",
		"id": "name",
		"title": "title",
		"allDay": "allDay"
	},
    get_events_method: "erpnext_enhancements.asset_management.doctype.asset_booking.asset_booking.get_events"
};
