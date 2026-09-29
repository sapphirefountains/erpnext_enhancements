// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Project form: the event-rental link (v1.562.0).
 *
 * A live Rental Booking owns its Project's event schedule and mirrors it onto the
 * Project's Events fields (asset_management/rental_availability.push_schedule_to_project).
 * An edit made here would be overwritten by the booking's next save, so while one exists
 * those fields are read-only and a banner says where to change them.
 *
 * On an Events project with no booking, Create > Rental Booking starts one with the
 * customer and schedule filled in (the booking form pulls the schedule on `project`).
 */

(() => {
	const SCHEDULE_FIELDS = [
		"custom_delivery_date_time",
		"custom_setup_date_time",
		"custom_event_date_time",
		"custom_take_down_date_time",
	];

	frappe.ui.form.on("Project", {
		refresh(frm) {
			if (frm.is_new()) {
				return;
			}
			frappe
				.call({
					method: "erpnext_enhancements.asset_management.rental_availability.get_project_rental",
					args: { project: frm.doc.name },
				})
				.then(({ message: booking }) => {
					const locked = Boolean(booking);
					SCHEDULE_FIELDS.forEach((f) => {
						if (frm.fields_dict[f]) {
							frm.set_df_property(f, "read_only", locked ? 1 : 0);
						}
					});
					if (booking) {
						frm.dashboard.set_headline_alert(
							__("Delivery, setup, event and take-down times come from rental {0} ({1}). Change them there.", [
								`<a href="/desk/rental-booking/${encodeURIComponent(booking.name)}">${frappe.utils.escape_html(booking.name)}</a>`,
								__(booking.status),
							]),
							"blue"
						);
					} else if (frm.doc.project_type === "Events" && frappe.model.can_create("Rental Booking")) {
						frm.add_custom_button(
							__("Rental Booking"),
							() => frappe.new_doc("Rental Booking", { project: frm.doc.name, customer: frm.doc.customer }),
							__("Create")
						);
					}
				});
		},
	});
})();
