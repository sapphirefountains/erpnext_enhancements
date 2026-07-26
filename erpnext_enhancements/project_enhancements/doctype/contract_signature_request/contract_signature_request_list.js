/**
 * @file Contract Signature Request list view.
 * @description Colour every row by what it needs from a human, not just by
 * status: a link nobody has opened reads differently from one that was opened
 * and abandoned, and both differ from one that has already been chased to
 * exhaustion. The indicator is the whole point of this list — nobody opens
 * individual signature requests.
 */

frappe.listview_settings["Contract Signature Request"] = {
	add_fields: ["status", "view_count", "reminders_sent", "expires_on", "document_drifted"],

	get_indicator(doc) {
		if (doc.status === "Signed") {
			return [
				doc.document_drifted ? __("Signed (text changed)") : __("Signed"),
				doc.document_drifted ? "orange" : "green",
				"status,=,Signed",
			];
		}
		if (doc.status === "Declined") return [__("Declined"), "red", "status,=,Declined"];
		if (doc.status === "Locked") return [__("Locked"), "red", "status,=,Locked"];
		if (doc.status === "Void") return [__("Void"), "gray", "status,=,Void"];
		if (doc.status === "Expired") return [__("Expired"), "gray", "status,=,Expired"];
		if (doc.status === "Superseded") return [__("Replaced"), "gray", "status,=,Superseded"];

		// Still live: what it needs from us depends on whether they've looked.
		if (doc.status === "Viewed") {
			return [
				doc.reminders_sent
					? __("Opened, chased {0}×", [doc.reminders_sent])
					: __("Opened, not signed"),
				"orange",
				"status,=,Viewed",
			];
		}
		return [
			doc.reminders_sent ? __("Chased {0}×", [doc.reminders_sent]) : __("Sent"),
			doc.reminders_sent ? "orange" : "blue",
			"status,=,Sent",
		];
	},
};
