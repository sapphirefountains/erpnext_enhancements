// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// Training Assignment list: the door again, one level up.
//
// `permission_query_conditions` already scopes this list to the learner's own rows
// (a supervisor also sees their reports'), so for the fifteen people who hold
// Training Learner this list IS "what I owe" -- which is why the learner workspace
// links straight at it and why the button here needs no filter of its own.
//
// The indicator is the reason a learner can tell at a glance which row is the one
// that matters. Frappe's guess_colour() would give all seven statuses the same grey,
// the same way it did for the seven Purchase Order stages.

frappe.listview_settings["Training Assignment"] = {
	add_fields: ["status", "course", "due_date"],

	get_indicator(doc) {
		const colours = {
			"Not Started": "orange",
			"In Progress": "blue",
			"Awaiting Sign-off": "purple",
			Completed: "green",
			Overdue: "red",
			Waived: "grey",
			Cancelled: "grey",
		};
		return [__(doc.status), colours[doc.status] || "grey", "status,=," + doc.status];
	},

	onload(listview) {
		// Not a bulk action: this opens the learner surface, which is about the person
		// looking at it rather than about the ticked rows.
		listview.page.add_inner_button(__("Open training"), () => {
			frappe.set_route("learn");
		});
	},
};
