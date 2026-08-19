// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// "What is waiting for me", on the page the sign-off email lands on.
//
// `training.signoff.get_signoff_queue` has existed since v1.215.0 with the
// docstring "for a supervisor's dashboard" and no caller of any kind. This is
// that dashboard, put where a supervisor already is rather than somewhere they
// would have to be told about.
//
// It is not the same thing as a saved filter, which is why it is worth an
// endpoint call: the queue answers "requests I may act on", and the server
// resolves that differently for a Training Manager (everybody's, because they
// hold the delegate path) than for a supervisor (only the ones naming them) —
// and it drops the caller's own requests from either, because nobody signs off
// their own competence. A list filter cannot express that, and a supervisor
// reading a filtered list has no way to know what it left out.

frappe.listview_settings["Training Signoff"] = {
	hide_name_column: true,

	get_indicator(doc) {
		if (doc.docstatus === 2) return [__("Cancelled"), "gray", "docstatus,=,2"];
		if (doc.docstatus === 0) return [__("Awaiting sign-off"), "orange", "docstatus,=,0"];
		if (doc.outcome === "Needs More Practice") {
			return [__("Needs more practice"), "red", "outcome,=,Needs More Practice"];
		}
		return [__("Competent"), "green", "outcome,=,Competent"];
	},

	onload(listview) {
		frappe
			.call({ method: "erpnext_enhancements.training.signoff.get_signoff_queue" })
			.then((r) => {
				const queue = (r && r.message) || [];
				if (!queue.length) return;

				// A count, and one click to see exactly those rows. Filtering by name
				// rather than by re-deriving the server's rule in the client: the point
				// of calling the endpoint is that the rule lives in one place.
				listview.page.add_inner_button(
					__("Waiting for you ({0})", [queue.length]),
					() => {
						listview.filter_area.clear().then(() => {
							listview.filter_area.add([
								[
									"Training Signoff",
									"name",
									"in",
									queue.map((row) => row.name),
								],
							]);
						});
					}
				);

				// Oldest first, because that is the order the server returns and the
				// order that matters: somebody has been on a "waiting for sign-off"
				// screen since that date and cannot finish their course without you.
				const oldest = queue[0];
				listview.page.set_indicator(
					__("{0} sign-off(s) waiting for you, oldest {1}", [
						queue.length,
						frappe.datetime.comment_when(oldest.creation),
					]),
					"orange"
				);
			});
	},
};
