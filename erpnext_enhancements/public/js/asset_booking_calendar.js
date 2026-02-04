frappe.views.calendar["Asset Booking"] = {
	field_map: {
		"start": "from_datetime",
		"end": "to_datetime",
		"id": "name",
		"title": "title",
		"allDay": "allDay"
	},
    get_events_method: "erpnext_enhancements.enhancements_core.doctype.asset_booking.asset_booking.get_events"
};
