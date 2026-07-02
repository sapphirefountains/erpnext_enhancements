// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// List-view triage for Water Feature Designs: the indicator answers "which
// designs need attention" without opening them — blockers (red) beat warnings
// (orange) beat package-ready (green) beat plain status. The counts are
// denormalized onto the doc by recompute(), so the list stays a cheap query.

frappe.listview_settings["Water Feature Design"] = {
	add_fields: ["blocker_count", "warning_count", "issue_ready", "status"],
	get_indicator(doc) {
		if (cint(doc.blocker_count) > 0) {
			return [__("{0} Blockers", [cint(doc.blocker_count)]), "red", "blocker_count,>,0"];
		}
		if (cint(doc.warning_count) > 0) {
			return [__("{0} Warnings", [cint(doc.warning_count)]), "orange", "warning_count,>,0"];
		}
		if (cint(doc.issue_ready) && doc.status !== "Issued") {
			return [__("Package Ready"), "green", "issue_ready,=,1"];
		}
		const colors = {
			Draft: "gray",
			"Inputs Gathered": "blue",
			Calculated: "blue",
			Reviewed: "green",
			Issued: "green",
		};
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
};
