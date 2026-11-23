// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// Job Interval form: the tracking-health indicator and the two buttons that
// take a supervisor from a suspicious row to the evidence.
//
// "View on Timeline" opens the Location Timeline page pre-filtered to this
// employee and this interval's day(s) via frappe.route_options — the page reads
// {employee, from_date, to_date} on load. "Refresh Tracking Health" recomputes
// the health block from the Time Kiosk Log rows on demand, for the case where a
// catch-up batch arrived after the interval closed.
//
// Both buttons are drawn only for the roles that can open the page / call the
// endpoint (System Manager, HR Manager, Projects Manager). A technician looking
// at their own interval gets the indicator and nothing that would 403.

const EE_TIMELINE_ROLES = ["System Manager", "HR Manager", "Projects Manager"];

const EE_HEALTH_COLOURS = {
	Pending: "gray",
	Good: "green",
	Gaps: "orange",
	None: "red",
	Off: "gray",
};

// A hand edit waiting for approval (workforce/time_edits.py) is left out of payroll
// until it is approved. The form says so, and shows "Approve Edit" / "Send Back" to a
// reader the server says may decide it (__onload.ee_may_decide_edit: the employee's
// reports_to supervisor, an HR Manager or a System Manager, who is neither the employee
// nor the person who made the edit). The queue is the Time Edit Approvals page
// (/desk/time-edit-approvals); the list's "Edit Approval" filter set to Waiting shows the
// same entries to those who can read the list.

frappe.ui.form.on("Job Interval", {
	refresh(frm) {
		frm.trigger("ee_indicator");
		frm.trigger("ee_buttons");
		frm.trigger("ee_edit_approval");
	},

	ee_edit_approval(frm) {
		if (frm.is_new()) return;
		const state = frm.doc.edit_approval || "";
		if (state !== "Waiting" && state !== "Sent Back") return;

		const esc = frappe.utils.escape_html;
		const when = (v) => (v ? frappe.datetime.str_to_user(v) : __("open"));
		let text =
			state === "Waiting"
				? __("Edited by {0} on {1}. Waiting for approval; not counted toward payroll.", [
						esc(frm.doc.edited_by || ""),
						when(frm.doc.edited_on),
				  ])
				: __("Sent back by {0}. Not counted toward payroll.", [esc(frm.doc.edit_reviewed_by || "")]);
		if (frm.doc.edit_previous_start || frm.doc.edit_previous_end) {
			text +=
				" " +
				__("Before the edit: {0} to {1}.", [when(frm.doc.edit_previous_start), when(frm.doc.edit_previous_end)]);
		} else {
			text += " " + __("Added by hand.");
		}
		frm.dashboard.add_comment(text, state === "Waiting" ? "orange" : "red", true);

		if (!(frm.doc.__onload && frm.doc.__onload.ee_may_decide_edit)) return;
		const decide = (method, label, message) => {
			frappe.prompt(
				[{ fieldname: "note", fieldtype: "Small Text", label: __("Note to the employee (optional)") }],
				(values) => {
					frappe.call({
						method: "erpnext_enhancements.workforce.time_edits." + method,
						args: { interval: frm.doc.name, note: values.note || "" },
						freeze: true,
						callback: () => {
							frappe.show_alert({ message, indicator: "green" });
							frm.reload_doc();
						},
					});
				},
				label,
				label
			);
		};
		frm.add_custom_button(
			__("Approve Edit"),
			() => decide("approve_time_edit", __("Approve Edit"), __("Approved. The interval counts toward payroll.")),
			__("Edit Approval")
		);
		if (state === "Waiting") {
			frm.add_custom_button(
				__("Send Back"),
				() => decide("send_back_time_edit", __("Send Back"), __("Sent back. The employee has been told.")),
				__("Edit Approval")
			);
		}
	},

	ee_indicator(frm) {
		if (frm.is_new()) return;
		const health = frm.doc.tracking_health || "Pending";
		const labels = {
			Pending: __("Tracking pending"),
			Good: __("Tracking good"),
			Gaps: __("Tracking gaps"),
			None: __("No tracking"),
			Off: __("Tracking off"),
		};
		let label = labels[health] || health;
		if (frm.doc.tracking_health === "Gaps" && frm.doc.tracking_coverage_pct != null) {
			label += ` (${Math.round(frm.doc.tracking_coverage_pct)}%)`;
		}
		frm.page.set_indicator(label, EE_HEALTH_COLOURS[health] || "gray");

		if (frm.doc.auto_closed) {
			frm.dashboard.add_comment(
				__("Auto-closed: {0}", [frappe.utils.escape_html(frm.doc.auto_close_reason || "")]),
				"orange",
				true
			);
		}
		if (frm.doc.offsite_start) {
			frm.dashboard.add_comment(
				__("Clocked in {0} m from the site (geofence {1} m).", [
					Math.round(frm.doc.start_distance_m || 0),
					frm.doc.site_radius_m || 0,
				]),
				"orange",
				true
			);
		}
	},

	ee_buttons(frm) {
		if (frm.is_new()) return;
		const is_manager = EE_TIMELINE_ROLES.some((r) => frappe.user.has_role(r));
		if (!is_manager) return;

		frm.add_custom_button(__("View on Timeline"), () => {
			const from_date = frappe.datetime.get_datetime_as_string(frm.doc.start_time).slice(0, 10);
			const to_date = frm.doc.end_time
				? frappe.datetime.get_datetime_as_string(frm.doc.end_time).slice(0, 10)
				: from_date;
			frappe.route_options = { employee: frm.doc.employee, from_date, to_date };
			frappe.set_route("location-timeline");
		});

		frm.add_custom_button(__("Refresh Tracking Health"), () => {
			frappe.call({
				method: "erpnext_enhancements.api.time_kiosk.refresh_tracking_health",
				args: { job_interval: frm.doc.name },
				freeze: true,
				freeze_message: __("Recomputing from the location log…"),
				callback: (r) => {
					const h = r.message || {};
					frappe.show_alert({
						message: __("Tracking health: {0} ({1}% covered, {2} fixes)", [
							h.health,
							h.coverage_pct,
							h.fix_count,
						]),
						indicator: EE_HEALTH_COLOURS[h.health] || "blue",
					});
					frm.reload_doc();
				},
			});
		});
	},
});
