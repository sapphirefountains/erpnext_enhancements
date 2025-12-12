// Ensure the settings object exists
frappe.listview_settings["ToDo"] = frappe.listview_settings["ToDo"] || {};

// Extend with new settings
Object.assign(frappe.listview_settings["ToDo"], {
	add_fields: ["custom_calendar_datetime_start", "custom_calendar_datetime_end", "description"],
	gantt: {
		field_map: {
			start: "custom_calendar_datetime_start",
			end: "custom_calendar_datetime_end",
			id: "name",
			title: "description",
		},
	},
});
