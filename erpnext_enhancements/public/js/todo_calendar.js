frappe.views.calendar["ToDo"] = {
	get_events_method: "erpnext_enhancements.todo.get_events",
};

frappe.ui.form.on("ToDo", {
	validate: function (frm) {
		if (
			frm.doc.custom_calendar_datetime_end &&
			frm.doc.custom_calendar_datetime_start &&
			frm.doc.custom_calendar_datetime_end < frm.doc.custom_calendar_datetime_start
		) {
			frappe.throw(__("End date and time cannot be before start date and time"));
		}
	},
});
