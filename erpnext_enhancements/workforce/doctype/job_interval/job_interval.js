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

frappe.ui.form.on("Job Interval", {
	refresh(frm) {
		frm.trigger("ee_indicator");
		frm.trigger("ee_buttons");
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
