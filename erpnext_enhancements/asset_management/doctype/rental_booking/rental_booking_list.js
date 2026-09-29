// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Rental Booking list: colour each row by status.
const RENTAL_STATUS_COLORS = {
	Tentative: "orange",
	Confirmed: "blue",
	Out: "purple",
	Returned: "cyan",
	Closed: "green",
	Expired: "gray",
	Canceled: "red",
};

frappe.listview_settings["Rental Booking"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		return [__(doc.status), RENTAL_STATUS_COLORS[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
