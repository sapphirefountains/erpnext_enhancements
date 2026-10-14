/**
 * Project form: View > Open in Project Planner (custom pages S5b, v1.667.0).
 *
 * Opens the Project Planner with this project as its Project filter. The planner reads
 * `project` from frappe.route_options and consumes it (project_planner.js, handle_route).
 *
 * frappe.set_route with an options object, never an href or a hand-built URL: on v16.50 it
 * fills frappe.route_options AND writes ?project= to the address, and it is a history entry of
 * its own, so Back returns to this form. A project that has not started yet opens on the week it
 * starts. The planner lists only the projects with tasks in the range it shows, and drops a
 * filter for any other, so this week's view would show nothing and forget the filter.
 *
 * Shown only to those whose roles open the page: frappe.boot.page_info lists the Pages a
 * person's roles reach. Anyone else would only be refused by the page.
 *
 * Loaded through doctype_js["Project"], which concatenates every Project script into one: so
 * nothing here is declared at the top level.
 */
frappe.ui.form.on("Project", {
	refresh(frm) {
		const pages = (frappe.boot && frappe.boot.page_info) || {};
		if (frm.is_new() || !pages["project-planner"]) return;
		frm.add_custom_button(
			__("Open in Project Planner"),
			() => {
				const options = { project: frm.doc.name };
				const starts = String(frm.doc.expected_start_date || "").slice(0, 10);
				if (starts && starts > frappe.datetime.get_today()) {
					frappe.set_route("project-planner", "week", starts, options);
				} else {
					frappe.set_route("project-planner", options);
				}
			},
			__("View")
		);
	},
});
